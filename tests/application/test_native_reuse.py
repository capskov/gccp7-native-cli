import unittest

from gccp_foundation.application.native_cli import Rejected
from gccp_foundation.application.native_reuse import normalize_variants, verify_paste


class NativeReuseTests(unittest.TestCase):
    def test_batch_pastes_original_below_latest_variant_and_preserves_siblings(self):
        def row(path, code, kind, name, quantity):
            return dict(row_path_session_only=path, values=[code, kind, name, '个', quantity, '', quantity,
                                                           '0', '0', '', '', '', ''])

        def capture(budget_rows, selected):
            return dict(tables=[dict(headers=['编码', '类别', '名称', '单位', '工程量表达式'],
                                     rows=budget_rows, complete=True, current=dict(selected, verified=True))])

        project = row('/0', '', '', '整个项目', '')
        section = row('/0/0', '', '部', '测试', '')
        source = row('/0/0/0', 'Q', '定', '同定额', '1')
        source_material = row('/0/0/0/0', 'M@1', '主', '三通', '1')
        anchor = row('/0/0/1', 'Q', '定', '同定额', '2')
        anchor_material = row('/0/0/1/0', 'M@2', '主', '弯头', '2')
        trailing = row('/0/0/2', '', '定', '', '0')
        before = [project, section, source, source_material, anchor, anchor_material, trailing]
        pasted = dict(source, row_path_session_only='/0/0/2')
        copied_material = dict(source_material, row_path_session_only='/0/0/2/0')
        shifted = dict(trailing, row_path_session_only='/0/0/3')
        after = before[:-1] + [pasted, copied_material, shifted]
        result = verify_paste(capture(before, anchor), capture(after, pasted),
                              dict(path='/0/0/0', quota=source['values']), dict(path='/0/0/1'))
        self.assertEqual(result, dict(path='/0/0/2', quota=source['values']))
        after[-2] = dict(anchor_material, row_path_session_only='/0/0/2/0')
        with self.assertRaisesRegex(ValueError, 'did not retain'):
            verify_paste(capture(before, anchor), capture(after, pasted),
                         dict(path='/0/0/0', quota=source['values']), dict(path='/0/0/1'))

    def test_batch_rejects_duplicate_variant_names_before_dispatch(self):
        with self.assertRaises(Rejected) as error:
            normalize_variants([dict(material_name='三通', quantity=1),
                                dict(material_name='三通', quantity=2)], '个')
        self.assertEqual(error.exception.code, 'REUSE_VARIANT_DUPLICATE')
