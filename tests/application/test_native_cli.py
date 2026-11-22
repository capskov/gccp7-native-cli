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
from unittest.mock import Mock, patch

from gccp_foundation.application.native_cli import (
    Catalog, Rejected, command_resource_evidence, execute, reconcile, initialize_commands, digest, main,
)
from gccp_foundation.application.native_bootstrap import price_only_budget_delta
from gccp_foundation.application.native_delete import verified_delta
from gccp_foundation.application.native_supplement import prepare_form
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

    def test_supplement_opens_once_then_reads_original_form(self):
        budget = dict(headers=[''] * 4 + ['工程量表达式'], complete=True, rows=[{'values': ['2-2']}],
                      current={'values': ['2-2']})
        closed = dict(tables=[budget], other_modals=0, supplement_dialogs=0)
        opened = dict(closed, supplement_dialogs=1)
        bridge = Mock()
        bridge.call.side_effect = [closed, dict(supplement_open_queued=True, business_dispatches=0), closed, opened]
        self.assertEqual(prepare_form(bridge, budget, check_only=True), (opened, True))
        self.assertEqual([call.args[0] for call in bridge.call.call_args_list],
                         ['inspect', 'supplement.open', 'inspect', 'inspect'])

    def test_replaced_workcopy_reports_rebind_without_retry(self):
        from gccp_foundation.domain.errors import FoundationError
        from gccp_foundation.execution.session import SessionManager

        ledger = Mock()
        ledger.db.execute.return_value.fetchone.return_value = {
            'file_id': 'volume:old', 'metadata': json.dumps(
                dict(workcopy_path=str(self.root / 'sample.GPB7'), owned_workcopy=True))
        }
        with patch('gccp_foundation.execution.session.file_identity', return_value='volume:new'):
            with self.assertRaises(FoundationError) as blocked:
                SessionManager(ledger, None, None)._project('project.test')
        error = blocked.exception.detail.to_dict()
        self.assertEqual(error['code'], 'WORKCOPY_FILE_REPLACED')
        self.assertEqual(error['recovery'], 'rebind')
        self.assertFalse(error['retry_allowed'])

    def test_query_wait_only_observes_until_control_is_ready(self):
        from gccp_foundation.application.native_lookup import wait_for_query

        bridge = Mock()
        ready = dict(query_search_ready=True)
        bridge.call.side_effect = [dict(query_search_ready=False), ready]
        with patch('gccp_foundation.application.native_lookup.time.sleep'):
            result = wait_for_query(bridge, lambda s: s['query_search_ready'], 'TEST', 'Not ready')
        self.assertEqual(result, ready)
        self.assertEqual([call.args[0] for call in bridge.call.call_args_list], ['probe', 'probe'])

    def test_price_rebind_rejects_quantity_and_conversion_changes(self):
        headers = ['编码', '类别', '工程量', '单价', '合价', '全费合价', '单位指标']
        before = dict(headers=headers, rows=[dict(row_path_session_only='/0', depth=0,
                      values=['11-34 R*1.15,J*1.15', '换', '50', '10', '500', '', ''])])
        after = json.loads(json.dumps(before))
        after['rows'][0]['values'][3:5] = ['20', '1000']
        self.assertEqual(price_only_budget_delta(before, after), 1)
        for index, value in ((1, '定'), (2, '51')):
            with self.subTest(index=index):
                changed = json.loads(json.dumps(after))
                changed['rows'][0]['values'][index] = value
                with self.assertRaises(Rejected) as error:
                    price_only_budget_delta(before, changed)
                self.assertEqual(error.exception.code, 'PRICE_BASELINE_SCOPE_CHANGED')

    def test_nested_delete_verifies_all_ancestors_and_child_shift(self):
        def row(path, amount):
            values = [''] * 13
            values[8] = amount
            return dict(row_path_session_only=path, depth=path.count('/') - 1, values=values)

        def capture(rows, **fields):
            return dict(tables=[dict(headers=['编码', '类别', '名称', '单位', '工程量表达式'],
                                     rows=rows, complete=True)], **fields)

        ancestors = ['/0', '/0/0', '/0/0/2']
        target = row('/0/0/2/0', '20')
        before = capture([*(row(p, '30') for p in ancestors), target, row('/0/0/2/0/0', '4'),
                          row('/0/0/2/1', '10')],
                         target=dict(path=target['row_path_session_only'], values=target['values']))
        after = capture([*(row(p, '10') for p in ancestors), row('/0/0/2/0', '10')], target_matches=0)
        self.assertEqual(list(verified_delta(before, after, 'permanent')), ancestors)
        after['tables'][0]['rows'][1]['values'][8] = '11'
        with self.assertRaises(ValueError):
            verified_delta(before, after, 'permanent')

    def test_resource_receipt_requires_exact_archived_budget_owner(self):
        quota = ['3-31', '定', '球墨铸铁管铺设', 'm', '100', '0', '100']
        path = '/0/0/2/4'
        resources = dict(headers=['编码', '类别', '名称', '规格及型号', '单位'],
                         rows=[dict(values=['1711000910', '主', '球墨铸铁管', 'φ500', 'm'])], complete=True)
        capture = dict(tables=[
            dict(headers=['编码', '类别', '名称', '单位', '工程量表达式'], complete=True,
                 current=dict(verified=True, row_path_session_only=path, values=quota)),
            resources,
        ])
        ledger = Ledger(self.state)
        try:
            ref = ledger.put_evidence('project.test', 'session.test',
                                      'new_quota_quantity_and_resources_verified', dict(capture=capture))
            result = dict(evidence_id=ref, target_path=path, quota=quota, resource_rows=1)
            command = dict(phase='readback_verified', project_id='project.test', result=result)
            self.assertEqual(command_resource_evidence(ledger, command)['rows'], resources['rows'])
            with self.assertRaises(Rejected) as error:
                command_resource_evidence(ledger, dict(command, result=dict(result, target_path='/0/0/2/3')))
            self.assertEqual(error.exception.code, 'RESOURCE_EVIDENCE_MISMATCH')
        finally:
            ledger.close()

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

    def test_section_path_write_is_blocked_even_for_an_existing_prepared_runtime(self):
        request = dict(self.request, parent_sections=['顶管工程', '顶进与注浆'])
        self.config['approved_request'] = request
        self.write_runtime()
        with patch('subprocess.run') as call, self.assertRaises(Rejected) as error:
            execute(self.state, self.db, self.runtime, request)
        self.assertEqual(error.exception.code, 'SECTION_PATH_WRITE_SUSPENDED')
        call.assert_not_called()

    def test_section_path_quota_run_does_not_prepare_or_contact_application(self):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream), patch(
            'gccp_foundation.application.native_runtime.prepare_entry'
        ) as prepare:
            code = main(['quota-run', '--state', str(self.state), '--inspector', 'inspector.json',
                         '--runtime-root', str(self.root), '--project-id', 'project.test',
                         '--parent-section', '顶管工程', '--parent-section', '顶进与注浆',
                         '--quantity', '50', '--command-id', 'test.section'])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(stream.getvalue())['error']['code'], 'SECTION_PATH_WRITE_SUSPENDED')
        prepare.assert_not_called()

    def test_section_path_defaults_to_no_save_for_prepare_and_run(self):
        manifest = self.root / 'runtime.json'
        (self.root / 'preparation.json').write_text('{}', encoding='utf-8')
        request = {'save': False}
        for operation in ('runtime-prepare', 'quota-run'):
            with self.subTest(operation=operation), contextlib.redirect_stdout(io.StringIO()), patch(
                'gccp_foundation.application.native_runtime.prepare_entry',
                return_value=(manifest, self.root / 'request.json', request),
            ) as prepare, patch('gccp_foundation.application.native_cli.execute', return_value={}) as run:
                code = main([operation, '--state', str(self.state), '--inspector', 'inspector.json',
                             '--runtime-root', str(self.root), '--project-id', 'project.test',
                             '--parent-section', '顶管工程', '--parent-section', '顶进与注浆',
                             '--selected-anchor', '--quantity', '50', '--command-id', 'test.section'])
                self.assertEqual(code, 0)
                self.assertIs(prepare.call_args.kwargs['save'], False)
                if operation == 'quota-run':
                    run.assert_called_once_with(self.state, None, manifest, request)
                else:
                    run.assert_not_called()

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

    def test_no_save_requires_readback_receipt_and_zero_save_dispatches(self):
        self.request['save'] = False
        self.write_runtime()
        result = dict(command_id='test.command', status='native_insert_quantity_readback_verified_not_saved',
                      quota=['3-199','定','管件500','个','1','0','1'], save_dispatches=0)
        with patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, stdout=json.dumps(result))) as call:
            self.assertEqual(execute(self.state, self.db, self.runtime, self.request)['save_dispatches'], 0)
            self.assertTrue(execute(self.state, self.db, self.runtime, self.request)['receipt_only'])
            call.assert_called_once()

    def test_candidate_lookup_from_sqlite_preserves_all_live_same_code_candidates(self):
        stream = io.StringIO()
        candidates = [dict(code='3-199', name='管件500', unit='个', specialty='管网'),
                      dict(code='3-199', name='另一专业', unit='个', specialty='轨道')]
        with contextlib.redirect_stdout(stream), patch(
            'gccp_foundation.application.native_lookup.lookup', return_value=dict(candidates=candidates)
        ) as lookup:
            code = main(['quota-candidates', '--state', str(self.state), '--runtime-root', str(self.root),
                         '--project-id', 'project.test', '--db', str(self.db), '--entry-id', '1'])
        self.assertEqual(code, 0)
        lookup.assert_called_once_with(self.state, self.root, 'project.test', '3-199')
        data = json.loads(stream.getvalue())['data']
        self.assertEqual(data['catalog_entry']['id'], 1)
        self.assertTrue(data['candidate_selection_required'])
        self.assertEqual(data['candidates'], candidates)

    def test_exact_live_candidate_can_dispatch_without_a_catalog(self):
        identity = dict(code='S-42', name='合成钢管安装 DN80', unit='m', specialty='安装')
        request = dict(self.request, entry_id=None, candidate_code=identity['code'], candidate_unit=identity['unit'],
                       candidate_name=identity['name'], candidate_specialty=identity['specialty'])
        self.config.update(approved_request=request, catalog_match='live_exact_candidate',
                           catalog_identity_digest=None, expected_live_identity=identity)
        self.write_runtime()
        receipt = dict(command_id=request['command_id'], status='native_insert_quantity_saved_readback_verified',
                       quota=['S-42','定',identity['name'],'m','1','0','1'])
        with patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, stdout=json.dumps(receipt))) as call:
            result = execute(self.state, None, self.runtime, request)
            self.assertIsNone(result['catalog_entry_id'])
            call.assert_called_once()

    def test_conflicting_exact_identity_stops_before_dispatch(self):
        request = dict(self.request, entry_id=None, candidate_code='S-42', candidate_unit='m',
                       candidate_name='合成钢管安装 DN80', candidate_specialty='安装')
        self.config.update(approved_request=request, catalog_match='live_exact_candidate',
                           catalog_identity_digest=None,
                           expected_live_identity=dict(code='S-42', name=request['candidate_name'], unit='m', specialty='管网'))
        self.write_runtime()
        with patch('subprocess.run') as call, self.assertRaises(Rejected):
            execute(self.state, None, self.runtime, request)
        call.assert_not_called()

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
