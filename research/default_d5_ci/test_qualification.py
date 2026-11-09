"""Synthetic parser and compiled-net proof checks; never starts an engine."""
import hashlib
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import chess
import benches
import qualify

START = chess.STARTING_FEN


def transcript():
    stdout = ["option name EvalFile type string default nn-d5c257728ba5.nnue", "uciok", "readyok",
              "info string NNUE evaluation using nn-d5c257728ba5.nnue (132MiB)"]
    stderr = []
    for index in range(1, 52):
        stderr.append(f"Position: {index}/51 ({START})")
        stdout.extend(["info depth 13 seldepth 14 multipv 1 score cp 20 nodes 100 nps 50000 hashfull 0 tbhits 0 time 2 pv e2e4 e7e5",
                       "bestmove e2e4 ponder e7e5"])
    stdout.append("readyok")
    stderr.append("Nodes searched  : 5100")
    specs = [{"source": START, "chess960": False} for _ in range(51)]
    return ("\n".join(stdout) + "\n").encode(), ("\n".join(stderr) + "\n").encode(), specs


class QualificationTests(unittest.TestCase):
    def test_all_51_complete_paths(self):
        stdout, stderr, specs = transcript()
        result = benches.parse_bench(stdout, stderr, 0, specs=specs)
        self.assertEqual(result["nodes"], 5100)
        self.assertEqual(result["searches"], 51)
        self.assertTrue(result["all_reported_pv_bestmove_and_ponder_paths_checked"])

    def test_illegal_any_pv_rejected(self):
        stdout, stderr, specs = transcript()
        stdout = stdout.replace(b"pv e2e4 e7e5", b"pv e2e5 e7e5", 1)
        with self.assertRaises(ValueError):
            benches.parse_bench(stdout, stderr, 0, specs=specs)

    def test_illegal_ponder_rejected(self):
        stdout, stderr, specs = transcript()
        stdout = stdout.replace(b"ponder e7e5", b"ponder e7e4", 1)
        with self.assertRaises(ValueError):
            benches.parse_bench(stdout, stderr, 0, specs=specs)

    def test_incomplete_roster_rejected(self):
        stdout, stderr, specs = transcript()
        with self.assertRaises(ValueError):
            benches.parse_bench(stdout, stderr.replace(b"Position: 51/51", b"Position: 50/51"), 0, specs=specs)

    def test_source_modes_and_fen4(self):
        raw = b'const std::vector<std::string> Defaults = {\n"8/8/8/8/8/6k1/6p1/6K1 w - -",\n"setoption name UCI_Chess960 value true",\n"4k3/8/8/8/8/8/8/R3K2R w HA - 0 1"\n};\n'
        specs = benches.benchmark_specs(raw, expected=2)
        self.assertEqual([s["chess960"] for s in specs], [False, True])
        self.assertTrue(benches.benchmark_root(specs[0]).is_game_over())
        board = benches.benchmark_root(specs[1])
        benches.benchmark_step(board, "e1g1")
        self.assertEqual(board.king(chess.WHITE), chess.G1)
        self.assertEqual(board.piece_type_at(chess.F1), chess.ROOK)

    def test_actual_default_guard(self):
        stdout, _, _ = transcript()
        qualify.check_default(stdout, require_use=True)
        with self.assertRaises(ValueError):
            qualify.check_default(stdout.replace(b"default nn-d5c257728ba5.nnue", b"default nn-18cd32e11a9c.nnue"), True)

    def test_actual_elf_span_hash(self):
        data = b"net!!"
        header = struct.pack("<16sHHIQQQIHHHHHH", b"\x7fELF\x02\x01\x01" + b"\0" * 9,
                             2, 62, 1, 0, 64, 0, 0, 64, 56, 1, 0, 0, 0)
        program = struct.pack("<IIQQQQQQ", 1, 4, 256, 0x400100, 0, 12, 12, 4096)
        raw = header + program
        raw += b"\0" * (256 - len(raw)) + data + b"\0" * 3 + struct.pack("<I", len(data))
        symbols = b"0000000000400100 R gEmbeddedNNUEData\n0000000000400105 R gEmbeddedNNUEEnd\n0000000000400108 R gEmbeddedNNUESize\n"
        with tempfile.TemporaryDirectory() as directory, patch.object(qualify, "NET_BYTES", len(data)), patch.object(qualify, "NETWORK", hashlib.sha256(data).hexdigest()):
            binary = Path(directory) / "synthetic.elf"
            binary.write_bytes(raw)
            result = qualify.elf_embedded(binary, symbols)
            self.assertEqual(result["file_offset"], 256)
            binary.write_bytes(raw[:256] + b"wrong" + raw[261:])
            with self.assertRaises(ValueError):
                qualify.elf_embedded(binary, symbols)


if __name__ == "__main__":
    unittest.main()
