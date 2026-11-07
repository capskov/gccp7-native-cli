"""Set up a scoped read-only inspector for an already open test workcopy."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

from .native_build import TEMPLATES, compile_bridge, profile_path, toolchain
from .native_cli import Rejected, initialize_commands
from .native_runtime import render_transport
from .native_worker_support import (
    Cancellation, Ledger, RuntimeProfile, SessionManager, WindowsDesktop,
    execution_lock, file_sha256, project_window_prefix, table,
)
from gccp_foundation.storage.native_save_attempts import NativeSaveAttempts


def doctor():
    toolchain()
    profile = RuntimeProfile.load(profile_path())
    components = profile.verify()
    return dict(profile=profile.profile_id, matched_components=len(components),
                qt='5.9.9', native_build_tools_present=True, application_dispatches=0)


def render_inspector(directory, session, profile, title):
    name = 'inspect-' + uuid.uuid4().hex
    files = render_transport(TEMPLATES, directory, name, session, profile, title)
    files['operations.h'] = '''static bool nativeOperation(const QJsonObject &req, QJsonObject &, QString &error) {
    if (req["operation"].toString() == "inspect") return true;
    error = "inspection_only";
    return false;
}
'''
    files['worker.py'] = '''import json
import sys
from cli_bridge import NativeBridge

if sys.argv[1:] != ["--inspect"]:
    raise SystemExit("Inspector only supports --inspect")
print(json.dumps(NativeBridge().call("inspect", timeout=30), ensure_ascii=False))
'''
    return files


def bind_open(state, runtime_root, workcopy, expected_sha256):
    if not re.fullmatch(r'[0-9a-fA-F]{64}', expected_sha256):
        raise Rejected('DISK_DIGEST_INVALID', 'Provide the reviewed workcopy SHA256')
    expected_sha256 = expected_sha256.lower()
    workcopy = workcopy.resolve(strict=True)
    if file_sha256(workcopy) != expected_sha256:
        raise Rejected('PROJECT_IDENTITY_MISMATCH', 'Reviewed workcopy digest differs')
    toolchain()
    profile = profile_path()
    store = Ledger(state)
    try:
        initialize_commands(store)
        manager = SessionManager(store, WindowsDesktop(), RuntimeProfile.load(profile))
        registered = [p for p in store.db.execute('SELECT * FROM projects')
                      if Path(json.loads(p['metadata']).get('workcopy_path', '')).resolve() == workcopy]
        if len(registered) > 1:
            raise Rejected('PROJECT_IDENTITY_MISMATCH', 'Conflicting project registrations')
        project_id = registered[0]['project_id'] if registered else manager.adopt_open_workcopy(workcopy, expected_sha256)
        row = store.db.execute('SELECT * FROM projects WHERE project_id=?', (project_id,)).fetchone()
        with execution_lock(store.root, row['file_id']):
            if (store.load_pending(project_id) or NativeSaveAttempts(store).active(project_id) or
                    store.db.execute("SELECT 1 FROM native_cli_commands WHERE project_id=? AND phase='armed_unknown'",
                                     (project_id,)).fetchone()):
                raise Rejected('PROJECT_NEEDS_RECONCILIATION', 'Use the original command evidence before binding again')
            ctx = manager.bind(project_id, time.monotonic()+8, Cancellation())
            metadata = manager.validate_session(ctx)
            if metadata.get('dirty') or file_sha256(workcopy) != expected_sha256:
                raise Rejected('PROJECT_IDENTITY_MISMATCH', 'Project changed during binding')
            directory = Path(runtime_root).resolve()/'inspectors'/uuid.uuid4().hex
            directory.mkdir(parents=True, exist_ok=False)
            files = render_inspector(directory, asdict(ctx), profile, project_window_prefix(workcopy))
            for name, content in files.items():
                (directory/name).write_text(content, encoding='utf-8', newline='\n')
            compile_bridge(directory)
            observed = subprocess.run([sys.executable, '-X', 'utf8', str(directory/'worker.py'), '--inspect'],
                capture_output=True, text=True, encoding='utf-8', timeout=50)
            if observed.returncode:
                raise Rejected('INSPECTOR_NOT_READY', 'No business action sent. Inspect the read-only IPC receipts.')
            capture = json.loads(observed.stdout)
            budget = table(capture, '工程量表达式')
            if capture['pid'] != ctx.pid or capture['created_filetime'] != ctx.created_filetime or not capture['callback_on_app_thread']:
                raise Rejected('SESSION_CHANGED', 'Inspector result does not match the bound session')
            manager.validate_session(ctx)
            if file_sha256(workcopy) != expected_sha256:
                raise Rejected('PROJECT_IDENTITY_MISMATCH', 'Project changed during inspection')
            artifacts = [directory/n for n in files] + [directory/'build/gccp_business.dll']
            pins = {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in artifacts}
            worker = str(directory/'worker.py')
            manifest = directory/'runtime.json'
            manifest.write_text(json.dumps(dict(runtime_kind='inspector', channel='native_qt_business',
                live_candidate_check_required=True, worker=worker, worker_sha256=pins[worker],
                artifact_sha256=pins), ensure_ascii=False, indent=2)+'\n',encoding='utf-8')
            parents = [{'id':r['values'][12], 'name':r['values'][2]} for r in budget['rows']
                       if r['depth'] == 1 and r['values'][1] == '部' and r['values'][12]]
            return dict(project_id=project_id, inspector=str(manifest), parents=parents,
                        budget_rows=len(budget['rows']), business_dispatches=0)
    finally:
        store.close()
