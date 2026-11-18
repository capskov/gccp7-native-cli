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
from .native_runtime import _replace, displayed_quantity, normalize_quantity, render_transport
from .native_worker_support import (
    Ledger, RuntimeProfile, SessionManager, WindowsDesktop, execution_lock, project_window_prefix, rows, table,
)
from .ports import SessionContext
from gccp_foundation.storage.native_save_attempts import NativeSaveAttempts


def edit_material(state, runtime_root, request, *, check_only=False):
    command = request['command_id']
    quota_edit = request['field'] == 'quantity'
    resource_edit = request['field'] == 'resource-content'
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', command):
        raise Rejected('IDENTIFIER_INVALID', 'Provide a bounded command ID')
    if not re.fullmatch(r'/\d+' if resource_edit else r'(?:/\d+){3,12}', request['path']):
        raise Rejected('MATERIAL_PATH_INVALID', 'Use the current native-inspect material row path')
    if quota_edit or request['field'] in ('content', 'resource-content'):
        request['value'] = normalize_quantity(request['value'], allow_zero=not quota_edit)
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
            budget = table(before, '工程量表达式')
            original = rows(budget)
            if resource_edit and request.get('select_quota'):
                owner = original.get(request['select_quota'], {}).get('values', [])
                if (len(owner) != 13 or owner[:4] != [request['quota_code'], owner[1],
                        request.get('quota_name'), request.get('quota_unit')] or owner[1] not in ('定', '换')
                        or owner[6] != request['quota_quantity'] or owner[12] != request['quota_note']):
                    raise Rejected('RESOURCE_TARGET_MISMATCH', 'Selected path does not match the requested owner')
                bridge.call('material.focus', expected_target=dict(path=request['select_quota'], quota=owner), timeout=25)
                before = bridge.call('inspect', timeout=25)
                budget = table(before, '工程量表达式')
                if rows(budget) != original or budget['current'].get('row_path_session_only') != request['select_quota']:
                    raise Rejected('RESOURCE_TARGET_MISMATCH', 'Could not verify selection and unchanged budget')
            if resource_edit:
                resources = table(before, '单位')
                current = budget['current']
                quota = current.get('values', [])
                material = rows(resources).get(path, {}).get('values', [])
                if (not current.get('verified') or len(quota) != 13 or quota[0] != request['quota_code']
                        or quota[1] not in ('定', '换')
                        or quota[6] != request['quota_quantity'] or quota[12] != request['quota_note']
                        or quota[2] != request.get('quota_name', quota[2]) or quota[3] != request.get('quota_unit', quota[3])
                        or len(material) != 13 or material[0] != request['material_code']
                        or material[6] != request['expected'] or material[10] != 'false'):
                    raise Rejected('RESOURCE_TARGET_MISMATCH', 'Check the selected quota and current resource row')
            material = original.get(path, {}).get('values', [])
            quota = original.get(parent, {}).get('values', [])
            column = {'name': 2, 'quantity': 4, 'content': 5, 'resource-content': 6}[request['field']]
            if resource_edit:
                quota = budget['current']['values']
                material = rows(resources)[path]['values']
                expected_quantity = Decimal(quota[6]) * Decimal(request['value'])
            elif quota_edit:
                if (len(material) != 13 or material[:4] != [request['quota_code'], '定',
                        request['quota_name'], request['quota_unit']]
                        or material[4] != request['expected'] or material[12] != request['quota_note']):
                    raise Rejected('QUOTA_TARGET_MISMATCH', 'The quota differs from the reviewed snapshot')
                expected_quantity = Decimal(displayed_quantity(material[3], request['value']))
            elif (len(material) != 13 or len(quota) != 13 or material[:2] != [request['material_code'], '主']
                    or quota[0] != request['quota_code'] or quota[1] not in ('定', '换')
                    or quota[6] != request['quota_quantity']
                    or quota[2] != request.get('quota_name', quota[2])
                    or quota[3] != request.get('quota_unit', quota[3])
                    or material[column] != request['expected']
                    or quota[12] != request['quota_note']):
                raise Rejected('MATERIAL_TARGET_MISMATCH', 'The quota or material differs from the reviewed snapshot')
            elif column == 5:
                expected_quantity = Decimal(quota[6]) * Decimal(request['value'])
            else:
                expected_quantity = Decimal(material[6])
            target = dict(path=path, material=material, quota=quota, column=column, value=request['value'])
            if resource_edit:
                target['quota_path'] = budget['current']['row_path_session_only']
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
                if resource_edit:
                    actual = rows(table(after, '单位'))[path]['values']
                    current_owner = table(after, '工程量表达式')['current']
                    identity_matches = (actual[:6] == material[:6] and actual[8] == material[8]
                                        and current_owner['row_path_session_only'] == target['quota_path'])
                    read_quantity = Decimal(actual[7].replace(',', ''))
                    edited_path = target['quota_path']
                elif quota_edit:
                    actual = current[path]['values']
                    identity_matches = (actual[:4] == material[:4] and actual[5] == material[5]
                                        and actual[11:] == material[11:])
                    read_quantity = Decimal(actual[6])
                    edited_path = path
                else:
                    actual = current[path]['values']
                    identity_matches = actual[1] == '主' and actual[3:5] == material[3:5]
                    read_quantity = Decimal(actual[6])
                    edited_path = path
                if (action.get('business_dispatches') != 1 or action.get('setter_accepted') is not True
                        or actual[column].replace(',', '') != request['value'] or read_quantity != expected_quantity
                        or not identity_matches or (not resource_edit and actual[7] != material[7])
                        or current.keys() != original.keys()):
                    raise ValueError('Material edit readback differs from the requested result')
                for key, previous in original.items():
                    if key == edited_path and not resource_edit:
                        continue
                    previous_values = previous['values']
                    actual_values = current[key]['values']
                    if edited_path.startswith(key + '/') or (resource_edit and key == edited_path):
                        previous_values = previous_values[:7] + previous_values[10:]
                        actual_values = actual_values[:7] + actual_values[10:]
                    if resource_edit and key == edited_path and actual_values[1] == '换':
                        actual_values = actual_values[:1] + previous_values[1:2] + actual_values[2:]
                    if quota_edit and key.startswith(path + '/'):
                        previous_values = previous_values[:6] + previous_values[7:8] + previous_values[9:]
                        actual_values = actual_values[:6] + actual_values[7:8] + actual_values[9:]
                    if actual_values != previous_values:
                        raise ValueError('Material edit changed an unrelated budget field')
                evidence = store.put_evidence(project_id, ctx.session_id, 'native_material_edit_readback',
                                              dict(capture=after, dispatch=action, preflight_evidence_id=proof))
                status = 'native_quota_quantity_verified_not_saved' if quota_edit else 'native_material_edit_verified_not_saved'
                result.update(status=status, evidence_id=evidence, business_dispatches=1,
                              target_path=path, ipc_records=bridge.records)
                result['quota' if quota_edit else 'material'] = actual
                save_result(store, result, 'readback_verified')
                return result
            except (ArithmeticError, ValueError, OSError, RuntimeError, KeyError) as exc:
                result.update(error=str(exc), ipc_records=bridge.records)
                save_result(store, result)
                raise Rejected('NEEDS_RECONCILIATION', 'Read back the original material edit before any retry') from exc


def reconcile_resource_edit(state, command_id):
    """Verify a resource edit from its original dispatch and fresh native snapshot."""
    with closing(Ledger(state)) as store:
        record = store.db.execute('SELECT * FROM native_cli_commands WHERE command_id=?', (command_id,)).fetchone()
        if record is None or record['phase'] != 'armed_unknown':
            raise Rejected('COMMAND_CHANGED', 'The original unresolved resource command is required')
        project_id = record['project_id']
        project = store.db.execute('SELECT * FROM projects WHERE project_id=?', (project_id,)).fetchone()
        with execution_lock(state, project['file_id']):
            if dict(store.db.execute('SELECT * FROM native_cli_commands WHERE command_id=?', (command_id,)).fetchone()) != dict(record):
                raise Rejected('COMMAND_CHANGED', 'The original resource command changed')
            result = json.loads(record['result'])
            evidence = store.read_evidence(result['preflight_evidence_id'], project_id)
            data = evidence['data']
            request = data['request']
            ctx = SessionContext(**json.loads(project['metadata'])['session'])
            if (evidence['kind'] != 'native_material_edit_preflight' or evidence['session_id'] != ctx.session_id
                    or request['field'] != 'resource-content' or digest(request) != record['request_digest']):
                raise Rejected('RESOURCE_EDIT_EVIDENCE_MISMATCH', 'The verified original resource intent is required')
            profile = profile_path(ctx.profile_id)
            SessionManager(store, WindowsDesktop(), RuntimeProfile.load(profile)).validate_session(ctx)
            dispatches = [json.loads(Path(p).read_text(encoding='utf-8')) for p in result['ipc_records']]
            dispatches = [p for p in dispatches if p['operation'] == 'material.edit']
            if (len(dispatches) != 1 or dispatches[0]['request'].get('outer_intent_id') != data['intent_id']
                    or dispatches[0]['receipt'].get('setter_accepted') is not True
                    or dispatches[0]['receipt'].get('business_dispatches') != 1):
                raise Rejected('RESOURCE_EDIT_EVIDENCE_MISMATCH', 'One accepted original resource dispatch is required')
            target = dispatches[0]['request']['expected_target']
            before = data['capture']
            bridge = load_bridge(result['runtime'])
            after = bridge.call('inspect', timeout=25)
            source_budget = table(before, '工程量表达式')
            target_budget = table(after, '工程量表达式')
            original = rows(source_budget)
            current = rows(target_budget)
            owner = target['quota_path']
            old_resource = rows(table(before, '单位'))[request['path']]['values']
            actual = rows(table(after, '单位'))[request['path']]['values']
            if (after['pid'] != ctx.pid or after['created_filetime'] != ctx.created_filetime
                    or target_budget['current']['row_path_session_only'] != owner
                    or original.keys() != current.keys() or actual[:6] != old_resource[:6]
                    or actual[8] != old_resource[8] or actual[6].replace(',', '') != request['value']
                    or Decimal(actual[7].replace(',', '')) != Decimal(original[owner]['values'][6]) * Decimal(request['value'])):
                raise Rejected('RESOURCE_EDIT_EVIDENCE_MISMATCH', 'The resource readback differs')
            for key, row in original.items():
                old, new = row['values'], current[key]['values']
                if key == owner:
                    if new[1] == '换':
                        new = new[:1] + old[1:2] + new[2:]
                    old, new = old[:7] + old[10:], new[:7] + new[10:]
                elif owner.startswith(key + '/'):
                    old, new = old[:7] + old[10:], new[:7] + new[10:]
                if old != new:
                    raise Rejected('RESOURCE_EDIT_EVIDENCE_MISMATCH', 'An unrelated budget field changed')
            proof = store.put_evidence(project_id, ctx.session_id, 'native_material_edit_readback',
                dict(capture=after, dispatch=dispatches[0]['receipt'], preflight_evidence_id=result['preflight_evidence_id']))
            result.update(status='native_material_edit_verified_not_saved', evidence_id=proof,
                          material=actual, target_path=request['path'], readonly_reconciled=True,
                          new_business_dispatches=0)
            result.pop('error', None)
            save_result(store, result, 'readback_verified')
            return result
