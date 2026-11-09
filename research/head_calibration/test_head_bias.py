"""Tiny synthetic test1757 heads, legal route examples and signed readout checks."""

import hashlib
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest

import chess
import numpy as np

import features_v19
import head_bias as bias
import permute_nnue as nnue


def fixture(selected=-470):
    """Encode three stored heads using the real affine widths and tiny feature tables."""
    roots = (0, 80, 191)
    fallback = tuple(r if r in roots else roots[r % 3] for r in range(192))
    table = struct.pack("<192H", *fallback)
    desc = b"toy"
    spec = nnue.Geometry(512, (1, 1, 1), 32, 32, 192, 3, len(desc), hashlib.sha256(table).hexdigest())
    data = bytearray(struct.pack("<3I", 0x6A448AFA, 0x9A4FCB66, len(desc)) + desc
                     + struct.pack("<3I", 0x5A7AB1E5, 192, 3) + table
                     + struct.pack("<I", 0xA3060B95) + bytes(2048 + 3 * 1024))
    stacks = []
    for fc2_bias in (41, selected, -700):
        data.extend(struct.pack("<I", 0x63337116))

        def add(payload):
            offset = len(data)
            data.extend(payload)
            return offset

        fc0 = [0] * 32
        fc0[0], fc0[30], fc0[31] = 128, 96, -130
        b0 = add(struct.pack("<32i", *fc0))
        w0 = add(b"\x80" + bytes(32 * 1024 - 2) + b"\x7f")
        b1 = add(struct.pack("<32i", 64, *([0] * 31)))
        w1 = add(bytes(32 * 64 - 1) + b"\x80")
        b2 = add(struct.pack("<i", fc2_bias))
        weights = [0] * 128
        weights[0], weights[32], weights[96], weights[127] = -128, 7, -3, 127
        w2 = add(struct.pack("<128b", *weights))
        stacks.append((b0, w0, b1, w1, b2, w2))
    return bytes(data), spec, stacks


def raw_reference(data, offsets):
    """Use the independently encoded field offsets and the reviewed scalar evaluator."""
    b0, w0, b1, w1, b2, w2 = offsets
    head = SimpleNamespace(
        fc0_bias=np.frombuffer(data, "<i4", 32, b0),
        fc0_weights=np.frombuffer(data, "i1", 32 * 1024, w0).reshape(32, 1024),
        fc1_bias=np.frombuffer(data, "<i4", 32, b1),
        fc1_weights=np.frombuffer(data, "i1", 32 * 64, w1).reshape(32, 64),
        fc2_bias=np.frombuffer(data, "<i4", 1, b2),
        fc2_weights=np.frombuffer(data, "i1", 128, w2).reshape(1, 128))
    scaled = features_v19.propagate(np.zeros(1024, dtype="u1"), head)
    return features_v19._trunc_div(scaled, 16)


class HeadBiasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data, cls.spec, cls.stacks = fixture()
        cls.layout = nnue.parse_layout(cls.data, cls.spec)
        cls.digest = hashlib.sha256(cls.data).hexdigest()

    def test_known_routes_and_aliases(self):
        cases = (
            (chess.STARTING_FEN, 187),
            ("7k/8/8/8/8/8/8/K7 w - - 0 1", 4),
            ("7k/8/8/8/8/8/P7/K7 w - - 0 1", 2),
            ("7k/8/8/8/8/8/7P/K7 w - - 0 1", 2),
            ("7k/8/8/8/8/8/P6P/K7 w - - 0 1", 0),
            ("7k/8/8/8/8/8/2Q5/K7 w - - 0 1", 16),
            ("7k/8/8/8/8/8/2Q5/K7 b - - 0 1", 10),
            ("7k/8/8/8/8/8/8/KN1B4 w - - 0 1", 5),  # two officers on one side
            ("6nk/8/8/8/8/8/8/KN6 w - - 0 1", 4),  # one per side
            ("7k/7p/8/8/8/8/8/KNB5 w - - 0 1", 27))
        for fen, raw in cases:
            with self.subTest(fen=fen):
                self.assertEqual(bias.head_for_fen(fen, self.layout), (raw, raw % 3))
        for fen in ("8/8/8/8/8/8/8/8 w - - 0 1", "invalid"):
            with self.assertRaises(ValueError):
                bias.head_for_fen(fen, self.layout)

    def test_actual_fc2_offset_and_single_word_edit(self):
        real = SimpleNamespace(spec=nnue.TEST1757, stacks_start=nnue.TEST1757.stacks_start)
        for h in (0, 1, 91):
            self.assertEqual(bias._bias_offset(real, h), 135121392 + 35208 * h + 35076)
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "parent"
            src.write_bytes(self.data)
            for delta, expected_raw in ((512, 9), (-512, -27)):
                out = Path(tmp) / str(delta)
                report = bias._write_bias(src, 1, delta, out, self.spec, self.digest)
                candidate = out.read_bytes()
                offset = self.stacks[1][4]
                expected = bytearray(self.data)
                struct.pack_into("<i", expected, offset, -470 + delta)
                self.assertEqual(candidate, expected)
                self.assertEqual(report["bias_offset"], offset)
                self.assertEqual((report["bias_before"], report["bias_after"]), (-470, -470 + delta))
                changes = [i for i, (a, b) in enumerate(zip(self.data, candidate)) if a != b]
                self.assertEqual(report["changed_byte_offsets"], changes)
                self.assertEqual(report["changed_byte_count"], 3 if delta > 0 else 1)
                self.assertEqual(report["candidate_sha256"], hashlib.sha256(candidate).hexdigest())
                self.assertEqual(report["inverse_sha256"], self.digest)
                affected = sorted({80} | {i for i in range(192) if i % 3 == 1})
                self.assertEqual(report["affected_raw_routes"], affected)
                self.assertEqual(raw_reference(self.data, self.stacks[1]), -8)
                self.assertEqual(raw_reference(candidate, self.stacks[1]), expected_raw)
                for other in (0, 2):
                    self.assertEqual(raw_reference(self.data, self.stacks[other]),
                                     raw_reference(candidate, self.stacks[other]))
            self.assertEqual(src.read_bytes(), self.data)

    def test_all_signed_scaling_cases(self):
        def exact(n):
            return (-1 if n < 0 else 1) * (abs(75 * n) // 2048)

        # One residue period is 2048 forwarded integers on either side of zero.
        for delta in (-512, 512):
            same_side, crossed = set(), set()
            for x in range(-4096, 4097):
                a, b = bias.raw_from_forwarded(x), bias.raw_from_forwarded(x + delta)
                self.assertEqual((a, b), (exact(x), exact(x + delta)))
                (crossed if x * (x + delta) < 0 else same_side).add(abs(b - a))
            self.assertEqual(same_side, {18, 19})
            self.assertEqual(crossed, {17, 18})
        self.assertEqual([bias.raw_from_forwarded(x) for x in (-240, 272)], [-8, 9])
        for x in (-(1 << 31), (1 << 31) - 1):
            self.assertEqual(bias.raw_from_forwarded(x), exact(x))
        for x in (1 << 31, -(1 << 31) - 1, 1.25, True):
            with self.assertRaises(ValueError):
                bias.raw_from_forwarded(x)

    def test_corruption_around_bias_or_in_another_head_fails_readback(self):
        offset = self.stacks[1][4]
        candidate = bytearray(self.data)
        struct.pack_into("<i", candidate, offset, 42)
        for corrupt in (offset - 1, offset, offset + 4, self.stacks[0][5], len(candidate) - 1):
            with self.subTest(corrupt=corrupt), self.assertRaises(ValueError):
                wrong = candidate.copy()
                wrong[corrupt] ^= 1
                bias._verify_saved(self.data, wrong, self.layout, offset, -470, 42, 512, self.digest)

    def test_bounds_pins_and_exclusive_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, out = Path(tmp) / "source", Path(tmp) / "candidate"
            src.write_bytes(self.data)
            for h, delta in ((-1, 512), (3, 512), (True, 512), (1, 0), (1, 513), (1, 512.0)):
                with self.subTest(head=h, delta=delta), self.assertRaises(ValueError):
                    bias._write_bias(src, h, delta, out, self.spec, self.digest)
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                bias._write_bias(src, 1, 512, out, self.spec, "0" * 64)
            with self.assertRaisesRegex(ValueError, "length"):
                bias.write_bias_candidate(src, 1, 512, out)
            for old, delta in (((1 << 31) - 1, 512), (-(1 << 31), -512)):
                payload, spec, _ = fixture(selected=old)
                src.write_bytes(payload)
                with self.assertRaisesRegex(ValueError, "overflow"):
                    bias._write_bias(src, 1, delta, out, spec, hashlib.sha256(payload).hexdigest())
            self.assertFalse(out.exists())
            out.write_bytes(b"existing")
            with self.assertRaises(FileExistsError):
                bias._write_bias(src, 1, 512, out, self.spec, self.digest)
            self.assertEqual(out.read_bytes(), b"existing")


if __name__ == "__main__":
    unittest.main()
