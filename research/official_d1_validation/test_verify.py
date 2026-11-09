"""Source/synthetic-only verification. No real model, teacher or heldout reads."""
from copy import deepcopy
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import verify as v
import validate_hosted as hosted


def root_fixture():
    ref = {"member": "invented", "bytes": 1, "sha256": "1" * 64}
    return {"schema": "official-d1-heldout-root-acceptance/1", "accepted": True, "scope": "one_fixed_official_d1_validation",
            "training_run_id": v.RUN, "training_artifact": {"id": v.ARTIFACT}, "source_commit": v.study.SOURCE,
            "source_tree": v.study.TREE, "engine_sha256": v.study.ENGINE, "parent_sha256": v.study.weights.PARENT_SHA256,
            "plan_sha256": v.PLAN, "no_performance_gate": True, "validation_sha256": v.VALIDATION_SHA,
            "validation_bytes": 162650, "validation_roots": 68,
            "files": {key: dict(ref) for key in ("frozen", "recipe", "candidate", "cache_manifest", "mapping", "train_cache", "edited_train_parity", "original_train_parity")}}


def validation_component(index):
    while True:
        component = sha256(("invented-component-" + str(index)).encode()).hexdigest()
        decision = sha256(("lichess-multipv-readout-v1/component|" + component).encode()).hexdigest()
        if int(decision, 16) % 5 == 0:
            return component, decision
        index += 1000


def synthetic_bundle():
    roots = []
    for index in range(68):
        board = v.chess.Board("4k3/8/8/8/8/8/8/R3K3 w Q - 0 1") if index == 0 else v.chess.Board()
        candidates = []
        for rank, token in enumerate(("a1a8", "a1a2") if index == 0 else ("e2e4", "d2d4"), 1):
            child = board.copy(); child.push_uci(token)
            candidates.append({"rank": rank, "standard_uci": [token], "child": {"fen": child.fen(en_passant="legal"),
                "position_id": "invented-child-" + str(index) + "-" + str(rank), "in_check": child.is_check(), "terminal": child.is_game_over(claim_draw=False)}})
        component, decision = validation_component(index)
        roots.append({"position_id": "invented-root-" + str(index), "component_id": component, "component_split_sha256": decision,
                      "split": "validation", "fen": board.fen(), "root_pov": "white", "teacher_margin_cp": 20, "candidates": candidates})
    return {"partition": "validation", "rows": roots}


def cache_record(index, active):
    board = v.chess.Board(); context = v.study.forward.eval_context(board)
    return {"source_root_index": index, "root_fen6": board.fen(), "component_id": "invented", "fold": "validation", "head": 31,
            "input128": [active] + [0] * 127, "skip_i32": 0, "side_to_move": context.side_to_move, "rule50": context.rule50,
            "simple_eval_stm": context.simple_eval_stm, "static_material": context.static_material, "wdl_material": context.wdl_material,
            "base_fc2_i32": 0, "base_raw_nnue_i32": 0, "base_static_zero_i32": 0, "base_static_cp_stm": 0}


def synthetic_transcript(request):
    text = "uciok\nreadyok\n"
    for expected in request["expected"]:
        cp = expected["static_cp_white"]
        text += ("info string NNUE evaluation using " + request["eval_file"] + " (synthetic)\n"
                 + "NNUE evaluation " + str(expected["raw_nnue_i32"]) + " (side to move, internal units)\n"
                 + f"Final evaluation {cp / 100:+.2f} (white side) [with scaled NNUE, ...]\nreadyok\n")
    return text.encode()


class HeldoutTests(unittest.TestCase):
    def test_distinct_official32_source_and_int64_quantizer_pins(self):
        plan = v.source_gate()
        self.assertEqual(plan["network_parent"]["heads"], 32)
        self.assertEqual(plan["quantizer"]["multiply_bits"], 64)
        row = cache_record(0, 0); row["base_fc2_i32"] = 1_000_000
        self.assertEqual(v.study.score(row)[0], 36621)

    def test_root_acceptance_fails_before_any_heldout_callback(self):
        root = root_fixture(); raw = v.canonical(root)
        for malformed, expected in ((raw, "0" * 64), (v.canonical({**root, "accepted": False}), v.sha(v.canonical({**root, "accepted": False})))):
            with self.assertRaises(ValueError):
                v.run_gated(root_raw=malformed, root_sha=expected, prepare_training=lambda _: self.fail("training opened"),
                            open_validation=lambda _: self.fail("heldout opened"), evaluate=lambda *args: self.fail("loss computed"))

    def test_producer_ci_success_is_not_independent_train_acceptance(self):
        accepted = {"schema": "official-d1-sibling-train-ci-acceptance/1", "status": "OFFICIAL_D1_TRAIN_CI_ACCEPTED", "run_id": v.RUN,
                    "source_commit": v.study.SOURCE, "source_tree": v.study.TREE, "engine_sha256": v.study.ENGINE,
                    "validation_read_or_predictions": False, "biases_and_every_other_byte_unchanged": True,
                    "inverse_restore_sha256": v.study.weights.PARENT_SHA256, "identity": False,
                    "candidate": {"sha256": "1" * 64, "bytes": v.study.weights.FILE_BYTES}}
        hosted.acceptance_gate(accepted, root_fixture())
        accepted["status"] = "completed_success"
        with self.assertRaisesRegex(ValueError, "producer CI"):
            hosted.acceptance_gate(accepted, root_fixture())

    def test_accepted_identity_needs_no_invented_candidate_or_inverse_receipt(self):
        root = root_fixture(); root["identity"] = True
        for key in ("candidate", "recipe", "edited_train_parity"):
            del root["files"][key]
        raw = v.canonical(root); v.root_gate(raw, v.sha(raw))
        accepted = {"schema": "official-d1-sibling-train-ci-acceptance/1", "status": "OFFICIAL_D1_TRAIN_CI_ACCEPTED", "run_id": v.RUN,
                    "source_commit": v.study.SOURCE, "source_tree": v.study.TREE, "engine_sha256": v.study.ENGINE,
                    "validation_read_or_predictions": False, "identity": True}
        hosted.acceptance_gate(accepted, root)

    def test_identity_stops_and_model_failure_stops_before_heldout(self):
        raw = v.canonical(root_fixture())
        result = v.run_gated(root_raw=raw, root_sha=v.sha(raw), prepare_training=lambda _: {"identity": True},
                            open_validation=lambda _: self.fail("heldout opened"), evaluate=lambda *args: self.fail("loss computed"))
        self.assertEqual(result["status"], "IDENTITY_STOPPED_BEFORE_HELDOUT")
        with self.assertRaises(ValueError):
            v.run_gated(root_raw=raw, root_sha=v.sha(raw), prepare_training=lambda _: {"identity": False, "model_bytes_verified": False, "edited_train_parity_verified": True},
                        open_validation=lambda _: self.fail("heldout opened"), evaluate=lambda *args: self.fail("loss computed"))

    def test_forward_inverse_checks_every_byte_and_preserves_biases(self):
        parent, edited = b"abcd\x0eefgh", b"abcd\x12efgh"
        edits = [{"offset": 4, "before": 14, "after": 18}]
        result = v.verify_streams(io.BytesIO(parent), io.BytesIO(edited), len(parent), edits, v.sha(parent), v.sha(edited))
        self.assertEqual(result["inverse_sha256"], v.sha(parent))
        with self.assertRaisesRegex(ValueError, "Non-frozen"):
            v.verify_streams(io.BytesIO(parent), io.BytesIO(b"xbcd\x12efgh"), len(parent), edits, v.sha(parent), v.sha(edited))

    def test_68_parent_accounting_includes_valid_counterpart_of_held_pair(self):
        bundle = synthetic_bundle()
        children, parents, dispositions = v.child_plan(bundle, [])
        self.assertEqual((len(parents), len(dispositions), len(children)), (68, 136, 135))
        self.assertEqual(parents[0]["source_root_indices"], [])
        self.assertIn(1, {child["source_root_index"] for child in children})
        self.assertEqual(sum(bool(parent["reasons"]) for parent in parents), 1)
        bundle["rows"][0]["component_split_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "component"):
            v.child_plan(bundle, [])

    def test_direct_native_raw_cp_and_no_holdout_improvement_gate(self):
        records = [cache_record(0, 127), cache_record(1, 0)]
        edits = [{"head": 31, "lane": 0, "delta": 1}]
        baseline_request = v.parity_request(records, "original.nnue", "1" * 64)
        edited_request = v.parity_request(records, "candidate.nnue", "2" * 64, edits)
        baseline = v.accept_parity(baseline_request, synthetic_transcript(baseline_request), b"", 0, records)
        candidate = v.accept_parity(edited_request, synthetic_transcript(edited_request), b"", 0, records)
        parents = [{"parent_position_id": "invented", "component_id": "invented", "teacher_margin_cp": 20, "root_pov": "black",
                    "reasons": [], "source_root_indices": [0, 1], "heads": [31, 31]}]
        report = v.loss_report(parents, records, baseline, candidate, edits, [])
        self.assertTrue(report["no_performance_gate"]); self.assertFalse(report["winner_decided"])
        a, b = report["measured_original_component_loss"], report["measured_edited_component_loss"]
        self.assertGreaterEqual(b["numerator"] / b["denominator"], a["numerator"] / a["denominator"])
        with self.assertRaises(ValueError):
            v.accept_parity(edited_request, synthetic_transcript(edited_request).replace(b"NNUE evaluation 4", b"NNUE evaluation 9"), b"", 0, records)

    def test_no_proxy_loss_before_both_raw_cp_parities(self):
        with self.assertRaisesRegex(ValueError, "both native"):
            v.loss_report([], [], {"status": "not-passed"}, {"status": "not-passed"}, [], [])

    def test_complete_bench_signatures_ignore_only_clock_nps(self):
        fen = v.chess.STARTING_FEN
        stdout = "uciok\nreadyok\n" + ("info depth 13 seldepth 14 multipv 1 score cp 0 nodes 100 nps 999 hashfull 0 tbhits 0 time 1 pv e2e4 e7e5\nbestmove e2e4 ponder e7e5\n" * 51) + "readyok\n"
        stderr = "".join(f"Position: {index}/51 ({fen})\n" for index in range(1, 52)) + "Nodes searched: 5100\n"
        specs = [{"source": fen, "chess960": False}] * 51
        first = v.parse_bench(stdout.encode(), stderr.encode(), 0, specs=specs)
        second = v.parse_bench(stdout.replace("nps 999", "nps 333").replace("time 1 ", "time 4 ").encode(), stderr.encode(), 0, specs=specs)
        self.assertEqual(first["search_signature_sha256"], second["search_signature_sha256"])
        self.assertEqual(first["nodes"], 5100)
        with self.assertRaises(ValueError):
            v.parse_bench(stdout.encode(), stderr.replace("Position: 51/51", "Position: 50/51").encode(), 0, specs=specs)

    def test_benchmark_source_modes_and_contextual_chess960_castling(self):
        # The final default roots are Chess960 even when castling spellings are
        # KQkq. Native king-to-rook castling must use that declared board mode.
        source = ('const std::vector<std::string> Defaults = {\n'
                  '"setoption name UCI_Chess960 value false",\n'
                  '"' + v.chess.STARTING_FEN + '",\n'
                  '"setoption name UCI_Chess960 value true",\n'
                  '"4k3/8/8/8/8/8/8/R3K2R w HA - 0 1",\n};\n').encode()
        specs = v.benchmark_specs(source, expected=2)
        self.assertEqual([spec["chess960"] for spec in specs], [False, True])
        board = v.benchmark_root(specs[1])
        v.benchmark_step(board, "e1h1")
        self.assertEqual(board.king(v.chess.WHITE), v.chess.G1)
        self.assertEqual(board.piece_at(v.chess.F1).piece_type, v.chess.ROOK)

    def test_benchmark_source_history_and_marker_are_not_guessed(self):
        spec = {"source": v.chess.STARTING_FEN + " moves e2e4 e7e5", "chess960": False}
        board = v.benchmark_root(spec)
        expected = v.chess.Board(); expected.push_uci("e2e4"); expected.push_uci("e7e5")
        self.assertEqual(board.fen(), expected.fen())
        with self.assertRaises(ValueError):
            v.official_benchmark_specs(b"unverified benchmark source")

    def test_actual_retained_position50_g8h8_castling_regression(self):
        # Exact position/PV from failed run42612377008 original-bench-1.
        spec = {"source": "bbqnnrkr/pppppppp/8/8/8/8/PPPPPPPP/BBQNNRKR w HFhf - 0 1 moves g2g3 d7d5 d2d4 c8h3 c1g5 e8d6 g5e7 f7f6", "chess960": True}
        board = v.benchmark_root(spec)
        fen = "bb1n1rkr/ppp1Q1pp/3n1p2/3p4/3P4/6Pq/PPP1PP1P/BB1NNRKR w HFhf - 0 5"
        self.assertEqual(board.fen(shredder=True, en_passant="legal"), fen)
        wrong = v.chess.Board(fen)
        for token in ("e7e3", "f8e8", "e3d3"):
            wrong.push_uci(token)
        self.assertFalse(wrong.is_legal(v.chess.Move.from_uci("g8h8")))
        for token in ("e7e3", "f8e8", "e3d3", "g8h8", "e1g2"):
            v.benchmark_step(board, token)
        self.assertEqual(board.piece_at(v.chess.F8).piece_type, v.chess.ROOK)

    def test_complete_standard_perft4_divides(self):
        values = {"a2a3":8457,"a2a4":9329,"b2b3":9345,"b2b4":9332,"c2c3":9272,"c2c4":9744,"d2d3":11959,"d2d4":12435,
                  "e2e3":13134,"e2e4":13160,"f2f3":8457,"f2f4":8929,"g2g3":9345,"g2g4":9328,"h2h3":8457,"h2h4":9329,
                  "b1a3":8885,"b1c3":9755,"g1f3":9748,"g1h3":8881}
        text = "uciok\nreadyok\n" + "".join(f"{move}: {count}\n" for move, count in values.items()) + "Nodes searched: 197281\nreadyok\n"
        self.assertEqual(v.parse_perft(text.encode(), b"", 0)["nodes"], 197281)
        with self.assertRaises(ValueError):
            v.parse_perft(text.replace("Nodes searched:", "a2a3: 8457\nNodes searched:").encode(), b"", 0)

    def test_hosted_entrypoint_refuses_local_before_any_data_open(self):
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "false"}), patch.object(Path, "read_bytes", side_effect=AssertionError("file opened")):
            with self.assertRaisesRegex(ValueError, "Root-dispatched"):
                hosted.run(root_sha="0" * 64, validation_url="never")


if __name__ == "__main__":
    unittest.main()
