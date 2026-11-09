"""In-memory/small-file fixtures. No trained network, corpus, search or native engine."""
import hashlib
from pathlib import Path
import struct
import tempfile
import unittest

import numpy as np

import bias_patch_set as edits
import permute_nnue as nnue


def fixture(first=-1, last=511):
    roots = list(range(0, 184, 2))
    fallback = [r if r in roots else roots[(7 * r + 17) % 92] for r in range(192)]
    table = struct.pack("<192H", *fallback)
    spec = nnue.Geometry(4, (1, 2, 1), 2, 1, 192, 92, 3, hashlib.sha256(table).hexdigest())
    data = bytearray(struct.pack("<3I", 0x6A448AFA, 0x9A4FCB66, 3) + b"toy"
                     + struct.pack("<3I", 0x5A7AB1E5, 192, 92) + table + struct.pack("<I", 0xA3060B95)
                     + bytes(range(48)))
    offsets = []
    for h in range(92):
        data.extend(struct.pack("<I", 0x63337116))
        # Two FC0 and one FC1 signed biases and their padded 32-byte rows.
        data.extend(bytes((53 * k + h) % 256 for k in range(3 * (4 + 32))))
        offsets.append(len(data))
        value = first if h == 0 else last if h == 91 else 1001 * h - 32000
        data.extend(struct.pack("<i", value) + bytes((77 * k + h) % 256 for k in range(32)))
    return bytes(data), spec, offsets, fallback


def fixture_writer(data, spec, source, steps, output, mode):
    expected = hashlib.sha256(data).hexdigest()

    def authenticate(raw):
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError("wrong fixture hash")
        return nnue.parse_layout(raw, spec)

    return edits._write(source, steps, output, mode, len(data), expected, authenticate)


class ReadoutSetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data, self.spec, self.offsets, self.fallback = fixture()
        self.src = self.root / "original"
        self.src.write_bytes(self.data)

    def write(self, steps, name, mode):
        return fixture_writer(self.data, self.spec, self.src, steps, self.root / name, mode)

    def test_distinct_edit_words_routes_and_inverse(self):
        candidates = [0] * 92
        candidates[0], candidates[38], candidates[91] = 16, 256, -256
        for name, changes, mode in (("fitted", candidates, "candidate"), ("sensitivity", [512] * 92, "probe")):
            r = self.write(changes, name, mode)
            expected = bytearray(self.data)
            for (off, d) in zip(self.offsets, changes):
                old, = struct.unpack_from("<i", self.data, off)
                struct.pack_into("<i", expected, off, old + d)
            disk = (self.root / name).read_bytes()
            self.assertEqual(disk, expected)
            diffs = [i for i, (a, b) in enumerate(zip(self.data, disk)) if a != b]
            self.assertEqual(r["changed_byte_count"], len(diffs))
            self.assertEqual([i for h in r["heads"] for i in h["changed_byte_offsets"]], diffs)
            self.assertEqual(r["changed_heads"], [i for i, d in enumerate(changes) if d])
            self.assertEqual(r["affected_raw_routes"], [i for i, f in enumerate(self.fallback) if changes[f // 2]])
            for head, off, d in zip(r["heads"], self.offsets, changes):
                self.assertEqual((head["bias_offset"], head["delta"], head["raw_delta_bounds"]),
                                 (off, d, list(edits.raw_delta_bounds(d))))
                self.assertEqual(head["raw_routes"], [raw for raw, target in enumerate(self.fallback)
                                                      if target // 2 == head["physical_head"]])
                for diff in head["changed_bytes"]:
                    i = diff["offset"]
                    self.assertEqual((diff["before"], diff["after"]), (self.data[i], disk[i]))
            self.assertEqual(r["candidate_sha256"], hashlib.sha256(disk).hexdigest())
            self.assertEqual(r["inverse_sha256"], hashlib.sha256(self.data).hexdigest())
            self.assertTrue(r["output_written"])
            self.assertFalse(r["identity"])
        self.assertEqual(self.src.read_bytes(), self.data)

    def test_zero_identity_and_no_overwrite(self):
        receipt = self.write([0] * 92, "identity", "candidate")
        self.assertTrue(receipt["identity"])
        self.assertFalse(receipt["output_written"] or (self.root / "identity").exists())
        self.assertEqual(receipt["parent_sha256"], receipt["candidate_sha256"])
        self.assertEqual(receipt["parent_sha256"], receipt["inverse_sha256"])
        self.assertEqual(receipt["changed_byte_count"], 0)
        (self.root / "existing").write_bytes(b"preserve")
        with self.assertRaises(FileExistsError):
            self.write([512] * 92, "existing", "probe")
        self.assertEqual((self.root / "existing").read_bytes(), b"preserve")

    def test_integer_lattice_caps_hash_and_overflow_fail_closed(self):
        for step in (-272, -255, -8, 1, 15, 17, 255, 272, 16.0, True, np.bool_(False)):
            with self.subTest(step=step), self.assertRaises(ValueError):
                self.write([step] + [0] * 91, "reject", "candidate")
        for steps, mode in (([0] * 91, "candidate"), ([512] * 91 + [0], "probe"), ([0] * 92, "other")):
            with self.assertRaises(ValueError):
                self.write(steps, "reject", mode)
        with self.assertRaisesRegex(ValueError, "size"):
            edits.write_bias_patch_set(self.src, [512] * 92, self.root / "reject", "probe")
        corrupt = bytearray(self.data)
        corrupt[-1] ^= 1
        self.src.write_bytes(corrupt)
        with self.assertRaisesRegex(ValueError, "hash"):
            self.write([512] * 92, "reject", "probe")
        for first, d in (((1 << 31) - 1, 16), (-(1 << 31), -16)):
            data, spec, _, _ = fixture(first)
            self.src.write_bytes(data)
            with self.assertRaisesRegex(ValueError, "overflow"):
                fixture_writer(data, spec, self.src, [d] + [0] * 91, self.root / "reject", "candidate")
        self.assertFalse((self.root / "reject").exists())

    def test_readback_detects_intervening_and_final_bytes(self):
        deltas = [0] * 92
        deltas[1], deltas[-1] = 16, -16
        r = self.write(deltas, "fitted", "candidate")
        good = (self.root / "fitted").read_bytes()
        layout = nnue.parse_layout(self.data, self.spec)
        for i in (self.offsets[0], self.offsets[1] - 1, self.offsets[1], self.offsets[91] + 4, len(good) - 1):
            with self.subTest(i=i), self.assertRaises(ValueError):
                bad = bytearray(good)
                bad[i] ^= 1
                edits._verify_saved(self.data, bad, layout, r["heads"], r["parent_sha256"])

    def test_exact_raw_score_extrema_including_zero_crossing(self):
        def trunc(n, d):
            return (1 if n >= 0 else -1) * (abs(n) // d)

        def native(f):
            return trunc(trunc(9600 * f, 16384), 16)

        for d in (-512, *range(-256, 257, 16), 512):
            differences = {native(f + d) - native(f) for f in range(-3072, 3073)}
            lo, hi = edits.raw_delta_bounds(d)
            self.assertEqual(differences, set(range(lo, hi + 1)))
        self.assertEqual(edits.raw_delta_bounds(16), (0, 1))
        self.assertEqual(edits.raw_delta_bounds(48), (0, 2))
        self.assertEqual(edits.raw_delta_bounds(256), (8, 10))
        self.assertEqual(edits.raw_delta_bounds(512), (17, 19))
        with self.assertRaises(ValueError):
            edits.raw_delta_bounds(np.int64(-(1 << 63)))


if __name__ == "__main__":
    unittest.main()
