"""Offline fixtures only; every native launch is mocked or absent."""
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import chess
import history_compare_v1 as history
import pgo_capture_v1 as pgo


HERE = Path(__file__).parent
NETWORK = history.EVALFILES["donor"]


def search_fixture():
    return [f"info string NNUE evaluation using {NETWORK} (130MiB, (1, 1))",
            "info depth 1 seldepth 1 score cp 10 nodes 12 time 1 nps 12000 pv e2e4 e7e5",
            "info depth 2 seldepth 3 score cp 15 nodes 30 time 2 nps 15000 pv d2d4 d7d5",
            "info depth 3 seldepth 4 score cp 15 nodes 50 pv d2d4 d7d5",
            "info depth 4 seldepth 5 score cp 15 nodes 70 pv d2d4 d7d5",
            "info depth 5 seldepth 6 score cp 15 nodes 90 pv d2d4 d7d5",
            "info depth 6 seldepth 7 score cp 15 nodes 110 pv d2d4 d7d5",
            "bestmove d2d4 ponder d7d5"]


class HistoryTests(unittest.TestCase):
    def test_fixed_histories_are_legal_and_cover_each_required_side(self):
        document = json.loads((HERE / "histories-v1.json").read_bytes())
        self.assertEqual(set(history.validate_histories(document)), history.REQUIRED_COVERAGE)
        self.assertEqual(sum(len(history.checkpoints(case)) for case in document["cases"]), 74)
        for case in document["cases"]:
            points = history.checkpoints(case)
            self.assertEqual([point["prefix"] for point in points[:-3]], list(range(len(case["moves"]) + 1)))
            self.assertEqual([point["kind"] for point in points[-3:]], ["repeat", "undo", "reuse"])
        broken = copy.deepcopy(document); broken["cases"][0]["moves"][0] = "e2e5"
        with self.assertRaises(ValueError): history.validate_histories(broken)

    def test_search_normalizes_timing_but_preserves_scores_nodes_and_pv(self):
        lines = search_fixture(); board = chess.Board()
        expected = history.parse_search(lines, board, NETWORK)
        changed_time = [line.replace("time 1 nps 12000", "time 987 nps 1") for line in lines]
        self.assertEqual(history.parse_search(changed_time, board, NETWORK), expected)
        for before, after in (("nodes 30", "nodes 31"), ("score cp 15", "score cp 16"),
                              ("pv d2d4 d7d5", "pv d2d4 g8f6")):
            changed = [line.replace(before, after) for line in lines]
            self.assertNotEqual(history.parse_search(changed, board, NETWORK), expected)

    def test_wrong_network_incomplete_reply_and_illegal_pv_refuse(self):
        lines = search_fixture()
        cases = [lines[:-1], lines + [lines[-1]],
                 [line.replace(NETWORK, history.EVALFILES["edited"]) for line in lines],
                 [line.replace("pv d2d4 d7d5", "pv e2e5") for line in lines]]
        for values in cases:
            with self.subTest(), self.assertRaises(ValueError): history.parse_search(values, chess.Board(), NETWORK)

    def test_go_waits_for_bestmove_before_ready_or_quit(self):
        session = history.Session.__new__(history.Session); events = []
        session.searching = False
        session.send = lambda command: events.append(command)
        def wait(predicate, seconds=60):
            if predicate("bestmove d2d4 ponder d7d5"):
                events.append("WAITED_BESTMOVE"); return search_fixture()
            self.assertTrue(predicate("readyok")); events.append("WAITED_READY"); return ["readyok"]
        session.wait_for = wait
        session.search({"fen": chess.STARTING_FEN, "moves": []}, 0, NETWORK)
        self.assertEqual(events[1:], ["go depth 6", "WAITED_BESTMOVE", "isready", "WAITED_READY"])
        self.assertNotIn("quit", events)
        self.assertFalse(session.searching)

    def test_pending_search_cleanup_kills_owned_process_without_pipelined_quit(self):
        session = history.Session.__new__(history.Session)
        session.process = Mock(); session.process.poll.return_value = None; session.process.returncode = -1
        session.commands = io.BytesIO(); session.threads = []; session.searching = True; session.forced = False
        session.output = Path("unused-fixture"); session.stem = "synthetic"; session.argv = ["fixture.exe"]; session.cwd = "."
        session.send = Mock(side_effect=AssertionError("Do not pipeline quit after go"))
        with patch.object(history, "file_hash", return_value="0" * 64), patch.object(history, "write_json"):
            with self.assertRaises(ValueError): session.close()
        session.process.kill.assert_called_once(); session.send.assert_not_called()

    def test_startup_failure_retains_command_identity_without_native_execution(self):
        with tempfile.TemporaryDirectory(dir=HERE) as directory:
            root = Path(directory).resolve(); executable = root / "fixture.exe"
            executable.write_bytes(b"synthetic non-executable fixture")
            with patch.object(history.subprocess, "Popen", side_effect=OSError("synthetic startup failure")):
                with self.assertRaises(OSError): history.Session(executable, root, root, "startup")
            record = json.loads((root / "startup.record.json").read_bytes())
            self.assertEqual(record["argv"], [str(executable)])
            self.assertIsNone(record["returncode"])
            self.assertEqual(record["startup_failure"]["type"], "OSError")


class PgoTests(unittest.TestCase):
    def test_wrapper_retains_complete_separate_pipes_and_default_arguments(self):
        with tempfile.TemporaryDirectory(dir=HERE) as directory:
            root = Path(directory).resolve(); output = root / "logs"; output.mkdir()
            (root / "stockfish.exe").write_bytes(b"synthetic non-executable fixture")
            stdout = b"info depth 1 score cp 1 nodes 10 pv e2e4\nbestmove e2e4\n"
            stderr = b"Position: 1/1 (synthetic)\nTotal time (ms) : 1\nNodes searched  : 10\nNodes/second : 10000\n"
            def invoke(argv, **kwargs):
                self.assertEqual(argv, [str(root / "stockfish.exe"), "bench"])
                kwargs["stdout"].write(stdout); kwargs["stderr"].write(stderr)
                return subprocess.CompletedProcess(argv, 0)
            previous = Path.cwd(); forwarded = io.BytesIO()
            try:
                os.chdir(root)
                with patch.object(pgo.subprocess, "run", side_effect=invoke), patch.object(pgo.sys, "stdout", SimpleNamespace(buffer=forwarded)):
                    self.assertEqual(pgo.run(output), 0)
            finally: os.chdir(previous)
            self.assertEqual((output / "pgo-native.stdout.txt").read_bytes(), stdout)
            self.assertEqual((output / "pgo-native.stderr.txt").read_bytes(), stderr)
            self.assertEqual(forwarded.getvalue(), stdout + stderr)
            record = json.loads((output / "pgo-native.record.json").read_bytes())
            self.assertEqual(record["default_profile_command"], "./stockfish.exe bench")
            self.assertEqual(record["instrumented_sha256_before"], record["instrumented_sha256_after"])


if __name__ == "__main__": unittest.main()
