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


class Rejected(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise Rejected('ARGUMENT_INVALID', message)


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


def execute(state, catalog_path, runtime_path, request):
    config, worker = runtime_config(runtime_path)
    if request != config['approved_request']:
        raise Rejected('REQUEST_NOT_REVIEWED', 'Request differs from the reviewed native runtime contract')
    catalog = Catalog(catalog_path)
    try:
        entry = catalog.entry(request['entry_id'])
    finally:
        catalog.close()
    if not all(entry.get(field) for field in ('spec', 'unit', 'name', 'code')):
        raise Rejected('CATALOG_REVIEW_REQUIRED', 'Incomplete extraction cannot identify the software candidate')
    live_family = config.get('catalog_match') == 'live_reviewed_family'
    if not live_family and entry['confidence'] not in ('high', 'manual_checked'):
        raise Rejected('CATALOG_REVIEW_REQUIRED', 'This fixed legacy runtime requires a reviewed catalog record')
    if entry['identity_digest'] != config['catalog_identity_digest']:
        raise Rejected('CATALOG_IDENTITY_CHANGED', 'Catalog record differs from the reviewed identity')
    expected = config['expected_live_identity']
    if live_family:
        from .native_runtime import reviewed_family
        identity, quantity = reviewed_family(entry, request['quantity'])
        if identity != expected or quantity != request['quantity']:
            raise Rejected('CATALOG_NATIVE_IDENTITY_MISMATCH', 'Runtime contract differs from the supported family')
    if expected['code'] != entry['code'] or expected['unit'] != entry['unit'] or entry['name'] not in expected['name']:
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
            text=True, encoding='utf-8', timeout=600)
        try:
            result = json.loads(completed.stdout)
        except ValueError as exc:
            raise Rejected('NEEDS_RECONCILIATION', 'Worker returned no structured result; do not replay') from exc
        if (completed.returncode or result.get('command_id') != config.get('native_command_id', request['command_id'])
                or result.get('status') != 'native_insert_quantity_saved_readback_verified'
                or result.get('quota', [])[:1] != [entry['code']]
                or len(result.get('quota', [])) < 7
                or result['quota'][3] != entry['unit']
                or result['quota'][6] != request['quantity']):
            with store._transaction():
                store.db.execute('UPDATE native_cli_commands SET result=? WHERE command_id=?',
                    (encoded(result), request['command_id']))
            raise Rejected('NEEDS_RECONCILIATION', 'Worker stopped; inspect command-status and native evidence without replay')
        result.update(native_command_id=result['command_id'], command_id=request['command_id'],
                      catalog_entry_id=entry['id'], catalog_identity_digest=entry['identity_digest'],
                      native_channel=config['channel'], reopen_verified=False)
        with store._transaction():
            store.db.execute("UPDATE native_cli_commands SET phase='readback_verified',result=? WHERE command_id=?",
                (encoded(result), request['command_id']))
        return result
    finally:
        store.close()


def reconcile(state, catalog_path, runtime_path, request):
    """Read-only native verification of an unknown outer dispatch, never run the worker business path."""
    config, worker = runtime_config(runtime_path)
    if config['approved_request'] != request:
        raise Rejected('REQUEST_NOT_REVIEWED', 'Exact original request required for reconciliation')
    catalog = Catalog(catalog_path)
    try:
        entry = catalog.entry(request['entry_id'])
    finally:
        catalog.close()
    if entry['identity_digest'] != config['catalog_identity_digest']:
        raise Rejected('CATALOG_IDENTITY_CHANGED', 'Original catalog identity changed')
    store = Ledger(state)
    try:
        initialize_commands(store)
        original = store.db.execute('SELECT * FROM native_cli_commands WHERE command_id=?', (request['command_id'],)).fetchone()
        if not original or original['request_digest'] != digest(request):
            raise Rejected('COMMAND_NOT_FOUND', 'Exact original durable command required')
        if original['phase'] == 'readback_verified':
            return {**json.loads(original['result']), 'idempotent_reentry': True, 'new_business_dispatches': 0}
        result = json.loads(original['result'] or '{}')
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
    parser = JsonArgumentParser(description='GCCP7 Native CLI developer preview')
    parser.add_argument('--version', action='version', version='gccp7-native-cli 0.1.0a1')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('doctor', help='Check the local version and toolchain without loading GCCP')
    binding = sub.add_parser('bind', help='Bind one explicitly selected already-open test workcopy')
    for flag in ('state', 'runtime-root', 'workcopy'):
        binding.add_argument('--' + flag, required=True, type=Path)
    binding.add_argument('--expected-sha256', required=True)
    cat = sub.add_parser('catalog')
    cats = cat.add_subparsers(dest='operation', required=True)
    for operation in ('inspect', 'search'):
        p = cats.add_parser(operation); p.add_argument('--db', required=True, type=Path)
        if operation == 'search':
            p.add_argument('--code'); p.add_argument('--text'); p.add_argument('--source-id', type=int)
            p.add_argument('--spec'); p.add_argument('--specialty'); p.add_argument('--limit', type=int, default=20)
    native = sub.add_parser('native-inspect'); native.add_argument('--runtime', required=True, type=Path)
    quota = sub.add_parser('quota-add'); quota.add_argument('--db', required=True, type=Path)
    quota.add_argument('--runtime', required=True, type=Path); quota.add_argument('--request', required=True, type=Path)
    quota.add_argument('--state', required=True, type=Path)
    recovery = sub.add_parser('command-reconcile')
    for flag in ('db', 'runtime', 'request', 'state'):
        recovery.add_argument('--' + flag, required=True, type=Path)
    for operation in ('runtime-prepare', 'quota-run'):
        p = sub.add_parser(operation)
        for flag in ('db', 'state', 'inspector', 'runtime-root'):
            p.add_argument('--' + flag, required=True, type=Path)
        for flag in ('project-id', 'parent-id', 'quantity', 'command-id'):
            p.add_argument('--' + flag, required=True)
        p.add_argument('--entry-id', required=True, type=int)
    deletion = sub.add_parser('quota-delete', help='Delete one exact quota in the current budget')
    for flag in ('state', 'runtime-root'):
        deletion.add_argument('--' + flag, required=True, type=Path)
    for flag in ('project-id', 'parent-id', 'code', 'name', 'unit', 'command-id'):
        deletion.add_argument('--' + flag, required=True)
    deletion.add_argument('--mode', required=True, choices=('temporary', 'permanent'))
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
    try:
        if sys.flags.optimize:
            raise Rejected('OPTIMIZATION_UNSUPPORTED', 'Run with Python assertions enabled')
        args = parser.parse_args(argv)
        if args.command == 'doctor':
            from .native_bootstrap import doctor
            data = doctor()
        elif args.command == 'bind':
            from .native_bootstrap import bind_open
            data = bind_open(args.state, args.runtime_root, args.workcopy, args.expected_sha256)
        elif args.command == 'catalog':
            catalog = Catalog(args.db)
            try:
                data = catalog.summary() if args.operation == 'inspect' else catalog.search(
                    code=args.code, text=args.text, source_id=args.source_id, spec=args.spec,
                    specialty=args.specialty, limit=args.limit)
            finally:
                catalog.close()
        elif args.command == 'quota-add':
            data = execute(args.state, args.db, args.runtime, json.loads(args.request.read_text(encoding='utf-8-sig')))
        elif args.command == 'command-reconcile':
            data = reconcile(args.state, args.db, args.runtime, json.loads(args.request.read_text(encoding='utf-8-sig')))
        elif args.command in ('runtime-prepare', 'quota-run'):
            from .native_runtime import prepare_entry
            manifest, request_path, request = prepare_entry(args.db, args.state, args.inspector,
                args.project_id, args.parent_id, args.quantity, args.command_id, args.entry_id, args.runtime_root)
            data = dict(runtime=str(manifest), request_path=str(request_path), business_dispatches=0)
            if args.command == 'quota-run':
                data = {**execute(args.state, args.db, manifest, request), 'runtime':str(manifest)}
        elif args.command == 'quota-delete':
            from .native_delete import delete_quota
            request = {k:getattr(args,k) for k in ('project_id','parent_id','code','name','unit','mode','command_id')}
            data = delete_quota(args.state, args.runtime_root, request)
        elif args.command in ('quota-delete-confirm', 'quota-delete-reconcile'):
            from .native_delete import confirm_pending_delete, reconcile_pending_delete
            if args.command == 'quota-delete-confirm':
                data = confirm_pending_delete(args.state, args.command_id, reviewed_file_sha256=args.reviewed_file_sha256)
            else:
                data = reconcile_pending_delete(args.state, args.command_id)
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
