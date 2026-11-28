from unittest import TestCase

from gccp_foundation.application.native_cli import Rejected
from gccp_foundation.application.native_runtime import displayed_quantity


class ScaledQuantityTests(TestCase):
    def test_area_expression_and_display_are_separate(self):
        self.assertEqual(displayed_quantity('100m2', '88'), '0.88')
        self.assertEqual(displayed_quantity('100m2', '0.88'), '0.0088')
        self.assertEqual(displayed_quantity('10m2', '338.38'), '33.838')
        self.assertEqual(displayed_quantity('m3', '15.376'), '15.38')

    def test_unverified_scales_stop_before_write(self):
        for unit, expression in [('10m3', '88'), ('100m', '88'), ('100m2', '0.001')]:
            with self.subTest(unit=unit, expression=expression), self.assertRaises(Rejected):
                displayed_quantity(unit, expression)
