"""Synthetic and source-only checks. Never compile or execute an engine."""
import copy
import hashlib
import json
from pathlib import Path
import unittest
import chess

import patch_source
import protocol as p

HERE = Path(__file__).resolve().parent


def fixture():
    c0 = [(index - 15) * 257 + 127 for index in range(32)]
    c1 = [(index - 13) * 131 + 63 for index in range(32)]
    canonical = p.activations(c0, 7) + p.activations(c1, 6)
    native = [0] * 128
    for index, value in enumerate(canonical): native[p.physical_slot(index)] = value
    weights = [(index * 37) % 256 - 128 for index in range(128)]
    bias = -811
    affine = bias + sum(w * x for w, x in zip(weights, canonical))
    skip = c0[30] - c0[31]
    combined = affine + skip
    scaled = p.tz(combined * 9600, 16384)
    raw = p.tz(scaled, 16)
    return {"schema": "test1757-fc2-activation/1", "source_commit": p.SOURCE, "row_id": 0,
            "history_scope": "supplied_fen_and_legal_prefix_only", "integer_parity": True,
            "physical_head": 91, "stored_heads": 92, "fc0": c0, "fc1": c1,
            "input_native": native, "input_serialized": canonical,
            "serialized_to_native": [p.physical_slot(index) for index in range(128)],
            "output_weights": weights, "output_bias": bias, "fc2_affine": affine,
            "skip": skip, "combined": combined, "scaled": scaled, "raw_nnue": raw, "ordinary_raw_nnue": raw}


def hardware_pair(value, shift):
    word = min(32767, max(-32768, value))
    high = (word * word) >> 16
    square = min(127, high >> (2 * shift + 7 - 16))
    linear = min(127, max(0, word) >> shift)
    return square, linear


class CaptureTests(unittest.TestCase):
    def test_asymmetric_full_128_lane_serialized_readout(self):
        row = fixture()
        self.assertNotEqual(row["input_native"], row["input_serialized"])
        self.assertNotEqual(row["input_serialized"][-1], 0)
        self.assertTrue(p.validate(row)["exact_integer_parity"])

    def test_wrong_simd_layout_or_last_nonpadding_weight_refused(self):
        row = fixture()
        row["input_serialized"] = row["input_native"]
        with self.assertRaisesRegex(ValueError, "vector mismatch"): p.validate(row)
        row = fixture()
        row["output_weights"][-1] += 1
        with self.assertRaisesRegex(ValueError, "dot"): p.validate(row)

    def test_signed_saturation_and_quantization_at_hardware_boundaries(self):
        values = [p.I32_MIN, p.I32_MAX, -32769, -32768, -32767, -16384, -8192, -129, -128, -1,
                  0, 1, 63, 64, 127, 128, 255, 8191, 8192, 16383, 16384, 32767, 32768]
        values += [index * 787 - 40000 for index in range(103)]
        for shift in (6, 7):
            scalar = p.activations(values, shift)
            for index, value in enumerate(values):
                self.assertEqual((scalar[index], scalar[index + len(values)]), hardware_pair(value, shift))

    def test_each_signed_rounding_and_skip_stage_is_required(self):
        self.assertEqual(p.tz(-1, 16), 0)
        self.assertEqual(p.tz(-17, 16), -1)
        for name in ("fc2_affine", "skip", "combined", "scaled", "raw_nnue", "ordinary_raw_nnue"):
            row = fixture()
            row[name] += 1
            with self.subTest(field=name), self.assertRaisesRegex(ValueError, "signed scaling"): p.validate(row)

    def test_overflow_and_unknown_schema_are_refused(self):
        row = fixture()
        row["output_bias"] = p.I32_MAX
        row["output_weights"] = [127] * 128
        with self.assertRaisesRegex(ValueError, "affine"): p.validate(row)
        row = fixture()
        row["source_commit"] = "unknown"
        with self.assertRaisesRegex(ValueError, "schema"): p.validate(row)

    def test_serialized_output_parameters_are_independent_authority(self):
        row = fixture()
        p.validate(row, readout=(row["output_bias"], row["output_weights"]))
        with self.assertRaisesRegex(ValueError, "serialized network"):
            p.validate(row, readout=(row["output_bias"] + 1, row["output_weights"]))

    def board_fixture(self, fen=chess.STARTING_FEN):
        row, board = fixture(), chess.Board(fen)
        mapping = [index % 92 for index in range(192)]
        mapping[p.raw_route(board)] = row["physical_head"]
        values = [p.static_zero(row["raw_nnue"] + offset, board) for offset in range(-24, 25)]
        cps = [p.to_cp(value, board) for value in values]
        row.update(supplied_fen=board.fen(), final_fen=board.fen(), moves=[], side_to_move=0 if board.turn else 1,
                   rule50=board.halfmove_clock, raw_offset_min=-24, raw_offset_max=24, cp_perspective="side_to_move",
                   in_check=board.is_check(), ordinary_eval_trace_available=not board.is_check(),
                   raw_offset_static_zero=values, raw_offset_cp=cps, helper_static_zero=values[24], helper_static_cp=cps[24],
                   ordinary_static_zero=None if board.is_check() else values[24], ordinary_static_cp=None if board.is_check() else cps[24],
                   static_loss_available=not board.is_check(), static_loss_unavailable_reason="in_check" if board.is_check() else None)
        return row, board, mapping

    def test_all_49_cp_offsets_preserve_actual_material_rule50_and_stm(self):
        for fen in (chess.STARTING_FEN, "8/8/4k3/8/4P3/3K4/8/8 b - - 41 70"):
            row, board, mapping = self.board_fixture(fen)
            p.validate(row, mapping=mapping, expected_board=board)
            row["raw_offset_cp"][0] += 1
            with self.assertRaisesRegex(ValueError, "CP lookup"):
                p.validate(row, mapping=mapping, expected_board=board)

    def test_in_check_helper_never_becomes_ordinary_static_target(self):
        row, board, mapping = self.board_fixture("4k3/8/8/8/8/8/4R3/4K3 b - - 0 1")
        self.assertTrue(board.is_check())
        p.validate(row, mapping=mapping, expected_board=board)
        row["ordinary_static_cp"] = row["helper_static_cp"]
        with self.assertRaisesRegex(ValueError, "promoted"):
            p.validate(row, mapping=mapping, expected_board=board)

    def test_lookup_domain_covers_four_int8_steps_at_all_quantizer_residues(self):
        largest = 0
        for before in range(-4096, 4097):
            base = p.tz(p.tz(before * 9600, 16384), 16)
            for delta in (-508, 508):
                after = p.tz(p.tz((before + delta) * 9600, 16384), 16)
                largest = max(largest, abs(after - base))
        self.assertEqual(largest, 19)
        self.assertLessEqual(largest, 24)

    def test_train_fixed_denominator_is_checked_without_validation_or_native_calls(self):
        from collect_training import planned
        requests, account = planned(HERE / "train.json")
        self.assertEqual((len(requests), len(account)), (586, 328))
        self.assertEqual(sum(bool(row["fixed_unavailability_reasons"]) for row in account), 35)
        self.assertEqual([row["row_id"] for row in requests], list(range(586)))

    def test_static_cpp_observer_calls_unchanged_helpers_in_actual_namespace(self):
        text = (HERE / "overlay/src/fc2_activation_command.h").read_text()
        self.assertIn("Stockfish::scale_evaluation", text)
        self.assertNotIn("Eval::scale_evaluation", text)
        self.assertIn("UCIEngine::to_cp(value, position)", text)
        self.assertIn("delta = -24; delta <= 24", text)
        self.assertIn('if (position.checkers()) out << "null"', text)

    def test_exact_prepared_overlay_and_macro_disabled_tokens(self):
        manifest = json.loads((HERE / "source-manifest.json").read_bytes())
        baseline = HERE.parents[2] / "source-before"
        if not baseline.exists():
            # Hosted driver supplies a pristine source checkout separately.
            baseline = Path("source")
        for name in patch_source.TARGETS:
            raw = (baseline / name).read_bytes()
            changed = patch_source.apply(name, raw)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), manifest["changes"][name]["before_sha256"])
            self.assertEqual(changed, (HERE / "overlay" / name).read_bytes())
            self.assertEqual(patch_source.tokens(patch_source.strip_diagnostic(changed.decode())), patch_source.tokens(raw.decode()))
        arch = (HERE / "overlay/src/nnue/nnue_architecture.h").read_text()
        self.assertIn("decltype(fc_2)::get_weight_index(i)", arch)
        self.assertIn("fc_2.write_parameters(readout)", arch)
        self.assertNotIn("search", patch_source.TARGETS)


if __name__ == "__main__": unittest.main()
