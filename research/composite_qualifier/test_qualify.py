"""No-engine tests of mixed-signature qualification and input failure gates."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import chess
import qualify as q


NET = r"..\ridge-input\verified-readout-ridge-candidate\candidate.nnue"
ACK = q.native.ACK_PREFIX + NET + q.native.ACK_SUFFIX


def bench_fixture():
    roster = [chess.STARTING_FEN] * 51
    roster[47] = "8/8/8/8/8/6k1/6p1/6K1 w - - 0 1"
    roster[48] = "7k/7P/6K1/8/3B4/8/8/8 b - - 0 1"
    out = ["uciok", "readyok"]
    err = []
    for index, fen in enumerate(roster, 1):
        err.append(f"Position: {index}/51 ({fen})")
        out.append(ACK)
        if index in (48, 49):
            out.extend(["info depth 0 score " + ("cp 0" if index == 48 else "mate 0"), "bestmove (none)"])
        else:
            for depth in range(1, 14):
                out.append(f"info depth {depth} seldepth 18 multipv 1 score cp 20 nodes 100 nps 1000 hashfull 0 tbhits 0 time 10 pv e2e4 e7e5")
            out.append("bestmove e2e4 ponder e7e5")
    out.append("readyok")
    err.extend(["Total time (ms) : 10", "Nodes searched : 4900", "Nodes/second : 490000"])
    return "\n".join(out), "\n".join(err), q.sha(q.canonical(roster))


class QualifierTests(unittest.TestCase):
    def setUp(self):
        patch = mock.patch("subprocess.Popen", side_effect=AssertionError("Native engine forbidden in tests"))
        patch.start()
        self.addCleanup(patch.stop)

    def test_unknown_mixed_total_is_derived_from_all_51_positions(self):
        out, err, roster = bench_fixture()
        result = q.parse_bench(out, err, NET, roster)
        self.assertEqual(result["nodes"], 4900)
        self.assertEqual(len(result["positions"]), 51)
        self.assertEqual(result["per_position_nodes"][47:49], [0, 0])
        self.assertEqual(result["all_reported_pv_lines_legally_replayed"], 637)
        self.assertNotEqual(result["nodes"], q.native.BENCH_NODES)
        q.compare_benches(result, q.parse_bench(out, err, NET, roster))

    def test_roster_total_ack_and_illegal_pv_fail_closed(self):
        out, err, roster = bench_fixture()
        for changed_out, changed_err, changed_roster in (
                (out, err.replace("4900", "4901"), roster),
                (out, err, "0" * 64),
                (out.replace(ACK, ACK + "wrong", 1), err, roster),
                (out.replace("pv e2e4 e7e5", "pv e2e5 e7e5", 1), err, roster)):
            with self.subTest(roster=changed_roster), self.assertRaises(ValueError):
                q.parse_bench(changed_out, changed_err, NET, changed_roster)

    def test_repetition_requires_search_signature_not_only_total(self):
        out, err, roster = bench_fixture()
        first = q.parse_bench(out, err, NET, roster)
        second = copy.deepcopy(first)
        second["positions"][0]["score"] += 1
        with self.assertRaises(ValueError):
            q.compare_benches(first, second)

    def test_frc_benchmark_castling_reconstructs_legally(self):
        board = q.board_for_bench("r3k2r/ppp2ppp/8/8/8/8/PPP2PPP/R3K2R w HAha - 0 1")
        self.assertTrue(board.chess960)
        self.assertEqual(len(q.replay_line(board, ["e1h1", "e8a8"])), 2)

    def test_exact_artifact_run_and_digest_metadata(self):
        plan = q.plan_document()
        for pin in plan["artifacts"]:
            actual = copy.deepcopy(pin)
            q.validate_artifact_metadata(actual, pin)
            for key, value in (("id", -1), ("digest", "sha256:" + "0" * 64), ("expired", True)):
                changed = copy.deepcopy(actual)
                changed[key] = value
                with self.subTest(key=key), self.assertRaises(ValueError):
                    q.validate_artifact_metadata(changed, pin)
            changed = copy.deepcopy(actual)
            changed["workflow_run"]["head_sha"] = "0" * 40
            with self.assertRaises(ValueError):
                q.validate_artifact_metadata(changed, pin)

    def test_eval_parity_includes_raw_every_bucket_and_used_physical_head(self):
        rows = [{"fen": f"8/8/8/8/8/4k3/8/4K2R w - - 0 {i+1}", "physical": i} for i in range(22)]
        parsed = {"rows": [{"fen": row["fen"], "used_bucket": i, "raw_score": 10,
                            "buckets": [{"index": h, "printed_value": "+0.00", "used": h == i} for h in range(92)]}
                           for i, row in enumerate(rows)]}
        self.assertEqual(q.compare_evals(parsed, copy.deepcopy(parsed), rows)["roots"], 22)
        changed = copy.deepcopy(parsed)
        changed["rows"][0]["buckets"][91]["printed_value"] = "+0.01"
        with self.assertRaises(ValueError):
            q.compare_evals(parsed, changed, rows)
        changed = copy.deepcopy(parsed)
        changed["rows"][0]["raw_score"] = 11
        with self.assertRaises(ValueError):
            q.compare_evals(parsed, changed, rows)

    def test_hosted_guard_retains_result_without_launch(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(q, "source_receipts", return_value={}), \
                mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "false"}):
            path = Path(temp)
            with self.assertRaises(ValueError):
                q.run(path, path, True)
            result = json.loads((path / "RESULT.json").read_bytes())
            self.assertEqual(result["status"], "failed_evidence_preserved")
            self.assertEqual(result["native_processes"], 0)
            self.assertFalse(result["verified_elo_gain"])


if __name__ == "__main__":
    unittest.main()
