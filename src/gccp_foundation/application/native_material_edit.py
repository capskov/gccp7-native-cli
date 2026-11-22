"""Edit one verified existing main material cell and read back the budget."""
from contextlib import closing
from decimal import Decimal, ROUND_HALF_UP
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
from .native_runtime import _replace, displayed_quantity, normalize_quantity, render_transport
from .native_worker_support import (
    Ledger, RuntimeProfile, SessionManager, WindowsDesktop, execution_lock, project_window_prefix, rows, table,
)
from .ports import SessionContext
from gccp_foundation.execution.session import exact_project_window
from gccp_foundation.storage.native_save_attempts import NativeSaveAttempts


def main_material_quantity(quota_quantity, content):
    return (Decimal(quota_quantity) * Decimal(content)).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)


def build_material_bridge(directory, command, meta, profile):
    generated = render_transport(TEMPLATES, directory, command, meta['session'], profile,
                                 project_window_prefix(meta['workcopy_path']))
    generated['operations.h'] = (TEMPLATES / 'material_edit.h.in').read_text(encoding='utf-8')
    guard = 'QStringList{"insert","material.confirm","note.commit","quantity.commit","name.set","quantity.set","quantity.normal.zero","quantity.normal.one","save"}.contains(op)'
    generated['bridge.cpp'] = _replace(generated['bridge.cpp'], guard, 'op == "material.edit"')
    directory.mkdir(parents=True)
    for filename, source in generated.items():
        (directory / filename).write_text(source, encoding='utf-8', newline='\n')
    compile_bridge(directory)
    return load_bridge(directory)


def edit_material(state, runtime_root, request, *, check_only=False):
    command = request['command_id']
    quota_edit = request['field'] == 'quantity'
    resource_edit = request['field'] == 'resource-content'
    concrete_edit = request['field'] == 'concrete-grade'
    section_edit = request['field'] == 'section-name'
    unit_edit = request['field'] == 'unit-name'
    tree_edit = request['field'] == 'project-name' or unit_edit
    quota_text = request['field'] in ('quota-name', 'quota-note')
    text_edit = quota_text or section_edit
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', command):
        raise Rejected('IDENTIFIER_INVALID', 'Provide a bounded command ID')
    minimum_depth = 2 if section_edit else 3
    if not tree_edit and not re.fullmatch(
            r'/\d+' if resource_edit else rf'(?:/\d+){{{minimum_depth},12}}', request['path']):
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
            bridge = build_material_bridge(directory, command, meta, profile)
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
            column = {'name': 2, 'quantity': 4, 'content': 5, 'resource-content': 6,
                      'quota-name': 2, 'quota-note': 12, 'section-name': 2,
                      'project-name': 0, 'unit-name': 0, 'concrete-grade': 2}[request['field']]
            if tree_edit:
                if not request['expected'].strip() or request['expected'] == request['value']:
                    raise Rejected('PROJECT_NAME_INVALID', 'Provide the exact current project name and a different new name')
                material = [request['expected']]
            elif resource_edit:
                quota = budget['current']['values']
                material = rows(resources)[path]['values']
                expected_quantity = Decimal(quota[6]) * Decimal(request['value'])
            elif section_edit:
                if (len(material) != 13 or material[:3] != ['', '部', request['expected']]
                        or request['value'] == request['expected']):
                    raise Rejected('SECTION_TARGET_MISMATCH', 'Use a section path and its exact current name')
                for sibling_path, sibling in original.items():
                    if (sibling_path.rsplit('/', 1)[0] == parent
                            and sibling['values'][:3] == ['', '部', request['value']]):
                        raise Rejected('SECTION_NAME_EXISTS', 'A sibling section already uses this name')
                expected_quantity = Decimal(material[6])
            elif concrete_edit:
                replacement = re.fullmatch(r'(\d{10})  预拌混凝土 (C\d+)', request['value'])
                if (len(material) != 13 or material[:4] != [request['quota_code'], material[1],
                        request.get('quota_name'), request.get('quota_unit')]
                        or material[1] not in ('定', '换') or material[6] != request['quota_quantity']
                        or material[12] != request['quota_note'] or not replacement or not check_only
                        or not re.fullmatch(r'C\d+', request['expected'])
                        or not re.fullmatch(r'\d{10}', request['material_code'])
                        or replacement[1] == request['material_code'] or replacement[2] == request['expected']):
                    raise Rejected('CONCRETE_TARGET_MISMATCH', 'Use --check-only with the exact selected quota and candidate material')
            elif quota_text:
                if (len(material) != 13 or material[1] not in ('定', '换')
                        or material[:4] != [request['quota_code'], material[1],
                        request.get('quota_name'), request.get('quota_unit')]
                        or material[6] != request['quota_quantity'] or material[12] != request['quota_note']
                        or material[column] != request['expected']):
                    raise Rejected('QUOTA_TARGET_MISMATCH', 'The quota differs from the reviewed snapshot')
                expected_quantity = Decimal(material[6])
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
                expected_quantity = main_material_quantity(quota[6], request['value'])
            else:
                expected_quantity = Decimal(material[6])
            target = dict(path=path, material=material, quota=quota, column=column, value=request['value'])
            if tree_edit:
                target['project_name'] = request.get('project_name', request['expected'])
            if unit_edit:
                target['unit_name'] = request['expected']
            if concrete_edit:
                target.update(concrete_grade=request['expected'], concrete_code=request['material_code'])
            if resource_edit:
                target['quota_path'] = budget['current']['row_path_session_only']
            checked = bridge.call('material.check', expected_target=target, timeout=25)
            if not checked.get('editable'):
                raise Rejected('MATERIAL_NOT_EDITABLE', 'GCCP did not expose the field as editable')
            if check_only:
                result = dict(status='checked', material=material, runtime=str(directory), business_dispatches=0)
                if concrete_edit:
                    result['conversion'] = checked['conversion']
                return result
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
                if tree_edit:
                    actual = [action.get('tree_name')]
                    identity_matches = current == original
                    edited_path = ''
                    read_quantity = expected_quantity = None
                elif resource_edit:
                    actual = rows(table(after, '单位'))[path]['values']
                    current_owner = table(after, '工程量表达式')['current']
                    identity_matches = (actual[:6] == material[:6] and actual[8] == material[8]
                                        and current_owner['row_path_session_only'] == target['quota_path'])
                    read_quantity = Decimal(actual[7].replace(',', ''))
                    edited_path = target['quota_path']
                elif text_edit:
                    actual = current[path]['values']
                    identity_matches = (actual[:column] + actual[column+1:]
                                        == material[:column] + material[column+1:])
                    read_quantity = Decimal(actual[6])
                    edited_path = path
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
                        or (actual[column] if text_edit or tree_edit else actual[column].replace(',', '')) != request['value']
                        or read_quantity != expected_quantity
                        or not identity_matches or (not (tree_edit or resource_edit) and actual[7] != material[7])
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
                if section_edit:
                    status = 'native_section_rename_verified_not_saved'
                if tree_edit:
                    status = 'native_unit_rename_verified_not_saved' if unit_edit else 'native_project_rename_verified_not_saved'
                result.update(status=status, evidence_id=evidence, business_dispatches=1,
                              target_path=path, ipc_records=bridge.records)
                result['quota' if quota_edit else 'material'] = actual
                save_result(store, result, 'readback_verified')
                return result
            except (ArithmeticError, ValueError, OSError, RuntimeError, KeyError) as exc:
                result.update(error=str(exc), ipc_records=bridge.records)
                save_result(store, result)
                raise Rejected('NEEDS_RECONCILIATION', 'Read back the original material edit before any retry') from exc


def select_project_unit(state, runtime_root, project_id, project_name, unit_name, expected_section,
                        *, reviewed_file_sha256=None):
    """Select the exact project tree child and verify that its budget is active."""
    if not all(name.strip() for name in (project_name, unit_name, expected_section)):
        raise Rejected('PROJECT_UNIT_INVALID', 'Provide the exact project, unit, and one known budget section')
    if reviewed_file_sha256 is not None:
        if not re.fullmatch(r'[0-9a-fA-F]{64}', reviewed_file_sha256):
            raise Rejected('DISK_DIGEST_INVALID', 'Provide the current reviewed workcopy SHA-256')
        reviewed_file_sha256 = reviewed_file_sha256.lower()
    with closing(Ledger(state)) as store:
        initialize_commands(store)
        project = store.db.execute('SELECT * FROM projects WHERE project_id=?', (project_id,)).fetchone()
        if project is None:
            raise Rejected('PROJECT_NOT_BOUND', 'Bind the current project first')
        with execution_lock(store.root, project['file_id']):
            meta = json.loads(project['metadata'])
            if not meta.get('session'):
                raise Rejected('PROJECT_NOT_BOUND', 'Bind the current project first')
            ctx = SessionContext(**meta['session'])
            profile = profile_path(ctx.profile_id)
            manager = SessionManager(store, WindowsDesktop(), RuntimeProfile.load(profile))
            if (meta.get('dirty') or store.load_pending(project_id) or NativeSaveAttempts(store).active(project_id)
                    or store.db.execute("SELECT 1 FROM native_cli_commands WHERE project_id=? AND phase='armed_unknown'",
                                        (project_id,)).fetchone()):
                raise Rejected('PROJECT_NEEDS_RECONCILIATION', 'Resolve the pending command before changing views')
            if reviewed_file_sha256 is None:
                meta = manager.validate_session(ctx)
            else:
                previous = manager.inspect_saved_open_identity(ctx, reviewed_file_sha256)
            command = 'unit-select-' + uuid.uuid4().hex
            directory = Path(runtime_root).resolve() / 'material-edit' / command
            bridge = build_material_bridge(directory, command, meta, profile)
            selection = bridge.call('material.select-unit',
                expected_target=dict(project_name=project_name, unit_name=unit_name), timeout=25)
            snapshot = bridge.call('inspect', timeout=25)
            budget = table(snapshot, '工程量表达式')
            sections = [row['values'][2] for row in budget['rows']
                        if row['depth'] == 1 and row['values'][1] == '部']
            if (selection.get('tree_name') != unit_name or not selection.get('application_selection_changed')
                    or str(snapshot['created_filetime']) != str(ctx.created_filetime)
                    or snapshot['pid'] != ctx.pid or sections.count(expected_section) != 1):
                raise Rejected('PROJECT_UNIT_VIEW_NOT_VERIFIED', 'The selected unit budget did not show its expected section')
            if (reviewed_file_sha256 is not None
                    and manager.inspect_saved_open_identity(ctx, reviewed_file_sha256) != previous):
                raise Rejected('PROJECT_IDENTITY_MISMATCH', 'The reviewed file changed while selecting the unit view')
            return dict(status='native_unit_view_verified', unit_name=unit_name, sections=sections,
                        business_dispatches=0, ipc_records=bridge.records)


def cancel_pending_quantity_edit(state, command_id):
    """Cancel the original quantity detail warning and verify the budget snapshot."""
    with closing(Ledger(state)) as store:
        initialize_commands(store)
        record = store.db.execute('SELECT * FROM native_cli_commands WHERE command_id=?', (command_id,)).fetchone()
        if record is None or record['phase'] != 'armed_unknown':
            raise Rejected('COMMAND_CHANGED', 'The original unresolved quantity command is required')
        project_id = record['project_id']
        project = store.db.execute('SELECT * FROM projects WHERE project_id=?', (project_id,)).fetchone()
        with execution_lock(state, project['file_id']):
            if dict(store.db.execute('SELECT * FROM native_cli_commands WHERE command_id=?', (command_id,)).fetchone()) != dict(record):
                raise Rejected('COMMAND_CHANGED', 'The original quantity command changed')
            result = json.loads(record['result'])
            evidence = store.read_evidence(result['preflight_evidence_id'], project_id)
            data = evidence['data']
            request = data['request']
            ctx = SessionContext(**json.loads(project['metadata'])['session'])
            if (evidence['kind'] != 'native_material_edit_preflight' or evidence['session_id'] != ctx.session_id
                    or request['field'] != 'quantity' or digest(request) != record['request_digest']):
                raise Rejected('QUANTITY_CANCEL_EVIDENCE_MISMATCH', 'The original quantity edit preflight is required')
            runtime = Path(result['runtime'])
            records = [json.loads(Path(path).read_text(encoding='utf-8')) for path in result['ipc_records']]
            dispatches = [entry for entry in records if entry['operation'] == 'material.edit']
            if len(dispatches) != 1:
                raise Rejected('QUANTITY_CANCEL_EVIDENCE_MISMATCH', 'One original setter dispatch is required')
            dispatch = dispatches[0]
            intent = dispatch['request']
            nonce = intent['nonce']
            if (not re.fullmatch(r'[a-f0-9]{32}', nonce) or dispatch.get('receipt') is not None
                    or intent.get('outer_intent_id') != data['intent_id']
                    or intent.get('pid') != ctx.pid or intent.get('created_filetime') != ctx.created_filetime):
                raise Rejected('QUANTITY_CANCEL_EVIDENCE_MISMATCH', 'The original setter result must still be unresolved')
            stage = runtime / 'build' / f'{nonce}.prepare-01-before-material-edit.stage'
            if not stage.is_file() or stage.read_text(encoding='utf-8').strip() != '01-before-material-edit':
                raise Rejected('QUANTITY_CANCEL_EVIDENCE_MISMATCH', 'The original native setter checkpoint is missing')
            profile = profile_path(ctx.profile_id)
            meta = SessionManager(store, WindowsDesktop(), RuntimeProfile.load(profile)).validate_session(ctx)
            recovery = runtime.parent / ('cancel-' + uuid.uuid4().hex)
            bridge = build_material_bridge(recovery, command_id, meta, profile)
            cancelled = bridge.call('material.cancel-quantity', expected_target=intent['expected_target'], timeout=25)
            after = bridge.call('inspect', timeout=25)
            before_budget = table(data['capture'], '工程量表达式')
            after_budget = table(after, '工程量表达式')
            if (not cancelled.get('quantity_detail_cancelled') or after['pid'] != ctx.pid
                    or after['created_filetime'] != ctx.created_filetime
                    or rows(after_budget) != rows(before_budget)):
                raise Rejected('QUANTITY_CANCEL_NOT_VERIFIED', 'Readback did not verify the original unchanged budget')
            proof = store.put_evidence(project_id, ctx.session_id, 'native_quantity_cancel_readback',
                dict(capture=after, cancellation=cancelled, preflight_evidence_id=result['preflight_evidence_id']))
            result.update(status='native_quantity_cancelled_readback_verified', evidence_id=proof,
                          new_business_dispatches=0, cancellation_ipc_records=bridge.records)
            result.pop('error', None)
            save_result(store, result, 'cancelled_verified')
            return result


def reconcile_resource_edit(state, command_id, *, inspector=None):
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
                    or request['field'] not in ('content', 'resource-content', 'quota-name', 'quota-note', 'concrete-grade')
                    or digest(request) != record['request_digest']):
                raise Rejected('RESOURCE_EDIT_EVIDENCE_MISMATCH', 'The verified original resource intent is required')
            profile = profile_path(ctx.profile_id)
            desktop = WindowsDesktop()
            runtime_profile = RuntimeProfile.load(profile)
            if request['field'] == 'resource-content':
                SessionManager(store, desktop, runtime_profile).validate_session(ctx)
            else:
                # Reconcile the original write even if an application save replaced the disk file.
                # This branch only reads the original live session and updates its receipt.
                metadata = json.loads(project['metadata'])
                runtime_profile.verify()
                windows = [window for window in desktop.windows()
                           if window['hwnd'] == ctx.hwnd and window['pid'] == ctx.pid
                           and exact_project_window(window, metadata['workcopy_path'])]
                if (not metadata.get('owned_workcopy') or len(windows) != 1
                        or desktop.process(ctx.pid)['created_filetime'] != ctx.created_filetime):
                    raise Rejected('RESOURCE_EDIT_EVIDENCE_MISMATCH', 'The original project window is required')
            dispatches = [json.loads(Path(p).read_text(encoding='utf-8')) for p in result['ipc_records']]
            dispatches = [p for p in dispatches if p['operation'] == 'material.edit']
            if (len(dispatches) != 1 or dispatches[0]['request'].get('outer_intent_id') != data['intent_id']
                    or dispatches[0]['receipt'].get('setter_accepted') is not True
                    or dispatches[0]['receipt'].get('business_dispatches') != 1):
                raise Rejected('RESOURCE_EDIT_EVIDENCE_MISMATCH', 'One accepted original resource dispatch is required')
            target = dispatches[0]['request']['expected_target']
            before = data['capture']
            if inspector is None:
                bridge = load_bridge(result['runtime'])
                after = bridge.call('inspect', timeout=25)
            else:
                if request['field'] not in ('quota-name', 'quota-note', 'content'):
                    raise Rejected('RESOURCE_EDIT_EVIDENCE_MISMATCH', 'A separate inspector is for budget rows only')
                _, worker = runtime_config(inspector)
                capture = subprocess.run([sys.executable, '-X', 'utf8', str(worker), '--inspect'],
                                         capture_output=True, text=True, encoding='utf-8', timeout=120, check=True)
                after = json.loads(capture.stdout)
                if (after.get('status') != 'observed' or not after.get('callback_on_app_thread')
                        or after.get('business_dispatches') != 0):
                    raise Rejected('RESOURCE_EDIT_EVIDENCE_MISMATCH', 'The read-only inspector capture is incomplete')
            source_budget = table(before, '工程量表达式')
            target_budget = table(after, '工程量表达式')
            original = rows(source_budget)
            current = rows(target_budget)
            if request['field'] == 'concrete-grade':
                path = request['path']
                dispatch = dispatches[0]['receipt']
                original_quota = original[path]['values']
                observed_quota = current[path]['values']
                replacement_grade = request['value'].rsplit(' ', 1)[1]
                expected_label = original_quota[2].replace(replacement_grade, '')
                rule = request['material_code'] + '  预拌混凝土 ' + request['expected']
                conversions = [model for model in after['tables']
                               if model['complete'] and model['headers'] == ['换算列表', '换算列表', '换算内容']]
                if (dispatch.get('error') != 'concrete_conversion_outcome_not_verified'
                        or dispatch.get('conversion') != rule or target.get('path') != path
                        or target.get('material') != original_quota or after['pid'] != ctx.pid
                        or after['created_filetime'] != ctx.created_filetime
                        or observed_quota[:2] + observed_quota[3:] != original_quota[:2] + original_quota[3:]
                        or observed_quota[2] not in (original_quota[2], expected_label)
                        or original.keys() != current.keys()
                        or any(current[key] != value for key, value in original.items() if key != path)
                        or len(conversions) != 1
                        or sum(row['values'][2] == rule for row in conversions[0]['rows']) != 1):
                    raise Rejected('CONCRETE_READBACK_MISMATCH', 'The original concrete rule and quantity must still match')
                proof = store.put_evidence(project_id, ctx.session_id, 'native_concrete_no_replacement_readback',
                    dict(capture=after, dispatch=dispatch, preflight_evidence_id=result['preflight_evidence_id']))
                result.update(status='native_concrete_grade_not_changed_verified', evidence_id=proof,
                              quota=observed_quota, new_business_dispatches=0)
                result.pop('error', None)
                save_result(store, result, 'readback_verified')
                return result
            if request['field'] in ('quota-name', 'quota-note'):
                path = request['path']
                column = 2 if request['field'] == 'quota-name' else 12
                old = original[path]['values']
                actual = current[path]['values']
                expected = old.copy()
                expected[column] = request['value']
                if (after['pid'] != ctx.pid or after['created_filetime'] != ctx.created_filetime
                        or target['path'] != path or target['material'] != old
                        or target['column'] != column or target['value'] != request['value']
                        or old[column] != request['expected'] or actual != expected
                        or original.keys() != current.keys()
                        or any(current[key]['values'] != row['values']
                               for key, row in original.items() if key != path)):
                    raise Rejected('RESOURCE_EDIT_EVIDENCE_MISMATCH', 'The quota text readback differs')
                return record_reconciled_material(store, project_id, ctx, result, after, dispatches[0], actual, path)
            if request['field'] == 'content':
                path = request['path']
                owner = path.rsplit('/', 1)[0]
                original_material = original[path]['values']
                actual = current[path]['values']
                if (after['pid'] != ctx.pid or after['created_filetime'] != ctx.created_filetime
                        or original.keys() != current.keys() or target['path'] != path
                        or target['material'] != original_material or target['quota'] != original[owner]['values']
                        or target['column'] != 5 or target['value'] != request['value']
                        or actual[:5] != original_material[:5] or actual[7:] != original_material[7:]
                        or actual[5] != request['value']
                        or Decimal(actual[6]) != main_material_quantity(original[owner]['values'][6], request['value'])):
                    raise Rejected('RESOURCE_EDIT_EVIDENCE_MISMATCH', 'The main-material readback differs')
                for key, row in original.items():
                    if key == path:
                        continue
                    old, new = row['values'], current[key]['values']
                    if path.startswith(key + '/'):
                        old, new = old[:7] + old[10:], new[:7] + new[10:]
                    if old != new:
                        raise Rejected('RESOURCE_EDIT_EVIDENCE_MISMATCH', 'An unrelated budget field changed')
                return record_reconciled_material(store, project_id, ctx, result, after, dispatches[0], actual, path)
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
            return record_reconciled_material(store, project_id, ctx, result, after, dispatches[0], actual, request['path'])


def record_reconciled_material(store, project_id, ctx, result, after, dispatch, actual, path):
    proof = store.put_evidence(project_id, ctx.session_id, 'native_material_edit_readback',
        dict(capture=after, dispatch=dispatch['receipt'], preflight_evidence_id=result['preflight_evidence_id']))
    result.update(status='native_material_edit_verified_not_saved', evidence_id=proof,
                  material=actual, target_path=path, readonly_reconciled=True,
                  new_business_dispatches=0)
    result.pop('error', None)
    save_result(store, result, 'readback_verified')
    return result
