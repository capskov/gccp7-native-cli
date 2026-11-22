import ast
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gccp_foundation.application.native_cli import Rejected
from gccp_foundation.application.native_build import TEMPLATES
from gccp_foundation.application.native_runtime import (
    bounded_anchor_route, displayed_quantity, find_section_path, is_blank_quota, normalize_quantity, reviewed_family, render,
    resolve_vendor_identity,
)
from gccp_foundation.application.vendor_catalog import LIBRARY, match_live_entry


class ScopedRuntimeTests(unittest.TestCase):
    def test_zero_is_only_allowed_for_explicit_content_edit(self):
        self.assertEqual(normalize_quantity('0.0', allow_zero=True), '0')
        with self.assertRaises(Rejected):
            normalize_quantity('0')
        with self.assertRaises(Rejected):
            normalize_quantity('-0.1', allow_zero=True)

    def test_vendor_identity_defaults_to_bundled_export_and_checks_live_unit(self):
        candidate = dict(code='4-102', name='型钢格栅 竖井', specialty='轨道', unit='t')
        live = dict(library=LIBRARY, candidates=[candidate], runtime='lookup.json')
        metadata = dict(library=LIBRARY, source='bundled', index_sha256='a'*64, trades_sha256='b'*64)
        entries = [dict(vendor_id=10413, code=candidate['code'], name=candidate['name'],
                        specialty=candidate['specialty'])]
        with patch('gccp_foundation.application.vendor_catalog.bundled_index',
                   return_value=(metadata, entries)), \
                patch('gccp_foundation.application.vendor_catalog.installed_index') as installed, \
                patch('gccp_foundation.application.native_lookup.lookup', return_value=live) as lookup:
            vendor, unit, runtime = resolve_vendor_identity(None, None, 'p', None, 10413, 't', None)
            self.assertEqual((vendor['source'], unit, runtime), ('bundled', 't', 'lookup.json'))
            lookup.assert_called_once_with(None, None, 'p', '4-102')
            approved = dict(vendor_entry=vendor, candidate_unit=unit)
            self.assertEqual(resolve_vendor_identity(None, None, 'p', None, 10413, 't', approved),
                             (vendor, 't', None))
            self.assertEqual(lookup.call_count, 1)
            installed.assert_not_called()

    def test_vendor_identity_rejects_duplicate_or_wrong_library_and_reuses_pinned_candidate(self):
        root = Path('reviewed-vendor-library').resolve()
        vendor = dict(vendor_id=10411, library='北京2025', library_root=str(root),
                      code='4-100', name='钢筋格栅 竖井', specialty='轨道')
        candidate = dict(code='4-100', name='钢筋格栅 竖井', specialty='轨道', unit='t')
        live = dict(library='北京2025', candidates=[dict(candidate, specialty='管网'), candidate])
        self.assertEqual(match_live_entry(vendor, live), candidate)
        for mismatch in (dict(live, library='另一套定额'), dict(live, candidates=[candidate, candidate])):
            with self.assertRaises(Rejected):
                match_live_entry(vendor, mismatch)
        with patch('gccp_foundation.application.native_lookup.lookup') as lookup:
            approved = dict(vendor_entry=vendor, candidate_unit='t')
            self.assertEqual(resolve_vendor_identity(None, None, 'p', root, 10411, 't', approved),
                             (vendor, 't', None))
            with self.assertRaises(Rejected):
                resolve_vendor_identity(None, None, 'p', root, 10411, 'm3', approved)
            with self.assertRaises(Rejected):
                resolve_vendor_identity(None, None, 'p', root, 10412, 't', approved)
            lookup.assert_not_called()

    def entry(self, **changes):
        return dict(dict(code='3-201',name='球墨铸铁管管件安装',spec='700',unit='个',
            confidence='high',source_filename='04市政第四册上.pdf'),**changes)

    def test_family_checks_identity_and_treats_extraction_confidence_as_advisory(self):
        identity, _ = reviewed_family(self.entry(confidence='review_required'), '2')
        self.assertEqual(identity['code'], '3-201')
        for changes in [dict(spec='800'),dict(unit='m'),
                        dict(source_filename='other.pdf'),dict(code='3-999')]:
            with self.assertRaises(Rejected):reviewed_family(self.entry(**changes),'2')

    def test_quantity_is_canonical_and_bounded_before_any_native_access(self):
        identity, quantity=reviewed_family(self.entry(),'2.5000')
        self.assertEqual(quantity,'2.5');self.assertTrue(identity['name'].endswith('700'))
        for value in ['NaN','Infinity','-1','0','0.00001','1000001']:
            with self.assertRaises(Rejected):reviewed_family(self.entry(),value)

    def test_cubic_metre_expression_and_readback_precision(self):
        self.assertEqual(displayed_quantity('m3', '15.376'), '15.38')
        self.assertEqual(displayed_quantity('m3', '19.096'), '19.1')
        self.assertEqual(displayed_quantity('t', '15.376'), '15.376')
        with self.assertRaises(Rejected):
            displayed_quantity('m3', '0.001')

    def test_render_freezes_real_parent_anchor_and_preserves_nonblank_rows(self):
        templates=TEMPLATES
        identity, quantity=reviewed_family(self.entry(),'2')
        request=dict(command_id='test.positive.2',project_id='project.synthetic',parent_id='BZ_TEST',
                     quantity='2',entry_id=1,save=True,save_timeout=480,_state='C:/synthetic/state')
        with tempfile.TemporaryDirectory() as root:
            output=render(templates,Path(root),entry=self.entry(),identity=identity,quantity=quantity,
                request=request,session=dict(pid=99,created_filetime='123',hwnd=987),
                profile=Path('C:/synthetic/profile.json'),title_prefix='synthetic - ',owner='job.synthetic',
                budget_count=77,parent_name='实际父项',parent_path='/0/4',
                anchor_values=['3-196','定','existing','个','0','0','0','0','0','','0','',''],child_count=3)
            # Executable Python is parsed, not run: this test cannot access GCCP.
            ast.parse(output['worker.py']);ast.parse(output['cli_bridge.py'])
            self.assertIn('bm->index(3,0,parent)',output['operations.h'])
            self.assertIn('bm->rowCount(parent)!=4',output['operations.h'])
            self.assertIn('PROFILE=Path(',output['cli_bridge.py'])
            self.assertNotIn('__PROJECT_TITLE_PREFIX__',output['cli_bridge.py'])
            self.assertIn("quantity='2'",output['worker.py'])
            self.assertIn("value='0'",output['worker.py'])
            self.assertIn('wait_for_saved_workcopy(file_path,prior,timeout=480)',output['worker.py'])

    def test_section_path_requires_unique_names_under_each_parent(self):
        def section(path, name):
            return dict(row_path_session_only=path, depth=path.count('/') - 1, values=['', '部', name])

        rows = [section('/0/0', '工作井'), section('/0/1', '接收井'),
                section('/0/0/0', '支护'), section('/0/1/0', '支护')]
        self.assertEqual(find_section_path(rows, ['接收井', '支护']), rows[3])
        with self.assertRaises(Rejected):
            find_section_path(rows, ['支护'])
        with self.assertRaises(Rejected):
            find_section_path(rows + [section('/0/1/1', '支护')], ['接收井', '支护'])

    def test_anchor_route_stays_in_one_scope_and_checks_initial_row(self):
        rows = [
            dict(row_path_session_only='/0/0/0/1', values=['4-49', '定']),
            dict(row_path_session_only='/0/0/1', values=['', '部']),
            dict(row_path_session_only='/0/0/1/0', values=['4-49', '定']),
            dict(row_path_session_only='/0/1/0', values=['1-26', '定']),
        ]
        self.assertEqual(bounded_anchor_route(rows, dict(rows[0], verified=True), rows[2]), rows[:3])
        self.assertEqual(bounded_anchor_route(rows, dict(rows[2], verified=True), rows[0]), rows[:3][::-1])
        self.assertEqual(bounded_anchor_route(rows, dict(rows[3], verified=True), rows[0]), [])
        self.assertEqual(bounded_anchor_route(rows, dict(rows[0], values=['', '部'], verified=True), rows[2]), [])

    def test_anchor_route_accepts_converted_and_supplemental_quota_rows(self):
        rows = [
            dict(row_path_session_only='/0/0/2/0', values=['11-62', '定']),
            dict(row_path_session_only='/0/0/2/1', values=['11-34 R*1.15,J*1.15', '换']),
            dict(row_path_session_only='/0/0/2/2', values=['补R16-01', '补']),
            dict(row_path_session_only='/0/0/2/3', values=['', '定']),
        ]
        self.assertEqual(bounded_anchor_route(rows, dict(rows[0], verified=True), rows[-1]), rows)

    def test_nested_runtime_pins_ancestors_and_exact_child_slot(self):
        identity, quantity = reviewed_family(self.entry(), '50')
        request = dict(command_id='test.nested', project_id='project.synthetic',
                       parent_id='section.synthetic', parent_sections=['顶管工程', '顶进与注浆'],
                       quantity=quantity, _state='C:/synthetic/state', save=False, selected_anchor=True,
                       item_label='独立工32a双拼对撑')
        blank = ['', '定', '', '', '', '0', '0', '0', '0', '', '0', '', '']
        preceding = ['3-196','定','existing','个','1','0','1','0','0','','0','','']
        self.assertTrue(is_blank_quota(blank))
        self.assertFalse(is_blank_quota(preceding))
        self.assertFalse(is_blank_quota([*blank[:-1], '备注']))
        with tempfile.TemporaryDirectory() as root:
            output = render(TEMPLATES, Path(root), entry=self.entry(), identity=identity,
                quantity=quantity, request=request, session=dict(pid=99, created_filetime='123', hwnd=987),
                profile=Path('C:/synthetic/profile.json'), title_prefix='synthetic - ', owner=None,
                budget_count=68, parent_name='顶进与注浆', parent_path='/0/1/0',
                anchor_values=blank, child_count=3, preceding_anchor_values=preceding)
        self.assertIn('sectionNames{"顶管工程","顶进与注浆"}', output['operations.h'])
        self.assertIn('sectionRows[]={ 1,0 }', output['operations.h'])
        self.assertIn('matches!=1 || match.row()!=sectionRows[depth]', output['operations.h'])
        self.assertIn("TARGET_PATH='/0/1/0/2'", output['worker.py'])
        self.assertIn('SAVE=False', output['worker.py'])
        self.assertIn("TRACE_NOTE='[用途:独立工32a双拼对撑] [CLI:", output['worker.py'])
        self.assertIn('target[:4]==[CODE,\'定\',NAME,UNIT]', output['worker.py'])
        self.assertIn('target[12]==TRACE_NOTE', output['worker.py'])
        self.assertIn('column=note?12:4', output['operations.h'])
        self.assertNotIn('name.set', output['bridge.cpp'])
        self.assertIn('candidateRows>MAX_OBSERVED_ROWS', output['operations.h'])
        prepare = output['operations.h'].split('if(op=="prepare"){', 1)[1].split('if(op=="resume.done")', 1)[0]
        self.assertIn('section_anchor_not_selected', prepare)
        self.assertIn('selected_anchor_verified', prepare)
        self.assertIn('selected.parent()==parent && selected.row()==1', prepare)
        self.assertIn('Qt::Key_Down', prepare)
        self.assertIn('blank_anchor_model_changed', prepare)
        self.assertNotIn('setCurrentIndex', prepare)
        self.assertNotIn('activateWindow', prepare)
        self.assertNotIn('setFocus', prepare)
        edit = output['operations.h'].split('if(op=="quantity.normal.zero"', 1)[1].split('if(op=="note.set"', 1)[0]
        self.assertIn('quantity_cell_not_selected_by_insert', edit)
        self.assertNotIn('setCurrentIndex', edit)

    def test_quota_scope_reads_current_remark_and_legacy_name(self):
        from gccp_foundation.application.native_runtime import quota_name_and_scope, quota_trace_note

        name = '球墨铸铁管管件安装 公称直径 250'
        note = quota_trace_note('test.scope', '独立接口')
        self.assertEqual(quota_name_and_scope(name, note), (name, '独立接口'))
        self.assertEqual(quota_name_and_scope(f'{name} {note}', ''), (name, '独立接口'))
        self.assertEqual(quota_name_and_scope(name, quota_trace_note('test.scope')), (name, ''))

    def test_remark_insertion_does_not_change_existing_material_edit_guard(self):
        from gccp_foundation.application.native_material_edit import build_material_bridge

        meta = dict(session=dict(pid=99, created_filetime='123', hwnd=987),
                    workcopy_path='C:/synthetic/sample.GPB7')
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'material-edit'
            with patch('gccp_foundation.application.native_material_edit.compile_bridge'), \
                 patch('gccp_foundation.application.native_material_edit.load_bridge'):
                build_material_bridge(path, 'test.edit', meta, Path(root) / 'profile.json')
            bridge = (path / 'bridge.cpp').read_text(encoding='utf-8')
        self.assertIn('const bool mutation=op == "material.edit";', bridge)

    def test_direct_anchor_still_verifies_the_selected_budget_row(self):
        identity, quantity = reviewed_family(self.entry(), '50')
        request = dict(command_id='test.direct', project_id='project.synthetic',
                       parent_id=None, parent_sections=['管道铺设'], quantity=quantity,
                       _state='C:/synthetic/state', save=False, selected_anchor=True)
        anchor = ['3-196', '定', 'existing', '个', '1', '0', '1', '0', '0', '', '0', '', '']
        with tempfile.TemporaryDirectory() as root:
            output = render(TEMPLATES, Path(root), entry=self.entry(), identity=identity,
                quantity=quantity, request=request, session=dict(pid=99, created_filetime='123', hwnd=987),
                profile=Path('C:/synthetic/profile.json'), title_prefix='synthetic - ', owner=None,
                budget_count=68, parent_name='管道铺设', parent_path='/0/1',
                anchor_values=anchor, child_count=3, direct_anchor=True)
        prepare = output['operations.h'].split('if(op=="prepare"){', 1)[1].split('if(op=="resume.done")', 1)[0]
        self.assertIn('setCurrentIndex(QModelIndex)', prepare)
        self.assertIn('anchor_navigation_model_changed', prepare)
        self.assertIn('selected.sibling(selected.row(),0)!=target', prepare)


if __name__=='__main__':unittest.main()
