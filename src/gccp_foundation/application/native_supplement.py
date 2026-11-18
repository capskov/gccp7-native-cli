"""Open and submit a GCCP supplemental item with zero fee fields."""

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
from .native_runtime import _replace, find_section_path, is_blank_quota, normalize_quantity, render_transport
from .native_worker_support import Ledger, RuntimeProfile, SessionManager, WindowsDesktop, execution_lock, project_window_prefix, table
from .ports import SessionContext
from gccp_foundation.storage.native_save_attempts import NativeSaveAttempts


def supplement_quota(state, runtime_root, inspector, request, *, check_only=False):
    command = request['command_id']
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', command):
        raise Rejected('IDENTIFIER_INVALID', 'Provide a bounded command ID')
    if not re.fullmatch(r'补[A-Za-z0-9-]{1,24}', request['code']):
        raise Rejected('SUPPLEMENT_CODE_INVALID', 'Provide 补 followed by a distinct supplemental code')
    if not request['name'].strip() or len(request['name']) > 100 or any(ord(c) < 32 for c in request['name']):
        raise Rejected('SUPPLEMENT_NAME_INVALID', 'Provide one nonempty supplemental item name')
    request['quantity'] = normalize_quantity(request['quantity'])
    project_id = request['project_id']
    signature = digest(request)
    config, observer = runtime_config(inspector)
    if config.get('runtime_kind') != 'inspector':
        raise Rejected('INSPECTOR_REQUIRED', 'Use the bound read-only inspector')
    with closing(Ledger(state)) as store:
        initialize_commands(store)
        project = store.db.execute('SELECT * FROM projects WHERE project_id=?', (project_id,)).fetchone()
        if project is None:
            raise Rejected('PROJECT_NOT_BOUND', 'Current project must be bound first')
        with execution_lock(store.root, project['file_id']):
            prior = store.db.execute('SELECT * FROM native_cli_commands WHERE command_id=?', (command,)).fetchone()
            if prior:
                if prior['request_digest'] != signature:
                    raise Rejected('IDEMPOTENCY_CONFLICT', 'Command parameters changed')
                if prior['phase'] != 'readback_verified':
                    raise Rejected('NEEDS_RECONCILIATION', 'Inspect the prior supplement outcome without dispatching again')
                return {**json.loads(prior['result']), 'receipt_only': True, 'new_business_dispatches': 0}
            ctx = SessionContext(**json.loads(project['metadata'])['session'])
            profile = profile_path(ctx.profile_id)
            manager = SessionManager(store, WindowsDesktop(), RuntimeProfile.load(profile))
            meta = manager.validate_session(ctx)
            if (meta.get('dirty') or store.load_pending(project_id) or NativeSaveAttempts(store).active(project_id)
                    or store.db.execute("SELECT 1 FROM native_cli_commands WHERE project_id=? AND phase='armed_unknown'", (project_id,)).fetchone()):
                raise Rejected('PROJECT_NEEDS_RECONCILIATION', 'Resolve existing intent before supplementing')
            observed = subprocess.run([sys.executable, '-X', 'utf8', str(observer), '--inspect'], capture_output=True,
                                      text=True, encoding='utf-8', timeout=50, check=True)
            snapshot = json.loads(observed.stdout)
            budget = table(snapshot, '工程量表达式')
            parent = find_section_path(budget['rows'], request['parent_sections'])['row_path_session_only']
            current = budget['current']
            anchor = current.get('row_path_session_only', '')
            navigate = False
            if (snapshot['pid'] != ctx.pid or snapshot['created_filetime'] != ctx.created_filetime
                    or not snapshot['callback_on_app_thread']):
                raise Rejected('SUPPLEMENT_SESSION_CHANGED', 'The original application session is required')
            if (not current.get('verified') or anchor.rsplit('/', 1)[0] != parent
                    or current['values'][1] not in ('定', '换', '补')
                    or not current['values'][0]):
                anchors = [row for row in budget['rows']
                           if row['row_path_session_only'].rsplit('/', 1)[0] == parent
                           and row['values'][1] in ('定', '换', '补') and row['values'][0]]
                if not request.get('navigate_anchor') or not anchors:
                    raise Rejected('SUPPLEMENT_SECTION_NOT_SELECTED', 'Select a direct quota in the requested section')
                current = anchors[-1]
                anchor = current['row_path_session_only']
                navigate = True
            if any(r['values'][0] == request['code'] or r['values'][2] == request['name'] for r in budget['rows']):
                raise Rejected('SUPPLEMENT_EXISTS', 'This supplemental code or name is already present')
            directory = Path(runtime_root).resolve()/'supplement'/uuid.uuid4().hex
            generated = render_transport(TEMPLATES, directory, command, meta['session'], profile,
                                         project_window_prefix(meta['workcopy_path']))
            constants = dict(kAnchor=anchor, kCode=request['code'], kName=request['name'],
                             kUnit=request['unit'], kQuantity=request['quantity'])
            header = ''.join(f'static const QString {key} = QStringLiteral({json.dumps(value, ensure_ascii=False)});\n'
                             for key, value in constants.items())
            generated['operations.h'] = header + (TEMPLATES/'supplement.h.in').read_text(encoding='utf-8')
            original_guard = 'QStringList{"insert","material.confirm","note.commit","quantity.commit","name.set","quantity.set","quantity.normal.zero","quantity.normal.one","save"}.contains(op)'
            generated['bridge.cpp'] = _replace(generated['bridge.cpp'], original_guard, 'op == "supplement.commit"')
            directory.mkdir(parents=True)
            for name, source in generated.items():
                (directory/name).write_text(source, encoding='utf-8', newline='\n')
            compile_bridge(directory)
            bridge = load_bridge(directory)
            if navigate:
                bridge.call('supplement.select', timeout=25, expected_target=current['values'])
                budget = {**budget, 'current': current}
            before, opened = prepare_form(bridge, budget, check_only)
            if check_only:
                checked = bridge.call('supplement.check', timeout=25, expected_target=current['values'])
                after = bridge.call('inspect', timeout=25)
                if (not checked['supplement_fields_checked'] or checked['business_dispatches']
                        or after['supplement_dialogs'] or after['other_modals']
                        or table(after, '工程量表达式')['rows'] != budget['rows']):
                    raise Rejected('SUPPLEMENT_CHECK_MISMATCH', 'Review the original form check and current budget')
                return dict(status='native_supplement_form_checked', command_id=command, runtime=str(directory),
                            opened_by_cli=opened, ipc_records=bridge.records, business_dispatches=0, save_dispatches=0)
            intent = 'intent.'+uuid.uuid4().hex
            evidence = store.put_evidence(project_id, ctx.session_id, 'native_supplement_preflight',
                                          dict(request=request, capture=before, intent_id=intent))
            result = dict(command_id=command, status='armed_unknown', runtime=str(directory),
                          preflight_evidence_id=evidence, opened_by_cli=opened, save_dispatches=0)
            with store._transaction():
                store.db.execute('INSERT INTO native_cli_commands VALUES(?,?,?,?,?,?)',
                                 (command, project_id, signature, 'armed_unknown', encoded(result), int(time.time()*1000)))
            try:
                dispatched = bridge.call('supplement.commit', intent, timeout=30, expected_target=current['values'])
                after = bridge.call('inspect', timeout=25)
                result.update(ipc_records=bridge.records, business_dispatches=dispatched['business_dispatches'])
                if after['supplement_dialogs'] or after['other_modals'] or dispatched['business_dispatches'] != 1:
                    raise ValueError('Supplement confirmation requires a closed form and one native dispatch')
                target = verify_supplement(budget, table(after, '工程量表达式'), request, parent)
                proof = store.put_evidence(project_id, ctx.session_id, 'native_supplement_readback',
                                           dict(capture=after, dispatch=dispatched, preflight_evidence_id=evidence))
                result.update(status='native_supplement_readback_verified_not_saved', quota=target, evidence_id=proof)
                save_result(store, result, 'readback_verified')
                return result
            except (InvalidOperation, ValueError, OSError, RuntimeError) as exc:
                result.update(error=str(exc), ipc_records=bridge.records)
                save_result(store, result)
                raise Rejected('NEEDS_RECONCILIATION', 'Inspect the original supplemental command without dispatching again') from exc


def prepare_form(bridge, budget, check_only):
    before = bridge.call('inspect', timeout=25)
    if before['other_modals'] or before['supplement_dialogs'] > 1:
        raise Rejected('SUPPLEMENT_DIALOG_CONFLICT', 'Close the unrelated modal before supplementing')
    if check_only and before['supplement_dialogs']:
        raise Rejected('SUPPLEMENT_CHECK_FORM_ALREADY_OPEN', 'Close the existing form so the CLI can verify its own open and cancel')
    opened = not before['supplement_dialogs']
    if table(before, '工程量表达式')['rows'] != budget['rows']:
        raise Rejected('SUPPLEMENT_BUDGET_CHANGED', 'Budget changed during preparation')
    if opened:
        queued = bridge.call('supplement.open', timeout=25, expected_target=budget['current']['values'])
        if not queued['supplement_open_queued'] or queued['business_dispatches']:
            raise Rejected('SUPPLEMENT_OPEN_NOT_QUEUED', 'The original supplement action was not queued')
        for _ in range(3):
            before = bridge.call('inspect', timeout=10)
            if before['supplement_dialogs'] or before['other_modals']:
                break
    if (before['supplement_dialogs'] != 1 or before['other_modals']
            or table(before, '工程量表达式')['rows'] != budget['rows']):
        raise Rejected('SUPPLEMENT_DIALOG_REQUIRED', 'Expected one original form and unchanged budget. Inspect the original action.')
    return before, opened


def verify_supplement(before, after, request, parent):
    matches = [r for r in after['rows'] if r['values'][0] == request['code']]
    if len(matches) != 1:
        raise ValueError('Expected one supplemental row with the approved code')
    row = matches[0]
    values = row['values']
    if (row['row_path_session_only'].rsplit('/', 1)[0] != parent
            or values[1] != '补'
            or values[2:4] != [request['name'], request['unit']]
            or Decimal(values[4]) != Decimal(request['quantity']) or Decimal(values[6]) != Decimal(request['quantity'])
            or Decimal(values[7]) != 0 or Decimal(values[8]) != 0):
        raise ValueError('Supplemental row identity, quantities or zero prices differ')

    def normalized(model, exclude=None):
        return [(r['depth'], r['values']) for r in model['rows'] if r is not exclude and not is_blank_quota(r['values'])]

    if normalized(before) != normalized(after, row):
        raise ValueError('Unrelated budget data changed during supplementation')
    return values
