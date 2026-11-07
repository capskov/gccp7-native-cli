"""Single-row native deletion against a bound GCCP budget, with durable no-replay intent."""

from decimal import Decimal, InvalidOperation
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import time
import uuid
import zipfile

from gccp_foundation.application.native_cli import Rejected, digest, encoded, initialize_commands
from gccp_foundation.application.native_runtime import _replace, render_transport
from gccp_foundation.application.native_build import TEMPLATES, compile_bridge, profile_path
from gccp_foundation.application.native_worker_support import (
    Ledger, RuntimeProfile, SessionManager, WindowsDesktop, execution_lock, file_sha256, project_window_prefix, rows, table,
)
from gccp_foundation.application.ports import SessionContext
from gccp_foundation.storage.native_save_attempts import NativeSaveAttempts


ACTIONS = {
    'temporary': ('PreDelete', '临时删除'),
    'permanent': ('Delete', '删除'),
}


def build_bridge(directory, profile, session, title, request, *, confirm_only=False):
    templates = TEMPLATES
    generated = render_transport(templates, directory, request['command_id'], session, profile, title)
    action, label = ACTIONS[request['mode']]
    constants = {
        'kParent': request['parent_id'], 'kCode': request['code'], 'kName': request['name'],
        'kUnit': request['unit'], 'kAction': action, 'kActionText': label,
    }
    header = ''.join(f'static const QString {name} = QStringLiteral({json.dumps(value, ensure_ascii=False)});\n'
                     for name, value in constants.items())
    header += f'static constexpr bool kConfirmOnly = {str(confirm_only).lower()};\n'
    generated['operations.h'] = header + (templates/'delete.h.in').read_text(encoding='utf-8')
    generated['bridge.cpp'] = _replace(generated['bridge.cpp'],
        'QStringList{"insert","material.confirm","note.commit","quantity.commit","name.set","quantity.set","quantity.normal.zero","quantity.normal.one","save"}.contains(op)',
        'op == "quota.delete" || op == "quota.delete.confirm"')
    directory.mkdir(parents=True, exist_ok=False)
    for name, source in generated.items():
        (directory/name).write_text(source, encoding='utf-8', newline='\n')
    compile_bridge(directory)
    return load_bridge(directory)


def load_bridge(directory):
    spec = importlib.util.spec_from_file_location('_gccp_delete_bridge', Path(directory)/'cli_bridge.py')
    client = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(client)
    return client.NativeBridge()


def verified_delta(before, after, mode):
    """Compare unrelated rows after removing the target subtree and normalizing indices."""
    target = before['target']
    path = target['path']
    parent, slot = path.rsplit('/', 1)
    slot = int(slot)
    old = rows(table(before, '工程量表达式'))
    new = rows(table(after, '工程量表达式'))
    if after['target_matches'] != (1 if mode == 'temporary' else 0):
        raise ValueError('Target retention/removal did not match the requested mode')
    if mode == 'temporary':
        post = after['target']
        if post['path'] != path or not any(post['strikeout']) or post['values'][:4] != target['values'][:4]:
            raise ValueError('Retained row has no verified temporary-delete marker')
    affected = ['/0', parent]
    old_outside = {k: r for k, r in old.items() if k != path and not k.startswith(path+'/')}
    new_outside = {}
    for k, row in new.items():
        if mode == 'temporary' and (k == path or k.startswith(path+'/')):
            continue
        if mode == 'permanent' and k.startswith(parent+'/'):
            child, _, descendants = k[len(parent)+1:].partition('/')
            if int(child) >= slot:
                k = f'{parent}/{int(child)+1}' + ('/'+descendants if descendants else '')
        new_outside[k] = {**row, 'row_path_session_only': k}
    totals = {}
    try:
        amount = Decimal(target['values'][8])
        for k in affected:
            prev, curr = old_outside[k]['values'], new_outside[k]['values']
            old_total, new_total = Decimal(prev[8]), Decimal(curr[8])
            if abs(old_total - new_total - amount) > Decimal('0.01'):
                raise ValueError('GCCP subtotal did not change by the target amount')
            totals[k] = {'before':prev[8], 'after':curr[8]}
            old_outside[k] = {**old_outside[k], 'values':prev[:8]+curr[8:9]+prev[9:]}
    except (InvalidOperation, KeyError) as exc:
        raise ValueError('GCCP subtotal readback unavailable') from exc
    if old_outside != new_outside:
        raise ValueError('Non-target budget rows changed during deletion')
    return totals


def delete_quota(state, runtime_root, request):
    for name in ('command_id', 'parent_id'):
        if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', request[name]):
            raise Rejected('IDENTIFIER_INVALID', f'Explicit bounded {name} required')
    for name in ('code', 'name', 'unit'):
        value = request[name]
        if not value.strip() or len(value) > 256 or any(ord(ch) < 32 for ch in value):
            raise Rejected('TARGET_IDENTITY_INVALID', f'Exact nonempty {name} required')
    project_id, command_id = request['project_id'], request['command_id']
    mode = request['mode']
    if mode not in ACTIONS:
        raise Rejected('DELETE_MODE_INVALID', 'Choose temporary or permanent explicitly')
    store = Ledger(state)
    try:
        initialize_commands(store)
        saves = NativeSaveAttempts(store)
        signature = digest(request)
        prior = store.db.execute('SELECT * FROM native_cli_commands WHERE command_id=?', (command_id,)).fetchone()
        if prior:
            if prior['request_digest'] != signature:
                raise Rejected('IDEMPOTENCY_CONFLICT', 'command_id has different business parameters')
            if prior['phase'] != 'readback_verified':
                raise Rejected('NEEDS_RECONCILIATION', 'Prior deletion outcome is unknown. Inspect command-status without redispatch.')
            return {**json.loads(prior['result']), 'idempotent_reentry': True,
                    'receipt_only': True, 'current_state_revalidated': False,
                    'new_business_dispatches': 0, 'new_confirmation_dispatches': 0}
        project = store.db.execute('SELECT * FROM projects WHERE project_id=?', (project_id,)).fetchone()
        if not project:
            raise Rejected('PROJECT_NOT_BOUND', 'Current project must be bound first')
        with execution_lock(state, project['file_id']):
            meta = json.loads(project['metadata'])
            ctx = SessionContext(**meta['session'])
            profile = profile_path(ctx.profile_id)
            manager = SessionManager(store, WindowsDesktop(), RuntimeProfile.load(profile))
            meta = manager.validate_session(ctx)
            if meta.get('dirty') or store.load_pending(project_id) or saves.active(project_id):
                raise Rejected('PROJECT_NEEDS_RECONCILIATION', 'Resolve existing business or Save intent first')
            if store.db.execute("SELECT 1 FROM native_cli_commands WHERE project_id=? AND phase='armed_unknown'", (project_id,)).fetchone():
                raise Rejected('PROJECT_NEEDS_RECONCILIATION', 'Resolve existing CLI intent first')
            scope = hashlib.sha256(command_id.encode()).hexdigest()[:20]
            directory = Path(runtime_root).resolve()/'delete'/scope/uuid.uuid4().hex[:12]
            bridge = build_bridge(directory, profile, meta['session'], project_window_prefix(meta['workcopy_path']), request)
            before = bridge.call('inspect', timeout=30)
            table(before, '工程量表达式')
            if before['modals']:
                raise Rejected('DELETE_WINDOW_BLOCKED', 'A modal dialog is visible. Inspect it before deleting.')
            if before['target_matches'] != 1:
                raise Rejected('DELETE_TARGET_NOT_UNIQUE', 'The exact quota must exist once under the requested budget parent')
            action, label = ACTIONS[mode]
            if sum(item['name'] == action and item['text'] == label and item['enabled']
                   for item in before['delete_actions']) != 1:
                raise Rejected('DELETE_ACTION_UNAVAILABLE', 'The reviewed budget action is not available')
            target = before['target']
            if mode == 'temporary' and any(target['strikeout']):
                raise Rejected('ALREADY_TEMPORARY_DELETED', 'Use a fresh readback to inspect the existing temporary deletion')
            intent = 'intent.'+uuid.uuid4().hex
            pre = store.put_evidence(project_id, ctx.session_id, 'native_quota_delete_preflight',
                                     dict(request=request, capture=before, intent_id=intent))
            result = dict(command_id=command_id, mode=mode, action=ACTIONS[mode][0],
                          target=target['values'], parent_id=request['parent_id'], preflight_evidence_id=pre,
                          runtime=str(directory), save_dispatches=0, status='armed_unknown')
            with store._transaction():
                if store.load_pending(project_id) or saves.active(project_id):
                    raise Rejected('PROJECT_NEEDS_RECONCILIATION', 'A new pending action or Save blocks deletion')
                store.db.execute('INSERT INTO native_cli_commands VALUES(?,?,?,?,?,?)',
                    (command_id, project_id, signature, 'armed_unknown', encoded(result), int(time.time()*1000)))
            try:
                dispatch = bridge.call('quota.delete', intent, timeout=30, expected_target=target['values'])
                if not dispatch.get('native_delete_queued'):
                    raise ValueError('Native deletion did not return a dispatch acknowledgement')
                after = bridge.call('inspect', timeout=30)
                result.update(business_dispatches=1, ipc_records=bridge.records)
                if after['modals']:
                    pending = store.put_evidence(project_id, ctx.session_id, 'native_quota_delete_readback',
                        dict(dispatch=dispatch, capture=after, preflight_evidence_id=pre))
                    result.update(evidence_id=pending, modals=after['modals'], target_after=after.get('target'))
                    save_result(store, result)
                    if mode != 'permanent':
                        raise ValueError('Unexpected temporary-delete dialog')
                    return confirm_permanent(store, ctx, bridge, before, after, intent, result)
                return finish_delete(store, ctx, result, before, after, dispatch)
            except (ValueError, OSError, RuntimeError) as exc:
                stop_delete(store, result, exc)
    finally:
        store.close()


def save_result(store, result, phase='armed_unknown'):
    with store._transaction():
        changed = store.db.execute(
            "UPDATE native_cli_commands SET phase=?,result=? WHERE command_id=? AND phase='armed_unknown'",
            (phase, encoded(result), result['command_id']))
        if changed.rowcount != 1:
            raise Rejected('COMMAND_CHANGED', 'Deletion intent changed during execution')


def stop_delete(store, result, exc):
    result['error'] = str(exc)
    save_result(store, result)
    raise Rejected('NEEDS_RECONCILIATION', str(exc) + ' Inspect command-status. Do not repeat the deletion.') from exc


def finish_delete(store, ctx, result, before, after, dispatch):
    proof = store.put_evidence(ctx.project_id, ctx.session_id, 'native_quota_delete_readback',
        dict(dispatch=dispatch, capture=after, preflight_evidence_id=result['preflight_evidence_id'],
             pending_confirmation_evidence_id=result.get('pending_confirmation_evidence_id')))
    result.update(evidence_id=proof, modals=after['modals'], target_after=after.get('target'))
    if after['modals']:
        raise ValueError('An unresolved dialog remains after deletion')
    totals = verified_delta(before, after, result['mode'])
    result.pop('error', None)
    result.update(status='native_delete_readback_verified_not_saved', totals=totals)
    save_result(store, result, 'readback_verified')
    return result


def require_confirmation(before, pending):
    dialogs = pending['modals']
    if len(dialogs) != 1:
        raise ValueError('Exactly one native deletion dialog is required')
    dialog = dialogs[0]
    if (dialog['class'] != 'GCCPMessageBox' or dialog['title'] != '确认' or
            sorted(t for t in dialog['buttons'] if t) != ['否', '是'] or
            dialog['labels'].count("<p style='line-height:23px;'>确认要删除当前选中行吗？</p>") != 1):
        raise ValueError('Unrecognized deletion confirmation. Inspect the recorded dialog.')
    model = table(pending, '工程量表达式')
    if (pending['target_matches'] != 1 or pending['target'] != before['target'] or
            model['current']['row_path_session_only'] != before['target']['path'] or
            model['rows'] != table(before, '工程量表达式')['rows']):
        raise ValueError('The pending deletion target or budget changed before confirmation')


def confirm_permanent(store, ctx, bridge, before, pending, intent, result):
    if result.get('confirmation_armed'):
        raise ValueError('Confirmation may have been dispatched. Do not send it again.')
    require_confirmation(before, pending)
    result.update(confirmation_armed=True, pending_confirmation_evidence_id=result['evidence_id'])
    save_result(store, result)
    try:
        confirmed = bridge.call('quota.delete.confirm', intent, timeout=30, expected_target=before['target']['values'])
        if confirmed.get('native_delete_confirmation_clicked') is not True:
            raise ValueError('No receipt for the native confirmation')
        result['confirmation_dispatches'] = 1
        after = bridge.call('inspect', timeout=30)
    finally:
        result['ipc_records'] = list(dict.fromkeys(result['ipc_records'] + bridge.records))
    return finish_delete(store, ctx, result, before, after, confirmed)


def confirm_pending_delete(state, command_id, *, reviewed_file_sha256=None):
    """Finish the already dispatched permanent action without invoking Delete again."""
    store = Ledger(state)
    try:
        initialize_commands(store)
        row = store.db.execute('SELECT * FROM native_cli_commands WHERE command_id=?', (command_id,)).fetchone()
        if not row:
            raise Rejected('COMMAND_NOT_FOUND', 'An original deletion command is required')
        result = json.loads(row['result'] or '{}')
        if result.get('mode') != 'permanent':
            raise Rejected('CONFIRMATION_NOT_AVAILABLE', 'Only a permanent deletion has this confirmation')
        if row['phase'] == 'readback_verified':
            return {**result, 'idempotent_reentry': True, 'receipt_only': True,
                    'new_business_dispatches': 0, 'new_confirmation_dispatches': 0}
        if result.get('confirmation_armed') or not result.get('evidence_id') or result.get('business_dispatches') != 1:
            raise Rejected('CONFIRMATION_NOT_AVAILABLE', 'No unconfirmed, recorded native dialog for this command')
        project_id = row['project_id']
        project = store.db.execute('SELECT * FROM projects WHERE project_id=?', (project_id,)).fetchone()
        with execution_lock(state, project['file_id']):
            current = store.db.execute('SELECT * FROM native_cli_commands WHERE command_id=?', (command_id,)).fetchone()
            if dict(current) != dict(row):
                raise Rejected('COMMAND_CHANGED', 'Deletion state changed before confirmation')
            meta = json.loads(project['metadata'])
            ctx = SessionContext(**meta['session'])
            profile = profile_path(ctx.profile_id)
            manager = SessionManager(store, WindowsDesktop(), RuntimeProfile.load(profile))
            disk_identity = None
            if reviewed_file_sha256 is None:
                manager.validate_session(ctx)
            else:
                if not re.fullmatch(r'[0-9a-f]{64}', reviewed_file_sha256):
                    raise Rejected('DISK_DIGEST_INVALID', 'A reviewed current-file SHA256 is required')
                disk_identity = manager.inspect_saved_open_identity(ctx, reviewed_file_sha256, allow_disabled_window=True)
                try:
                    with zipfile.ZipFile(disk_identity['path']) as archive:
                        if archive.testzip() is not None:
                            raise Rejected('DISK_CRC_MISMATCH', 'Current project file has a failed CRC')
                except zipfile.BadZipFile as exc:
                    raise Rejected('DISK_CRC_MISMATCH', 'Current project file is not a readable archive') from exc
            request, before, recorded, dispatch, intent = pending_delete_evidence(store, row, ctx, result)
            require_confirmation(before, recorded)
            directory = Path(result['runtime'])/('confirm-'+uuid.uuid4().hex[:12])
            bridge = build_bridge(directory, profile, meta['session'], project_window_prefix(meta['workcopy_path']),
                                  request, confirm_only=True)
            fresh = bridge.call('inspect', timeout=30)
            if fresh['modals'] != recorded['modals']:
                raise Rejected('CONFIRMATION_NOT_AVAILABLE', 'Recorded native dialog changed')
            if disk_identity is not None:
                current_disk = manager.inspect_saved_open_identity(ctx, reviewed_file_sha256, allow_disabled_window=True)
                if current_disk != disk_identity:
                    raise Rejected('SESSION_CHANGED', 'Reviewed disk identity changed during modal inspection')
                result['reviewed_confirmation_disk_identity'] = disk_identity
            try:
                final = confirm_permanent(store, ctx, bridge, before, fresh, intent, result)
                return {**final, 'new_business_dispatches': 0, 'new_confirmation_dispatches': 1}
            except (ValueError, OSError, RuntimeError) as exc:
                stop_delete(store, result, exc)
    finally:
        store.close()

def pending_delete_evidence(store, row, ctx, result):
    pre = store.read_evidence(result['preflight_evidence_id'], ctx.project_id)
    pending_id = result.get('pending_confirmation_evidence_id') or result['evidence_id']
    pending = store.read_evidence(pending_id, ctx.project_id)
    request = pre['data']['request']
    dispatch = pending['data']['dispatch']
    if (pre['kind'] != 'native_quota_delete_preflight' or pending['kind'] != 'native_quota_delete_readback' or
            pre['session_id'] != ctx.session_id or pending['session_id'] != ctx.session_id or
            pending['data']['preflight_evidence_id'] != result['preflight_evidence_id'] or
            digest(request) != row['request_digest'] or dispatch.get('native_delete_queued') is not True):
        raise Rejected('CONFIRMATION_NOT_AVAILABLE', 'Original action and native session proof required')
    return request, pre['data']['capture'], pending['data']['capture'], dispatch, pre['data']['intent_id']


def reconcile_pending_delete(state, command_id):
    """Verify a pending permanent deletion through its existing bridge, with no write dispatch."""
    store = Ledger(state)
    try:
        initialize_commands(store)
        row = store.db.execute('SELECT * FROM native_cli_commands WHERE command_id=?', (command_id,)).fetchone()
        if not row:
            raise Rejected('COMMAND_NOT_FOUND', 'An original deletion command is required')
        result = json.loads(row['result'] or '{}')
        if result.get('mode') != 'permanent':
            raise Rejected('RECONCILIATION_NOT_AVAILABLE', 'Only a recorded permanent-delete confirmation is supported')
        if row['phase'] == 'readback_verified':
            return {**result, 'idempotent_reentry': True, 'receipt_only': True, 'new_business_dispatches': 0}
        if not result.get('evidence_id') or result.get('business_dispatches') != 1:
            raise Rejected('RECONCILIATION_NOT_AVAILABLE', 'Original dispatch and dialog evidence required')
        project = store.db.execute('SELECT * FROM projects WHERE project_id=?', (row['project_id'],)).fetchone()
        with execution_lock(state, project['file_id']):
            current = store.db.execute('SELECT * FROM native_cli_commands WHERE command_id=?', (command_id,)).fetchone()
            if dict(current) != dict(row):
                raise Rejected('COMMAND_CHANGED', 'Deletion state changed before reconciliation')
            meta = json.loads(project['metadata'])
            ctx = SessionContext(**meta['session'])
            profile = profile_path(ctx.profile_id)
            manager = SessionManager(store, WindowsDesktop(), RuntimeProfile.load(profile))
            _, before, recorded, dispatch, _ = pending_delete_evidence(store, row, ctx, result)
            require_confirmation(before, recorded)
            bridge = load_bridge(result['runtime'])
            after = bridge.call('inspect', timeout=30)
            if after['modals']:
                raise Rejected('CONFIRMATION_STILL_OPEN', 'Original deletion still awaits confirmation')
            if (after['pid'] != ctx.pid or after['created_filetime'] != ctx.created_filetime or
                    after['callback_on_app_thread'] is not True):
                raise Rejected('SESSION_CHANGED', 'Original bound process required')
            disk = manager.inspect_saved_open_identity(ctx, file_sha256(Path(meta['workcopy_path'])))
            result.update(readonly_reconciled=True, new_business_dispatches=0, new_confirmation_dispatches=0,
                          observed_disk_identity=disk, pending_confirmation_evidence_id=result['evidence_id'],
                          ipc_records=list(dict.fromkeys(result['ipc_records'] + bridge.records)))
            return finish_delete(store, ctx, result, before, after, dispatch)
    finally:
        store.close()
