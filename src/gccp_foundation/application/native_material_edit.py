"""Edit one verified existing main material cell and read back the budget."""
from contextlib import closing
from decimal import Decimal
import json
from pathlib import Path
import re
import time
import uuid

from .native_build import TEMPLATES, compile_bridge, profile_path
from .native_cli import Rejected, digest, encoded, initialize_commands
from .native_delete import load_bridge, save_result
from .native_runtime import _replace, normalize_quantity, render_transport
from .native_worker_support import (
    Ledger, RuntimeProfile, SessionManager, WindowsDesktop, execution_lock, project_window_prefix, rows, table,
)
from .ports import SessionContext
from gccp_foundation.storage.native_save_attempts import NativeSaveAttempts


def edit_material(state, runtime_root, request, *, check_only=False):
    command = request['command_id']
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', command):
        raise Rejected('IDENTIFIER_INVALID', 'Provide a bounded command ID')
    if not re.fullmatch(r'(?:/\d+){3,12}', request['path']):
        raise Rejected('MATERIAL_PATH_INVALID', 'Use the current native-inspect material row path')
    if request['field'] == 'content':
        request['value'] = normalize_quantity(request['value'])
    elif not request['value'].strip() or len(request['value']) > 160:
        raise Rejected('MATERIAL_NAME_INVALID', 'Provide a nonempty name of at most 160 characters')
    signature = digest(request)
    project_id = request['project_id']
    with closing(Ledger(state)) as store:
        initialize_commands(store)
        project = store.db.execute('SELECT * FROM projects WHERE project_id=?', (project_id,)).fetchone()
        if project is None:
            raise Rejected('PROJECT_NOT_BOUND', 'Bind the current project first')
        with execution_lock(store.root, project['file_id']):
            prior = store.db.execute('SELECT * FROM native_cli_commands WHERE command_id=?', (command,)).fetchone()
            if prior:
                if prior['request_digest'] == signature and prior['phase'] == 'readback_verified':
                    return {**json.loads(prior['result']), 'receipt_only': True}
                raise Rejected('COMMAND_ALREADY_RECORDED', 'Check the existing command receipt before any further write')
            ctx = SessionContext(**json.loads(project['metadata'])['session'])
            profile = profile_path(ctx.profile_id)
            meta = SessionManager(store, WindowsDesktop(), RuntimeProfile.load(profile)).validate_session(ctx)
            if (meta.get('dirty') or store.load_pending(project_id) or NativeSaveAttempts(store).active(project_id)
                    or store.db.execute("SELECT 1 FROM native_cli_commands WHERE project_id=? AND phase='armed_unknown'",
                                        (project_id,)).fetchone()):
                raise Rejected('PROJECT_NEEDS_RECONCILIATION', 'Resolve the pending command before editing')
            directory = Path(runtime_root).resolve() / 'material-edit' / uuid.uuid4().hex
            generated = render_transport(TEMPLATES, directory, command, meta['session'], profile,
                                         project_window_prefix(meta['workcopy_path']))
            generated['operations.h'] = (TEMPLATES / 'material_edit.h.in').read_text(encoding='utf-8')
            guard = 'QStringList{"insert","material.confirm","note.commit","quantity.commit","name.set","quantity.set","quantity.normal.zero","quantity.normal.one","save"}.contains(op)'
            generated['bridge.cpp'] = _replace(generated['bridge.cpp'], guard, 'op == "material.edit"')
            directory.mkdir(parents=True)
            for filename, source in generated.items():
                (directory / filename).write_text(source, encoding='utf-8', newline='\n')
            compile_bridge(directory)
            bridge = load_bridge(directory)
            before = bridge.call('inspect', timeout=25)
            path = request['path']
            parent = path.rsplit('/', 1)[0]
            original = rows(table(before, '工程量表达式'))
            material = original.get(path, {}).get('values', [])
            quota = original.get(parent, {}).get('values', [])
            column = 5 if request['field'] == 'content' else 2
            if (len(material) != 13 or len(quota) != 13 or material[:2] != [request['material_code'], '主']
                    or quota[:2] != [request['quota_code'], '定'] or quota[6] != request['quota_quantity']
                    or material[column] != request['expected'] or quota[12] != request['quota_note']):
                raise Rejected('MATERIAL_TARGET_MISMATCH', 'The quota or material differs from the reviewed snapshot')
            target = dict(path=path, material=material, quota=quota, column=column, value=request['value'])
            checked = bridge.call('material.check', expected_target=target, timeout=25)
            if not checked.get('editable'):
                raise Rejected('MATERIAL_NOT_EDITABLE', 'GCCP did not expose the field as editable')
            if check_only:
                return dict(status='checked', material=material, runtime=str(directory), business_dispatches=0)
            intent = 'intent.' + uuid.uuid4().hex
            proof = store.put_evidence(project_id, ctx.session_id, 'native_material_edit_preflight',
                                       dict(request=request, capture=before, intent_id=intent))
            result = dict(command_id=command, status='armed_unknown', runtime=str(directory),
                          preflight_evidence_id=proof, save_dispatches=0)
            with store._transaction():
                store.db.execute('INSERT INTO native_cli_commands VALUES(?,?,?,?,?,?)',
                                 (command, project_id, signature, 'armed_unknown', encoded(result), int(time.time()*1000)))
            try:
                action = bridge.call('material.edit', intent, expected_target=target, timeout=25)
                after = bridge.call('inspect', timeout=25)
                current = rows(table(after, '工程量表达式'))
                actual = current[path]['values']
                expected_quantity = (Decimal(quota[6]) * Decimal(request['value']) if column == 5 else Decimal(material[6]))
                if (action.get('business_dispatches') != 1 or action.get('setter_accepted') is not True
                        or actual[column] != request['value'] or Decimal(actual[6]) != expected_quantity
                        or actual[1] != '主' or actual[3:5] != material[3:5] or actual[7] != material[7]
                        or current.keys() != original.keys()):
                    raise ValueError('Material edit readback differs from the requested result')
                for key, previous in original.items():
                    if key == path:
                        continue
                    previous_values = previous['values']
                    actual_values = current[key]['values']
                    if path.startswith(key + '/'):
                        previous_values = previous_values[:7] + previous_values[10:]
                        actual_values = actual_values[:7] + actual_values[10:]
                    if actual_values != previous_values:
                        raise ValueError('Material edit changed an unrelated budget field')
                evidence = store.put_evidence(project_id, ctx.session_id, 'native_material_edit_readback',
                                              dict(capture=after, dispatch=action, preflight_evidence_id=proof))
                result.update(status='native_material_edit_verified_not_saved', material=actual,
                              evidence_id=evidence, business_dispatches=1, ipc_records=bridge.records)
                save_result(store, result, 'readback_verified')
                return result
            except (ArithmeticError, ValueError, OSError, RuntimeError, KeyError) as exc:
                result.update(error=str(exc), ipc_records=bridge.records)
                save_result(store, result)
                raise Rejected('NEEDS_RECONCILIATION', 'Read back the original material edit before any retry') from exc
