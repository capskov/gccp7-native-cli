"""An uncertain native Save remains blocked across process death and file replacement."""
from dataclasses import asdict, replace
import json
from pathlib import Path
import tempfile
import unittest

from gccp_foundation.application.ports import SessionContext
from gccp_foundation.domain.errors import ContractError
from gccp_foundation.storage import Ledger
from gccp_foundation.storage.ledger import encoded
from gccp_foundation.storage.native_save_attempts import NativeSaveAttempts


class NativeSaveJournal(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Ledger(self.root)
        self.session = SessionContext('project.test','session.test',42,'birth',123,'file.before','profile.test')
        self.store.register_project('project.test','file.before',dict(session=asdict(self.session)))
        self.gate = NativeSaveAttempts(self.store)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def checkpoint(self, session=None, sha='a'*64):
        saved = session or replace(self.session,project_file_id='file.saved')
        self.store.db.execute('UPDATE projects SET file_id=?,metadata=? WHERE project_id=?',
            (saved.project_file_id,encoded(dict(session=asdict(saved),native_saves=[dict(saved_sha256=sha)])),
             saved.project_id))
        return saved

    def test_armed_crash_survives_restart_and_cannot_begin_or_arm_again(self):
        attempt = self.gate.begin(self.session,self.root/'attempt')
        self.gate.arm(self.session,attempt)
        self.store.close()
        self.store = Ledger(self.root)
        self.gate = NativeSaveAttempts(self.store)
        self.assertEqual(self.gate.active('project.test')['attempt_id'],attempt)
        with self.assertRaises(ContractError):self.gate.begin(self.session,self.root/'retry')
        with self.assertRaises(ContractError):self.gate.arm(self.session,attempt)
        with self.assertRaises(ContractError):self.gate.abandon_before_dispatch(self.session,attempt,'timeout')
        self.assertEqual(self.gate.active('project.test')['phase'],'dispatch_armed_outcome_unknown')

    def test_readonly_preflight_failure_is_audited_and_next_attempt_can_start(self):
        attempt = self.gate.begin(self.session,self.root/'attempt')
        self.gate.abandon_before_dispatch(self.session,attempt,'catalog unavailable')
        self.assertIsNone(self.gate.active('project.test'))
        row = self.store.db.execute('SELECT * FROM native_save_attempts WHERE attempt_id=?',(attempt,)).fetchone()
        self.assertEqual(row['phase'],'not_dispatched_read_only_failure')
        self.assertEqual(json.loads(row['evidence'])['error'],'catalog unavailable')
        self.assertNotEqual(self.gate.begin(self.session,self.root/'next'),attempt)

    def test_backup_does_not_unlock_before_real_file_identity_reconciliation(self):
        attempt = self.gate.begin(self.session,self.root/'attempt')
        self.gate.arm(self.session,attempt)
        saved = replace(self.session,project_file_id='file.saved')
        with self.assertRaises(ContractError):
            self.gate.finish_checkpoint(self.session,attempt,saved,backup_receipt='receipt',backup_sha256='a'*64)
        self.assertIsNotNone(self.gate.active('project.test'))
        saved = self.checkpoint()
        self.gate.finish_checkpoint(self.session,attempt,saved,backup_receipt='receipt',backup_sha256='a'*64)
        self.assertIsNone(self.gate.active('project.test'))
        row = self.store.db.execute('SELECT * FROM native_save_attempts WHERE attempt_id=?',(attempt,)).fetchone()
        evidence = json.loads(row['evidence'])
        self.assertEqual(evidence['original_phase'],'dispatch_armed_outcome_unknown')
        self.assertFalse(evidence['save_replayed'])
        self.assertFalse(evidence['native_business_persistence_verified'])
        self.assertFalse(evidence['native_reopen_verified'])

    def test_wrong_attempt_foreign_process_or_digest_never_unlocks(self):
        attempt = self.gate.begin(self.session,self.root/'attempt')
        self.gate.arm(self.session,attempt)
        saved = self.checkpoint()
        for original, token, after, sha in [
            (self.session,'wrong',saved,'a'*64),
            (replace(self.session,created_filetime='another'),attempt,saved,'a'*64),
            (self.session,attempt,replace(saved,pid=43),'a'*64),
            (self.session,attempt,saved,'b'*64),
            (self.session,attempt,saved,'not-a-digest')]:
            with self.assertRaises(ContractError):
                self.gate.finish_checkpoint(original,token,after,backup_receipt='receipt',backup_sha256=sha)
        self.assertIsNotNone(self.gate.active('project.test'))

    def test_readonly_recovery_after_binding_was_already_replaced(self):
        attempt = self.gate.begin(self.session,self.root/'attempt')
        self.gate.arm(self.session,attempt)
        saved = self.checkpoint()
        row = self.gate.require_reconciliation(saved,attempt)
        self.assertEqual(row['session'],asdict(self.session))
        with self.assertRaises(ContractError):
            self.gate.require_reconciliation(replace(saved,session_id='another'),attempt)
        self.gate.finish_checkpoint(self.session,attempt,saved,backup_receipt='receipt',
                                   backup_sha256='a'*64,read_only_reconciliation=True)
        evidence = json.loads(self.store.db.execute(
            'SELECT evidence FROM native_save_attempts WHERE attempt_id=?',(attempt,)).fetchone()[0])
        self.assertTrue(evidence['read_only_reconciliation'])
        self.assertFalse(evidence['save_replayed'])

    def test_second_connection_cannot_start_a_parallel_save(self):
        attempt = self.gate.begin(self.session,self.root/'attempt')
        other = Ledger(self.root)
        try:
            gate = NativeSaveAttempts(other)
            with self.assertRaises(ContractError):gate.begin(self.session,self.root/'parallel')
            self.assertEqual(gate.active('project.test')['attempt_id'],attempt)
        finally:
            other.close()

    def test_open_readback_without_copy_requires_complete_evidence_and_records_limits(self):
        attempt=self.gate.begin(self.session,self.root/'no-copy')
        self.gate.arm(self.session,attempt)
        saved=self.checkpoint()
        data=dict(native_save_dispatched=True,before_after_models_equal=True,crc_verified=False,
                  stable_file_verified=True,saved_file_id=saved.project_file_id,sha256='a'*64)
        proof=self.store.put_evidence(saved.project_id,saved.session_id,'native_cli_open_save_readback',data)
        with self.assertRaises(ContractError):
            self.gate.finish_open_readback(self.session,attempt,saved,proof)
        self.assertIsNotNone(self.gate.active(saved.project_id))
        data['crc_verified']=True
        proof=self.store.put_evidence(saved.project_id,saved.session_id,'native_cli_open_save_readback',data)
        self.gate.finish_open_readback(self.session,attempt,saved,proof)
        self.assertIsNone(self.gate.active(saved.project_id))
        record=json.loads(self.store.db.execute('SELECT evidence FROM native_save_attempts WHERE attempt_id=?',
                                                (attempt,)).fetchone()[0])
        self.assertFalse(record['backup_created'])
        self.assertTrue(record['same_process_readback_verified'])
        self.assertFalse(record['native_reopen_verified'])

    def test_open_readback_cannot_unlock_a_different_process(self):
        attempt=self.gate.begin(self.session,self.root/'no-copy')
        self.gate.arm(self.session,attempt)
        saved=self.checkpoint()
        data=dict(native_save_dispatched=True,before_after_models_equal=True,crc_verified=True,
                  stable_file_verified=True,saved_file_id=saved.project_file_id,sha256='a'*64)
        proof=self.store.put_evidence(saved.project_id,saved.session_id,'native_cli_open_save_readback',data)
        with self.assertRaises(ContractError):
            self.gate.finish_open_readback(self.session,attempt,replace(saved,pid=99),proof)
        self.assertIsNotNone(self.gate.active(saved.project_id))


if __name__ == '__main__':unittest.main()
