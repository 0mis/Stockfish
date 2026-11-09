from fractions import Fraction
import json
import unittest

from ridge_fit import evaluate_offsets, fit_offsets


def inputs(specs):
    labels, statics = [], []
    for i, (head, family, deep, static) in enumerate(specs):
        identity = {"physical_head": head, "family": family, "position_id": f"p{i}"}
        labels.append({**identity, "stable": deep is not None, "parent_static_cp": 0, "deep_cp": deep,
                       "reason": "stable" if deep is not None else "shallow_mate_score"})
        statics.append({**identity, "static_cp": static})
    return labels, list(reversed(statics))


def rational(data):
    return Fraction(data["numerator"], data["denominator"])


class RidgeFitTests(unittest.TestCase):
    def test_clipped_products_and_ridge_are_exact(self):
        specs = [(4, "a", 80, -1), (4, "a", -75, 2), (4, "a", 2, 4)]
        specs += [(4, name, 1, 1) for name in "bcdef" for _ in range(3)]
        specs.append((4, "a", None, 200))
        result = fit_offsets(*inputs(specs))
        json.dumps(result, allow_nan=False)
        head = result["heads"][4]
        self.assertEqual((head["stable_rows"], head["stable_families"], rational(head["S"]), rational(head["D"])),
                         (18, 6, Fraction(-67, 3), Fraction(588)))
        self.assertEqual(rational(head["steps_before_rounding"]), Fraction(-536, 441))
        self.assertEqual(result["deltas"], [0] * 4 + [-16] + [0] * 87)
        self.assertEqual([r["clipped_residual_cp"] for r in result["rows"][:3]], [30, -30, 2])
        self.assertEqual(result["rows"][-1]["reason"], "shallow_mate_score")

    def test_exact_ties_to_even_and_caps(self):
        for values, exact, delta in (([2, 3, 3, 3], Fraction(1, 2), 0), ([8, 8, 8, 9], Fraction(3, 2), 32),
                                     ([-8, -8, -8, -9], Fraction(-3, 2), -32), ([-13, -14, -14, -14], Fraction(-5, 2), -32)):
            specs = [(0, "a", r, 4) for r in values] + [(0, name, 0, 4) for name in "bcdefgh" for _ in range(2)]
            head = fit_offsets(*inputs(specs))["heads"][0]
            self.assertEqual((rational(head["steps_before_rounding"]), head["delta"]), (exact, delta))
        for r, expected in ((30, 256), (-30, -256)):
            specs = [(91, name, r, 8) for name in "abcdef" for _ in range(3)]
            self.assertEqual(fit_offsets(*inputs(specs))["deltas"][91], expected)

    def test_unequal_family_sizes_and_support(self):
        specs = [(6, "large", 30, 6)] * 11 + [(6, name, -6, 6) for name in "abcde"]
        head = fit_offsets(*inputs(specs))["heads"][6]
        self.assertEqual((head["eligible"], head["stable_rows"], rational(head["S"]), head["delta"]), (True, 16, 0, 0))
        fewer_rows = fit_offsets(*inputs(specs[1:]))["heads"][6]
        fewer_families = fit_offsets(*inputs([(6, n, 30, 6) for n in "abcde" for _ in range(4)]))["heads"][6]
        self.assertEqual((fewer_rows["delta"], fewer_rows["support_reasons"]), (0, ["fewer_than_16_stable_rows"]))
        self.assertEqual((fewer_families["delta"], fewer_families["support_reasons"]), (0, ["fewer_than_6_stable_families"]))

    def test_holdout_equal_family_mae_counts_unchanged_and_missing_rows(self):
        specs = [(0, "a", 1, 3), (0, "a", 2, 6), (1, "a", 3, 0), (1, "b", 10, 2), (1, "c", None, 0)]
        labels, statics = inputs(specs)
        complete = evaluate_offsets(labels, statics)
        self.assertEqual(complete["counts"]["paired_stable_rows"], 4)
        self.assertEqual(rational(complete["equal_family_parent_mae_cp"]), 6)
        self.assertEqual(rational(complete["equal_family_candidate_mae_cp"]), Fraction(11, 2))
        self.assertEqual(rational(complete["equal_family_improvement_cp"]), Fraction(1, 2))
        self.assertEqual(len(complete["heads"]), 92)
        self.assertEqual(rational(complete["heads"][1]["families"][0]["improvement_cp"]), 0)
        missing = evaluate_offsets(labels, [r for r in statics if r["position_id"] != "p3"])
        self.assertEqual(missing["missing_stable_candidate_position_ids"], ["p3"])
        self.assertEqual(missing["counts"]["paired_stable_rows"], 3)
        self.assertIsNone(missing["equal_family_candidate_mae_cp"])
        self.assertEqual(rational(missing["equal_family_parent_mae_cp"]), 6)
        self.assertEqual(len(missing["rows"]), 5)
        json.dumps(missing, allow_nan=False)

    def test_ambiguous_or_incomplete_optimization_join(self):
        labels, statics = inputs([(0, "a", None, 2)])
        for wrong in ([], statics * 2, [{**statics[0], "family": "b"}], [{**statics[0], "physical_head": 1}]):
            with self.assertRaises(ValueError):
                fit_offsets(labels, wrong)


if __name__ == "__main__":
    unittest.main()
