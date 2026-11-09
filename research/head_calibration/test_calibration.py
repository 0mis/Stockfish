"""Small in-memory calibration cases."""

import json
import unittest

from calibration import evaluate_bias, fit_bias, select_heads


TARGET, CONTROL = 7, 2


def label(family, head, position, parent, deep, stable=True):
    return {"family": family, "physical_head": head, "position_id": position,
            "parent_static_cp": parent, "deep_cp": deep, "stable": stable}


def training(direction=1):
    rows = []
    for i in range(15):
        for j in range(4 if i < 5 else 3):
            rows.append(label(f"f{i:02}", TARGET, f"t-{i}-{j}", 40, 40 + 6 * direction))
            rows.append(label(f"f{i:02}", CONTROL, f"c-{i}-{j}", -3, -3 + (0 if i < 10 else 12 * direction)))
    return rows


def holdout(gains, sizes):
    rows, scores = [], {}
    for i, (gain, size) in enumerate(zip(gains, sizes, strict=True)):
        for j in range(size):
            position = f"h-{i}-{j}"
            rows.append(label(f"h{i:02}", TARGET, position, 0, 20))
            scores[position] = gain
    return rows, scores


def rational(n, d=1):
    return {"numerator": n, "denominator": d}


class CalibrationTests(unittest.TestCase):
    def test_head_ranking_uses_counts_and_index_only(self):
        rows = [{"physical_head": head} for head in [8, 6, 2, 8, 2, 6, 9]]
        selected = select_heads(rows)
        self.assertEqual((selected["target_head"], selected["control_head"]), (2, 6))
        self.assertEqual([r["physical_head"] for r in selected["head_counts"]], [2, 6, 8, 9])
        self.assertEqual(select_heads([{"physical_head": 4}])["status"], "abstain")
        self.assertEqual(select_heads([])["status"], "abstain")

    def test_exact_training_boundaries_and_single_signed_delta(self):
        for sign in (-1, 1):
            rows = training(sign)
            rows += [label("unpaired", TARGET, "large", 0, -10000 * sign),
                     label("f01", TARGET, "unstable", None, None, False), label("f02", 1, "another-head", None, None)]
            result = fit_bias(rows, TARGET, CONTROL)
            self.assertEqual(result["status"], "frozen")
            self.assertEqual(result["fc2_bias_delta"], 512 * sign)
            self.assertEqual(result["median_family_contrast_cp"], rational(6 * sign))
            self.assertEqual(result["median_target_family_residual_cp"], rational(6 * sign))
            self.assertEqual(result["strict_agreement_fraction"], rational(2, 3))
            counts = result["counts"]
            self.assertEqual((counts["paired_families"], counts["paired_stable_target_rows"], counts["paired_stable_control_rows"]), (15, 50, 50))
            json.dumps(result, allow_nan=False)

    def test_unpaired_rows_cannot_rescue_training_support(self):
        rows = [row for row in training() if row["family"] != "f14"]
        rows += [label("only-target", TARGET, f"extra-{i}", 0, 100) for i in range(20)]
        result = fit_bias(rows, TARGET, CONTROL)
        self.assertEqual(result["status"], "abstain")
        self.assertIsNone(result["fc2_bias_delta"])
        self.assertEqual(result["counts"]["paired_families"], 14)
        self.assertIn("fewer_than_50_stable_target_rows_in_paired_families", result["reasons"])
        self.assertIn("fewer_than_50_stable_control_rows_in_paired_families", result["reasons"])

    def test_medians_are_exact_and_positions_need_no_fabricated_training_id(self):
        rows = [label("f", TARGET, "a", 0, 6), label("f", TARGET, "b", 0, 7), label("f", CONTROL, "c", 0, 0)]
        rows[0].pop("position_id")
        result = fit_bias(rows, TARGET, CONTROL)
        self.assertEqual(result["median_family_contrast_cp"], rational(13, 2))
        first = result["families"][0]["target_rows"][0]
        self.assertIsNone(first["position_id"])
        self.assertEqual(first["label_index"], 0)
        self.assertEqual(result["status"], "abstain")

    def test_contrast_own_sign_magnitude_and_strict_agreement_gate(self):
        for mode in ("weak_own", "weak_contrast", "opposite_own", "zero_family_does_not_agree"):
            rows = training()
            for row in rows:
                if mode == "weak_own":
                    row["deep_cp"] = row["parent_static_cp"] + (5 if row["physical_head"] == TARGET else -5)
                elif mode == "weak_contrast":
                    row["deep_cp"] = row["parent_static_cp"] + (10 if row["physical_head"] == TARGET else 5)
                elif mode == "opposite_own":
                    row["deep_cp"] = row["parent_static_cp"] + (-8 if row["physical_head"] == TARGET else -16)
                elif row["family"] == "f09" and row["physical_head"] == CONTROL:
                    row["deep_cp"] = row["parent_static_cp"] + 6
            result = fit_bias(rows, TARGET, CONTROL)
            self.assertEqual(result["status"], "abstain")
            self.assertIsNone(result["fc2_bias_delta"])
            if mode == "zero_family_does_not_agree":
                self.assertEqual(result["strict_agreement_fraction"], rational(3, 5))
            elif mode == "weak_contrast":
                self.assertEqual(result["reasons"], ["median_family_contrast_below_6_cp"])

    def test_exact_holdout_boundaries_and_position_join(self):
        rows, scores = holdout([2, 2, 2, 2, 1, 1, 0, 0, 0, 0], [2] * 10)
        rows += [label("other", CONTROL, "other-target", None, None), label("h00", TARGET, "unstable", None, None, False)]
        records = [{"position_id": key, "candidate_static_cp": cp} for key, cp in reversed(scores.items())]
        records.append({"position_id": "unused", "candidate_static_cp": 999})
        result = evaluate_bias(rows, records, TARGET)
        self.assertTrue(result["passed"])
        self.assertEqual(result["counts"]["stable_target_rows"], 20)
        self.assertEqual(result["equal_family_parent_mae_cp"], rational(20))
        self.assertEqual(result["equal_family_candidate_mae_cp"], rational(19))
        self.assertEqual(result["equal_family_improvement_cp"], rational(1))
        self.assertEqual(result["strictly_improving_family_fraction"], rational(3, 5))
        self.assertEqual(result["counts"]["tied_families"], 4)
        self.assertEqual(result["unused_candidate_position_ids"], ["unused"])
        json.dumps(result, allow_nan=False)

    def test_equal_family_weight_and_ties_are_exact(self):
        rows, scores = holdout([10, 0, 0, 0, 0, 0], [20, 1, 1, 1, 1, 1])
        result = evaluate_bias(rows, scores, TARGET)
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["equal_family_improvement_cp"], rational(5, 3))
        self.assertEqual(result["strictly_improving_family_fraction"], rational(1, 6))
        rows, scores = holdout([1] * 6, [4] * 6)
        scores["h-0-0"] = 0
        result = evaluate_bias(rows, scores, TARGET)
        self.assertEqual(result["equal_family_improvement_cp"], rational(23, 24))
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["counts"]["strictly_improving_families"], 6)

    def test_missing_candidates_or_small_holdout_abstain(self):
        rows, scores = holdout([2] * 6, [4] * 6)
        del scores["h-2-0"]
        result = evaluate_bias(rows, scores, TARGET)
        self.assertEqual(result["status"], "abstain")
        self.assertFalse(result["passed"])
        self.assertEqual(result["missing_candidate_position_ids"], ["h-2-0"])
        self.assertIsNone(result["equal_family_improvement_cp"])
        for sizes in ([4] * 5, [3] * 6):
            rows, scores = holdout([2] * len(sizes), sizes)
            self.assertEqual(evaluate_bias(rows, scores, TARGET)["status"], "abstain")

    def test_duplicate_ids_untyped_scores_and_duplicate_heads_refuse(self):
        row = label("f", TARGET, "id", 0, 7)
        with self.assertRaises(ValueError):
            fit_bias([row, row], TARGET, CONTROL)
        with self.assertRaises(ValueError):
            fit_bias([row], TARGET, TARGET)
        with self.assertRaises(ValueError):
            evaluate_bias([row], [{"position_id":"id","candidate_static_cp":2}] * 2, TARGET)
        with self.assertRaises(ValueError):
            evaluate_bias([row], {"id": True}, TARGET)
        with self.assertRaises(ValueError):
            evaluate_bias([{**row, "position_id": None}], {}, TARGET)


if __name__ == "__main__":
    unittest.main()
