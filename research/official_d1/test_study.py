"""Small source/synthetic tests only. No model bytes, native or validation I/O."""
from collections import Counter
from fractions import Fraction
import io
import json
from pathlib import Path
import random
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
import chess
import study


class OfficialD1Tests(unittest.TestCase):
    def test_reuses_unmodified_D1_sources_and_only32_stack_offsets(self):
        plan = study.policy()
        self.assertEqual(plan["network_parent"]["heads"], 32)
        self.assertEqual((study.weights.FILE_BYTES, study.weights.STACKS_OFFSET + study.weights.FC2_IN_STACK), (136247652, 135156072))
        heads = tuple(study.weights.Head(i, (0,) * 128) for i in range(32))
        mapping = study.map_heads(heads)
        self.assertEqual(mapping["heads"][-1]["weight_offsets"][-1], 136247651)
        self.assertNotIn("raw_to_physical", mapping)

    def test_inherited_D1_signed_algebra_and_stream_self_tests(self):
        self.assertEqual(study.forward.self_test()["status"], "PASS")
        self.assertEqual(study.weights.self_test()["status"], "PASS")

    def test_current_pawn_pair_enumeration_equals_old_definition(self):
        rng = random.Random(730119)
        for _ in range(200):
            squares = rng.sample(list(range(8, 56)), rng.randrange(17))
            pawns = {sq: bool(rng.randrange(2)) for sq in squares}
            for perspective in (True, False):
                king = rng.randrange(64)
                old = []
                white = sorted(sq for sq in pawns if pawns[sq]); black = sorted(sq for sq in pawns if not pawns[sq])
                for group in (white, black):
                    for i, a in enumerate(group):
                        for b in group[i + 1:]:
                            if abs(a % 8 - b % 8) <= 1: old.append(study.features.pair_index(a, pawns[a], b, pawns[b], king, perspective))
                for a in white:
                    for b in black:
                        if abs(a % 8 - b % 8) <= 1: old.append(study.features.pair_index(a, pawns[a], b, pawns[b], king, perspective))
                remaining = set(pawns); new = []
                while remaining:
                    a = min(remaining); remaining.remove(a)
                    for b in sorted(remaining):
                        if abs(a % 8 - b % 8) <= 1: new.append(study.features.pair_index(a, pawns[a], b, pawns[b], king, perspective))
                self.assertEqual(Counter(old), Counter(new))

    def test_psq_block_and_bounded_pair_accumulator_equivalence(self):
        for own_queens in range(4):
            for enemy_queens in range(4):
                for rooks in range(5):
                    old = 2 if enemy_queens else int(rooks + own_queens + enemy_queens > 0)
                    new = 2 if enemy_queens > 0 else bool(rooks or own_queens or enemy_queens)
                    self.assertEqual(old, new)
        for added in range(-128, 128):
            for removed in (-128, -1, 0, 1, 127):
                biased_difference = (added + 128) - (removed + 128)
                self.assertEqual(biased_difference, added - removed)
                self.assertLessEqual(abs(biased_difference), 255)
                self.assertEqual(((32760 + added - removed + 32768) % 65536) - 32768,
                                 ((32760 + biased_difference + 32768) % 65536) - 32768)

    def test_exact_component_Huber_penalty_and_identity_ties(self):
        self.assertEqual(study.huber20(20), 200); self.assertEqual(study.huber20(21), 220)
        self.assertEqual(study.component_mean([("a", 0), ("a", 100), ("b", 100)]), 75)
        self.assertLess(study.tie(5, 0, None), study.tie(5, 1, 0))
        self.assertLess(study.tie(5, -1, 7), study.tie(5, 2, 0))

    def test_reused_CacheBuilder_row_accepts_actual32_shape_on_invented_arrays(self):
        w0 = np.zeros((32, 1024), dtype=np.int32); b0 = np.arange(32, dtype=np.int32) * 129 - 1400
        w1 = np.zeros((32, 64), dtype=np.int32); b1 = np.arange(32, dtype=np.int32) * 100 - 1200
        w2 = (np.arange(128, dtype=np.int32) % 7 - 3).astype(np.int8).reshape(1, 128); b2 = np.array([71], dtype=np.int32)
        stack = SimpleNamespace(fc0_bias=b0, fc0_weights=w0, fc1_bias=b1, fc1_weights=w1, fc2_bias=b2, fc2_weights=w2)
        payload = struct.pack("<i128b", 71, *map(int, w2.reshape(-1)))
        class TailOnlySyntheticData:
            def __getitem__(self, key):
                self_outer.assertEqual(key.stop - key.start, 132)
                return payload
        self_outer = self
        network = SimpleNamespace(sha256=study.weights.PARENT_SHA256, stacks_offset=study.weights.STACKS_OFFSET,
                                  stacks=[stack] * 32, data=TailOnlySyntheticData())
        builder = study.forward.CacheBuilder(network); board = chess.Board(); context = study.forward.eval_context(board)
        label = {"source_root_index": 0, "root_fen6": board.fen(), "component_id": "synthetic", "fold": "train", "head": 31,
                 "rule50": context.rule50, "wdl_material": context.wdl_material, "static_material": context.static_material,
                 "simple_eval_stm": context.simple_eval_stm}
        with patch.object(study.features, "transform", return_value=np.arange(1024, dtype=np.uint16).astype(np.uint8) % 128):
            row = builder.row(label)
        self.assertEqual(len(row.input128), 128); self.assertEqual(row.head, 31)
        affine = 71 + sum(int(w) * x for w, x in zip(w2.reshape(-1), row.input128))
        self.assertEqual(row.base_fc2_i32, affine)
        self.assertEqual(row.base_raw_nnue_i32, study.forward.trunc_div(study.forward.trunc_div((affine + row.skip_i32) * 9600, 16384), 16))

    def test_train_routing_support_before_predictions(self):
        bundle = study.load_train(study.HERE / "train.json")
        children, parents = study.child_plan(bundle)
        self.assertEqual((len(children), len(parents)), (586, 328))
        counts = Counter(p["heads"][0] for p in parents if not p["reasons"] and p["heads"][0] == p["heads"][1])
        self.assertEqual((counts[31], counts[27]), (175, 47))
        self.assertEqual(sum(bool(p["reasons"]) for p in parents), 35)

    def test_heldout_gate_refuses_before_reading_validation(self):
        import heldout_design
        with patch.object(heldout_design, "gate", side_effect=ValueError("no frozen candidate")), patch.object(Path, "read_bytes", side_effect=AssertionError("heldout opened")):
            with self.assertRaisesRegex(ValueError, "no frozen candidate"):
                heldout_design.validate(original_network_path="never", cache_dir="never", freeze_path="never", recipe_path="never", candidate_path="never",
                                        edited_train_parity_path="never", validation_path="never", output_dir="never", native_parity_callback=lambda **kwargs: None)

    def test_local_native_hosted_entrypoint_refuses(self):
        import hosted
        with patch.dict(study.os.environ, {"GITHUB_ACTIONS": "false"}):
            with self.assertRaisesRegex(ValueError, "dispatched Windows CI"): hosted.run("synthetic")

    def synthetic_fit_data(self, informative=True):
        heads = tuple(study.weights.Head(0, (0,) * 128) for _ in range(32)); mapping = study.map_heads(heads)
        context = study.forward.eval_context(chess.Board())
        parents, rows = [], []
        for index in range(24):
            indices = [2 * index, 2 * index + 1]
            parents.append({"parent_position_id": "synthetic-parent-" + str(index), "component_id": "group-" + str(index % 8),
                            "component_split_sha256": "synthetic", "teacher_margin_cp": 20, "root_pov": "black", "fold": "train",
                            "reasons": [], "source_root_indices": indices, "heads": [31, 31]})
            for rank, source_index in enumerate(indices):
                values = (127 if informative and rank == 0 else 0,) + (0,) * 127
                row = study.forward.CacheRow(source_index, chess.STARTING_FEN, parents[-1]["component_id"], "train", 31, values, 0, context, 0, 0, 0, 0)
                rows.append(row.record())
        return parents, rows, mapping

    def test_new_sparse_fit_selects_one_real_coefficient_and_keeps_biases(self):
        parents, rows, mapping = self.synthetic_fit_data()
        result = study.fit_records(parents, rows, mapping)
        self.assertEqual(len(result["edits"]), 1)
        edit = result["edits"][0]
        self.assertEqual((edit["head"], edit["lane"], edit["delta"]), (31, 0, -4))
        self.assertEqual(edit["offset"], 135156076 + 31 * 35208)
        self.assertEqual(mapping["heads"][31]["bias_before"], 0)

    def test_new_sparse_fit_does_not_force_nonidentity(self):
        parents, rows, mapping = self.synthetic_fit_data(informative=False)
        result = study.fit_records(parents, rows, mapping)
        self.assertEqual(result["edits"], [])
        self.assertEqual(result["choices"][0]["delta"], 0)

    def test_old_c009_native_receipt_cannot_authorize_official_fit(self):
        parity = {"status": "OFFICIAL_NATIVE_PARITY_PASSED", "network_sha256": study.weights.PARENT_SHA256,
                  "cache_manifest": {"synthetic": True}, "rows": 586, "source_commit": study.SOURCE,
                  "source_tree": study.TREE,
                  "engine_sha256": "c0092fd059ae81d9fad61f9372e985f5b0d99b934bd8639867590cf988fa4cd9"}
        with patch.object(study, "load_cache", return_value=({}, {}, [])), patch.object(study, "reference", return_value={"synthetic": True}), \
             patch.object(Path, "read_bytes", return_value=json.dumps(parity).encode()), \
             patch.object(study, "fit_records", side_effect=AssertionError("Costs computed before official native parity")):
            with self.assertRaisesRegex(ValueError, "required before any fit"): study.fit_train("synthetic", "synthetic", "never-created")


if __name__ == "__main__": unittest.main()
