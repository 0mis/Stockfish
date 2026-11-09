import unittest

import numpy as np

from experiment import gate


class HoldoutGateTest(unittest.TestCase):
    @staticmethod
    def example(benefiting_families, dense_rows=0):
        rows, masks = [], []
        p = list(range(512))
        p[1], p[4] = p[4], p[1]
        for family in range(15):
            row = {"family": str(family)}
            mask = np.ones(1024, dtype=bool)
            if family < benefiting_families:
                mask[[0, 4, 512, 516]] = False
            rows.append(row)
            masks.append(mask)
            for _ in range(dense_rows):
                rows.append(row)
                masks.append(np.zeros(1024, dtype=bool))
        return gate(p, rows, np.stack(masks))

    def test_both_conditions_required(self):
        self.assertTrue(self.example(8)["passed"])
        seven = self.example(7)
        self.assertGreater(seven["relative_active_groups_saved"], 0.01)
        self.assertFalse(seven["passed"])
        dense = self.example(15, dense_rows=10)
        self.assertEqual(dense["favorable_families"], 15)
        self.assertLess(dense["relative_active_groups_saved"], 0.01)
        self.assertFalse(dense["passed"])

    def test_inactive_identity_has_no_gain(self):
        self.assertFalse(self.example(0)["passed"])
