import hashlib
import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from gccp_foundation.application.native_cli import Catalog, Rejected, execute, reconcile, initialize_commands, digest, main
from gccp_foundation.storage import Ledger
from gccp_foundation.storage.native_save_attempts import NativeSaveAttempts
from gccp_foundation.application.ports import SessionContext


class NativeCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / 'catalog.sqlite'
        c = sqlite3.connect(self.db)
        c.executescript('''CREATE TABLE sources(source_id INTEGER,filename TEXT,sha256 TEXT,status TEXT);
          CREATE TABLE quota_entries(id INTEGER,source_id INTEGER,code TEXT,name TEXT,spec TEXT,unit TEXT,confidence TEXT);
          INSERT INTO sources VALUES(1,'pipe.pdf','source-digest','extracted_unverified');
          INSERT INTO sources VALUES(2,'other.pdf','other-digest','extracted_unverified');
          INSERT INTO quota_entries VALUES(1,1,'3-199','管件','500','个','high');
          INSERT INTO quota_entries VALUES(2,2,'3-199','另一专业','500','个','review_required');''')
        c.close()
        self.state = self.root / 'state'
        s = Ledger(self.state); s.register_project('project.test', 'file.test'); s.close()
        self.worker = self.root / 'worker.py'; self.worker.write_text('pass\n', encoding='utf-8')
        self.request = dict(command_id='test.command', project_id='project.test', entry_id=1,
                            parent_id='parent.test', quantity='1', save=True)
        catalog = Catalog(self.db); entry = catalog.entry(1); catalog.close()
        self.config = dict(channel='native_qt_business', live_candidate_check_required=True,
            worker=str(self.worker), worker_sha256=hashlib.sha256(self.worker.read_bytes()).hexdigest(),
            approved_request=self.request, catalog_identity_digest=entry['identity_digest'],
            expected_live_identity=dict(code='3-199', name='管件500', unit='个'))
        self.runtime = self.root / 'runtime.json'; self.write_runtime()

    def write_runtime(self):
        self.runtime.write_text(json.dumps(self.config), encoding='utf-8')

    def test_search_preserves_same_code_across_sources_and_is_read_only(self):
        before = self.db.read_bytes(); c = Catalog(self.db)
        self.assertEqual(len(c.search(code='3-199')), 2)
        self.assertEqual(c.search(code='3-199', source_id=1, spec='500')[0]['id'], 1)
        self.assertFalse(c.entry(1)['automatic_insertion_approved'])
        with self.assertRaises(sqlite3.OperationalError):
            c.db.execute('DELETE FROM quota_entries')
        c.close(); self.assertEqual(before, self.db.read_bytes())

    def test_unreviewed_request_and_changed_worker_never_dispatch(self):
        with patch('subprocess.run') as call:
            with self.assertRaises(Rejected):
                execute(self.state, self.db, self.runtime, {**self.request, 'quantity':'2'})
            self.worker.write_text('changed\n', encoding='utf-8')
            with self.assertRaises(Rejected):
                execute(self.state, self.db, self.runtime, self.request)
            call.assert_not_called()

    def test_json_output_is_utf8_under_a_non_utf8_console(self):
        result=subprocess.run([sys.executable,'-m','gccp_foundation.application.native_cli',
            'catalog','search','--db',str(self.db),'--code','3-199'],capture_output=True,
            encoding='utf-8',check=True,env={**os.environ,'PYTHONUTF8':'0','PYTHONIOENCODING':'cp936'})
        self.assertEqual(json.loads(result.stdout)['data'][0]['name'],'管件')

    def test_unknown_attempt_survives_timeout_and_cannot_be_replayed(self):
        with patch('subprocess.run', side_effect=subprocess.TimeoutExpired('worker', 600)) as call:
            with self.assertRaises(subprocess.TimeoutExpired):
                execute(self.state, self.db, self.runtime, self.request)
            with self.assertRaises(Rejected) as error:
                execute(self.state, self.db, self.runtime, self.request)
            self.assertEqual(error.exception.code, 'NEEDS_RECONCILIATION')
            self.assertEqual(call.call_count, 1)

    def test_different_command_cannot_bypass_an_unknown_project_dispatch(self):
        with patch('subprocess.run',side_effect=subprocess.TimeoutExpired('worker',600)) as call:
            with self.assertRaises(subprocess.TimeoutExpired):
                execute(self.state,self.db,self.runtime,self.request)
            other={**self.request,'command_id':'second.command'}
            self.config['approved_request']=other;self.write_runtime()
            with self.assertRaises(Rejected) as error:
                execute(self.state,self.db,self.runtime,other)
            self.assertEqual(error.exception.code,'PROJECT_NEEDS_RECONCILIATION')
            self.assertEqual(call.call_count,1)

    def test_invalid_arguments_have_structured_json_errors(self):
        stream=io.StringIO()
        with contextlib.redirect_stdout(stream):
            code=main(['quota-add'])
        self.assertEqual(code,2)
        self.assertEqual(json.loads(stream.getvalue())['error']['code'],'ARGUMENT_INVALID')

    def test_pending_native_save_blocks_new_business_before_worker_invocation(self):
        store=Ledger(self.state)
        gate=NativeSaveAttempts(store)
        gate.begin(SessionContext('project.test','session.test',42,'birth',123,'file.test','profile'),self.root)
        store.close()
        with patch('subprocess.run') as call:
            with self.assertRaises(Rejected) as error:
                execute(self.state,self.db,self.runtime,self.request)
            self.assertEqual(error.exception.code,'PENDING_SAVE')
            call.assert_not_called()

    def test_verified_retry_returns_receipt_without_second_native_dispatch(self):
        result = dict(command_id='test.command',status='native_insert_quantity_saved_readback_verified',
                      quota=['3-199','定','管件500','个','1','0','1'])
        completed = subprocess.CompletedProcess([], 0, stdout=json.dumps(result))
        with patch('subprocess.run', return_value=completed) as call:
            execute(self.state, self.db, self.runtime, self.request)
            retry = execute(self.state, self.db, self.runtime, self.request)
            self.assertEqual(call.call_count, 1)
            self.assertTrue(retry['idempotent_reentry'])
            self.assertEqual(retry['new_business_dispatches'], 0)

    def test_review_required_entry_cannot_dispatch_even_with_manifest(self):
        self.request['entry_id'] = 2; self.config['approved_request'] = self.request
        self.write_runtime()
        with patch('subprocess.run') as call:
            with self.assertRaises(Rejected) as error:
                execute(self.state, self.db, self.runtime, self.request)
            self.assertEqual(error.exception.code, 'CATALOG_REVIEW_REQUIRED')
            call.assert_not_called()

    def test_incomplete_high_confidence_record_is_not_an_execution_permission(self):
        db=sqlite3.connect(self.db)
        db.execute('UPDATE quota_entries SET name=NULL WHERE id=1');db.commit();db.close()
        with patch('subprocess.run') as call:
            with self.assertRaises(Rejected) as error:
                execute(self.state,self.db,self.runtime,self.request)
            self.assertEqual(error.exception.code,'CATALOG_REVIEW_REQUIRED')
            call.assert_not_called()

    def recovery_case(self):
        self.config['native_command_id']='test.native'
        self.config['expected_live_identity']['specialty']='管网'
        self.write_runtime()
        saved=self.root/'saved.GPB7';saved.write_bytes(b'synthetic saved checkpoint')
        sha=hashlib.sha256(saved.read_bytes()).hexdigest()
        capture=dict(status='observed',pid=42,created_filetime='birth',callback_on_app_thread=True,
                     tables=[dict(headers=['synthetic'],rows=[],complete=True)])
        s=Ledger(self.state)
        s.db.execute('UPDATE projects SET file_id=?,metadata=? WHERE project_id=?',('saved',json.dumps(dict(
            workcopy_path=str(saved),session=dict(pid=42,created_filetime='birth'))),'project.test'))
        proof=s.put_evidence('project.test','session.test','native_cli_open_save_readback',dict(
            capture=capture,sha256=sha,saved_file_id='saved',native_save_dispatched=True,
            before_after_models_equal=True,crc_verified=True,stable_file_verified=True))
        result=dict(command_id='test.native',status='native_insert_quantity_saved_readback_verified',
                    job_id='job.synthetic',evidence_id=proof,save_sha256=sha)
        initialize_commands(s)
        s.db.execute('INSERT INTO native_cli_commands VALUES(?,?,?,?,?,?)',(
            'test.command','project.test',digest(self.request),'armed_unknown',json.dumps(result),0))
        s.close()
        job=dict(project_id='project.test',job_key='test.native',job_id='job.synthetic',plan=json.dumps(dict(actions=[
            dict(action_id='insert',kind='ensure_quota',payload=dict(parent_ref='division.parent.test',quota_key=dict(
                code='3-199',name='管件500',unit='个',trade='管网'))),
            dict(action_id='quantity',kind='set_quantity',payload=dict(quantity=dict(value='1')))])))
        return saved,capture,job

    def test_reconcile_checks_fresh_native_state_without_business_redispatch(self):
        _,capture,job=self.recovery_case()
        completed=subprocess.CompletedProcess([],0,stdout=json.dumps(capture))
        with patch.object(Ledger,'job',return_value=job), patch.object(Ledger,'action_state',return_value='readback_verified'), \
                patch('subprocess.run',return_value=completed) as call:
            result=reconcile(self.state,self.db,self.runtime,self.request)
            self.assertTrue(result['readonly_reconciled'])
            self.assertEqual(result['new_business_dispatches'],0)
            self.assertEqual(call.call_args.args[0][-1],'--inspect')

    def test_changed_saved_file_keeps_command_unknown_without_worker_invocation(self):
        saved,_,job=self.recovery_case();saved.write_bytes(b'foreign later changes')
        with patch.object(Ledger,'job',return_value=job), patch.object(Ledger,'action_state',return_value='readback_verified'), \
                patch('subprocess.run') as call:
            with self.assertRaises(Rejected) as error:
                reconcile(self.state,self.db,self.runtime,self.request)
            self.assertEqual(error.exception.code,'SAVED_FILE_CHANGED')
            call.assert_not_called()
        s=Ledger(self.state)
        self.assertEqual(s.db.execute('SELECT phase FROM native_cli_commands').fetchone()[0],'armed_unknown')
        s.close()


if __name__ == '__main__':
    unittest.main()
