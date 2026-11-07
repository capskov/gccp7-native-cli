from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from gccp_foundation.application.native_delete import delete_quota, require_confirmation
from gccp_foundation.storage import Ledger


def budget_row(path, code, kind, name, unit, amount):
    values = [code, kind, name, unit, '1', '0', '1', amount if kind == '定' else '', amount, '', '0', '', '']
    return {'row_path_session_only': path, 'values': values}


def capture(*, temporary=False, removed=False, modal=False):
    quota = budget_row('/0/0/0', '3-201', '定', 'Test quota', '个', '0' if temporary else '10')
    rows = [budget_row('/0', '', '', 'Project', '', '0' if temporary or removed else '10'),
            budget_row('/0/0', '', '部', 'Division', '', '0' if temporary or removed else '10')]
    if not removed:
        rows.append(quota)
    target = None if removed else dict(path='/0/0/0', values=quota['values'], strikeout=[temporary]*13)
    dialogs = [dict(
        title='确认', buttons=['', '是', '否'],
        labels=['确认', '', "<p style='line-height:23px;'>确认要删除当前选中行吗？</p>"],
        **{'class': 'GCCPMessageBox'},
    )] if modal else []
    return dict(
        target=target, target_matches=int(not removed), modals=dialogs,
        delete_actions=[dict(name='PreDelete', text='临时删除', enabled=True), dict(name='Delete', text='删除', enabled=True)],
        tables=[dict(headers=['']*4+['工程量表达式'], complete=True, rows=rows,
                     current=dict(row_path_session_only='/0/0/0'))],
    )


class DeleteLifecycleTests(unittest.TestCase):
    def run_delete(self, mode, *observations):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            session = dict(project_id='project.test', session_id='session.test', pid=1,
                           created_filetime='1', hwnd=1, project_file_id='file.test', profile_id='gccp7-bj2025-local-session')
            metadata = dict(session=session, workcopy_path=str(root/'test.GPB7'), dirty=False)
            store = Ledger(root)
            store.register_project('project.test', 'file.test', metadata)
            store.close()
            request = dict(command_id='delete.test', project_id='project.test', parent_id='division.test',
                           code='3-201', name='Test quota', unit='个', mode=mode)
            bridge = Mock(records=[])
            bridge.call.side_effect = observations
            prefix = 'gccp_foundation.application.native_delete.'
            with patch(prefix+'RuntimeProfile.load'), patch(prefix+'WindowsDesktop'), patch(prefix+'SessionManager') as manager, patch(prefix+'build_bridge', return_value=bridge):
                manager.return_value.validate_session.return_value = metadata
                receipt = delete_quota(root, root, request)
                retry = delete_quota(root, root, request)
            self.assertEqual(receipt['status'], 'native_delete_readback_verified_not_saved')
            self.assertEqual(retry['new_business_dispatches'], 0)
            self.assertTrue(retry['receipt_only'])
            return [call.args[0] for call in bridge.call.call_args_list]

    def test_temporary_arms_once_and_uses_one_action(self):
        calls = self.run_delete('temporary', capture(), {'native_delete_queued': True}, capture(temporary=True))
        self.assertEqual(calls, ['inspect', 'quota.delete', 'inspect'])

    def test_permanent_confirms_reviewed_dialog_without_replaying_delete(self):
        calls = self.run_delete('permanent', capture(), {'native_delete_queued': True}, capture(modal=True),
                                {'native_delete_confirmation_clicked': True}, capture(removed=True))
        self.assertEqual(calls, ['inspect', 'quota.delete', 'inspect', 'quota.delete.confirm', 'inspect'])

    def test_different_confirmation_message_does_not_match(self):
        pending = capture(modal=True)
        pending['modals'][0]['labels'][-1] = '是否删除全部？'
        with self.assertRaisesRegex(ValueError, 'Unrecognized deletion confirmation'):
            require_confirmation(capture(), pending)
