"""Intent-before-dispatch ledger. SQLite and immutable evidence fail closed."""
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid

from gccp_foundation.domain.contracts import load_job_plan
from gccp_foundation.domain.errors import (DispatchState, ErrorCategory, ErrorDetail,
                                           FoundationError)
from gccp_foundation.domain.json_values import digest, freeze, loads, thaw


def now():
    return datetime.now(timezone.utc).isoformat()


def encoded(value):
    return json.dumps(thaw(freeze(value)), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def problem(code, message):
    return FoundationError(ErrorDetail(code, ErrorCategory.STORAGE, message))


class Ledger:
    def __init__(self, state_root):
        self.root = Path(state_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "evidence").mkdir(exist_ok=True)
        self.poisoned = False
        self.db = sqlite3.connect(self.root / "ledger.sqlite3", timeout=5, isolation_level=None)
        try:
            self._initialize_database()
        except BaseException:
            self.db.close()
            raise

    def _initialize_database(self):
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS projects(project_id TEXT PRIMARY KEY, file_id TEXT UNIQUE NOT NULL,
            owner TEXT, metadata TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs(job_id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects,
            job_key TEXT NOT NULL, plan_digest TEXT NOT NULL, plan TEXT NOT NULL,
            state TEXT NOT NULL, metadata TEXT NOT NULL, UNIQUE(project_id,job_key));
        CREATE TABLE IF NOT EXISTS actions(job_id TEXT NOT NULL REFERENCES jobs,
            action_id TEXT NOT NULL, line_id TEXT NOT NULL, body TEXT NOT NULL, state TEXT NOT NULL,
            intent_id TEXT UNIQUE, context TEXT, receipt TEXT, PRIMARY KEY(job_id,action_id));
        CREATE TABLE IF NOT EXISTS evidence(artifact_id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects,
            session_id TEXT NOT NULL, sha256 TEXT NOT NULL, kind TEXT NOT NULL, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events(sequence INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT,
            kind TEXT NOT NULL, body TEXT NOT NULL, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS line_bindings(project_id TEXT NOT NULL REFERENCES projects,
            line_id TEXT NOT NULL, quota_key TEXT NOT NULL, binding TEXT NOT NULL,
            PRIMARY KEY(project_id,line_id));
        """)

    def close(self):
        self.db.close()

    @contextmanager
    def _transaction(self):
        if self.poisoned:
            raise problem("JOURNAL_UNAVAILABLE", "Reload ledger after a persistence failure")
        try:
            self.db.execute("BEGIN IMMEDIATE")
            yield
            self.db.execute("COMMIT")
        except BaseException as exc:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            if isinstance(exc, (sqlite3.Error, OSError)):
                self.poisoned = True
            raise

    def _event(self, job, kind, body):
        self.db.execute("INSERT INTO events(job_id,kind,body,created) VALUES(?,?,?,?)",
                        (job, kind, encoded(body), now()))

    def register_project(self, project_id, file_id, metadata=None):
        with self._transaction():
            row = self.db.execute("SELECT * FROM projects WHERE project_id=? OR file_id=?",
                                  (project_id, file_id)).fetchone()
            if row:
                if row["project_id"] != project_id or row["file_id"] != file_id:
                    raise problem("PROJECT_IDENTITY_MISMATCH", "Project/file already registered under another identity")
                return
            self.db.execute("INSERT INTO projects VALUES(?,?,NULL,?)",
                            (project_id, file_id, encoded(metadata or {})))

    def submit(self, plan):
        # Revalidate even when the caller supplies a manually built JobPlan.
        plan = load_job_plan(plan.to_dict())
        with self._transaction():
            row = self.db.execute("SELECT * FROM jobs WHERE project_id=? AND job_key=?",
                                  (plan.project_id, plan.job_key)).fetchone()
            if row:
                if row["plan_digest"] != plan.plan_digest:
                    raise problem("IDEMPOTENCY_CONFLICT", "Existing job key has different execution content")
                return dict(row)
            job = "job." + uuid.uuid4().hex
            self.db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,'queued','{}')",
                            (job, plan.project_id, plan.job_key, plan.plan_digest, encoded(plan.to_dict())))
            for action in plan.actions:
                self.db.execute("INSERT INTO actions VALUES(?,?,?,?, 'planned',NULL,NULL,NULL)",
                                (job, action.action_id, action.line_id, encoded(action.to_dict())))
            self._event(job, "submitted", {"plan_digest": plan.plan_digest})
        return self.job(job)

    def job(self, job):
        row = self.db.execute("SELECT * FROM jobs WHERE job_id=?", (job,)).fetchone()
        if row is None:
            raise problem("UNKNOWN_JOB", "Unknown job")
        return dict(row)

    def claim_project(self, project_id, job_id):
        with self._transaction():
            job = self.job(job_id)
            project = self.db.execute("SELECT * FROM projects WHERE project_id=?", (project_id,)).fetchone()
            if job["project_id"] != project_id or not project:
                raise problem("PROJECT_IDENTITY_MISMATCH", "Job does not own this project")
            if job["state"] in ("completed", "cancelled", "failed"):
                raise problem("TERMINAL_JOB", "Cannot claim project for terminal job")
            if project["owner"] not in (None, job_id):
                raise problem("PROJECT_OWNED", "Unfinished job owns project")
            self.db.execute("UPDATE projects SET owner=? WHERE project_id=?", (job_id, project_id))
            self.db.execute("UPDATE jobs SET state='running' WHERE job_id=? AND state='queued'", (job_id,))

    def _action(self, job, action):
        row = self.db.execute("SELECT * FROM actions WHERE job_id=? AND action_id=?", (job, action)).fetchone()
        if not row:
            raise problem("UNKNOWN_ACTION", "Action is not in submitted plan")
        return row

    def action_state(self, job_id, action_id):
        return self._action(job_id, action_id)["state"]

    def action_observation(self, job_id, action_id):
        row = self._action(job_id, action_id)
        if row["state"] not in ("readback_verified", "persistence_verified") or not row["receipt"]:
            raise problem("DEPENDENCY_UNVERIFIED", "Referenced action has no verified readback")
        receipt = loads(row["receipt"])
        observed = receipt.get("observation")
        if not observed or not observed["complete"] or not observed["evidence_ids"]:
            raise problem("EVIDENCE_MISMATCH", "Referenced action has no complete observation")
        from gccp_foundation.application.ports import Observation
        from gccp_foundation.domain.models import SnapshotRef
        return Observation(SnapshotRef(**observed["snapshot"]), observed["complete"],
                           observed["scope"], observed["data"], tuple(observed["evidence_ids"]))

    def begin_intent(self, job_id, action, ctx, preflight_evidence_ids=()):
        with self._transaction():
            job = self.job(job_id)
            owner = self.db.execute("SELECT owner,file_id FROM projects WHERE project_id=?", (job["project_id"],)).fetchone()
            if owner[0] != job_id or ctx.session.project_id != job["project_id"] or owner[1] != ctx.session.project_file_id:
                raise problem("PROJECT_OWNED", "Claim matching project before dispatch")
            if ctx.cancellation.is_cancelled() or time.monotonic() >= ctx.deadline_monotonic:
                raise problem("DISPATCH_EXPIRED", "Cancelled or expired operation must not dispatch")
            row = self._action(job_id, action.action_id)
            if row["body"] != encoded(action.to_dict()):
                raise problem("ACTION_CHANGED", "Action differs from submitted plan")
            if row["state"] != "planned":
                raise problem("NO_REPLAY", "Intent already exists; reconcile instead of dispatching again")
            if self.load_pending(job["project_id"]):
                raise problem("PENDING_ACTION", "Resolve previous unknown intent before any new dispatch")
            for dep in action.depends_on:
                if self._action(job_id, dep)["state"] not in ("readback_verified", "persistence_verified"):
                    raise problem("DEPENDENCY_UNVERIFIED", "Previous action lacks verified readback")
            if len(preflight_evidence_ids) != len(set(preflight_evidence_ids)):
                raise problem("EVIDENCE_MISMATCH", "Duplicate preflight evidence")
            for artifact in preflight_evidence_ids:
                evidence = self.read_evidence(artifact, job["project_id"])
                if evidence["session_id"] != ctx.session.session_id:
                    raise problem("EVIDENCE_MISMATCH", "Preflight belongs to another session")
            intent = "intent." + uuid.uuid4().hex
            self.db.execute("UPDATE actions SET state='intent_durable',intent_id=?,context=? WHERE job_id=? AND action_id=?",
                            (intent, encoded({"session": asdict(ctx.session), "expected_snapshot": asdict(ctx.expected_snapshot),
                                              "preflight_evidence_ids": list(preflight_evidence_ids)}), job_id, action.action_id))
            self._event(job_id, "intent_durable", {"intent_id": intent, "action_id": action.action_id})
        return intent

    def put_evidence(self, project_id, session_id, kind, data):
        artifact = "artifact." + uuid.uuid4().hex
        body = encoded(dict(project_id=project_id, session_id=session_id, kind=kind, data=data)).encode("utf-8")
        path = self.root / "evidence" / (artifact + ".json")
        temporary = path.with_suffix(".tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            with self._transaction():
                self.db.execute("INSERT INTO evidence VALUES(?,?,?,?,?,?)",
                                (artifact, project_id, session_id, hashlib.sha256(body).hexdigest(), kind, now()))
        except BaseException:
            self.poisoned = True
            raise
        return artifact

    def read_evidence(self, artifact_id, project_id=None):
        row = self.db.execute("SELECT * FROM evidence WHERE artifact_id=?", (artifact_id,)).fetchone()
        if not row or (project_id and row["project_id"] != project_id):
            raise problem("EVIDENCE_MISMATCH", "Unknown or foreign artifact")
        path = self.root / "evidence" / (row["artifact_id"] + ".json")
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() != row["sha256"]:
            raise problem("EVIDENCE_MISMATCH", "Immutable evidence changed")
        data = loads(body.decode("utf-8"))
        if data["project_id"] != row["project_id"] or data["session_id"] != row["session_id"]:
            raise problem("EVIDENCE_MISMATCH", "Artifact binding changed")
        return data

    def append_receipt(self, intent_id, receipt):
        try:
            with self._transaction():
                row = self.db.execute("SELECT a.*,j.project_id FROM actions a JOIN jobs j USING(job_id) WHERE intent_id=?", (intent_id,)).fetchone()
                if not row or row["action_id"] != receipt.action_id:
                    raise problem("UNKNOWN_INTENT", "Receipt is not bound to this intent")
                if row["state"] not in ("intent_durable", "needs_reconciliation"):
                    raise problem("NO_REPLAY", "Action already has a verified receipt")
                context = loads(row["context"])["session"]
                for artifact in receipt.evidence_ids:
                    data = self.read_evidence(artifact, row["project_id"])
                    if data["session_id"] != context["session_id"]:
                        raise problem("EVIDENCE_MISMATCH", "Receipt from another session")
                verified = bool(receipt.observation and receipt.observation.complete and receipt.evidence_ids
                                and receipt.dispatch_state == DispatchState.SENT)
                not_sent = False
                if (receipt.dispatch_state == DispatchState.NONE and receipt.observation
                        and receipt.observation.scope == 'resource_spec_guard_not_sent'):
                    if (receipt.observation.complete or len(receipt.evidence_ids)!=1
                            or tuple(receipt.observation.evidence_ids)!=tuple(receipt.evidence_ids)):
                        raise problem('EVIDENCE_MISMATCH','Explicit incomplete not-sent receipt required')
                    refused=self.read_evidence(receipt.evidence_ids[0],row['project_id'])
                    data=refused['data'];proof=data.get('proof',{})
                    durable=loads(row['context'])
                    ref=data.get('preflight_evidence_id')
                    if (refused['kind']!='native_parent_resource_not_sent'
                            or loads(row['body'])['kind']!='edit_resource'
                            or data.get('job_id')!=row['job_id'] or data.get('action_id')!=row['action_id']
                            or durable.get('preflight_evidence_ids')!=[ref]
                            or proof.get('field_dispatch_attempted') is not False
                            or proof.get('session')!=context
                            or proof.get('reason',{}).get('code')!='RESOURCE_SPEC_NOT_SENT'
                            or asdict(receipt.observation.snapshot)!=durable['expected_snapshot']):
                        raise problem('EVIDENCE_MISMATCH','Not-sent evidence differs from owned resource intent')
                    preflight=self.read_evidence(ref,row['project_id'])
                    if (preflight['kind']!='native_parent_resource_preflight'
                            or preflight['session_id']!=context['session_id']
                            or proof.get('record_sha256')!=digest(preflight['data']['record'])):
                        raise problem('EVIDENCE_MISMATCH','Not-sent proof lacks exact original material record')
                    not_sent=True
                if verified:
                    if not set(receipt.observation.evidence_ids) <= set(receipt.evidence_ids) or not receipt.observation.evidence_ids:
                        raise problem("EVIDENCE_MISMATCH", "Observation lacks bound evidence")
                    state = "readback_verified"
                elif not_sent:
                    state='failed'
                else:
                    state = "needs_reconciliation"
                body = dict(action_id=receipt.action_id, dispatch_state=str(receipt.dispatch_state),
                            evidence_ids=list(receipt.evidence_ids), observation=None if receipt.observation is None else
                            dict(snapshot=asdict(receipt.observation.snapshot), complete=receipt.observation.complete,
                                 scope=receipt.observation.scope, data=thaw(receipt.observation.data), evidence_ids=list(receipt.observation.evidence_ids)))
                self.db.execute("UPDATE actions SET state=?,receipt=? WHERE intent_id=?", (state, encoded(body), intent_id))
                self.db.execute("UPDATE jobs SET state=? WHERE job_id=?", ("running" if verified else "failed" if not_sent else "needs_reconciliation", row["job_id"]))
                self._event(row["job_id"], state, {"intent_id": intent_id})
        except (OSError, sqlite3.Error):
            self.poisoned = True
            raise

    def load_pending(self, project_id):
        return tuple(dict(row) for row in self.db.execute("""SELECT a.* FROM actions a JOIN jobs j USING(job_id)
            WHERE j.project_id=? AND a.state IN ('intent_durable','needs_reconciliation')""", (project_id,)))

    def reconcile_receipt(self, intent_id, receipt, ctx):
        """Record a fresh session's verified readback, never authorize redispatch."""
        if receipt.dispatch_state == DispatchState.UNKNOWN or not receipt.observation or not receipt.observation.complete:
            raise problem("PENDING_ACTION", "Reconciliation needs verified readback")
        with self._transaction():
            row = self.db.execute("SELECT a.*,j.project_id,p.file_id,p.owner FROM actions a JOIN jobs j USING(job_id) JOIN projects p USING(project_id) WHERE intent_id=?", (intent_id,)).fetchone()
            if not row or row["state"] not in ("intent_durable", "needs_reconciliation") or row["owner"] != row["job_id"]:
                raise problem("UNKNOWN_INTENT", "Pending owned intent required")
            if ctx.session.project_id != row["project_id"] or ctx.session.project_file_id != row["file_id"]:
                raise problem("PROJECT_IDENTITY_MISMATCH", "Reconciliation cannot switch project")
            previous = loads(row["context"])
            self.db.execute("UPDATE actions SET context=? WHERE intent_id=?", (encoded({"session": asdict(ctx.session), "expected_snapshot": asdict(ctx.expected_snapshot),
                                                                                       "preflight_evidence_ids": previous.get("preflight_evidence_ids", [])}), intent_id))
            self._event(row["job_id"], "reconciliation_session_rebound_no_dispatch", {"intent_id": intent_id, "session_id": ctx.session.session_id})
        self.append_receipt(intent_id, receipt)

    def finish_reconciled_native_insert(self, job_id, action_id, session, marker, snapshot):
        """Clear only the helper marker proved by the recorded insertion readback."""
        return self._finish_reconciled_marker(job_id, action_id, session, marker, snapshot,
            "ensure_quota", "quota_insert_readonly_reconciled", "pending_native_insert",
            "native_insert_marker_reconciled_no_dispatch")

    def finish_reconciled_quantity_cell(self, job_id, action_id, session, marker, snapshot):
        """Clear one quantity helper marker after its exact recorded readback."""
        return self._finish_reconciled_marker(job_id, action_id, session, marker, snapshot,
            "set_quantity", "quantity_and_amount_readonly_reconciled", "pending_cell_action",
            "quantity_cell_marker_reconciled_no_dispatch")

    def _finish_reconciled_marker(self, job_id, action_id, session, marker, snapshot,
                                  kind, scope, marker_key, event):
        with self._transaction():
            job = self.job(job_id)
            action = self._action(job_id, action_id)
            project = self.db.execute("SELECT * FROM projects WHERE project_id=?",
                                      (job["project_id"],)).fetchone()
            observed = self.action_observation(job_id, action_id)
            if (project["owner"] != job_id or session.project_id != job["project_id"]
                    or session.project_file_id != project["file_id"]
                    or loads(action["body"])["kind"] != kind
                    or observed.scope != scope
                    or observed.snapshot != snapshot or self.load_pending(job["project_id"])):
                raise problem("EVIDENCE_MISMATCH", "Current matching verified readback required")
            metadata = loads(project["metadata"])
            if metadata.get("session") != asdict(session):
                raise problem("PROJECT_IDENTITY_MISMATCH", "Native marker belongs to another session")
            if metadata.get(marker_key) is None and not metadata.get("dirty"):
                return False
            if metadata.get(marker_key) != marker or metadata.get("dirty") is not True:
                raise problem("EVIDENCE_MISMATCH", "Native marker changed")
            metadata[marker_key] = None
            metadata["dirty"] = False
            self.db.execute("UPDATE projects SET metadata=? WHERE project_id=?",
                            (encoded(metadata), job["project_id"]))
            self._event(job_id, event,
                        dict(action_id=action_id, marker=marker,
                             evidence_ids=list(observed.evidence_ids)))
            return True

    def checkpoint(self, job_id, state):
        # Metadata cannot clear pending actions or fabricate lifecycle completion.
        with self._transaction():
            self.job(job_id)
            self.db.execute("UPDATE jobs SET metadata=? WHERE job_id=?", (encoded(state), job_id))
            self._event(job_id, "checkpoint_metadata", state)

    def record_open_save_checkpoint(self, job_id, session, artifact_id, snapshot):
        """Record an independently read saved file, without claiming reopen persistence."""
        from gccp_foundation.domain.models import SnapshotRef
        if not isinstance(snapshot, SnapshotRef):
            raise problem("EVIDENCE_MISMATCH", "Complete budget snapshot reference required")
        with self._transaction():
            job = self.job(job_id)
            project = self.db.execute("SELECT file_id,owner FROM projects WHERE project_id=?",
                                      (job["project_id"],)).fetchone()
            if (job["state"] != "running" or not project or project["owner"] != job_id
                    or session.project_id != job["project_id"]
                    or session.project_file_id != project["file_id"]
                    or self.load_pending(job["project_id"])):
                raise problem("PROJECT_OWNED", "Only the current owned, reconciled job may record Save")
            plan = load_job_plan(loads(job["plan"]))
            states = [self._action(job_id, action_id)["state"] for action_id in plan.execution_order]
            if not states or any(state != "readback_verified" for state in states):
                raise problem("DEPENDENCY_UNVERIFIED", "All job actions need verified readback")
            final = self.action_observation(job_id, plan.execution_order[-1]).snapshot
            if final != snapshot:
                raise problem("EVIDENCE_MISMATCH", "Saved budget differs from final job readback")
            evidence = self.read_evidence(artifact_id, job["project_id"])
            data = evidence["data"]
            if (evidence["session_id"] != session.session_id
                    or evidence["kind"] != "open_native_save_checkpoint"
                    or data.get("native_save_dispatched") is not True
                    or data.get("nas_upload_verified") is not True
                    or data.get("saved_file_id") != session.project_file_id
                    or data.get("budget_snapshot") != asdict(snapshot)
                    or not isinstance(data.get("saved_sha256"), str)
                    or len(data["saved_sha256"]) != 64):
                raise problem("EVIDENCE_MISMATCH", "Native Save/NAS evidence is incomplete")
            metadata = loads(job["metadata"])
            previous = metadata.get("open_save_checkpoint")
            if previous:
                if previous["artifact_id"] == artifact_id:
                    return previous
                raise problem("NO_REPLAY", "Job already has an open-save checkpoint")
            record = dict(artifact_id=artifact_id, saved_file_id=session.project_file_id,
                          saved_sha256=data["saved_sha256"], budget_snapshot=asdict(snapshot),
                          native_reopen_verified=False)
            metadata["open_save_checkpoint"] = record
            self.db.execute("UPDATE jobs SET metadata=? WHERE job_id=?", (encoded(metadata), job_id))
            self._event(job_id, "open_native_save_checkpoint_unverified_reopen", record)
        return record

    def cancel(self, job_id):
        with self._transaction():
            job = self.job(job_id)
            if job["state"] == "cancelled":
                return
            if job["state"] == "completed":
                raise problem("TERMINAL_JOB", "Completed job cannot be cancelled")
            if self.load_pending(job["project_id"]):
                raise problem("PENDING_ACTION", "Read back unknown effects before cancellation")
            self.db.execute("UPDATE jobs SET state='cancelled' WHERE job_id=?", (job_id,))
            self.db.execute("UPDATE actions SET state='cancelled' WHERE job_id=? AND state='planned'", (job_id,))
            self.db.execute("UPDATE projects SET owner=NULL WHERE project_id=? AND owner=?", (job["project_id"], job_id))
            self._event(job_id, "cancelled_at_safe_boundary", {})

    def bind_line(self, project_id, line_id, quota_key, binding):
        with self._transaction():
            row = self.db.execute("SELECT quota_key FROM line_bindings WHERE project_id=? AND line_id=?", (project_id, line_id)).fetchone()
            if row and row[0] != encoded(quota_key):
                raise problem("LINE_IDENTITY_CONFLICT", "Existing line cannot silently change quota")
            self.db.execute("INSERT INTO line_bindings VALUES(?,?,?,?) ON CONFLICT(project_id,line_id) DO UPDATE SET binding=excluded.binding",
                            (project_id, line_id, encoded(quota_key), encoded(binding)))

    def line_binding(self, project_id, line_id):
        row = self.db.execute("SELECT quota_key,binding FROM line_bindings WHERE project_id=? AND line_id=?",
                              (project_id, line_id)).fetchone()
        if row is None:
            raise problem("LINE_BINDING_MISSING", "Existing business line has no reviewed native binding")
        return dict(quota_key=loads(row["quota_key"]), binding=loads(row["binding"]))

    def adopt_legacy(self, project_id, source):
        source = Path(source)
        raw = source.read_bytes()
        data = loads(raw.decode("utf-8-sig"))
        artifact = self.put_evidence(project_id, "historical", "historical_adoption",
                                     {"source": str(source.resolve()), "sha256": hashlib.sha256(raw).hexdigest(), "legacy": data})
        with self._transaction():
            self._event(None, "historical_adoption_not_new_execution", {"artifact": artifact})
        return artifact
