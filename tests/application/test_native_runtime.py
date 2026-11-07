import ast
from pathlib import Path
import tempfile
import unittest

from gccp_foundation.application.native_cli import Rejected
from gccp_foundation.application.native_build import TEMPLATES
from gccp_foundation.application.native_runtime import reviewed_family, render


class ScopedRuntimeTests(unittest.TestCase):
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

    def test_render_freezes_real_parent_anchor_and_preserves_nonblank_rows(self):
        templates=TEMPLATES
        identity, quantity=reviewed_family(self.entry(),'2')
        request=dict(command_id='test.positive.2',project_id='project.synthetic',parent_id='BZ_TEST',
                     quantity='2',entry_id=1,save=True,_state='C:/synthetic/state')
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


if __name__=='__main__':unittest.main()
