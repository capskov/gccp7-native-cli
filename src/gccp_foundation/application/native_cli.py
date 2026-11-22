"""Native GCCP7 CLI entry point. Reviewed native workers share the existing Ledger.

SQLite catalogs are evidence for discovery, never authority to auto-select a quota.
Workers are explicitly pinned runtime configurations, not shell command strings.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

from gccp_foundation.storage import Ledger
from gccp_foundation.storage.ledger import encoded
from gccp_foundation.storage.native_save_attempts import NativeSaveAttempts
from gccp_foundation import __version__


class Rejected(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise Rejected('ARGUMENT_INVALID', message)


DEFAULT_SAVE_TIMEOUT = 300


def save_timeout_seconds(value):
    if type(value) is not int or not 30 <= value <= 1800:
        raise Rejected('SAVE_TIMEOUT_INVALID', 'Save timeout must be between 30 and 1800 seconds')
    return value


def digest(value):
    return hashlib.sha256(encoded(value).encode('utf-8')).hexdigest()


class Catalog:
    def __init__(self, path):
        self.path = Path(path).resolve(strict=True)
        self.db = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA query_only=ON')
        self.db.execute('SELECT id,source_id,code,name,spec,unit,confidence FROM quota_entries LIMIT 0')
        self.db.execute('SELECT source_id,filename,sha256,status FROM sources LIMIT 0')

    def close(self):
        self.db.close()

    def search(self, *, code=None, text=None, source_id=None, spec=None, specialty=None, limit=20):
        if not 1 <= limit <= 100:
            raise Rejected('LIMIT_INVALID', 'limit must be between 1 and 100')
        clauses, values = [], []
        if specialty is not None:
            # Only the professionally identified sources used by the live POC.
            sources = {'管网': ('04市政第四册上.pdf', '04市政第四册下.pdf')}.get(specialty)
            if sources is None:
                raise Rejected('SPECIALTY_MAPPING_UNREVIEWED', 'Use --source-id until this specialty mapping is reviewed')
            clauses.append('s.filename IN (?,?)'); values.extend(sources)
        for column, value in [('q.code', code), ('q.source_id', source_id), ('q.spec', spec)]:
            if value is not None:
                clauses.append(column + '=?'); values.append(value)
        if text:
            for token in text.split():
                token = {'球墨管件':'球墨铸铁管管件安装'}.get(token, token)
                clauses.append("instr(coalesce(q.name,'')||' '||coalesce(q.spec,''),?)>0")
                values.append(token)
        query = ('SELECT q.*,s.filename AS source_filename,s.sha256 AS source_sha256,'
                 's.status AS source_status FROM quota_entries q JOIN sources s USING(source_id)')
        if clauses:
            query += ' WHERE ' + ' AND '.join(clauses)
        return [self._record(dict(r)) for r in self.db.execute(query + ' ORDER BY q.source_id,q.id LIMIT ?', values + [limit])]

    @staticmethod
    def _record(row):
        row['automatic_insertion_approved'] = False
        row['live_candidate_check_required'] = True
        row['identity_digest'] = digest(row)
        return row

    def entry(self, entry_id):
        row = self.db.execute('SELECT q.*,s.filename AS source_filename,s.sha256 AS source_sha256,'
            's.status AS source_status FROM quota_entries q JOIN sources s USING(source_id) WHERE q.id=?', (entry_id,)).fetchone()
        if row is None:
            raise Rejected('ENTRY_NOT_FOUND', 'Catalog entry not found')
        return self._record(dict(row))

    def summary(self):
        return dict(path=str(self.path), entries=self.db.execute('SELECT count(*) FROM quota_entries').fetchone()[0],
            sources=self.db.execute('SELECT count(*) FROM sources').fetchone()[0],
            confidence_counts=dict(self.db.execute('SELECT confidence,count(*) FROM quota_entries GROUP BY confidence')),
            automatic_insertion_approved=False, mode='read_only')


def request_catalog_entry(catalog_path, request):
    entry_id = request.get('entry_id')
    if entry_id is None:
        if catalog_path is not None:
            raise Rejected('CATALOG_ARGUMENTS', 'Use --db with --entry-id, or supply an exact live candidate')
        return dict(id=None, identity_digest=None, code=request.get('candidate_code'), unit=request.get('candidate_unit'))
    if catalog_path is None:
        raise Rejected('CATALOG_ARGUMENTS', '--entry-id requires --db')
    catalog = Catalog(catalog_path)
    try:
        return catalog.entry(entry_id)
    finally:
        catalog.close()


def runtime_config(path):
    config = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    worker = Path(config['worker']).resolve(strict=True)
    if hashlib.sha256(worker.read_bytes()).hexdigest() != config['worker_sha256']:
        raise Rejected('RUNTIME_CHANGED', 'Reviewed worker hash changed')
    for name, expected in config.get('artifact_sha256', {}).items():
        artifact = Path(name).resolve(strict=True)
        if artifact.parent not in (worker.parent, worker.parent/'build') or hashlib.sha256(artifact.read_bytes()).hexdigest() != expected:
            raise Rejected('RUNTIME_CHANGED', 'Prepared native artifact changed')
    if config.get('channel') != 'native_qt_business' or config.get('live_candidate_check_required') is not True:
        raise Rejected('RUNTIME_UNREVIEWED', 'A reviewed native worker with live candidate checks is required')
    return config, worker


def initialize_commands(store):
    with store._transaction():
        store.db.execute('''CREATE TABLE IF NOT EXISTS native_cli_commands(
            command_id TEXT PRIMARY KEY,project_id TEXT NOT NULL,request_digest TEXT NOT NULL,
            phase TEXT NOT NULL,result TEXT,created_ms INTEGER NOT NULL)''')
        store.db.execute('''CREATE UNIQUE INDEX IF NOT EXISTS native_cli_one_inflight_project
            ON native_cli_commands(project_id) WHERE phase='armed_unknown' ''')


def command_resource_evidence(store, command):
    from .native_worker_support import table

    result = command['result'] or {}
    evidence_id = result.get('evidence_id')
    if command['phase'] != 'readback_verified' or not evidence_id:
        raise Rejected('RESOURCE_EVIDENCE_UNAVAILABLE', 'A completed command with resource readback is required')
    evidence = store.read_evidence(evidence_id, command['project_id'])
    if evidence['kind'] not in ('new_quota_quantity_and_resources_verified', 'native_cli_open_save_readback'):
        raise Rejected('RESOURCE_EVIDENCE_UNAVAILABLE', 'This receipt does not certify resource readback')
    capture = evidence['data']['capture']
    current = table(capture, '工程量表达式')['current']
    if (current.get('verified') is not True or current.get('row_path_session_only') != result['target_path']
            or current.get('values') != result['quota']):
        raise Rejected('RESOURCE_EVIDENCE_MISMATCH', 'Archived resources must belong to the exact inserted quota')
    resources = table(capture, '单位')
    if len(resources['rows']) != result['resource_rows'] or not resources['rows']:
        raise Rejected('RESOURCE_EVIDENCE_MISMATCH', 'Archived resource count differs from the command receipt')
    return dict(evidence_id=evidence_id, historical=True, headers=resources['headers'], rows=resources['rows'])


def require_quota_execution_scope(parent_sections, *, selected_anchor=False, save=True):
    if selected_anchor and (parent_sections is None or save):
        raise Rejected('SECTION_ANCHOR_ARGUMENTS', 'The selected anchor requires a section path in no-save mode')
    if parent_sections is not None and not selected_anchor:
        raise Rejected('SECTION_PATH_WRITE_SUSPENDED',
                       'Section-path execution requires the explicitly selected anchor and no-save mode')


def execute(state, catalog_path, runtime_path, request):
    config, worker = runtime_config(runtime_path)
    if request != config['approved_request']:
        raise Rejected('REQUEST_NOT_REVIEWED', 'Request differs from the reviewed native runtime contract')
    selected_anchor = request.get('selected_anchor') is True
    require_quota_execution_scope(request.get('parent_sections'), selected_anchor=selected_anchor,
                                  save=request.get('save', True))
    if selected_anchor and config.get('selected_anchor_contract') != 1:
        raise Rejected('SECTION_ANCHOR_RUNTIME_UNREVIEWED', 'Prepare a runtime with the selected-anchor safeguards')
    save_timeout = save_timeout_seconds(request.get('save_timeout', DEFAULT_SAVE_TIMEOUT))
    entry = request_catalog_entry(catalog_path, request)
    exact = config.get('catalog_match') == 'live_exact_candidate'
    if not all(entry.get(field) for field in (('unit', 'code') if exact else ('spec', 'unit', 'name', 'code'))):
        raise Rejected('CATALOG_REVIEW_REQUIRED', 'Incomplete extraction cannot identify the software candidate')
    live_family = config.get('catalog_match') == 'live_reviewed_family'
    if not (live_family or exact) and entry['confidence'] not in ('high', 'manual_checked'):
        raise Rejected('CATALOG_REVIEW_REQUIRED', 'This fixed legacy runtime requires a reviewed catalog record')
    if entry['identity_digest'] != config['catalog_identity_digest']:
        raise Rejected('CATALOG_IDENTITY_CHANGED', 'Catalog record differs from the reviewed identity')
    expected = config['expected_live_identity']
    if live_family or exact:
        from .native_runtime import candidate_identity
        identity, quantity = candidate_identity(entry, request['quantity'], name=request.get('candidate_name'), specialty=request.get('candidate_specialty'))
        if identity != expected or quantity != request['quantity']:
            raise Rejected('CATALOG_NATIVE_IDENTITY_MISMATCH', 'Runtime contract differs from the supported family')
    if expected['code'] != entry['code'] or expected['unit'] != entry['unit'] or (not exact and entry['name'] not in expected['name']):
        raise Rejected('CATALOG_NATIVE_IDENTITY_MISMATCH', 'Reviewed software candidate does not match the local record')
    store = Ledger(state)
    try:
        initialize_commands(store)
        saves = NativeSaveAttempts(store)
        with store._transaction():
            prior = store.db.execute('SELECT * FROM native_cli_commands WHERE command_id=?', (request['command_id'],)).fetchone()
            if prior:
                if prior['request_digest'] != digest(request):
                    raise Rejected('IDEMPOTENCY_CONFLICT', 'command_id has different business parameters')
                if prior['phase'] == 'no_dispatch_verified':
                    raise Rejected('COMMAND_CLOSED_WITHOUT_DISPATCH', 'The original command was closed without a write')
                if prior['phase'] != 'readback_verified':
                    raise Rejected('NEEDS_RECONCILIATION', 'Prior dispatch may have executed; read back before retry')
                return {**json.loads(prior['result']), 'idempotent_reentry': True,
                        'receipt_only': True, 'current_state_revalidated': False, 'new_business_dispatches': 0}
            if not store.db.execute('SELECT 1 FROM projects WHERE project_id=?', (request['project_id'],)).fetchone():
                raise Rejected('PROJECT_NOT_BOUND', 'Bind the current project before execution')
            if store.load_pending(request['project_id']):
                raise Rejected('PENDING_ACTION', 'Resolve existing business intent first')
            if saves.active(request['project_id']):
                raise Rejected('PENDING_SAVE', 'Reconcile the existing native Save attempt before any new business action')
            busy = store.db.execute("SELECT command_id FROM native_cli_commands WHERE project_id=? AND phase='armed_unknown'",
                                    (request['project_id'],)).fetchone()
            if busy:
                raise Rejected('PROJECT_NEEDS_RECONCILIATION', 'An unresolved CLI command owns this project: ' + busy['command_id'])
            store.db.execute('INSERT INTO native_cli_commands VALUES(?,?,?,?,NULL,?)',
                (request['command_id'], request['project_id'], digest(request), 'armed_unknown', int(time.time()*1000)))
        # No shell, automatic retries, alternate GUI route, or process launch for GCCP.
        completed = subprocess.run([sys.executable, '-X', 'utf8', str(worker)], capture_output=True,
            text=True, encoding='utf-8', timeout=600 + save_timeout)
        try:
            result = json.loads(completed.stdout)
        except ValueError as exc:
            raise Rejected('NEEDS_RECONCILIATION', 'Worker returned no structured result; do not replay') from exc
        save_requested = request.get('save', True)
        success_status = ('native_insert_quantity_saved_readback_verified' if save_requested
                          else 'native_insert_quantity_readback_verified_not_saved')
        display = config.get('expected_display_quantity', request['quantity'])
        if (completed.returncode or result.get('command_id') != config.get('native_command_id', request['command_id'])
                or result.get('status') != success_status
                or (not save_requested and result.get('save_dispatches') != 0)
                or result.get('quota', [])[:1] != [entry['code']]
                or len(result.get('quota', [])) < 7
                or result['quota'][3] != entry['unit']
                or result['quota'][6] != display
                or (display != request['quantity'] and result['quota'][4] != request['quantity'])):
            with store._transaction():
                store.db.execute('UPDATE native_cli_commands SET result=? WHERE command_id=?',
                    (encoded(result), request['command_id']))
            raise Rejected('NEEDS_RECONCILIATION', 'Worker stopped; inspect command-status and native evidence without replay')
        result.update(native_command_id=result['command_id'], command_id=request['command_id'],
                      catalog_entry_id=entry['id'], catalog_identity_digest=entry['identity_digest'],
                      native_channel=config['channel'], reopen_verified=False)
        if 'vendor_entry' in request:
            result['vendor_entry'] = request['vendor_entry']
        with store._transaction():
            store.db.execute("UPDATE native_cli_commands SET phase='readback_verified',result=? WHERE command_id=?",
                (encoded(result), request['command_id']))
        return result
    finally:
        store.close()


def reconcile_pre_dispatch_stop(store, worker, request, original, result, *, candidate_stop=False):
    from .native_worker_support import table

    project_id = request['project_id']
    command_id = request['command_id']
    job = store.job(result['job_id'])
    job_id = job['job_id']
    project = store.db.execute('SELECT * FROM projects WHERE project_id=?', (project_id,)).fetchone()
    meta = json.loads(project['metadata'])
    actions = tuple(store.action_state(job_id, action) for action in ('insert', 'quantity'))
    if (job['project_id'] != project_id or job['job_key'] != command_id
            or (project['owner'], actions) not in ((job_id, ('planned', 'planned')), (None, ('cancelled', 'cancelled')))
            or meta.get('dirty') or store.load_pending(project_id) or NativeSaveAttempts(store).active(project_id)):
        raise Rejected('BUSINESS_RECEIPT_MISMATCH', 'The original job cannot be verified as stopped before dispatch')
    records = [Path(p).resolve(strict=True) for p in result['ipc_records']]
    operations = ('inspect', 'prepare', 'open', 'search', 'inspect', 'select') if candidate_stop else ('inspect', 'prepare')
    if len(records) != len(operations) or any(p.parent != worker.parent/'ipc' for p in records):
        raise Rejected('EVIDENCE_INCOMPLETE', 'Expected only the original pre-dispatch IPC records')
    evidence = [json.loads(p.read_text(encoding='utf-8')) for p in records]
    session = meta['session']
    for index, (item, operation) in enumerate(zip(evidence, operations)):
        status = 'blocked' if index == len(operations) - 1 else 'observed'
        native = item.get('receipt', {})
        if (item.get('operation') != operation or item.get('request', {}).get('command_id') != command_id
                or native.get('status') != status or native.get('business_dispatches') != 0
                or native.get('callback_on_app_thread') is not True or native.get('pid') != session['pid']
                or native.get('created_filetime') != session['created_filetime']):
            raise Rejected('EVIDENCE_INCOMPLETE', 'Original native IPC records do not establish a pre-dispatch stop')
    if result['error'] != 'Native request blocked: ' + evidence[-1]['receipt'].get('error', ''):
        raise Rejected('EVIDENCE_INCOMPLETE', 'The stopped operation does not match its native receipt')
    baseline = table(evidence[0]['receipt'], '工程量表达式')['rows']

    def verify_budget(capture):
        if (capture.get('status') != 'observed' or capture.get('callback_on_app_thread') is not True
                or capture.get('pid') != session['pid'] or capture.get('created_filetime') != session['created_filetime']
                or table(capture, '工程量表达式')['rows'] != baseline):
            raise Rejected('NATIVE_READBACK_CHANGED', 'Original process and unchanged full budget are required')

    if candidate_stop:
        from .native_delete import load_bridge

        bridge = load_bridge(worker.parent)
        capture = bridge.call('inspect', timeout=30)
        verify_budget(capture)
        original_models = [t for t in evidence[-2]['receipt']['tables'] if t['headers'] == ['编码','名称','单位','单价','专业']]
        current_models = [t for t in capture['tables'] if t['headers'] == ['编码','名称','单位','单价','专业']]
        if current_models:
            if (len(original_models) != 1 or len(current_models) != 1
                    or original_models[0] != current_models[0] or not current_models[0]['complete']):
                raise Rejected('QUERY_CONTEXT_CHANGED', 'The original candidate panel could not be verified')
            bridge.call('close', timeout=30)
            capture = bridge.call('inspect', timeout=30)
            verify_budget(capture)
            if any(t['headers'] == ['编码','名称','单位','单价','专业'] for t in capture['tables']):
                raise Rejected('QUERY_CONTEXT_CHANGED', 'The original candidate panel did not close')
    else:
        checked = subprocess.run([sys.executable, '-X', 'utf8', str(worker), '--inspect'], capture_output=True,
                                 text=True, encoding='utf-8', timeout=120, check=True)
        verify_budget(json.loads(checked.stdout))
    final = {**result, 'status':('native_candidate_blocked_no_dispatch' if candidate_stop else 'native_navigation_blocked_no_dispatch'),
             'readonly_reconciled':True,
             'business_dispatches':0, 'new_business_dispatches':0}
    store.cancel(job_id)
    with store._transaction():
        current = store.db.execute('SELECT * FROM native_cli_commands WHERE command_id=?', (command_id,)).fetchone()
        if dict(current) != dict(original):
            raise Rejected('COMMAND_CHANGED', 'Command state changed during readback')
        store.db.execute("UPDATE native_cli_commands SET phase='no_dispatch_verified',result=? WHERE command_id=?",
                         (encoded(final), command_id))
        store._event(job_id, 'native_cli_candidate_block_no_dispatch_verified' if candidate_stop else 'native_cli_anchor_block_no_dispatch_verified',
                     dict(command_id=command_id, same_process=True, budget_rows_unchanged=True))
    return final


def reconcile_rounded_quantity(store, worker, config, request, result):
    from .native_build import profile_path
    from .native_runtime import displayed_quantity, is_blank_quota, quota_trace_note
    from .native_worker_support import RuntimeProfile, SessionManager, WindowsDesktop, execution_lock, rows, snap, table
    from .ports import DispatchReceipt, Observation, SessionContext
    from gccp_foundation.domain.errors import DispatchState

    project_id, command_id = request['project_id'], request['command_id']
    job = store.job(result['job_id'])
    project = store.db.execute('SELECT * FROM projects WHERE project_id=?', (project_id,)).fetchone()
    expected = config['expected_live_identity']
    display = displayed_quantity(expected['unit'], request['quantity'])
    if (request.get('save', True) or display == request['quantity'] or job['project_id'] != project_id
            or job['job_key'] != command_id or project['owner'] != job['job_id']):
        raise Rejected('EVIDENCE_INCOMPLETE', 'A no-save owned command with a reviewed display difference is required')
    with execution_lock(store.root, project['file_id']):
        project = store.db.execute('SELECT * FROM projects WHERE project_id=?', (project_id,)).fetchone()
        if project['owner'] != job['job_id'] or NativeSaveAttempts(store).active(project_id):
            raise Rejected('EVIDENCE_INCOMPLETE', 'Original job must still own the project without a pending Save')
        session = SessionContext(**json.loads(project['metadata'])['session'])
        manager = SessionManager(store, WindowsDesktop(), RuntimeProfile.load(profile_path(session.profile_id)))
        meta = manager.validate_session(session)
        action = store.db.execute("SELECT * FROM actions WHERE job_id=? AND action_id='quantity'", (job['job_id'],)).fetchone()
        pending = store.load_pending(project_id)
        intent = action['intent_id']
        if (store.action_state(job['job_id'], 'insert') != 'readback_verified'
                or action['state'] not in ('intent_durable', 'needs_reconciliation', 'readback_verified')
                or any(row['intent_id'] != intent for row in pending)
                or meta.get('pending_one_command') not in (None, intent)
                or (pending and meta.get('pending_one_command') != intent)):
            raise Rejected('EVIDENCE_INCOMPLETE', 'The original quantity action must be the only pending write')
        records = [Path(p).resolve(strict=True) for p in result['ipc_records']]
        if len(records) < 3 or any(p.parent != worker.parent/'ipc' for p in records):
            raise Rejected('EVIDENCE_INCOMPLETE', 'Original IPC files required')
        sent, observed, blocked = (json.loads(p.read_text(encoding='utf-8')) for p in records[-3:])
        if ([item['operation'] for item in (sent, observed, blocked)] != ['quantity.normal.one', 'inspect', 'resources.key.up']
                or any(item['request']['command_id'] != command_id for item in (sent, observed, blocked))
                or sent['request'].get('outer_intent_id') != intent
                or sent['receipt'].get('quantity_field_write_dispatches') != 1
                or blocked['receipt'].get('error') != 'normal_up_context_guard'):
            raise Rejected('EVIDENCE_INCOMPLETE', 'One quantity write and its blocked follow-up must be recorded')
        before = store.read_evidence(json.loads(action['context'])['preflight_evidence_ids'][0], project_id)
        path = before['data']['target_path']
        if before['kind'] != 'new_quota_zero_insert_verified' or before['session_id'] != session.session_id:
            raise Rejected('EVIDENCE_INCOMPLETE', 'Verified zero-quantity insertion evidence required')
        fresh = subprocess.run([sys.executable, '-X', 'utf8', str(worker), '--inspect'], capture_output=True,
                               text=True, encoding='utf-8', timeout=120, check=True)
        capture = json.loads(fresh.stdout)
        budget = table(capture, '工程量表达式')
        if (capture.get('status') != 'observed' or capture.get('callback_on_app_thread') is not True
                or capture.get('pid') != session.pid or capture.get('created_filetime') != session.created_filetime
                or budget['rows'] != table(observed['receipt'], '工程量表达式')['rows']):
            raise Rejected('NATIVE_READBACK_CHANGED', 'Fresh budget differs from the original post-write observation')
        previous, current = rows(table(before['data']['capture'], '工程量表达式')), rows(budget)
        quota = current[path]['values']
        original_quota = previous[path]['values']
        note = quota_trace_note(command_id, request.get('item_label'))
        identity = (quota[2], quota[12])
        valid_identities = {(expected['name'], note), (f"{expected['name']} {note}", '')}
        if (identity not in valid_identities or identity != (original_quota[2], original_quota[12])
                or quota[:2] != [expected['code'], '定'] or quota[3:5] != [expected['unit'], request['quantity']]
                or quota[6] != display or original_quota[6] != '0'):
            raise Rejected('NATIVE_READBACK_CHANGED', 'The exact inserted item and quantity expression must match')
        parent, slot = path.rsplit('/', 1)
        blank = f'{parent}/{int(slot)+1}'
        if (set(previous) - set(current) or set(current) - set(previous) != {blank}
                or not is_blank_quota(current[blank]['values'])):
            raise Rejected('NATIVE_READBACK_CHANGED', 'Unexpected budget rows changed')
        for row_path, old in previous.items():
            allowed = {4, 6, 8} if row_path == path else {8} if path.startswith(row_path + '/') else set()
            if any(a != b and col not in allowed
                   for col, (a, b) in enumerate(zip(old['values'], current[row_path]['values'], strict=True))):
                raise Rejected('NATIVE_READBACK_CHANGED', 'A non-target budget field changed')
        if pending:
            ref = store.put_evidence(project_id, session.session_id, 'native_rounded_quantity_readback',
                                     dict(capture=capture, target_path=path, quantity=request['quantity'], display=display))
            store.append_receipt(intent, DispatchReceipt('quantity', DispatchState.SENT,
                Observation(snap(budget), True, 'native_rounded_quantity_readback',
                            dict(target_path=path, quantity=request['quantity'], display=display), (ref,)), (ref,)))
        else:
            ref, = json.loads(action['receipt'])['evidence_ids']
            if store.read_evidence(ref, project_id)['kind'] != 'native_rounded_quantity_readback':
                raise Rejected('EVIDENCE_INCOMPLETE', 'The original action lacks a rounded readback receipt')
        meta['dirty'] = False
        meta['pending_one_command'] = None
        manager._metadata(project_id, meta)
        store.checkpoint(job['job_id'], dict(status='readback_verified_not_saved', evidence_id=ref, no_save=True))
        final = {**result, 'status':'native_insert_quantity_budget_readback_verified_not_saved',
                 'readonly_reconciled':True, 'new_business_dispatches':0, 'target_path':path,
                 'quota':quota, 'resource_readback_verified':False, 'evidence_id':ref}
        final.pop('error', None)
        final.pop('error_type', None)
        if 'vendor_entry' in request:
            final['vendor_entry'] = request['vendor_entry']
        with store._transaction():
            store.db.execute("UPDATE native_cli_commands SET phase='readback_verified',result=? WHERE command_id=?",
                             (encoded(final), command_id))
        return final


def reconcile_inspection_timeout(store, worker, request, original, result, inspector):
    from .native_worker_support import execution_lock, table

    if inspector is None:
        raise Rejected('INSPECTOR_REQUIRED', 'Provide the bound read-only inspector for pre-dispatch reconciliation')
    config, observer = runtime_config(inspector)
    if config.get('runtime_kind') != 'inspector' or observer == worker:
        raise Rejected('INSPECTOR_REQUIRED', 'A separate verified read-only inspector is required')
    project_id, command_id = request['project_id'], request['command_id']
    project = store.db.execute('SELECT * FROM projects WHERE project_id=?', (project_id,)).fetchone()
    with execution_lock(store.root, project['file_id']):
        meta = json.loads(project['metadata'])
        if (result.get('job_id') or meta.get('dirty') or store.load_pending(project_id)
                or NativeSaveAttempts(store).active(project_id)
                or store.db.execute('SELECT 1 FROM jobs WHERE project_id=? AND job_key=?', (project_id, command_id)).fetchone()):
            raise Rejected('BUSINESS_RECEIPT_MISMATCH', 'Inspection timeout is recoverable only before any job or intent')
        record_path = Path(result['ipc_records'][0]).resolve(strict=True)
        ipc = worker.parent/'ipc'
        if (record_path.parent != ipc or set(ipc.glob('*.result.json')) != {record_path}
                or len(list(ipc.glob('*.intent.json'))) != 1):
            raise Rejected('EVIDENCE_INCOMPLETE', 'Expected a single original inspect request and receipt')
        record = json.loads(record_path.read_text(encoding='utf-8'))
        intent = json.loads(record_path.with_name(record_path.name.replace('.result.json', '.intent.json')).read_text(encoding='utf-8'))
        session = meta['session']
        native = record['request']
        if (record.get('operation') != 'inspect' or record.get('replay_allowed') is not False
                or native != intent.get('request') or native.get('operation') != 'inspect'
                or native.get('command_id') != command_id or native.get('pid') != session['pid']
                or native.get('created_filetime') != session['created_filetime']):
            raise Rejected('EVIDENCE_INCOMPLETE', 'Original request is not the bound read-only inspection')
        prepared = json.loads((worker.parent/'preparation.json').read_text(encoding='utf-8'))['capture']
        checked = subprocess.run([sys.executable, '-X', 'utf8', str(observer), '--inspect'], capture_output=True,
                                 text=True, encoding='utf-8', timeout=120, check=True)
        capture = json.loads(checked.stdout)
        for snapshot in (prepared, capture):
            if (snapshot.get('status') != 'observed' or snapshot.get('callback_on_app_thread') is not True
                    or snapshot.get('pid') != session['pid'] or snapshot.get('created_filetime') != session['created_filetime']
                    or snapshot.get('business_dispatches') != 0):
                raise Rejected('NATIVE_READBACK_CHANGED', 'Original process and read-only observations required')
        if table(capture, '工程量表达式')['rows'] != table(prepared, '工程量表达式')['rows']:
            raise Rejected('NATIVE_READBACK_CHANGED', 'Original full budget and current readback differ')
        final = {**result, 'status':'native_inspection_timeout_no_dispatch', 'readonly_reconciled':True,
                 'business_dispatches':0, 'new_business_dispatches':0}
        with store._transaction():
            current = store.db.execute('SELECT * FROM native_cli_commands WHERE command_id=?', (command_id,)).fetchone()
            if dict(current) != dict(original):
                raise Rejected('COMMAND_CHANGED', 'Command state changed during readback')
            store.db.execute("UPDATE native_cli_commands SET phase='no_dispatch_verified',result=? WHERE command_id=?",
                             (encoded(final), command_id))
        return final


def reconcile(state, catalog_path, runtime_path, request, *, inspector=None):
    """Read-only native verification of an unknown outer dispatch, never run the worker business path."""
    config, worker = runtime_config(runtime_path)
    if config['approved_request'] != request:
        raise Rejected('REQUEST_NOT_REVIEWED', 'Exact original request required for reconciliation')
    entry = request_catalog_entry(catalog_path, request)
    if entry['identity_digest'] != config['catalog_identity_digest']:
        raise Rejected('CATALOG_IDENTITY_CHANGED', 'Original catalog identity changed')
    store = Ledger(state)
    try:
        initialize_commands(store)
        original = store.db.execute('SELECT * FROM native_cli_commands WHERE command_id=?', (request['command_id'],)).fetchone()
        if not original or original['request_digest'] != digest(request):
            raise Rejected('COMMAND_NOT_FOUND', 'Exact original durable command required')
        if original['phase'] in ('readback_verified', 'no_dispatch_verified'):
            return {**json.loads(original['result']), 'idempotent_reentry': True, 'new_business_dispatches': 0}
        result = json.loads(original['result'] or '{}')
        if (config.get('selected_anchor_contract') == 1 and result.get('command_id') == request['command_id']
                and result.get('save_dispatches') == 0 and result.get('error_type') == 'TimeoutError'
                and len(result.get('ipc_records', [])) == 1 and not result.get('job_id')):
            return reconcile_inspection_timeout(store, worker, request, original, result, inspector)
        if (request.get('navigate_anchor') is True and config.get('selected_anchor_contract') == 1
                and result.get('command_id') == request['command_id'] and result.get('save_dispatches') == 0
                and result.get('error') == 'Native request blocked: anchor_navigation_step_mismatch'):
            return reconcile_pre_dispatch_stop(store, worker, request, original, result)
        if (config.get('selected_anchor_contract') == 1 and result.get('command_id') == request['command_id']
                and result.get('save_dispatches') == 0 and result.get('error') in (
                    'Native request blocked: candidate_model_not_unique', 'Native request blocked: candidate_row_bound_rejected')):
            from .native_worker_support import execution_lock
            project = store.db.execute('SELECT file_id FROM projects WHERE project_id=?', (request['project_id'],)).fetchone()
            with execution_lock(store.root, project['file_id']):
                return reconcile_pre_dispatch_stop(store, worker, request, original, result, candidate_stop=True)
        if (config.get('selected_anchor_contract') == 1 and result.get('command_id') == request['command_id']
                and result.get('save_dispatches') == 0
                and result.get('error') == 'Native request blocked: normal_up_context_guard'):
            return reconcile_rounded_quantity(store, worker, config, request, result)
        if (result.get('status') != 'native_insert_quantity_saved_readback_verified'
                or result.get('command_id') != config.get('native_command_id', request['command_id'])):
            raise Rejected('EVIDENCE_INCOMPLETE', 'No complete saved native worker receipt; keep unknown')
        job = store.job(result['job_id'])
        plan = json.loads(job['plan'])
        insert = next(a for a in plan['actions'] if a['kind'] == 'ensure_quota')
        quantity = next(a for a in plan['actions'] if a['kind'] == 'set_quantity')
        expected = config['expected_live_identity']
        key = insert['payload']['quota_key']
        if (job['project_id'] != request['project_id'] or job['job_key'] != result['command_id']
                or key['code'] != expected['code'] or key['name'] != expected['name']
                or key['unit'] != expected['unit'] or key['trade'] != expected['specialty']
                or insert['payload']['parent_ref'] != 'division.' + request['parent_id']
                or quantity['payload']['quantity']['value'] != request['quantity']
                or store.load_pending(request['project_id'])
                or any(store.action_state(job['job_id'], a['action_id']) != 'readback_verified' for a in plan['actions'])):
            raise Rejected('BUSINESS_RECEIPT_MISMATCH', 'Native job differs from the original approved business command')
        evidence = store.read_evidence(result['evidence_id'], request['project_id'])
        data = evidence['data']
        if (evidence['kind'] != 'native_cli_open_save_readback' or data.get('sha256') != result['save_sha256']
                or data.get('native_save_dispatched') is not True
                or data.get('before_after_models_equal') is not True
                or data.get('crc_verified') is not True
                or data.get('stable_file_verified') is not True):
            raise Rejected('SAVE_EVIDENCE_MISMATCH', 'Original native Save proof required')
        project = store.db.execute('SELECT * FROM projects WHERE project_id=?', (request['project_id'],)).fetchone()
        meta = json.loads(project['metadata'])
        from gccp_foundation.execution.profiles import file_sha256
        if file_sha256(Path(meta['workcopy_path'])) != data['sha256'] or project['file_id'] != data['saved_file_id']:
            raise Rejected('SAVED_FILE_CHANGED', 'Current saved checkpoint differs; keep unknown')
        inspected = subprocess.run([sys.executable, '-X', 'utf8', str(worker), '--inspect'], capture_output=True,
            text=True, encoding='utf-8', timeout=120, check=True)
        capture = json.loads(inspected.stdout)
        if (capture.get('status') != 'observed' or not capture.get('callback_on_app_thread')
                or capture.get('pid') != meta['session']['pid']
                or capture.get('created_filetime') != meta['session']['created_filetime']):
            raise Rejected('SESSION_CHANGED', 'Fresh original-process native observation required')
        for old in data['capture']['tables']:
            matches = [t for t in capture['tables'] if t['headers'] == old['headers']]
            if len(matches) != 1 or not matches[0]['complete'] or matches[0]['rows'] != old['rows']:
                raise Rejected('NATIVE_READBACK_CHANGED', 'Current native model differs from the saved receipt')
        final = {**result, 'native_command_id':result['command_id'], 'command_id':request['command_id'],
                 'readonly_reconciled':True, 'new_business_dispatches':0, 'catalog_entry_id':entry['id'],
                 'catalog_identity_digest':entry['identity_digest'], 'reopen_verified':False}
        with store._transaction():
            current = store.db.execute('SELECT * FROM native_cli_commands WHERE command_id=?', (request['command_id'],)).fetchone()
            if dict(current) != dict(original):
                raise Rejected('COMMAND_CHANGED', 'Command state changed during readback')
            store.db.execute("UPDATE native_cli_commands SET phase='readback_verified',result=? WHERE command_id=?",
                             (encoded(final), request['command_id']))
            store._event(job['job_id'], 'native_cli_unknown_readonly_reconciled',
                dict(command_id=request['command_id'], original_native_command_id=result['command_id'],
                     save_evidence_id=result['evidence_id'], new_dispatches=0))
        return final
    finally:
        store.close()


def main(argv=None):
    # JSON wire encoding is UTF-8 even when a Windows console defaults to GBK.
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = JsonArgumentParser(description='GCCP7 Native CLI for Beijing 2025 estimate quota operations')
    parser.add_argument('--version', action='version', version=f'gccp7-native-cli {__version__}')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('doctor', help='Check the local version and toolchain without loading GCCP')
    binding = sub.add_parser('bind', help='Bind one explicitly selected already-open test workcopy')
    for flag in ('state', 'runtime-root', 'workcopy'):
        binding.add_argument('--' + flag, required=True, type=Path)
    binding.add_argument('--expected-sha256', required=True)
    binding.add_argument('--refresh-from-inspector', type=Path,
                         help='Refresh the same open project after a disk replacement with unchanged verified budget')
    binding.add_argument('--accept-price-updates', action='store_true',
                         help='With --refresh-from-inspector, accept operator-confirmed price-only changes')
    cat = sub.add_parser('catalog')
    cats = cat.add_subparsers(dest='operation', required=True)
    for operation in ('inspect', 'search', 'apply-review'):
        p = cats.add_parser(operation)
        p.add_argument('--db', required=operation == 'apply-review', type=Path,
                       help='Explicit SQLite input for historical workflows')
        if operation == 'search':
            p.add_argument('--code'); p.add_argument('--text'); p.add_argument('--source-id', type=int)
            p.add_argument('--spec'); p.add_argument('--specialty'); p.add_argument('--limit', type=int, default=20)
            p.add_argument('--vendor-id', type=int)
        if operation == 'apply-review':
            p.add_argument('--review', required=True, type=Path)
    vendor = cats.add_parser('vendor-index', help='Read or export the Beijing 2025 subitem index')
    vendor.add_argument('--library-root', type=Path, help='Read from this installation instead of the bundled export')
    vendor.add_argument('--vendor-id', type=int)
    vendor.add_argument('--code')
    vendor.add_argument('--text')
    vendor.add_argument('--specialty')
    vendor.add_argument('--output', type=Path, help='Create a new UTF-8 JSON export')
    vendor.add_argument('--limit', type=int, default=20, help='Maximum query rows when no file is exported')
    candidates = sub.add_parser('quota-candidates', help='Read exact candidate identities in the active GCCP library')
    candidates.add_argument('--state', type=Path, required=True)
    candidates.add_argument('--runtime-root', type=Path, required=True)
    candidates.add_argument('--project-id', required=True)
    identity = candidates.add_mutually_exclusive_group(required=True)
    identity.add_argument('--code')
    identity.add_argument('--vendor-id', type=int, help='Use an ID from the bundled Beijing 2025 export')
    identity.add_argument('--entry-id', type=int, help='Use a reviewed SQLite entry as the lookup starting point')
    candidates.add_argument('--db', type=Path)
    native = sub.add_parser('native-inspect'); native.add_argument('--runtime', required=True, type=Path)
    reuse = sub.add_parser('quota-reuse-plan', help='Compare reviewed material variants with a captured budget, without writing')
    reuse.add_argument('--capture', required=True, type=Path, help='JSON output of native-inspect')
    reuse.add_argument('--request', required=True, type=Path, help='JSON quota and material variants to review')
    clone = sub.add_parser('quota-clone', help='Copy the selected quota below itself, rename one main material and fill quantity')
    for flag in ('state', 'runtime-root', 'inspector'):
        clone.add_argument('--' + flag, required=True, type=Path)
    for flag in ('project-id', 'command-id', 'code', 'quota-name', 'unit', 'material-code'):
        clone.add_argument('--' + flag, required=True)
    clone.add_argument('--material-name')
    clone.add_argument('--quantity')
    clone.add_argument('--variants', type=Path, help='JSON array of material_name and quantity pairs, copied from one source')
    clone.add_argument('--parent-section', action='append', required=True)
    clone_reconcile = sub.add_parser('quota-clone-reconcile', help='Verify completed copies after a documented no-effect paste')
    clone_reconcile.add_argument('--state', required=True, type=Path)
    clone_reconcile.add_argument('--command-id', required=True)
    quota = sub.add_parser('quota-add'); quota.add_argument('--db', type=Path)
    quota.add_argument('--runtime', required=True, type=Path); quota.add_argument('--request', required=True, type=Path)
    quota.add_argument('--state', required=True, type=Path)
    recovery = sub.add_parser('command-reconcile')
    for flag in ('db', 'runtime', 'request', 'state'):
        recovery.add_argument('--' + flag, required=flag != 'db', type=Path)
    recovery.add_argument('--inspector', type=Path, help='Bound read-only inspector for an initial inspection timeout')
    for operation, help_text in (
        ('runtime-prepare', 'Check scope and prepare a runtime without dispatching a quota write'),
        ('quota-run', 'Insert one verified quota, fill its quantity, and read it back'),
    ):
        p = sub.add_parser(operation, help=help_text)
        for flag in ('db', 'state', 'inspector', 'runtime-root'):
            p.add_argument('--' + flag, required=flag != 'db', type=Path)
        for flag in ('project-id', 'quantity', 'command-id'):
            p.add_argument('--' + flag, required=True)
        parent = p.add_mutually_exclusive_group(required=True)
        parent.add_argument('--parent-id', help='Business ID of an existing top-level section')
        parent.add_argument('--parent-section', action='append',
                            help='Exact section name, repeat from the top level to the target section')
        p.add_argument('--selected-anchor', action='store_true',
                       help='Require the already-selected final quota in a section path')
        p.add_argument('--navigate-anchor', action='store_true',
                       help='With --selected-anchor, allow bounded verified native grid steps within the target top-level section')
        p.add_argument('--entry-id', type=int)
        p.add_argument('--vendor-id', type=int, help='Exact subitem ID from catalog search')
        p.add_argument('--library-root', type=Path, help='Use this installed library instead of the bundled export')
        p.add_argument('--expect-unit', help='With --vendor-id, require this exact live unit before insertion')
        p.add_argument('--item-label', help='Distinct work scope written to the remark; the same scope cannot be inserted twice')
        p.add_argument('--candidate-code', help='Exact GCCP code for catalog-free execution')
        p.add_argument('--candidate-unit', help='Exact GCCP unit for catalog-free execution')
        p.add_argument('--candidate-name', help='Exact name from the GCCP candidate table')
        p.add_argument('--candidate-specialty', help='Exact specialty from the same candidate row')
        p.add_argument('--save-timeout', type=int, default=DEFAULT_SAVE_TIMEOUT,
                       help='Seconds to wait for a stable saved file, from 30 to 1800 (default: 300)')
        p.add_argument('--no-save', action='store_true',
                       help='Apply and read back without dispatching Save; implied by --parent-section')
    supplement = sub.add_parser('quota-supplement', help='Open and submit one verified supplemental item at zero price')
    for flag in ('state', 'runtime-root', 'inspector'):
        supplement.add_argument('--' + flag, required=True, type=Path)
    for flag in ('project-id', 'code', 'name', 'quantity', 'command-id'):
        supplement.add_argument('--' + flag, required=True)
    supplement.add_argument('--unit', required=True, choices=('m', 'm2', 'm3', '段', '项', '套', '组', '个', '处', '道'))
    supplement.add_argument('--parent-section', required=True, action='append')
    supplement.add_argument('--navigate-anchor', action='store_true')
    supplement.add_argument('--check-only', action='store_true',
                            help='Open the original form, check fields and cancel without changing the budget')
    material = sub.add_parser('material-edit', help='Edit one existing main material by verified row identity')
    for flag in ('state', 'runtime-root'):
        material.add_argument('--' + flag, required=True, type=Path)
    for flag in ('project-id', 'command-id', 'path', 'quota-code', 'quota-quantity',
                 'quota-note', 'material-code', 'expected', 'value'):
        material.add_argument('--' + flag, required=True)
    material.add_argument('--field', required=True, choices=('name', 'content', 'quota-name', 'quota-note', 'concrete-grade'))
    material.add_argument('--quota-name', help='Also match the full parent quota name')
    material.add_argument('--quota-unit', help='Also match the parent quota unit')
    material.add_argument('--resource-content', action='store_true',
                          help='Edit the selected quota resource table, using its resource row path')
    material.add_argument('--select-quota', help='Select this verified parent quota path before a resource edit')
    material.add_argument('--check-only', action='store_true')
    resource_reconcile = sub.add_parser('resource-edit-reconcile', help='Verify an original resource edit without replay')
    resource_reconcile.add_argument('--state', required=True, type=Path)
    resource_reconcile.add_argument('--command-id', required=True)
    resource_reconcile.add_argument('--inspector', type=Path)
    quantity = sub.add_parser('quota-quantity', help='Update one existing quota quantity and read it back')
    for flag in ('state', 'runtime-root'):
        quantity.add_argument('--' + flag, required=True, type=Path)
    for flag in ('project-id', 'command-id', 'path', 'quota-code', 'quota-name',
                 'quota-unit', 'quota-note', 'expected', 'value'):
        quantity.add_argument('--' + flag, required=True)
    quantity.add_argument('--check-only', action='store_true')
    cancel_quantity = sub.add_parser('quota-quantity-cancel', help='Cancel a pending quantity detail warning')
    cancel_quantity.add_argument('--state', required=True, type=Path)
    cancel_quantity.add_argument('--command-id', required=True)
    rename_section = sub.add_parser('section-rename', help='Rename one verified budget section')
    for flag in ('state', 'runtime-root'):
        rename_section.add_argument('--' + flag, required=True, type=Path)
    for flag in ('project-id', 'command-id', 'path', 'expected', 'value'):
        rename_section.add_argument('--' + flag, required=True)
    rename_section.add_argument('--check-only', action='store_true')
    rename_project = sub.add_parser('project-rename', help='Rename the verified root project node')
    for flag in ('state', 'runtime-root'):
        rename_project.add_argument('--' + flag, required=True, type=Path)
    for flag in ('project-id', 'command-id', 'expected', 'value'):
        rename_project.add_argument('--' + flag, required=True)
    rename_project.add_argument('--check-only', action='store_true')
    rename_unit = sub.add_parser('unit-rename', help='Rename a unit under the verified project')
    for flag in ('state', 'runtime-root'):
        rename_unit.add_argument('--' + flag, required=True, type=Path)
    for flag in ('project-id', 'command-id', 'project-name', 'expected', 'value'):
        rename_unit.add_argument('--' + flag, required=True)
    rename_unit.add_argument('--check-only', action='store_true')
    select_unit = sub.add_parser('unit-select', help='Select a unit and verify its budget view')
    for flag in ('state', 'runtime-root'):
        select_unit.add_argument('--' + flag, required=True, type=Path)
    for flag in ('project-id', 'project-name', 'unit-name', 'expected-section'):
        select_unit.add_argument('--' + flag, required=True)
    save = sub.add_parser('project-save', help='Save the bound open project once and verify the changed file')
    for flag in ('state', 'runtime-root'):
        save.add_argument('--' + flag, required=True, type=Path)
    save.add_argument('--project-id', required=True)
    save.add_argument('--command-id', required=True)
    save.add_argument('--save-timeout', type=int, default=DEFAULT_SAVE_TIMEOUT)
    save.add_argument('--check-only', action='store_true')
    factor = sub.add_parser('quota-short-drive-factor', help='Apply the reviewed Beijing 2025 11-34 short-drive rule')
    for flag in ('state', 'runtime-root', 'inspector'):
        factor.add_argument('--' + flag, required=True, type=Path)
    for flag in ('project-id', 'quantity', 'command-id'):
        factor.add_argument('--' + flag, required=True)
    factor.add_argument('--parent-section', required=True, action='append')
    factor.add_argument('--verify-only', action='store_true',
                        help='Verify an accepted original conversion receipt with no new dispatch')
    deletion = sub.add_parser('quota-delete', help='Delete one exact quota in the current budget')
    for flag in ('state', 'runtime-root'):
        deletion.add_argument('--' + flag, required=True, type=Path)
    for flag in ('project-id', 'code', 'name', 'unit', 'command-id'):
        deletion.add_argument('--' + flag, required=True)
    delete_parent = deletion.add_mutually_exclusive_group(required=True)
    delete_parent.add_argument('--parent-id')
    delete_parent.add_argument('--parent-section', action='append',
                               help='Exact parent path, repeat per level. The target row must already be current')
    deletion.add_argument('--expected-quantity', help='Exact displayed quantity required for section-path deletion')
    deletion.add_argument('--expected-note', help='Also require this exact remark in section-path deletion')
    deletion.add_argument('--path', help='Also require the reviewed current row path in section-path deletion')
    deletion.add_argument('--kind', choices=('定', '换', '借', '补', '主'),
                          help='Budget row category for section-path deletion (default: 定)')
    deletion.add_argument('--parent-quota', help='Full parent quota name for temporary main material deletion')
    deletion.add_argument('--navigate-target', action='store_true',
                          help='Select the uniquely matched budget row using native navigation')
    deletion.add_argument('--mode', required=True, choices=('temporary', 'permanent'))
    section_delete = sub.add_parser('section-delete', help='Remove one exact empty budget section')
    for flag in ('state', 'runtime-root'):
        section_delete.add_argument('--' + flag, required=True, type=Path)
    section_delete.add_argument('--project-id', required=True)
    section_delete.add_argument('--command-id', required=True)
    section_delete.add_argument('--parent-section', required=True, action='append',
                                help='Exact section path, including the empty section to remove')
    for operation, help_text in (
        ('quota-delete-confirm', 'Confirm the original pending permanent deletion'),
        ('quota-delete-reconcile', 'Verify the original pending permanent deletion without dispatch'),
    ):
        action = sub.add_parser(operation, help=help_text)
        action.add_argument('--state', required=True, type=Path)
        action.add_argument('--command-id', required=True)
        if operation == 'quota-delete-confirm':
            action.add_argument('--reviewed-file-sha256', help='Operator-reviewed current disk digest for the original open dialog')
    status = sub.add_parser('command-status'); status.add_argument('--state', required=True, type=Path)
    status.add_argument('--command-id', required=True)
    status.add_argument('--include-resources', action='store_true',
                        help='Include archived resource rows verified for this command')
    try:
        if sys.flags.optimize:
            raise Rejected('OPTIMIZATION_UNSUPPORTED', 'Run with Python assertions enabled')
        args = parser.parse_args(argv)
        if args.command == 'doctor':
            from .native_bootstrap import doctor
            data = doctor()
            data.update(cli_version=__version__, cli_module=__file__)
        elif args.command == 'bind':
            from .native_bootstrap import bind_open
            data = bind_open(args.state, args.runtime_root, args.workcopy, args.expected_sha256,
                             refresh_from_inspector=args.refresh_from_inspector,
                             accept_price_updates=args.accept_price_updates)
        elif args.command == 'catalog' and args.operation == 'apply-review':
            from .catalog_review import apply_review
            data = apply_review(args.db, args.review)
        elif args.command == 'catalog' and args.operation == 'vendor-index':
            from .vendor_catalog import vendor_index
            data = vendor_index(args.library_root, code=args.code, text=args.text,
                                specialty=args.specialty, vendor_id=args.vendor_id, output=args.output, limit=args.limit)
        elif args.command == 'catalog':
            if args.db is None:
                from .vendor_catalog import bundled_index, vendor_index
                if args.operation == 'inspect':
                    data, _ = bundled_index()
                else:
                    if args.source_id is not None or args.spec is not None:
                        raise Rejected('CATALOG_ARGUMENTS', 'Search the bundled export with --code, --text, --specialty or --vendor-id')
                    data = vendor_index(code=args.code, text=args.text, specialty=args.specialty,
                                        vendor_id=args.vendor_id, limit=args.limit)
            else:
                if args.operation == 'search' and args.vendor_id is not None:
                    raise Rejected('CATALOG_ARGUMENTS', 'Use --vendor-id with the bundled export, without --db')
                catalog = Catalog(args.db)
                try:
                    data = catalog.summary() if args.operation == 'inspect' else catalog.search(
                        code=args.code, text=args.text, source_id=args.source_id, spec=args.spec,
                        specialty=args.specialty, limit=args.limit)
                finally:
                    catalog.close()
        elif args.command == 'quota-candidates':
            from .native_lookup import lookup, lookup_vendor
            code = args.code
            vendor = entry = None
            if args.vendor_id is not None:
                from .vendor_catalog import match_live_entry, vendor_entry
                if args.db is not None:
                    raise Rejected('CATALOG_ARGUMENTS', 'Use --vendor-id without --db')
                vendor = vendor_entry(None, args.vendor_id)
                code = vendor['code']
            elif args.entry_id is not None:
                if args.db is None:
                    raise Rejected('CATALOG_ARGUMENTS', '--entry-id requires --db')
                catalog = Catalog(args.db)
                try:
                    entry = catalog.entry(args.entry_id)
                finally:
                    catalog.close()
                code = entry['code']
            if vendor is not None:
                data = lookup_vendor(args.state, args.runtime_root, args.project_id, vendor)
                data['vendor_entry'] = vendor
                data['matched_candidate'] = match_live_entry(vendor, data)
            else:
                data = lookup(args.state, args.runtime_root, args.project_id, code)
                if entry is not None:
                    data['catalog_entry'] = entry
                    data['candidate_selection_required'] = True
        elif args.command == 'quota-add':
            data = execute(args.state, args.db, args.runtime, json.loads(args.request.read_text(encoding='utf-8-sig')))
        elif args.command == 'command-reconcile':
            data = reconcile(args.state, args.db, args.runtime, json.loads(args.request.read_text(encoding='utf-8-sig')),
                             inspector=args.inspector)
        elif args.command in ('runtime-prepare', 'quota-run'):
            from .native_runtime import prepare_entry
            save = args.parent_section is None and not args.no_save
            if args.command == 'quota-run':
                require_quota_execution_scope(args.parent_section, selected_anchor=args.selected_anchor,
                                              save=save)
            manifest, request_path, request = prepare_entry(args.db, args.state, args.inspector,
                args.project_id, args.parent_id, args.quantity, args.command_id, args.entry_id, args.runtime_root,
                candidate_name=args.candidate_name, candidate_specialty=args.candidate_specialty,
                candidate_code=args.candidate_code, candidate_unit=args.candidate_unit,
                parent_sections=args.parent_section, save_timeout=args.save_timeout,
                save=save, selected_anchor=args.selected_anchor,
                navigate_anchor=args.navigate_anchor, vendor_id=args.vendor_id,
                library_root=args.library_root, expect_unit=args.expect_unit, item_label=args.item_label)
            data = dict(runtime=str(manifest), request_path=str(request_path), business_dispatches=0)
            if args.parent_section is not None:
                data['execution_eligible'] = args.selected_anchor
                preparation = json.loads((manifest.parent/'preparation.json').read_text(encoding='utf-8'))
                data['anchor_check'] = preparation.get('anchor_check')
            if args.command == 'quota-run':
                data = {**execute(args.state, args.db, manifest, request), 'runtime':str(manifest)}
        elif args.command == 'quota-supplement':
            from .native_supplement import supplement_quota
            request = dict(project_id=args.project_id, code=args.code, name=args.name, unit=args.unit,
                           quantity=args.quantity, command_id=args.command_id, parent_sections=args.parent_section)
            if args.navigate_anchor:
                request['navigate_anchor'] = True
            data = supplement_quota(args.state, args.runtime_root, args.inspector, request, check_only=args.check_only)
        elif args.command == 'resource-edit-reconcile':
            from .native_material_edit import reconcile_resource_edit
            data = reconcile_resource_edit(args.state, args.command_id, inspector=args.inspector)
        elif args.command == 'material-edit':
            from .native_material_edit import edit_material
            request = {key: getattr(args, key) for key in (
                'project_id', 'command_id', 'path', 'quota_code', 'quota_quantity', 'quota_note',
                'material_code', 'field', 'expected', 'value',
            )}
            for key in ('quota_name', 'quota_unit'):
                if getattr(args, key) is not None:
                    request[key] = getattr(args, key)
            if args.resource_content:
                if args.field != 'content':
                    raise Rejected('RESOURCE_FIELD_INVALID', 'Resource editing requires --field content')
                request['field'] = 'resource-content'
                if args.select_quota is not None:
                    request['select_quota'] = args.select_quota
            elif args.select_quota is not None:
                raise Rejected('RESOURCE_FIELD_INVALID', '--select-quota requires --resource-content')
            data = edit_material(args.state, args.runtime_root, request, check_only=args.check_only)
        elif args.command == 'quota-quantity':
            from .native_material_edit import edit_material
            request = {key: getattr(args, key) for key in (
                'project_id', 'command_id', 'path', 'quota_code', 'quota_name', 'quota_unit', 'quota_note',
                'expected', 'value',
            )}
            data = edit_material(args.state, args.runtime_root, {**request, 'field': 'quantity'},
                                 check_only=args.check_only)
        elif args.command == 'quota-quantity-cancel':
            from .native_material_edit import cancel_pending_quantity_edit
            data = cancel_pending_quantity_edit(args.state, args.command_id)
        elif args.command in ('section-rename', 'project-rename', 'unit-rename'):
            from .native_material_edit import edit_material
            request = {key: getattr(args, key) for key in (
                'project_id', 'command_id', 'expected', 'value',
            )}
            request.update(path=getattr(args, 'path', ''), field={
                'section-rename': 'section-name', 'project-rename': 'project-name', 'unit-rename': 'unit-name',
            }[args.command])
            if args.command == 'unit-rename':
                request['project_name'] = args.project_name
            data = edit_material(args.state, args.runtime_root, request, check_only=args.check_only)
        elif args.command == 'unit-select':
            from .native_material_edit import select_project_unit
            data = select_project_unit(args.state, args.runtime_root, args.project_id,
                                       args.project_name, args.unit_name, args.expected_section)
        elif args.command == 'project-save':
            from .native_project_save import save_project
            data = save_project(args.state, args.runtime_root, args.project_id, args.command_id,
                                args.save_timeout, check_only=args.check_only)
        elif args.command == 'quota-short-drive-factor':
            from .native_standard_factor import apply_short_drive_factor
            request = dict(project_id=args.project_id, quantity=args.quantity, command_id=args.command_id,
                           parent_sections=args.parent_section)
            data = apply_short_drive_factor(args.state, args.runtime_root, args.inspector, request,
                                            verify_only=args.verify_only)
        elif args.command == 'quota-delete':
            from .native_delete import delete_quota
            request = {k:getattr(args,k) for k in ('project_id','parent_id','code','name','unit','mode','command_id')}
            if args.parent_section is not None:
                request.update(parent_sections=args.parent_section, expected_quantity=args.expected_quantity)
                if args.expected_note is not None:
                    request['expected_note'] = args.expected_note
                if args.path is not None:
                    request['path'] = args.path
                if args.kind is not None:
                    request['kind'] = args.kind
                if args.parent_quota is not None:
                    request['parent_quota'] = args.parent_quota
                if args.navigate_target:
                    request['navigate_target'] = True
            elif args.path is not None:
                raise Rejected('DELETE_SCOPE_INVALID', '--path requires --parent-section')
            elif (args.expected_quantity is not None or args.expected_note is not None
                  or args.kind is not None or args.parent_quota is not None or args.navigate_target):
                raise Rejected('DELETE_SCOPE_INVALID', 'Target filters and navigation require a section path')
            data = delete_quota(args.state, args.runtime_root, request)
        elif args.command == 'section-delete':
            from .native_delete import delete_quota
            request = dict(project_id=args.project_id, parent_id=None, code='', unit='',
                           name=args.parent_section[-1], kind='部', mode='permanent',
                           command_id=args.command_id, parent_sections=args.parent_section,
                           expected_quantity='0', navigate_target=True)
            data = delete_quota(args.state, args.runtime_root, request)
        elif args.command in ('quota-delete-confirm', 'quota-delete-reconcile'):
            from .native_delete import confirm_pending_delete, reconcile_pending_delete
            if args.command == 'quota-delete-confirm':
                data = confirm_pending_delete(args.state, args.command_id, reviewed_file_sha256=args.reviewed_file_sha256)
            else:
                data = reconcile_pending_delete(args.state, args.command_id)
        elif args.command == 'quota-clone-reconcile':
            from .native_reuse import reconcile_no_effect_paste
            data = reconcile_no_effect_paste(args.state, args.command_id)
        elif args.command == 'quota-clone':
            from .native_reuse import clone_quota
            request = {key: getattr(args, key) for key in ('project_id', 'command_id', 'code', 'quota_name',
                       'unit', 'material_code')}
            request['parent_sections'] = args.parent_section
            if args.variants is not None and args.material_name is None and args.quantity is None:
                request['variants'] = json.loads(args.variants.read_text(encoding='utf-8-sig'))
            elif args.variants is None and args.material_name is not None and args.quantity is not None:
                request['variants'] = [dict(material_name=args.material_name, quantity=args.quantity)]
            else:
                raise Rejected('REUSE_ARGUMENT_INVALID', 'Use --variants or both --material-name and --quantity')
            data = clone_quota(args.state, args.runtime_root, args.inspector, request)
        elif args.command == 'quota-reuse-plan':
            from .quota_reuse import plan_reuse
            data = plan_reuse(json.loads(args.capture.read_text(encoding='utf-8-sig')),
                              json.loads(args.request.read_text(encoding='utf-8-sig')))
        elif args.command == 'native-inspect':
            _, worker = runtime_config(args.runtime)
            r = subprocess.run([sys.executable, '-X', 'utf8', str(worker), '--inspect'], capture_output=True,
                               text=True, encoding='utf-8', timeout=120, check=True)
            data = json.loads(r.stdout)
        else:
            store = Ledger(args.state)
            try:
                initialize_commands(store)
                row = store.db.execute('SELECT * FROM native_cli_commands WHERE command_id=?', (args.command_id,)).fetchone()
                data = dict(row) if row else None
                if data and data['result']:
                    data['result'] = json.loads(data['result'])
                if args.include_resources:
                    if data is None:
                        raise Rejected('COMMAND_NOT_FOUND', 'No durable command found with this ID')
                    data['resources'] = command_resource_evidence(store, data)
            finally:
                store.close()
        print(json.dumps(dict(schema='gccp.native-cli.v0.1', status='ok', data=data, error=None), ensure_ascii=False, indent=2))
        return 0
    except (ValueError, TypeError, KeyError, OSError, RuntimeError, sqlite3.Error, subprocess.SubprocessError) as exc:
        print(json.dumps(dict(schema='gccp.native-cli.v0.1', status='blocked', data=None,
            error=dict(code=getattr(exc, 'code', 'NATIVE_CLI_ERROR'), message=str(exc),
                       retry_allowed=False)), ensure_ascii=False, indent=2))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
