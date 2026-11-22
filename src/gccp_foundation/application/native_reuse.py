"""Copy the current ordinary quota below itself, rename its main material and set quantity."""

from contextlib import closing
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
from .native_runtime import _replace, displayed_quantity, find_section_path, normalize_quantity, render_transport
from .native_worker_support import Ledger, RuntimeProfile, SessionManager, WindowsDesktop, execution_lock, project_window_prefix, rows, table
from .ports import SessionContext
from gccp_foundation.storage.native_save_attempts import NativeSaveAttempts


def build_reuse_bridge(directory, profile, session, title, command):
    generated = render_transport(TEMPLATES, directory, command, session, profile, title)
    generated['operations.h'] = (TEMPLATES / 'reuse.h.in').read_text(encoding='utf-8')
    guard = 'QStringList{"insert","material.confirm","note.commit","quantity.commit","name.set","quantity.set","quantity.normal.zero","quantity.normal.one","save"}.contains(op)'
    generated['bridge.cpp'] = _replace(generated['bridge.cpp'], guard,
        'QStringList{"reuse.copy","reuse.paste","reuse.rename","reuse.quantity"}.contains(op)')
    directory.mkdir(parents=True)
    for name, source in generated.items():
        (directory / name).write_text(source, encoding='utf-8', newline='\n')
    compile_bridge(directory)
    return load_bridge(directory)


def source_target(capture, request):
    budget = table(capture, '工程量表达式')
    selected = budget['current']
    path = selected.get('row_path_session_only', '')
    quota = selected.get('values', [])
    parent = find_section_path(budget['rows'], request['parent_sections'])['row_path_session_only']
    if (not selected.get('verified') or not budget['rows'] or budget['rows'][0]['values'][2] != '整个项目'
            or path.rsplit('/', 1)[0] != parent
            or quota[:4] != [request['code'], '定', request['quota_name'], request['unit']]):
        raise Rejected('REUSE_SOURCE_MISMATCH', 'Select the exact ordinary quota in the requested section')
    material = main_material(capture, code=request['material_code'])
    if quota[4] != quota[6]:
        raise Rejected('REUSE_SOURCE_NOT_REVIEWED', 'Require a literal source quantity')
    normalize_quantity(quota[6])
    if material[8] != '0' or material[10] != 'false':
        raise Rejected('REUSE_MATERIAL_NOT_REVIEWED', 'Use an unpriced main material without a locked quantity')
    items = rows(budget)
    if not any(key.startswith(path + '/') and row['values'][:4] == [material[0], '主', material[2], material[4]]
               for key, row in items.items()):
        raise Rejected('REUSE_MATERIAL_OWNER_UNVERIFIED', 'Selected main material must belong to the source quota')
    names = {variant['material_name'] for variant in request['variants']}
    if material[2] in names or any(row['values'][1] == '主' and row['values'][2] in names
           for key, row in items.items() if key.startswith(parent + '/')):
        raise Rejected('REUSE_MATERIAL_EXISTS', 'This material name already exists in the target section')
    return dict(path=path, quota=quota), material


def main_material(capture, *, code=None, name=None):
    resources = table(capture, '单位')
    matches = [row['values'] for row in resources['rows'] if row['values'][1] == '主'
               and (row['values'][0] == code if code is not None else row['values'][2] == name)]
    if resources['headers'][:5] != ['编码', '类别', '名称', '规格及型号', '单位'] or len(matches) != 1:
        raise ValueError('A unique main material in the current 工料机显示 table is required')
    return matches[0]


def verify_paste(before, after, source, anchor):
    old, new = rows(table(before, '工程量表达式')), rows(table(after, '工程量表达式'))
    parent, slot = anchor['path'].rsplit('/', 1)
    position = int(slot) + 1
    path = f'{parent}/{position}'
    current = table(after, '工程量表达式')['current']
    if (not current.get('verified') or current['row_path_session_only'] != path
            or current['values'] != source['quota']):
        raise ValueError('Paste did not select the adjacent copy')
    original = {k[len(source['path']):]: r['values'] for k, r in old.items()
                if k == source['path'] or k.startswith(source['path'] + '/')}
    copied = {k[len(path):]: r['values'] for k, r in new.items() if k == path or k.startswith(path + '/')}
    if copied != original or len(copied) < 2:
        raise ValueError('The copy did not retain the quota and its child rows')
    expected = set()
    for key, row in old.items():
        destination = key
        if key.startswith(parent + '/'):
            child, _, tail = key[len(parent) + 1:].partition('/')
            if int(child) >= position:
                destination = f'{parent}/{int(child) + 1}' + ('/' + tail if tail else '')
        values = new[destination]['values']
        prior = row['values']
        if values != prior and not (parent == key or parent.startswith(key + '/')):
            raise ValueError('Paste changed an existing non-ancestor row')
        if (parent == key or parent.startswith(key + '/')) and values[:8] + values[9:] != prior[:8] + prior[9:]:
            raise ValueError('Paste changed ancestor fields beyond the subtotal')
        expected.add(destination)
    if set(new) != expected | {path + key for key in copied}:
        raise ValueError('Paste added unexpected rows')
    return dict(path=path, quota=current['values'])


def verify_edit(before, after, target, *, quantity=False):
    old, new = rows(table(before, '工程量表达式')), rows(table(after, '工程量表达式'))
    path = target['path']
    if old.keys() != new.keys():
        raise ValueError('Editing changed budget structure')
    for key, row in old.items():
        if key == path or key.startswith(path + '/'):
            continue
        prior, actual = row['values'], new[key]['values']
        if quantity and path.startswith(key + '/'):
            prior, actual = prior[:8] + prior[9:], actual[:8] + actual[9:]
        if prior != actual:
            raise ValueError('Editing changed an unrelated budget row')
    return new[path]['values']


def clone_quota(state, runtime_root, inspector, request):
    command, project_id = request['command_id'], request['project_id']
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', command):
        raise Rejected('IDENTIFIER_INVALID', 'Provide a bounded command ID')
    request = {**request, 'variants': normalize_variants(request['variants'], request['unit'])}
    signature = digest(request)
    config, observer = runtime_config(inspector)
    if config.get('runtime_kind') != 'inspector':
        raise Rejected('INSPECTOR_REQUIRED', 'Use the current read-only inspector')
    with closing(Ledger(state)) as store:
        initialize_commands(store)
        project = store.db.execute('SELECT * FROM projects WHERE project_id=?', (project_id,)).fetchone()
        if not project:
            raise Rejected('PROJECT_NOT_BOUND', 'Bind the current project first')
        with execution_lock(store.root, project['file_id']):
            prior = store.db.execute('SELECT * FROM native_cli_commands WHERE command_id=?', (command,)).fetchone()
            if prior:
                if prior['request_digest'] != signature:
                    raise Rejected('IDEMPOTENCY_CONFLICT', 'Command parameters changed')
                if prior['phase'] != 'readback_verified':
                    raise Rejected('NEEDS_RECONCILIATION', 'Read back the original copy without dispatching again')
                return {**json.loads(prior['result']), 'receipt_only': True, 'new_business_dispatches': 0}
            ctx = SessionContext(**json.loads(project['metadata'])['session'])
            profile = profile_path(ctx.profile_id)
            manager = SessionManager(store, WindowsDesktop(), RuntimeProfile.load(profile))
            meta = manager.validate_session(ctx)
            if (meta.get('dirty') or store.load_pending(project_id) or NativeSaveAttempts(store).active(project_id)
                    or store.db.execute("SELECT 1 FROM native_cli_commands WHERE project_id=? AND phase='armed_unknown'",
                                        (project_id,)).fetchone()):
                raise Rejected('PROJECT_NEEDS_RECONCILIATION', 'Resolve the previous intent first')
            observed = subprocess.run([sys.executable, '-X', 'utf8', str(observer), '--inspect'], capture_output=True,
                                      encoding='utf-8', timeout=50, check=True)
            snapshot = json.loads(observed.stdout)
            if (snapshot['pid'] != ctx.pid or snapshot['created_filetime'] != ctx.created_filetime
                    or not snapshot['callback_on_app_thread']):
                raise Rejected('SESSION_CHANGED', 'Capture does not match the bound session')
            source, material = source_target(snapshot, request)
            directory = Path(runtime_root).resolve() / 'quota-reuse' / uuid.uuid4().hex
            bridge = build_reuse_bridge(directory, profile, meta['session'], project_window_prefix(meta['workcopy_path']), command)
            before = bridge.call('inspect')
            if (source_target(before, request) != (source, material)
                    or table(before, '工程量表达式')['rows'] != table(snapshot, '工程量表达式')['rows']):
                raise Rejected('REUSE_PREFLIGHT_CHANGED', 'Budget changed during bridge preparation')
            intent = 'intent.' + uuid.uuid4().hex
            copied = bridge.call('reuse.copy', intent, expected_target=source)
            ready = bridge.call('inspect')
            if copied['copy_verified'] and not paste_enabled(ready):
                bridge.call('reuse.menu', expected_target=source)
                ready = bridge.call('inspect')
            if (not copied['copy_verified'] or table(before, '工程量表达式')['rows'] != table(ready, '工程量表达式')['rows']
                    or not paste_enabled(ready)):
                raise Rejected('REUSE_COPY_NOT_VERIFIED', 'Copy or paste availability could not be verified')
            proof = store.put_evidence(project_id, ctx.session_id, 'native_reuse_preflight',
                                       dict(request=request, capture=before, intent_id=intent))
            result = dict(command_id=command, status='armed_unknown', runtime=str(directory),
                          preflight_evidence_id=proof, save_dispatches=0)
            with store._transaction():
                store.db.execute('INSERT INTO native_cli_commands VALUES(?,?,?,?,?,?)',
                                 (command, project_id, signature, 'armed_unknown', encoded(result), int(time.time()*1000)))
            try:
                prior, anchor = ready, source
                result['clones'] = []
                for variant in request['variants']:
                    if not paste_enabled(prior):
                        bridge.call('reuse.menu', expected_target=anchor)
                        prior = bridge.call('inspect')
                    if not paste_enabled(prior):
                        raise ValueError('Original copy is no longer available for paste')
                    new, prior = run_edits(bridge, intent, prior, source, anchor, material, request['unit'], variant)
                    anchor = dict(path=new['path'], quota=new['quota'])
                    result['clones'].append(new)
                    result['ipc_records'] = bridge.records
                    save_result(store, result)
                result['evidence_id'] = store.put_evidence(
                    project_id, ctx.session_id, 'native_quota_clone_readback',
                    dict(capture=prior, preflight_evidence_id=result['preflight_evidence_id']))
                result.update(status='native_quota_clone_readback_verified_not_saved', ipc_records=bridge.records)
                save_result(store, result, 'readback_verified')
                return result
            except (ArithmeticError, ValueError, KeyError, OSError, RuntimeError) as exc:
                result.update(error=str(exc), ipc_records=bridge.records)
                save_result(store, result)
                raise Rejected('NEEDS_RECONCILIATION', str(exc)) from exc


def reconcile_no_effect_paste(state, command_id):
    """Keep previously verified copies when the final paste left the budget unchanged."""
    with closing(Ledger(state)) as store:
        initialize_commands(store)
        record = store.db.execute('SELECT * FROM native_cli_commands WHERE command_id=?', (command_id,)).fetchone()
        if record is None:
            raise Rejected('COMMAND_CHANGED', 'The original clone command is required')
        result = json.loads(record['result'] or '{}')
        if result.get('status') == 'native_quota_clone_partial_readback_verified':
            return {**result, 'receipt_only': True, 'new_business_dispatches': 0}
        if record['phase'] != 'armed_unknown' or result.get('error') != 'Paste did not select the adjacent copy':
            raise Rejected('CLONE_RECONCILIATION_UNAVAILABLE', 'No supported no-effect paste to verify')
        project_id = record['project_id']
        project = store.db.execute('SELECT * FROM projects WHERE project_id=?', (project_id,)).fetchone()
        with execution_lock(store.root, project['file_id']):
            if dict(store.db.execute('SELECT * FROM native_cli_commands WHERE command_id=?', (command_id,)).fetchone()) != dict(record):
                raise Rejected('COMMAND_CHANGED', 'The original clone command changed')
            ctx = SessionContext(**json.loads(project['metadata'])['session'])
            profile = profile_path(ctx.profile_id)
            SessionManager(store, WindowsDesktop(), RuntimeProfile.load(profile)).validate_session(ctx)
            proof = store.read_evidence(result['preflight_evidence_id'], project_id)
            request = proof['data']['request']
            if (proof['kind'] != 'native_reuse_preflight' or proof['session_id'] != ctx.session_id
                    or digest(request) != record['request_digest']):
                raise Rejected('CLONE_EVIDENCE_MISMATCH', 'The original clone preflight must match')
            ipc = result.get('ipc_records', [])[-3:]
            records = [json.loads(Path(path).read_text(encoding='utf-8')) for path in ipc]
            if [item['operation'] for item in records] != ['inspect', 'reuse.paste', 'inspect']:
                raise Rejected('CLONE_EVIDENCE_MISMATCH', 'The final paste and its two observations are required')
            before, dispatch, observed = [item.get('receipt', {}) for item in records]
            if (records[1]['request'].get('outer_intent_id') != proof['data']['intent_id']
                    or dispatch.get('status') != 'observed' or dispatch.get('business_dispatches') != 1
                    or any(item.get('pid') != ctx.pid or item.get('created_filetime') != ctx.created_filetime
                           for item in (before, dispatch, observed))):
                raise Rejected('CLONE_EVIDENCE_MISMATCH', 'The final paste must belong to the bound original process')
            baseline = rows(table(before, '工程量表达式'))
            if rows(table(observed, '工程量表达式')) != baseline:
                raise Rejected('CLONE_READBACK_CHANGED', 'The final paste changed the budget')
            for clone in result.get('clones', []):
                current = baseline.get(clone['path'], {}).get('values')
                material = clone['material']
                if current != clone['quota'] or not any(
                        key.startswith(clone['path'] + '/') and item['values'][:4] == [
                            material[0], '主', material[2], material[4]]
                        for key, item in baseline.items()):
                    raise Rejected('CLONE_READBACK_CHANGED', 'An earlier verified copy did not match')
            bridge = load_bridge(Path(result['runtime']))
            after = bridge.call('inspect')
            if (after.get('pid') != ctx.pid or after.get('created_filetime') != ctx.created_filetime
                    or rows(table(after, '工程量表达式')) != baseline):
                raise Rejected('CLONE_READBACK_CHANGED', 'The fresh budget differs from the original paste readback')
            evidence = store.put_evidence(project_id, ctx.session_id, 'native_quota_clone_partial_readback',
                dict(capture=after, dispatch=dispatch, preflight_evidence_id=result['preflight_evidence_id']))
            result.update(status='native_quota_clone_partial_readback_verified', evidence_id=evidence,
                          new_business_dispatches=0, ipc_records=result['ipc_records'] + bridge.records)
            result.pop('error', None)
            save_result(store, result, 'readback_verified')
            return result


def paste_enabled(capture):
    return any(a['name'] == 'Paste' and a['enabled'] and a['visible'] for a in capture['reuse_actions'])


def normalize_variants(variants, unit):
    # Each variant can use up to eight requests. Leave room for preflight in the 48-call bridge.
    if not isinstance(variants, list) or not 1 <= len(variants) <= 5:
        raise Rejected('REUSE_VARIANTS_INVALID', 'Provide 1 to 5 variants')
    reviewed = []
    for variant in variants:
        name = variant['material_name']
        if not isinstance(name, str) or not name.strip() or name != name.strip() or len(name) > 100 or any(ord(c) < 32 for c in name):
            raise Rejected('REUSE_ARGUMENT_INVALID', 'Provide a bounded material name')
        quantity = normalize_quantity(variant['quantity'])
        displayed_quantity(unit, quantity)
        reviewed.append(dict(material_name=name, quantity=quantity))
    if len({variant['material_name'] for variant in reviewed}) != len(reviewed):
        raise Rejected('REUSE_VARIANT_DUPLICATE', 'Combine repeated material quantities first')
    return reviewed


def run_edits(bridge, intent, before, source, anchor, material, unit, variant):
    def dispatch(operation, target):
        receipt = bridge.call(operation, intent, expected_target=target)
        if receipt['business_dispatches'] != 1:
            raise ValueError('Expected one native dispatch for ' + operation)
        return bridge.call('inspect')

    pasted = dispatch('reuse.paste', anchor)
    target = verify_paste(before, pasted, source, anchor)
    if main_material(pasted, code=material[0]) != material:
        raise ValueError('Pasted main material does not match the source')
    renamed = dispatch('reuse.rename', {**target, 'resource': material, 'value': variant['material_name']})
    new_material = main_material(renamed, name=variant['material_name'])
    if (verify_edit(pasted, renamed, target) != target['quota'] or not new_material[0]
            or new_material[3:] != material[3:]):
        raise ValueError('Unexpected changes after renaming the main material')
    prior_resources = table(pasted, '单位')['rows']
    current_resources = table(renamed, '单位')['rows']
    expected_resources = [{**row, 'values': new_material} if row['values'] == material else row
                          for row in prior_resources]
    if current_resources != expected_resources:
        raise ValueError('Renaming changed another resource')
    quantity = variant['quantity']
    shown = displayed_quantity(unit, quantity)
    final = renamed if quantity == target['quota'][4] else dispatch('reuse.quantity', {**target, 'value': quantity})
    quota = verify_edit(renamed, final, target, quantity=True)
    if quota[:4] != target['quota'][:4] or quota[4] != quantity or normalize_quantity(quota[6]) != shown:
        raise ValueError('Cloned quota quantity did not read back')
    material_after = main_material(final, name=variant['material_name'])
    if (material_after[:7] != new_material[:7] or material_after[8] != new_material[8]
            or material_after[10:] != new_material[10:]):
        raise ValueError('Quantity editing changed the main material identity or pricing basis')
    return dict(path=target['path'], quota=quota, material=material_after), final
