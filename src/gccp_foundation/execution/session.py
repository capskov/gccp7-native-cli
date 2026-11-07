"""Owned workcopies and process binding. Window readiness is not business readiness."""
from contextlib import contextmanager
from dataclasses import asdict, replace
import ctypes as C
from ctypes import wintypes as W
import os
from pathlib import Path
import time
import uuid

from gccp_foundation.application.ports import SessionContext
from gccp_foundation.domain.errors import ErrorCategory, ErrorDetail, FoundationError
from gccp_foundation.domain.json_values import loads
from gccp_foundation.execution.profiles import file_sha256
from gccp_foundation.adapters.native_windows.desktop import await_condition
from gccp_foundation.storage.locks import execution_lock


def error(code, message):
    return FoundationError(ErrorDetail(code, ErrorCategory.BINDING, message))


def file_identity(path):
    stat = Path(path).stat()
    if not stat.st_ino:
        raise error("PROJECT_IDENTITY_MISMATCH", "Filesystem does not expose a stable file identity")
    return f"{stat.st_dev}:{stat.st_ino}"


def exact_project_window(window, path):
    path = Path(path).resolve()
    title = window["title"].removesuffix(" (Injected by GammaRay)").replace("\\", "/")
    expected = f"{path.stem} - {path.parent.as_posix()} - "
    return title.casefold().startswith(expected.casefold()) and title.endswith(" - 广联达概算计价") and not window["owner"]


@contextmanager
def stable_source(path):
    """Deny writers while copying, in addition to checking source windows."""
    if os.name == "nt":
        import msvcrt
        kernel = C.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, W.LPVOID, W.DWORD, W.DWORD, W.HANDLE]
        kernel.CreateFileW.restype = W.HANDLE
        handle = kernel.CreateFileW(str(path), 0x80000000, 1, None, 3, 0x80, None)
        if handle == C.c_void_p(-1).value:
            raise error("SOURCE_BUSY", "Source has an active writer or is unavailable")
        fd = msvcrt.open_osfhandle(int(handle), os.O_RDONLY)
        with os.fdopen(fd, "rb") as stream:
            yield stream
    else:
        import fcntl
        with Path(path).open("rb") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield stream


class SessionManager:
    def __init__(self, ledger, desktop, profile, business_reader=None, save_adapter=None):
        self.ledger, self.desktop, self.profile = ledger, desktop, profile
        self.business_reader = business_reader
        self.save_adapter = save_adapter

    def _project(self, project_id):
        row = self.ledger.db.execute("SELECT * FROM projects WHERE project_id=?", (project_id,)).fetchone()
        if not row:
            raise error("PROJECT_IDENTITY_MISMATCH", "Unknown managed project")
        metadata = loads(row["metadata"])
        path = Path(metadata["workcopy_path"])
        if not metadata.get("owned_workcopy") or file_identity(path) != row["file_id"]:
            raise error("PROJECT_IDENTITY_MISMATCH", "Workcopy ownership or file identity changed")
        if metadata.get('provenance') == 'explicit_user_selected_open_workcopy':
            if not metadata.get('keep_open') or not metadata.get('initial_sha256') or metadata.get('source_path') is not None:
                raise error('PROJECT_IDENTITY_MISMATCH', 'Invalid open-workcopy registration')
        else:
            source = Path(metadata["source_path"])
            if source.samefile(path) or file_sha256(source) != metadata["source_sha256"]:
                raise error("PROJECT_IDENTITY_MISMATCH", "Protected source changed")
        return dict(row), metadata

    def adopt_open_workcopy(self, workcopy, expected_sha256):
        """Explicit opt-in for a user's already open file, without copying it.

        The reviewed initial digest and exact unique worker must both match.
        This does not certify business readiness or permit close/relaunch.
        """
        self.profile.verify()
        path = Path(workcopy).resolve(strict=True)
        if path.suffix.casefold() != '.gpb7' or file_sha256(path) != expected_sha256:
            raise error('PROJECT_IDENTITY_MISMATCH', 'Reviewed open-workcopy digest mismatch')
        windows = [w for w in self.desktop.windows() if exact_project_window(w, path)]
        if len(windows) != 1 or not windows[0]['enabled']:
            raise error('PROJECT_IDENTITY_MISMATCH', 'Unique enabled existing project window required')
        proc = self.desktop.process(windows[0]['pid'])
        if Path(proc['executable']).resolve() != Path(self.profile.process_executable).resolve():
            raise error('PROFILE_MISMATCH', 'Unexpected open-workcopy executable')
        identity = file_identity(path)
        with execution_lock(self.ledger.root, identity):
            if self.ledger.db.execute('SELECT 1 FROM projects WHERE file_id=?', (identity,)).fetchone():
                raise error('PROJECT_IDENTITY_MISMATCH', 'Open workcopy already registered')
            if file_sha256(path) != expected_sha256:
                raise error('PROJECT_IDENTITY_MISMATCH', 'Open workcopy changed during registration')
            project_id = 'project.' + uuid.uuid4().hex
            self.ledger.register_project(project_id, identity, dict(
                owned_workcopy=True, workcopy_path=str(path), source_path=None,
                initial_sha256=expected_sha256, provenance='explicit_user_selected_open_workcopy',
                keep_open=True, profile_id=self.profile.profile_id,
                business_ready=False, session=None, dirty=False))
        return project_id

    def inspect_saved_open_identity(self, session, expected_saved_sha256, *, allow_disabled_window=False):
        """Read-only inspect a saved replacement while a verified helper marker remains.

        Does not change ownership, binding or dirty/pending state and grants no dispatch.
        """
        self.profile.verify()
        row = self.ledger.db.execute('SELECT * FROM projects WHERE project_id=?',
                                     (session.project_id,)).fetchone()
        if not row:
            raise error('PROJECT_IDENTITY_MISMATCH', 'Unknown saved project')
        metadata = loads(row['metadata'])
        if (metadata.get('session') != asdict(session) or not metadata.get('owned_workcopy')
                or metadata.get('provenance') != 'explicit_user_selected_open_workcopy'
                or not metadata.get('keep_open') or self.ledger.load_pending(session.project_id)):
            raise error('PENDING_ACTION', 'Only an already reconciled open project can be inspected')
        path = Path(metadata['workcopy_path'])
        windows = [w for w in self.desktop.windows()
                   if w['hwnd']==session.hwnd and exact_project_window(w,path)]
        proc = self.desktop.process(session.pid)
        if (len(windows)!=1 or windows[0]['pid']!=session.pid
                or (not windows[0]['enabled'] and not allow_disabled_window)
                or proc['created_filetime']!=session.created_filetime
                or Path(proc['executable']).resolve()!=Path(self.profile.process_executable).resolve()
                or file_sha256(path)!=expected_saved_sha256):
            raise error('SESSION_CHANGED', 'Saved open process, window or reviewed digest differs')
        return dict(path=str(path), file_id=file_identity(path), sha256=expected_saved_sha256)

    def rebind_reviewed_open_baseline(self, session, expected_sha256, artifact_id):
        """Accept explicitly reviewed user edits without claiming a native Save."""
        observed = self.inspect_saved_open_identity(session, expected_sha256)
        row = self.ledger.db.execute('SELECT * FROM projects WHERE project_id=?',
                                    (session.project_id,)).fetchone()
        metadata = loads(row['metadata'])
        evidence = self.ledger.read_evidence(artifact_id, session.project_id)
        proof = evidence['data']; capture = proof.get('current_capture', {})
        if (evidence['kind'] != 'user_edited_open_project_baseline_observed'
                or evidence['session_id'] != session.session_id
                or proof.get('session') != asdict(session)
                or not (proof.get('user_confirmed_deletion') is True
                        or proof.get('user_confirmed_current_baseline') is True)
                or proof.get('user_requested_rebind') is not True
                or proof.get('disk_sha256') != expected_sha256
                or proof.get('disk_identity') != observed
                or proof.get('old_file_id') != row['file_id']
                or proof.get('prior_owner') != row['owner']
                or metadata.get('dirty') or self.ledger.load_pending(session.project_id)
                or capture.get('pid') != session.pid
                or capture.get('created_filetime') != session.created_filetime
                or capture.get('callback_on_app_thread') is not True
                or capture.get('status') != 'observed'
                or not capture.get('tables')
                or not all(t.get('complete') is True for t in capture['tables'])):
            raise error('EVIDENCE_MISMATCH', 'Explicit reviewed current open baseline required')
        updated = replace(session, project_file_id=observed['file_id'])
        metadata['session'] = asdict(updated)
        metadata['business_ready'] = False
        metadata.setdefault('user_baseline_rebindings', []).append(dict(
            artifact_id=artifact_id, prior_session=asdict(session),
            observed_session=asdict(updated), disk_memory_equivalence_verified=False,
            native_save_dispatched=False))
        from gccp_foundation.storage.ledger import encoded
        with self.ledger._transaction():
            current = self.ledger.db.execute('SELECT * FROM projects WHERE project_id=?',
                                             (session.project_id,)).fetchone()
            if (dict(current) != dict(row) or self.ledger.load_pending(session.project_id)
                    or file_identity(observed['path']) != observed['file_id']
                    or file_sha256(observed['path']) != expected_sha256):
                raise error('SESSION_CHANGED', 'Reviewed baseline changed during rebind')
            self.ledger.db.execute('UPDATE projects SET file_id=?,metadata=? WHERE project_id=?',
                (observed['file_id'], encoded(metadata), session.project_id))
            self.ledger._event(row['owner'], 'user_reviewed_open_baseline_rebound_no_save',
                dict(artifact_id=artifact_id, session=asdict(updated)))
        return updated

    def reconcile_open_native_save(self, session, expected_saved_sha256):
        """Explicitly accept a verified native atomic save, never an unknown edit.

        Unlike normal validation this records a deliberate file-id transition.
        The caller supplies the saved/backup-verified digest; old process and
        window bindings, source policy and pending-action guards still apply.
        """
        self.profile.verify()
        row = self.ledger.db.execute('SELECT * FROM projects WHERE project_id=?', (session.project_id,)).fetchone()
        if not row:
            raise error('PROJECT_IDENTITY_MISMATCH', 'Unknown native-save project')
        metadata = loads(row['metadata'])
        if (metadata.get('session') != asdict(session) or
                metadata.get('provenance') != 'explicit_user_selected_open_workcopy' or
                not metadata.get('keep_open') or metadata.get('dirty') or
                self.ledger.load_pending(session.project_id)):
            raise error('PENDING_ACTION', 'Only a reconciled user-selected open project may accept native save')
        path = Path(metadata['workcopy_path'])
        windows = [w for w in self.desktop.windows() if w['hwnd']==session.hwnd and exact_project_window(w,path)]
        proc = self.desktop.process(session.pid)
        if (len(windows)!=1 or windows[0]['pid']!=session.pid or not windows[0]['enabled'] or
                proc['created_filetime']!=session.created_filetime or
                Path(proc['executable']).resolve()!=Path(self.profile.process_executable).resolve()):
            raise error('SESSION_CHANGED', 'Native-save worker/window changed')
        if file_sha256(path) != expected_saved_sha256:
            raise error('PROJECT_IDENTITY_MISMATCH', 'Saved digest does not match independently reviewed backup')
        identity = file_identity(path)
        new_session = replace(session, project_file_id=identity)
        metadata.setdefault('native_saves', []).append(dict(
            prior_file_id=row['file_id'], saved_file_id=identity,
            saved_sha256=expected_saved_sha256, recorded_ms=int(time.time()*1000)))
        metadata['session'] = asdict(new_session)
        metadata['business_ready'] = False
        from gccp_foundation.storage.ledger import encoded
        with self.ledger._transaction():
            if file_sha256(path)!=expected_saved_sha256 or file_identity(path)!=identity:
                raise error('PROJECT_IDENTITY_MISMATCH', 'Native-save file changed during reconciliation')
            self.ledger.db.execute('UPDATE projects SET file_id=?,metadata=? WHERE project_id=?',
                                   (identity,encoded(metadata),session.project_id))
        return new_session

    def observe_owned_save_preparation_identity(self, session, expected_sha256, artifact_id):
        """Record atomic file replacement during an owned Save preparation.

        All business actions must already have readback. This retains dirty and
        the Save marker, grants no replay, and does not certify persistence.
        """
        observed = self.inspect_saved_open_identity(session, expected_sha256)
        row = self.ledger.db.execute('SELECT * FROM projects WHERE project_id=?',
                                    (session.project_id,)).fetchone()
        metadata = loads(row['metadata'])
        marker = metadata.get('pending_one_command_save')
        job = self.ledger.job(row['owner']) if row['owner'] else None
        states = self.ledger.db.execute('SELECT state FROM actions WHERE job_id=?',
                                       (row['owner'],)).fetchall()
        evidence = self.ledger.read_evidence(artifact_id, session.project_id)
        data = evidence['data']; capture = data.get('capture', {})
        if (not job or job['state'] != 'running' or job['project_id'] != session.project_id
                or row['file_id'] != session.project_file_id
                or observed['file_id'] == session.project_file_id
                or metadata.get('dirty') is not True
                or metadata.get('pending_one_command') is not None
                or not isinstance(marker, str) or not marker.startswith('intent.')
                or len(marker) != 39 or not states
                or any(a['state'] != 'readback_verified' for a in states)
                or evidence['session_id'] != session.session_id
                or evidence['kind'] != 'native_owned_save_preparation_atomic_observed'
                or data.get('save_intent_id') != marker
                or data.get('sha256') != expected_sha256
                or data.get('file_id') != observed['file_id']
                or data.get('business_rows_verified') is not True
                or capture.get('status') != 'observed'
                or capture.get('pid') != session.pid
                or str(capture.get('created_filetime')) != session.created_filetime
                or capture.get('callback_on_app_thread') is not True
                or not capture.get('tables')
                or not all(t.get('complete') is True for t in capture['tables'])):
            raise error('EVIDENCE_MISMATCH', 'Owned Save preparation and complete native evidence required')
        updated = replace(session, project_file_id=observed['file_id'])
        prior = row['metadata']
        metadata['session'] = asdict(updated)
        metadata['business_ready'] = False
        metadata.setdefault('native_save_preparation_observations', []).append(dict(
            prior_file_id=row['file_id'], observed_file_id=observed['file_id'],
            observed_sha256=expected_sha256, save_intent_retained=marker,
            artifact_id=artifact_id))
        from gccp_foundation.storage.ledger import encoded
        with self.ledger._transaction():
            current = self.ledger.db.execute('SELECT metadata,owner FROM projects WHERE project_id=?',
                                             (session.project_id,)).fetchone()
            if (current['metadata'] != prior or current['owner'] != row['owner']
                    or file_identity(observed['path']) != observed['file_id']
                    or file_sha256(observed['path']) != expected_sha256):
                raise error('SESSION_CHANGED', 'Save preparation changed during identity observation')
            self.ledger.db.execute('UPDATE projects SET file_id=?,metadata=? WHERE project_id=?',
                (observed['file_id'], encoded(metadata), session.project_id))
            self.ledger._event(row['owner'], 'save_preparation_atomic_identity_observed_no_replay', dict(
                artifact_id=artifact_id, save_intent_id=marker,
                prior_session=asdict(session), observed_session=asdict(updated)))
        return updated

    def _metadata(self, project_id, metadata):
        from gccp_foundation.storage.ledger import encoded
        with self.ledger._transaction():
            self.ledger.db.execute("UPDATE projects SET metadata=? WHERE project_id=?", (encoded(metadata), project_id))

    def observe_pending_insert_save_identity(self, session, expected_sha256, artifact_id):
        """Record same-process atomic-save identity while retaining an owned intent.

        This neither resolves the insertion nor permits its replay. Its original
        body, expected snapshot, preflight evidence and durable intent survive.
        A caller must still reconcile the business result before another action.
        """
        self.profile.verify()
        row = self.ledger.db.execute('SELECT * FROM projects WHERE project_id=?',
                                     (session.project_id,)).fetchone()
        pending = self.ledger.load_pending(session.project_id)
        if not row or len(pending) != 1:
            raise error('PENDING_ACTION', 'Exactly one owned pending insertion required')
        metadata = loads(row['metadata'])
        action = pending[0]
        prior_context = loads(action['context'])
        if (row['owner'] != action['job_id'] or row['file_id'] != session.project_file_id
                or metadata.get('session') != asdict(session)
                or prior_context.get('session') != asdict(session)
                or not metadata.get('owned_workcopy') or not metadata.get('keep_open')
                or metadata.get('provenance') != 'explicit_user_selected_open_workcopy'
                or loads(action['body'])['kind'] != 'ensure_quota'):
            raise error('PROJECT_IDENTITY_MISMATCH', 'Owned pending insertion/session differs')
        evidence = self.ledger.read_evidence(artifact_id, session.project_id)
        proof = evidence['data']
        capture = proof.get('capture', {})
        if (evidence['session_id'] != session.session_id
                or evidence['kind'] != 'native_pending_insert_atomic_save_observed'
                or proof.get('intent_id') != action['intent_id']
                or proof.get('sha256') != expected_sha256
                or capture.get('status') != 'observed' or capture.get('pid') != session.pid
                or capture.get('created_filetime') != session.created_filetime
                or capture.get('callback_on_app_thread') is not True
                or not capture.get('tables')
                or any(not t.get('complete') for t in capture['tables'])):
            raise error('EVIDENCE_MISMATCH', 'Complete current native observation required')
        path = Path(metadata['workcopy_path'])
        windows = [w for w in self.desktop.windows()
                   if w['hwnd'] == session.hwnd and exact_project_window(w, path)]
        process = self.desktop.process(session.pid)
        identity = file_identity(path)
        if (len(windows) != 1 or windows[0]['pid'] != session.pid or not windows[0]['enabled']
                or process['created_filetime'] != session.created_filetime
                or Path(process['executable']).resolve() != Path(self.profile.process_executable).resolve()
                or file_sha256(path) != expected_sha256 or proof.get('file_id') != identity
                or identity == session.project_file_id):
            raise error('SESSION_CHANGED', 'Observed native save/process differs')
        updated = replace(session, project_file_id=identity)
        metadata.setdefault('native_saves', []).append(dict(prior_file_id=row['file_id'],
            saved_file_id=identity, saved_sha256=expected_sha256, artifact_id=artifact_id,
            pending_intent_retained=action['intent_id']))
        metadata['session'] = asdict(updated)
        metadata['business_ready'] = False
        from gccp_foundation.storage.ledger import encoded
        with self.ledger._transaction():
            current = self.ledger.db.execute('SELECT * FROM projects WHERE project_id=?',
                                             (session.project_id,)).fetchone()
            if (dict(current) != dict(row) or tuple(self.ledger.load_pending(session.project_id)) != pending
                    or file_identity(path) != identity or file_sha256(path) != expected_sha256):
                raise error('SESSION_CHANGED', 'Identity/pending changed during observation')
            self.ledger.db.execute('UPDATE projects SET file_id=?,metadata=? WHERE project_id=?',
                                  (identity, encoded(metadata), session.project_id))
            rebound = dict(prior_context, session=asdict(updated))
            self.ledger.db.execute('UPDATE actions SET context=? WHERE intent_id=?',
                                  (encoded(rebound), action['intent_id']))
            self.ledger._event(action['job_id'], 'pending_insert_save_identity_observed_no_dispatch',
                dict(intent_id=action['intent_id'], artifact_id=artifact_id,
                     prior_context=prior_context, current_session=asdict(updated)))
        return updated

    def prepare_copy(self, source, workspace):
        self.profile.verify()
        source = Path(source).resolve(strict=True)
        if source.suffix.casefold() != ".gpb7":
            raise error("PROJECT_IDENTITY_MISMATCH", "Only GPB7 projects supported")
        source_id = file_identity(source)
        with execution_lock(self.ledger.root, source_id):
            if any(exact_project_window(w, source) for w in self.desktop.windows()):
                raise error("SOURCE_BUSY", "Source project must be saved and closed before copying")
            directory = Path(workspace).resolve() / ("managed-" + uuid.uuid4().hex)
            directory.mkdir(parents=True, exist_ok=False)
            workcopy = directory / (source.stem + "-智能副本.GPB7")
            with stable_source(source) as stream:
                before = file_sha256(source)
                with workcopy.open("xb") as target:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        target.write(chunk)
                    target.flush()
                    os.fsync(target.fileno())
                if before != file_sha256(source) or before != file_sha256(workcopy) or source_id != file_identity(source):
                    raise error("PROJECT_IDENTITY_MISMATCH", "Source changed while creating workcopy")
            if file_identity(workcopy) == source_id:
                raise error("PROJECT_IDENTITY_MISMATCH", "Workcopy must be a distinct file")
            project_id = "project." + uuid.uuid4().hex
            metadata = dict(owned_workcopy=True, workcopy_path=str(workcopy), source_path=str(source),
                            source_sha256=before, profile_id=self.profile.profile_id,
                            business_ready=False, session=None, dirty=False)
            self.ledger.register_project(project_id, file_identity(workcopy), metadata)
        return project_id

    def bind(self, project_id, deadline, cancellation):
        self.profile.verify()
        row, metadata = self._project(project_id)
        path = Path(metadata["workcopy_path"])

        def probe():
            return [w for w in self.desktop.windows() if exact_project_window(w, path)]

        windows = await_condition(probe, lambda rows: len(rows) == 1 and rows[0]["enabled"], deadline, cancellation)
        window = windows[0]
        proc = self.desktop.process(window["pid"])
        if Path(proc["executable"]).resolve() != Path(self.profile.process_executable).resolve():
            raise error("PROFILE_MISMATCH", "Project window belongs to an unexpected executable")
        if any(w["pid"] == window["pid"] and w["hwnd"] != window["hwnd"] and not w["owner"]
               and w["title"].removesuffix(" (Injected by GammaRay)").endswith(" - 广联达概算计价")
               for w in self.desktop.windows()):
            raise error("PROJECT_IDENTITY_MISMATCH", "Do not manage a process shared with another project")
        session = SessionContext(project_id, "session." + uuid.uuid4().hex, proc["pid"],
                                 proc["created_filetime"], window["hwnd"], row["file_id"], self.profile.profile_id)
        if metadata.get("session"):
            old = metadata["session"]
            if all(old[k] == getattr(session, k) for k in ("pid", "created_filetime", "hwnd", "project_file_id")):
                return SessionContext(**old)
        metadata["session"] = asdict(session)
        metadata["business_ready"] = False
        self._metadata(project_id, metadata)
        return session

    def adopt_native_copy(self, source, workcopy, expected_source_sha256, expected_workcopy_sha256):
        """Register an explicitly supplied native Save As result without rewriting it.

        Hashes are caller-reviewed provenance, not proof of business equivalence.
        Business readiness remains false until the normal reader verifies it.
        """
        self.profile.verify()
        source = Path(source).resolve(strict=True)
        workcopy = Path(workcopy).resolve(strict=True)
        if source.suffix.casefold() != '.gpb7' or workcopy.suffix.casefold() != '.gpb7' or source.samefile(workcopy):
            raise error('PROJECT_IDENTITY_MISMATCH', 'Distinct GPB7 source and native workcopy required')
        with execution_lock(self.ledger.root, file_identity(source)):
            if any(exact_project_window(w, source) for w in self.desktop.windows()):
                raise error('SOURCE_BUSY', 'Protected source is still open')
            with stable_source(source), stable_source(workcopy):
                if file_sha256(source) != expected_source_sha256 or file_sha256(workcopy) != expected_workcopy_sha256:
                    raise error('PROJECT_IDENTITY_MISMATCH', 'Reviewed native-copy hashes changed')
                identity = file_identity(workcopy)
                if self.ledger.db.execute('SELECT 1 FROM projects WHERE file_id=?', (identity,)).fetchone():
                    raise error('PROJECT_IDENTITY_MISMATCH', 'Workcopy is already registered')
                project_id = 'project.' + uuid.uuid4().hex
                self.ledger.register_project(project_id, identity, dict(
                    owned_workcopy=True, workcopy_path=str(workcopy), source_path=str(source),
                    source_sha256=expected_source_sha256, native_copy_initial_sha256=expected_workcopy_sha256,
                    provenance='explicit_native_save_as', profile_id=self.profile.profile_id,
                    business_ready=False, session=None, dirty=False))
        return project_id

    def validate_session(self, session):
        row, metadata = self._project(session.project_id)
        if metadata.get("session") != asdict(session):
            raise error("SESSION_CHANGED", "Session is no longer the managed session")
        windows = [w for w in self.desktop.windows() if w["hwnd"] == session.hwnd and exact_project_window(w, metadata["workcopy_path"])]
        process = self.desktop.process(session.pid)
        if len(windows) != 1 or windows[0]["pid"] != session.pid or process["created_filetime"] != session.created_filetime:
            raise error("SESSION_CHANGED", "PID/window reference changed; rebind required")
        self.profile.verify()
        return metadata

    def open_existing_workcopy(self, project_id, deadline, cancellation):
        row, metadata = self._project(project_id)
        with execution_lock(self.ledger.root, row["file_id"]):
            if self.desktop.interactive_state() != "ready":
                raise error("NO_INTERACTIVE_DESKTOP", "Interactive desktop unavailable")
            self.profile.verify()
            if not any(exact_project_window(w, metadata["workcopy_path"]) for w in self.desktop.windows()):
                if metadata.get('keep_open'):
                    raise error('SESSION_CHANGED', 'User-selected open workcopy must not be relaunched')
                metadata["pids_before_launch"] = [p["pid"] for p in self.desktop.processes()]
                self._metadata(project_id, metadata)
                self.desktop.launch(self.profile.launcher, metadata["workcopy_path"])
            return self.bind(project_id, deadline, cancellation)

    def read_business(self, session):
        metadata = self.validate_session(session)
        if self.business_reader is None:
            raise error("UNSUPPORTED_CAPABILITY", "Window bound; structured business reader not yet connected")
        observation = self.business_reader(session)
        if not observation.complete or observation.data.get("library") != self.profile.library or observation.data.get("edition") != self.profile.edition or observation.data.get("trade") != self.profile.trade:
            raise error("PROJECT_NOT_READY", "Business library/edition not verified")
        metadata["business_ready"] = True
        self._metadata(session.project_id, metadata)
        return observation

    def create_workcopy(self, request, deadline, cancellation):
        # Native blank-project wizard is a separate unverified operation, not a template copy.
        raise error("UNSUPPORTED_CAPABILITY", "Native blank-2025 project creation is not certified yet")

    def save_owned(self, ctx, action):
        self.validate_session(ctx.session)
        if self.save_adapter is None:
            raise error("UNSUPPORTED_CAPABILITY", "Save adapter not connected")
        return self.save_adapter(ctx, action)

    def close_owned(self, session, deadline, cancellation):
        row, _ = self._project(session.project_id)
        with execution_lock(self.ledger.root, row["file_id"]):
            metadata = self.validate_session(session)
            if metadata.get('keep_open'):
                raise error('UNSUPPORTED_CAPABILITY', 'User requires this workcopy to remain open')
            if self.ledger.load_pending(session.project_id) or metadata.get("dirty"):
                raise error("PENDING_ACTION", "Save and verify unknown edits before normal close")
            self.desktop.close_window(session.hwnd)
            await_condition(lambda: self.desktop.exists(session.hwnd), lambda exists: not exists, deadline, cancellation)
            metadata["session"] = None
            metadata["business_ready"] = False
            self._metadata(session.project_id, metadata)

    def reopen_owned(self, session, deadline, cancellation):
        self.close_owned(session, deadline, cancellation)
        return self.open_existing_workcopy(session.project_id, deadline, cancellation)
