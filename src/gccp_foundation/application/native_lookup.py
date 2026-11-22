"""Read exact candidate identities through the normal GCCP query action."""
from contextlib import closing
from dataclasses import asdict
import json
from pathlib import Path
import time
import uuid

from .native_build import TEMPLATES, compile_bridge, profile_path
from .native_cli import Rejected, initialize_commands
from .native_delete import load_bridge
from .native_runtime import _replace, render_transport
from .native_worker_support import Ledger, RuntimeProfile, SessionManager, WindowsDesktop, execution_lock, project_window_prefix, table
from .ports import SessionContext
from gccp_foundation.storage.native_save_attempts import NativeSaveAttempts


def render_lookup(directory, command, ctx, profile, title, code):
    files = render_transport(TEMPLATES, directory, command, ctx, profile, title)
    files['bridge.cpp'] = _replace(files['bridge.cpp'], 'req["operation"].toString()=="inspect"',
                                  'QStringList{"inspect","probe"}.contains(req["operation"].toString())')
    source = (TEMPLATES/'lookup.h.in').read_text(encoding='utf-8')
    files['operations.h'] = source.replace('__LOOKUP_CODE__', json.dumps(code, ensure_ascii=False))
    return files


def lookup_vendor(state, runtime_root, project_id, vendor):
    try:
        return lookup(state, runtime_root, project_id, vendor['code'])
    except Rejected as exc:
        if exc.code != 'CANDIDATE_NOT_OBSERVED':
            raise
    return lookup(state, runtime_root, project_id, vendor['code'], search=vendor['name'])


def wait_for_query(bridge, predicate, code, message):
    for _ in range(12):
        capture = bridge.call('probe', timeout=15)
        if predicate(capture):
            return capture
        time.sleep(.4)
    raise Rejected(code, message)


def lookup(state, runtime_root, project_id, code, *, search=None, query_action=None):
    if not query_action and (not code or len(code) > 80 or code != code.strip() or any(ord(c) < 32 for c in code)):
        raise Rejected('INVALID_QUOTA_CODE', 'Provide one exact quota code')
    with closing(Ledger(state)) as store:
        initialize_commands(store)
        row = store.db.execute('SELECT * FROM projects WHERE project_id=?', (project_id,)).fetchone()
        if row is None:
            raise Rejected('PROJECT_NOT_BOUND', 'Bind the open project first')
        with execution_lock(store.root, row['file_id']):
            meta = json.loads(row['metadata'])
            if (not meta.get('session') or meta.get('dirty') or store.load_pending(project_id)
                    or NativeSaveAttempts(store).active(project_id)
                    or store.db.execute("SELECT 1 FROM native_cli_commands WHERE project_id=? AND phase='armed_unknown'", (project_id,)).fetchone()):
                raise Rejected('PROJECT_NEEDS_RECONCILIATION', 'A clean current project is required for candidate lookup')
            ctx = SessionContext(**meta['session'])
            profile = profile_path(ctx.profile_id)
            manager = SessionManager(store, WindowsDesktop(), RuntimeProfile.load(profile))
            manager.validate_session(ctx)
            command = 'lookup-' + uuid.uuid4().hex
            directory = Path(runtime_root).resolve()/'lookups'/command
            directory.mkdir(parents=True, exist_ok=False)
            for name, content in render_lookup(directory, command, asdict(ctx), profile,
                                               project_window_prefix(meta['workcopy_path']), search or code or '').items():
                (directory/name).write_text(content, encoding='utf-8', newline=chr(10))
            compile_bridge(directory)
            bridge = load_bridge(directory)
            before = bridge.call('probe', timeout=15)
            if query_action == 'status':
                return {key: before[key] for key in ('query_windows', 'query_frames', 'query_close_ready',
                                                     'query_search_ready', 'query_controls', 'business_dispatches')}
            if query_action == 'close':
                if before['query_windows'] == 0:
                    return dict(status='native_quota_query_already_closed', business_dispatches=0)
                if before['query_windows'] != 1 or not before['query_close_ready']:
                    raise Rejected('QUOTA_QUERY_NOT_UNIQUE', 'Cannot identify one query window and its close control')
                original = table(before, '工程量表达式')
                bridge.call('close', timeout=30)
                after = wait_for_query(bridge, lambda s: s['query_windows'] == 0,
                                       'QUOTA_QUERY_CLOSE_NOT_VERIFIED', 'The query window did not close')
                if original['rows'] != table(after, '工程量表达式')['rows']:
                    raise Rejected('QUOTA_QUERY_CLOSE_NOT_VERIFIED', 'The budget differs from the pre-close snapshot')
                return dict(status='native_quota_query_closed_verified', business_dispatches=0)
            if before['query_windows']:
                raise Rejected('QUOTA_QUERY_ALREADY_OPEN', 'Use quota-candidates --close-query to clear the existing query window')
            bridge.call('open', timeout=30)
            try:
                wait_for_query(bridge, lambda s: s['query_search_ready'] and s['query_close_ready'],
                               'QUOTA_QUERY_NOT_READY', 'The expected query search and close controls did not become ready')
                bridge.call('search', timeout=30)
                for _ in range(12):
                    capture = bridge.call('inspect', timeout=15)
                    models = [t for t in capture['tables'] if len(t['headers']) == 5 and t['headers'][4] == '专业' and t['complete']]
                    if len(models) == 1:
                        candidates = [dict(code=v[0], name=v[1], unit=v[2], specialty=v[4])
                                      for r in models[0]['rows'] if (v:=r['values'])[0] == code]
                        if candidates:
                            return dict(library=capture['candidate_library'], candidates=candidates,
                                        insert_dispatches=0, runtime=str(directory))
                    time.sleep(.4)
                raise Rejected('CANDIDATE_NOT_OBSERVED', 'No exact result observed within the bounded query')
            finally:
                current = bridge.call('probe', timeout=15)
                if current['query_close_ready']:
                    bridge.call('close', timeout=30)
                    wait_for_query(bridge, lambda s: s['query_windows'] == 0,
                                   'QUOTA_QUERY_CLOSE_NOT_VERIFIED', 'The query window did not close')
                elif current['query_windows']:
                    raise Rejected('QUOTA_QUERY_CLOSE_NOT_VERIFIED', 'The existing query window could not be safely closed')
