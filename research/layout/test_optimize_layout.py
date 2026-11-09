import unittest
from unittest.mock import patch

import numpy as np

import optimize_layout as layout


def direct_score(samples, order):
    return sum(
        all(row[old] for old in order[start : start + 4])
        for row in samples for start in range(0, len(order), 4)
    )


def opponent_fixture(aligned):
    # Across three positions, only the opponent supplies any zero channels.
    masks = np.zeros((3, 1024), dtype=np.uint8)
    a = np.array([1, 1, 0], dtype=np.uint8)
    b = 1 - a
    columns = (a, a, a, a, b, b, b, b) if aligned else (a, a, a, b, a, b, b, b)
    masks[:, 512:520] = np.column_stack(columns)
    return masks


class LayoutTests(unittest.TestCase):
    def test_scores_and_batches_match_exhaustive_swaps(self):
        rng = np.random.default_rng(17)
        samples = rng.random((19, 16)) < 0.65
        order = rng.permutation(16).tolist()
        bits = layout._channel_bits(samples)
        replacements = np.empty((16, 16), dtype=np.int64)
        changed = list(range(4))
        applied_batches = 0

        while True:
            before = direct_score(samples, order)
            layout._refresh_replacements(replacements, bits, order, changed)
            gains = layout._swap_gains(replacements, order)
            positive = []
            for left in range(16):
                for right in range(left, 16):
                    trial = order.copy()
                    trial[left], trial[right] = trial[right], trial[left]
                    gain = direct_score(samples, trial) - before
                    self.assertEqual(gains[left, right], gain)
                    if gain > 0:
                        positive.append((-gain, left, right))

            used = set()
            expected = []
            for negative_gain, left, right in sorted(positive):
                groups = {left // 4, right // 4}
                if used.isdisjoint(groups):
                    expected.append((left, right, -negative_gain))
                    used.update(groups)
            swaps = layout._disjoint_swaps(gains)
            self.assertEqual(swaps, expected)
            if not swaps:
                break
            for left, right, _ in swaps:
                order[left], order[right] = order[right], order[left]
            self.assertEqual(direct_score(samples, order) - before, sum(gain for _, _, gain in swaps))
            changed = sorted(used)
            applied_batches += 1

        self.assertGreaterEqual(applied_batches, 2)

    def test_ties_use_ascending_slots_and_exclude_nonpositive_gains(self):
        gains = np.full((16, 16), -1, dtype=np.int64)
        self.assertEqual(layout._disjoint_swaps(gains), [])
        for i, j, value in ((1, 4, 5), (0, 5, 5), (0, 8, 5), (4, 12, 5),
                            (9, 13, 3), (8, 12, 3), (10, 14, 0)):
            gains[i, j] = gains[j, i] = value
        self.assertEqual(layout._disjoint_swaps(gains), [(0, 5, 5), (8, 12, 3)])

    def test_fit_and_independent_heldout(self):
        fit_masks = opponent_fixture(aligned=False)
        before = fit_masks.copy()
        fit = layout.fit_layout(fit_masks)
        self.assertEqual(fit, layout.fit_layout(fit_masks.astype(bool)))
        np.testing.assert_array_equal(fit_masks, before)
        order = list(range(512))
        order[3], order[4] = 4, 3
        self.assertEqual(fit["permutation"], order)
        self.assertEqual((fit["identity_zero_groups"], fit["permuted_zero_groups"]), (0, 3))
        self.assertEqual(fit["rounds"], [{"round": 1, "gain": 3, "swaps": [{"left": 3, "right": 4, "gain": 3}]}])
        self.assertEqual((fit["rounds_evaluated"], fit["stop_reason"]), (2, "no_positive_swap"))
        self.assertEqual(fit["max_batches"], 64)

        frozen = tuple(order)
        heldout = opponent_fixture(aligned=True)
        heldout.flags.writeable = False
        scored = layout.evaluate_layout(frozen, heldout)
        self.assertEqual(scored, {"positions": 3, "total_groups": 768, "identity_zero_groups": 3, "permuted_zero_groups": 0})
        self.assertEqual(tuple(fit["permutation"]), frozen)

    def test_no_improvement_and_round_cap_are_distinct(self):
        flat = layout.fit_layout(np.ones((1, 1024), dtype=np.bool_))
        self.assertEqual(flat["identity_zero_groups"], flat["permuted_zero_groups"])
        self.assertEqual(flat["permuted_zero_groups"], 256)
        self.assertEqual(flat["permutation"], list(range(512)))
        self.assertEqual((flat["rounds"], flat["rounds_evaluated"], flat["stop_reason"]), ([], 1, "no_positive_swap"))

        with patch.object(layout, "MAX_BATCHES", 1):
            capped = layout.fit_layout(opponent_fixture(aligned=False))
        self.assertEqual((len(capped["rounds"]), capped["rounds_evaluated"], capped["stop_reason"]), (1, 1, "round_limit"))

    def test_direction_both_views_and_block_symmetry(self):
        masks = np.zeros((1, 1024), dtype=np.bool_)
        masks[0, [4, 1, 2, 3]] = True
        masks[0, np.array([8, 5, 6, 7]) + 512] = True
        order = np.arange(512)
        order[[0, 4, 8]] = [4, 8, 0]
        expected = {"positions": 1, "total_groups": 256, "identity_zero_groups": 0, "permuted_zero_groups": 2}
        self.assertEqual(layout.evaluate_layout(order, masks), expected)
        reverse_blocks_and_slots = order.reshape(-1, 4)[::-1, ::-1].flatten()
        self.assertEqual(layout.evaluate_layout(reverse_blocks_and_slots, masks), expected)
        self.assertEqual(layout.evaluate_layout(np.argsort(order), masks)["permuted_zero_groups"], 0)

    def test_mask_contract(self):
        order = list(range(512))
        invalid = [
            ([], TypeError), (np.ma.masked_all((1, 1024), dtype=np.bool_), TypeError),
            (np.zeros((0, 1024), dtype=np.bool_), ValueError), (np.zeros(1024, dtype=np.bool_), ValueError),
            (np.zeros((2, 512), dtype=np.bool_), ValueError), (np.full((1, 1024), 2, dtype=np.uint8), ValueError),
            (np.zeros((1, 1024), dtype=np.float32), TypeError), (np.zeros((1, 1024), dtype=np.int8), TypeError),
        ]
        for masks, error in invalid:
            with self.subTest(shape=np.shape(masks), dtype=getattr(masks, "dtype", None)):
                with self.assertRaises(error):
                    layout.fit_layout(masks)
                with self.assertRaises(error):
                    layout.evaluate_layout(order, masks)

    def test_frozen_permutation_contract(self):
        order = list(range(512))
        masks = np.zeros((1, 1024), dtype=np.bool_)
        invalid = (order[:-1], [0] + order[:-1], order[:-1] + [-1], order[:-1] + [512],
                   [0, True] + order[2:], np.arange(512.0), np.arange(512).reshape(2, 256), set(order))
        for permutation in invalid:
            with self.subTest(permutation_type=type(permutation)), self.assertRaises(ValueError):
                layout.evaluate_layout(permutation, masks)


if __name__ == "__main__":
    unittest.main()
