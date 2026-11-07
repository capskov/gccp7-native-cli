"""Durable Save admission: an armed attempt survives client crashes, without replay.

This journal supplements business pending intents; it never clears them or
certifies native business persistence. Callers still validate the real session,
hold execution_lock, observe completion and reconcile a verified disk backup.
"""
from dataclasses import asdict
import json
import re
import time
import uuid

from gccp_foundation.domain.errors import ContractError
from gccp_foundation.storage.ledger import encoded


class NativeSaveAttempts:
    def __init__(self, ledger):
        self.ledger = ledger
        with ledger._transaction():
            ledger.db.execute("""CREATE TABLE IF NOT EXISTS native_save_attempts(
                attempt_id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects,
                session TEXT NOT NULL, action_dir TEXT NOT NULL, phase TEXT NOT NULL,
                resolved INTEGER NOT NULL DEFAULT 0, evidence TEXT NOT NULL,
                created_ms INTEGER NOT NULL, updated_ms INTEGER NOT NULL)""")
            ledger.db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS one_unresolved_native_save
                ON native_save_attempts(project_id) WHERE resolved=0""")

    def active(self, project_id):
        row = self.ledger.db.execute(
            "SELECT * FROM native_save_attempts WHERE project_id=? AND resolved=0",
            (project_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        for field in ("session", "evidence"):
            result[field] = json.loads(result[field])
        return result

    def begin(self, session, action_dir):
        with self.ledger._transaction():
            if self.active(session.project_id):
                raise ContractError("Unresolved native Save attempt; inspect and reconcile, never replay")
            attempt = uuid.uuid4().hex
            now = int(time.time() * 1000)
            self.ledger.db.execute("INSERT INTO native_save_attempts VALUES(?,?,?,?,?,0,?,?,?)",
                (attempt, session.project_id, encoded(asdict(session)), str(action_dir),
                 "prepared_read_only", encoded({}), now, now))
            return attempt

    def _require(self, session, attempt_id):
        row = self.active(session.project_id)
        if (row is None or row["attempt_id"] != attempt_id
                or row["session"] != asdict(session)):
            raise ContractError("Exact unresolved Save attempt and original session required")
        return row

    def require_reconciliation(self, session, attempt_id):
        """Read-only precheck before independent saved-file reconciliation."""
        row = self.active(session.project_id)
        if (row is None or row['attempt_id'] != attempt_id
                or any(row['session'].get(k) != getattr(session, k) for k in
                       ('project_id', 'session_id', 'pid', 'created_filetime', 'hwnd', 'profile_id'))):
            raise ContractError("Exact Save attempt and same original process session required")
        return row

    def arm(self, session, attempt_id):
        """Commit before the sole native invocation; no caller may arm twice."""
        with self.ledger._transaction():
            row = self._require(session, attempt_id)
            if row["phase"] != "prepared_read_only":
                raise ContractError("Save dispatch already armed; no replay")
            self.ledger.db.execute(
                "UPDATE native_save_attempts SET phase=?,updated_ms=? WHERE attempt_id=?",
                ("dispatch_armed_outcome_unknown", int(time.time()*1000), attempt_id))

    def abandon_before_dispatch(self, session, attempt_id, error):
        """Only failures preceding arm have positive no-dispatch evidence."""
        with self.ledger._transaction():
            row = self._require(session, attempt_id)
            if row["phase"] != "prepared_read_only":
                raise ContractError("Armed Save remains unresolved; no replay")
            self.ledger.db.execute("""UPDATE native_save_attempts SET phase=?,resolved=1,
                evidence=?,updated_ms=? WHERE attempt_id=?""",
                ("not_dispatched_read_only_failure", encoded(dict(error=str(error))),
                 int(time.time()*1000), attempt_id))

    def finish_checkpoint(self, original_session, attempt_id, saved_session, *,
                          backup_receipt, backup_sha256, read_only_reconciliation=False):
        """Record evidence after the caller's actual SessionManager reconciliation.

        This does not infer that an unknown Save succeeded. A read-only recovery
        accepts the independently verified current disk checkpoint, without
        dispatching anything. The original attempt and unknown phase are kept.
        """
        if (not isinstance(backup_sha256, str)
                or not re.fullmatch(r"[0-9a-f]{64}", backup_sha256)
                or not str(backup_receipt).strip()
                or type(read_only_reconciliation) is not bool
                or any(getattr(original_session, k) != getattr(saved_session, k)
                       for k in ("project_id", "session_id", "pid", "created_filetime", "hwnd", "profile_id"))):
            raise ContractError("Same-process saved session and verified checkpoint evidence required")
        with self.ledger._transaction():
            row = self._require(original_session, attempt_id)
            project = self.ledger.db.execute("SELECT metadata,file_id FROM projects WHERE project_id=?",
                                             (original_session.project_id,)).fetchone()
            meta = json.loads(project["metadata"])
            if (meta.get("session") != asdict(saved_session)
                    or project["file_id"] != saved_session.project_file_id
                    or not meta.get("native_saves")
                    or meta["native_saves"][-1].get("saved_sha256") != backup_sha256):
                raise ContractError("Real saved-file identity reconciliation required before checkpoint")
            evidence = dict(backup_receipt=str(backup_receipt), sha256=backup_sha256,
                saved_session=asdict(saved_session), original_phase=row["phase"],
                read_only_reconciliation=read_only_reconciliation, save_replayed=False,
                native_business_persistence_verified=False, native_reopen_verified=False)
            self.ledger.db.execute("""UPDATE native_save_attempts SET phase=?,resolved=1,
                evidence=?,updated_ms=? WHERE attempt_id=?""",
                ("disk_checkpoint_reconciled_no_replay", encoded(evidence),
                 int(time.time()*1000), attempt_id))

    def finish_open_readback(self, original_session, attempt_id, saved_session, artifact_id):
        """Resolve an observed native Save without creating a forbidden project copy."""
        evidence = self.ledger.read_evidence(artifact_id, original_session.project_id)
        data = evidence['data']
        if (evidence['kind'] != 'native_cli_open_save_readback'
                or evidence['session_id'] != saved_session.session_id
                or data.get('native_save_dispatched') is not True
                or data.get('before_after_models_equal') is not True
                or data.get('crc_verified') is not True
                or data.get('stable_file_verified') is not True
                or data.get('saved_file_id') != saved_session.project_file_id
                or not re.fullmatch(r'[0-9a-f]{64}', data.get('sha256', ''))
                or any(getattr(original_session, k) != getattr(saved_session, k) for k in
                       ('project_id', 'session_id', 'pid', 'created_filetime', 'hwnd', 'profile_id'))):
            raise ContractError('Same-process native Save readback evidence required')
        with self.ledger._transaction():
            row = self._require(original_session, attempt_id)
            project = self.ledger.db.execute('SELECT metadata,file_id FROM projects WHERE project_id=?',
                                             (original_session.project_id,)).fetchone()
            meta = json.loads(project['metadata'])
            if (row['phase'] != 'dispatch_armed_outcome_unknown'
                    or meta.get('session') != asdict(saved_session)
                    or project['file_id'] != saved_session.project_file_id
                    or not meta.get('native_saves')
                    or meta['native_saves'][-1]['saved_sha256'] != data['sha256']):
                raise ContractError('Verified native saved identity required')
            self.ledger.db.execute('UPDATE native_save_attempts SET phase=?,resolved=1,evidence=?,updated_ms=? WHERE attempt_id=?',
                ('open_readback_verified_not_reopened', encoded(dict(artifact_id=artifact_id,
                    sha256=data['sha256'], backup_created=False, same_process_readback_verified=True,
                    native_business_persistence_verified=False, native_reopen_verified=False)),
                 int(time.time()*1000), attempt_id))
