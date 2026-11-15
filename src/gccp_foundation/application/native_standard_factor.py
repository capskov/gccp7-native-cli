"""Apply the reviewed Beijing 2025 short-drive standard factor to one selected quota."""

from contextlib import closing
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

from .native_build import TEMPLATES, compile_bridge, profile_path
from .native_cli import Rejected, digest, encoded, initialize_commands, runtime_config
from .native_delete import load_bridge, save_result
from .native_runtime import _replace, find_section_path, normalize_quantity, render_transport
from .native_worker_support import (
    Ledger, RuntimeProfile, SessionManager, WindowsDesktop, execution_lock, project_window_prefix, rows, table,
)
from .ports import SessionContext
from gccp_foundation.storage.native_save_attempts import NativeSaveAttempts


RULE = '顶进长度≤60m 人工*1.15,机械*1.15'
QUOTA_NAME = '泥水平衡掘进混凝土管顶进 管内径(mm以内) 2150'


def apply_short_drive_factor(state, runtime_root, inspector, request, *, verify_only=False):
    command, project_id = request['command_id'], request['project_id']
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', command):
        raise Rejected('IDENTIFIER_INVALID', 'Provide a bounded command ID')
    request['quantity'] = normalize_quantity(request['quantity'])
    if Decimal(request['quantity']) > 60:
        raise Rejected('CONVERSION_QUANTITY_OUT_OF_SCOPE', 'Short drive applies to at most 60 m')
    signature = digest(request)
    with closing(Ledger(state)) as store:
        initialize_commands(store)
        project = store.db.execute('SELECT * FROM projects WHERE project_id=?', (project_id,)).fetchone()
        if project is None:
            raise Rejected('PROJECT_NOT_BOUND', 'Bind the current project first')
        with execution_lock(store.root, project['file_id']):
            prior = store.db.execute('SELECT * FROM native_cli_commands WHERE command_id=?', (command,)).fetchone()
            if prior is not None:
                if prior['request_digest'] != signature:
                    raise Rejected('IDEMPOTENCY_CONFLICT', 'Command parameters changed')
                if prior['phase'] == 'readback_verified':
                    return {**json.loads(prior['result']), 'receipt_only': True, 'new_business_dispatches': 0}
                if not verify_only:
                    raise Rejected('NEEDS_RECONCILIATION', 'Inspect the original conversion without dispatching again')
            elif verify_only:
                raise Rejected('COMMAND_NOT_FOUND', 'No original standard conversion command was recorded')

            ctx = SessionContext(**json.loads(project['metadata'])['session'])
            profile = profile_path(ctx.profile_id)
            manager = SessionManager(store, WindowsDesktop(), RuntimeProfile.load(profile))
            meta = manager.validate_session(ctx)
            if (meta.get('dirty') or store.load_pending(project_id) or NativeSaveAttempts(store).active(project_id)
                    or store.db.execute("SELECT 1 FROM native_cli_commands WHERE project_id=? AND phase='armed_unknown'"
                                        " AND command_id<>?", (project_id, command)).fetchone()):
                raise Rejected('PROJECT_NEEDS_RECONCILIATION', 'Resolve the original intent before converting')
            config, worker = runtime_config(inspector)
            if config.get('runtime_kind') != 'inspector':
                raise Rejected('INSPECTOR_REQUIRED', 'Use the bound read-only inspector')
            observed = subprocess.run([sys.executable, '-X', 'utf8', str(worker), '--inspect'], capture_output=True,
                                      text=True, encoding='utf-8', timeout=50, check=True)
            snapshot = json.loads(observed.stdout)
            if (snapshot['pid'] != ctx.pid or snapshot['created_filetime'] != ctx.created_filetime
                    or not snapshot['callback_on_app_thread']):
                raise Rejected('SESSION_CHANGED', 'Conversion preflight does not match the bound session')
            if verify_only:
                return verify_prior(store, ctx, prior, snapshot)
            budget = table(snapshot, '工程量表达式')
            selection = budget['current']
            parent_path = find_section_path(budget['rows'], request['parent_sections'])['row_path_session_only']
            path = selection.get('row_path_session_only', '')
            if (not selection.get('verified') or path.rsplit('/', 1)[0] != parent_path
                    or selection['values'][:4] != ['11-34', '定', QUOTA_NAME, 'm']
                    or normalize_quantity(selection['values'][6]) != request['quantity']):
                raise Rejected('CONVERSION_TARGET_MISMATCH', 'Select the reviewed 11-34 at the exact section and quantity')
            verify_rule(snapshot, applied=False)
            directory = Path(runtime_root).resolve()/'standard-factor'/uuid.uuid4().hex
            bridge = compile_factor(directory, profile, meta, command, path)
            preflight = bridge.call('inspect', timeout=25)
            latest_budget = table(preflight, '工程量表达式')
            if latest_budget['rows'] != budget['rows'] or latest_budget['current'] != selection:
                raise Rejected('CONVERSION_PREFLIGHT_CHANGED', 'Selection or budget changed during preparation')
            verify_rule(preflight, applied=False)
            intent = 'intent.' + uuid.uuid4().hex
            proof = store.put_evidence(project_id, ctx.session_id, 'native_standard_factor_preflight',
                                       dict(request=request, capture=preflight, intent_id=intent))
            result = dict(command_id=command, status='armed_unknown', runtime=str(directory),
                          preflight_evidence_id=proof, save_dispatches=0)
            with store._transaction():
                store.db.execute('INSERT INTO native_cli_commands VALUES(?,?,?,?,?,?)',
                                 (command, project_id, signature, 'armed_unknown', encoded(result), int(time.time()*1000)))
            try:
                action = bridge.call('standard.factor', intent, timeout=40, expected_target=selection['values'])
                if action['business_dispatches'] != 1:
                    raise ValueError('Expected exactly one standard conversion dispatch')
                after = bridge.call('inspect', timeout=30)
                result['ipc_records'] = bridge.records
                return finish_readback(store, ctx, result, preflight, action, after)
            except (InvalidOperation, ValueError, OSError, RuntimeError, KeyError) as exc:
                result.update(error=str(exc), ipc_records=bridge.records)
                save_result(store, result)
                raise Rejected('NEEDS_RECONCILIATION', 'Read back the original conversion without dispatching again') from exc


def verify_prior(store, ctx, prior, snapshot):
    result = json.loads(prior['result'])
    proof = store.read_evidence(result['preflight_evidence_id'], ctx.project_id)
    before = proof['data']['capture']
    attempts = [json.loads(Path(path).read_text(encoding='utf-8')) for path in result.get('ipc_records', [])]
    actions = [attempt for attempt in attempts if attempt['operation'] == 'standard.factor']
    if (proof['kind'] != 'native_standard_factor_preflight' or proof['session_id'] != ctx.session_id
            or len(actions) != 1 or 'receipt' not in actions[0]):
        raise Rejected('CONVERSION_RECEIPT_REQUIRED', 'Original successful conversion receipt is required')
    action = actions[0]['receipt']
    if (action.get('pid') != ctx.pid or action.get('created_filetime') != ctx.created_filetime
            or actions[0]['request'].get('outer_intent_id') != proof['data']['intent_id']
            or action.get('business_dispatches') != 1 or action.get('setter_accepted') is not True):
        raise Rejected('CONVERSION_RECEIPT_REQUIRED', 'Original conversion receipt does not match the durable intent')
    return {**finish_readback(store, ctx, result, before, action, snapshot), 'new_business_dispatches': 0}


def finish_readback(store, ctx, result, before, action, after):
    verify_rule(after, applied=True)
    original = table(before, '工程量表达式')
    path = original['current']['row_path_session_only']
    converted = verify_budget_delta(original, table(after, '工程量表达式'), path)
    proof = store.put_evidence(ctx.project_id, ctx.session_id, 'native_standard_factor_readback',
                              dict(capture=after, dispatch=action, preflight_evidence_id=result['preflight_evidence_id']))
    result.pop('error', None)
    result.update(status='native_standard_factor_readback_verified_not_saved',
                  quota=converted, evidence_id=proof, business_dispatches=1)
    save_result(store, result, 'readback_verified')
    return result


def compile_factor(directory, profile, meta, command, path):
    generated = render_transport(TEMPLATES, directory, command, meta['session'], profile,
                                 project_window_prefix(meta['workcopy_path']))
    constants = dict(kPath=path, kRule=RULE)
    header = ''.join(f'static const QString {key} = QStringLiteral({json.dumps(value, ensure_ascii=False)});\n'
                      for key, value in constants.items())
    generated['operations.h'] = header + (TEMPLATES/'standard_factor.h.in').read_text(encoding='utf-8')
    guard = 'QStringList{"insert","material.confirm","note.commit","quantity.commit","name.set","quantity.set","quantity.normal.zero","quantity.normal.one","save"}.contains(op)'
    generated['bridge.cpp'] = _replace(generated['bridge.cpp'], guard, 'op == "standard.factor"')
    directory.mkdir(parents=True, exist_ok=False)
    for name, source in generated.items():
        (directory/name).write_text(source, encoding='utf-8', newline='\n')
    compile_bridge(directory)
    return load_bridge(directory)


def verify_rule(capture, *, applied):
    tables = [model for model in capture['tables'] if model['model_class'] == 'GBQStdConvModel'
              and len(model['headers']) == 3 and model['headers'][2] == '换算内容']
    if len(tables) != 1 or not tables[0]['complete']:
        raise ValueError('Complete unique standard conversion model required')
    expected = str(applied).lower()
    selected = [row['values'] for row in tables[0]['rows'] if row['values'][1] == RULE]
    if len(selected) != 1 or selected[0][2] != expected:
        raise ValueError(f'Standard rule readback differs from {expected}')


def verify_budget_delta(before, after, path):
    old, new = rows(before), rows(after)
    if old.keys() != new.keys():
        raise ValueError('Conversion changed the budget structure')
    quota = new[path]['values']
    prior = old[path]['values']
    if (quota[:3] != [prior[0]+' R*1.15,J*1.15', '换', prior[2]+' \x7f'+RULE]
            or quota[3:7] != prior[3:7] or quota[9:] != prior[9:]
            or quota[7:9] == prior[7:9]):
        raise ValueError('Conversion changed quota identity or quantity, or failed to recalculate')
    delta = Decimal(quota[8]) - Decimal(prior[8])
    for key, previous in old.items():
        if key == path:
            continue
        current = new[key]
        if path.startswith(key+'/'):
            old_cells, new_cells = previous['values'], current['values']
            if (old_cells[:8] != new_cells[:8] or old_cells[9:] != new_cells[9:]
                    or abs(Decimal(new_cells[8]) - Decimal(old_cells[8]) - delta) > Decimal('0.01')):
                raise ValueError('Conversion changed ancestor fields outside totals')
        elif previous != current:
            raise ValueError('Conversion changed a non-target budget row or main material')
    return quota
