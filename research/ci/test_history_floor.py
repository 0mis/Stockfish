"""Small rejection tests for real evidence and source selection failures."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from capture_common import parse_bench
import embedded_reference as embedded
from native_eval import BORDER, EVALFILE, NETWORK_NAME, capture_tables, compare_tables
from source_evidence import HERE, read_pin, sole_delta


def example_eval():
    rows = [{"id": "probe", "physical": 10, "donor": -7, "side_to_move": "white"}]
    lines = ["option name EvalFile type string default " + NETWORK_NAME, "uciok", "readyok",
             f"info string NNUE evaluation using {EVALFILE} (131MiB, (131952, 1024, 32, 32, 1))",
             "NNUE network contributions (Normalized, White to move)", BORDER, "|   Bucket   | Evaluation |", BORDER]
    for bucket in range(92):
        sign = "" if bucket == 2 else "+"
        lines.append(f"|  {bucket}  |  {sign}  0.00   |" + (" <-- this bucket is used" if bucket == 10 else ""))
    lines += [BORDER, "NNUE evaluation   -7 (side to move, internal units)", "readyok"]
    return "\n".join(lines), rows


class EvidenceTests(unittest.TestCase):
    def test_inactive_bucket_change_missing_row_or_wrong_marker_is_detected(self):
        stdout, rows = example_eval()
        expected = capture_tables(stdout, "", rows)
        changed = stdout.replace("|  14  |  +  0.00   |", "|  14  |  -  0.00   |")
        with self.assertRaisesRegex(ValueError, "differ"):
            compare_tables(capture_tables(changed, "", rows), expected)
        for bad in (stdout.replace("|  13  |  +  0.00   |\n", ""), stdout.replace(" <-- this bucket is used", ""),
                    stdout.replace("-7 (side to move", "-6 (side to move"), stdout + "\nreadyok"):
            with self.subTest(), self.assertRaises(ValueError):
                capture_tables(bad, "", rows)

    def test_only_exact_nonzero_source_ids_and_single_tree_delta(self):
        pin = read_pin(HERE / "source-pin.json")
        with tempfile.TemporaryDirectory() as temp:
            spec = Path(temp) / "pin.json"
            for value in (None, "main", "refs/heads/example", "0" * 40):
                spec.write_text(json.dumps({**pin, "source_commit": value}), encoding="utf-8")
                with self.subTest(value=value), self.assertRaises(ValueError):
                    read_pin(spec)
        base = {"search.cpp": ("100644", "blob", "old"), "evaluate.h": ("100644", "blob", "original")}
        new = copy.deepcopy(base)
        new["search.cpp"] = ("100644", "blob", "updated")
        sole_delta(base, new, "search.cpp")
        for bad in ({**new, "evaluate.h": ("100644", "blob", "different")},
                    {**new, "search.cpp": ("100755", "blob", "updated")}, {"search.cpp": new["search.cpp"]}):
            with self.subTest(), self.assertRaises(ValueError):
                sole_delta(base, bad, "search.cpp")

    def test_embedding_requires_verified_reference_unique_prefix_and_exact_network(self):
        network = embedded.NETWORK_HEADER + bytes(range(64))
        prefix_size = len(embedded.NETWORK_HEADER) + 5
        prefix = hashlib.sha256(network[:prefix_size]).hexdigest()
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            reference = parent / "reference.exe"
            target = parent / "target"
            cases = (("accepted", b"PE" + network + b"tail", True),
                     ("ambiguous", network * 2, False),
                     ("damaged", b"PE" + network[:-1] + b"x", False),
                     ("truncated", network[:-3], False))
            spec = embedded.PinnedFile("parent.nnue", len(network), hashlib.sha256(network).hexdigest())
            for label, content, good in cases:
                reference.write_bytes(content)
                refspec = embedded.PinnedFile("reference.exe", len(content), hashlib.sha256(content).hexdigest())
                with self.subTest(label=label), patch.multiple(embedded, REFERENCE=refspec, NETWORK=spec,
                                                               PREFIX_SIZE=prefix_size, PREFIX_SHA256=prefix):
                    if good:
                        record = embedded.install_reference_network(reference, target)
                        self.assertEqual(target.read_bytes(), network)
                        self.assertEqual(record["offset_bytes"], 2)
                        self.assertEqual(embedded.verify_finished_embedding(reference)["sha256"], spec.sha256)
                        target.unlink()
                    else:
                        with self.assertRaises(ValueError):
                            embedded.install_reference_network(reference, target)
                        with self.assertRaises(ValueError):
                            embedded.verify_finished_embedding(reference)
                        self.assertFalse(target.exists())
            with patch.object(embedded, "REFERENCE", refspec):
                reference.write_bytes(b"x" * len(content))
                with self.assertRaises(ValueError):
                    embedded.install_reference_network(reference, target)

    def test_bench_parser_accepts_new_signature_and_rejects_missing_position(self):
        stdout = "info depth 13 score cp 3 nodes 11 nps 10\nbestmove e2e4\ninfo depth 13 score cp 2 nodes 34 nps 20\nbestmove d2d4\n"
        stderr = "Position: 1/2 (one)\nPosition: 2/2 (two)\nTotal time (ms) : 2\nNodes searched : 45\nNodes/second : 22500\n"
        self.assertEqual(parse_bench(stdout, stderr)["per_position_nodes"], [11, 34])
        with self.assertRaises(ValueError):
            parse_bench(stdout, stderr.replace("Position: 2/2 (two)\n", ""))


if __name__ == "__main__":
    unittest.main()
