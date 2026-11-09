"""Small synthetic tests. No engine, compiler, network, or Git processes start."""
import copy
import json
from pathlib import Path
import unittest

import build_and_capture_v1 as driver


class DriverTests(unittest.TestCase):
    def setUp(self):
        self.parent = b"0123456789abcdef"
        self.expected = b"01AB456789aCDdef"
        self.patch = {"bytes": len(self.parent), "parent_sha256": driver.digest(self.parent),
                      "candidate_sha256": driver.digest(self.expected), "changed_bytes": 4,
                      "runs": [{"offset": 2, "before": "3233", "after": "4142"},
                               {"offset": 11, "before": "6263", "after": "4344"}]}

    def test_patch_reconstructs_exact_output_without_changing_parent(self):
        before = bytes(self.parent)
        self.assertEqual(driver.sparse_patch(self.parent, self.patch), self.expected)
        self.assertEqual(self.parent, before)

    def test_patch_rejects_wrong_bytes_overlaps_bounds_and_wrong_final_hash(self):
        changes = [lambda p: p.update(parent_sha256="0" * 64),
                   lambda p: p["runs"][0].update(before="3031"),
                   lambda p: p["runs"][1].update(offset=3),
                   lambda p: p["runs"][1].update(offset=16),
                   lambda p: p["runs"][0].update(after="41"),
                   lambda p: p.update(candidate_sha256="0" * 64),
                   lambda p: p.update(changed_bytes=3)]
        for change in changes:
            patch = copy.deepcopy(self.patch)
            change(patch)
            with self.subTest(change=changes.index(change)), self.assertRaises(ValueError):
                driver.sparse_patch(self.parent, patch)

    def test_bench_parser_requires_complete_searches_and_matching_sum(self):
        stdout = ("info depth 13 score cp 1 nodes 11 nps 10\nbestmove e2e4\n"
                  "info depth 13 score cp 2 nodes 13 nps 10\nbestmove d2d4\n")
        stderr = ("Position: 1/2 (synthetic one)\nPosition: 2/2 (synthetic two)\n"
                  "Total time (ms) : 2\nNodes searched  : 24\nNodes/second    : 12000\n")
        self.assertEqual(driver.parse_bench(stdout, stderr)["nodes"], 24)
        for out, err in ((stdout, stderr.replace("Nodes searched  : 24", "Nodes searched  : 25")),
                         (stdout, stderr.replace("Position: 2/2 (synthetic two)\n", "")),
                         (stdout.replace("bestmove d2d4\n", ""), stderr)):
            with self.subTest(), self.assertRaises(ValueError):
                driver.parse_bench(out, err)

    def test_frozen_eval_commands_preserve_22_probe_order_and_explicit_networks(self):
        probes = json.loads((Path(__file__).parent / "probes-v1.json").read_bytes())
        for role, name in driver.EVALFILES.items():
            self.assertEqual(probes["arms"][role]["external_evalfile"]["path"], name)
            text = driver.commands_for(name, "eval", probes["rows"])
            self.assertIn(f"setoption name EvalFile value {name}\n", text)
            self.assertEqual([line[13:] for line in text.splitlines() if line.startswith("position fen ")],
                             [row["fen"] for row in probes["rows"]])
            self.assertEqual(text.splitlines().count("eval"), 22)
            self.assertEqual(text.splitlines().count("isready"), 23)
            self.assertTrue(text.endswith("quit\n"))
            self.assertNotIn("C:\\", text)
            self.assertIn("bench 16 1 13 default depth", driver.commands_for(name, "bench"))
            self.assertIn("position startpos\ngo perft 4", driver.commands_for(name, "perft"))

    def test_command_injection_in_filename_or_fen_is_rejected(self):
        with self.assertRaises(ValueError):
            driver.commands_for("network.nnue\nquit", "bench")
        with self.assertRaises(ValueError):
            driver.commands_for(driver.EVALFILES["donor"], "eval", [{"fen": "startpos\nquit"}])


if __name__ == "__main__":
    unittest.main()
