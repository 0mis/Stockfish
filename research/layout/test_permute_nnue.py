"""Synthetic format/evaluator tests. No engine, original network or training data."""

import hashlib
import json
from dataclasses import replace
from pathlib import Path
import random
import struct
import tempfile
import unittest

import numpy as np

import permute_nnue as ed


P = (2, 0, 5, 1, 3, 4)  # non-self-inverse; direction matters
LANES = 12
FEATURE_ROWS = (3, 2, 6)


def fixture():
    """Encode directly (no production serializer). Keep real route/FC0 counts, but
    use small feature tables, nonzero padding and a short FC1 for cheap scalar tests.
    Real tag constants mark fields in this miniature model; it is not an engine net.
    """
    rng = random.Random(570)
    data = bytearray()

    def append(payload):
        start = len(data)
        data.extend(payload)
        return start

    roots = list(range(0, 184, 2))
    fallback = [i if i in roots else roots[(i * 37 + 13) % 92] for i in range(192)]
    table = struct.pack("<192H", *fallback)
    desc = b"\x00fixture\xff"
    append(struct.pack("<3I", 0x6A448AFA, 0x9A4FCB66, len(desc)) + desc)
    route_header = append(struct.pack("<3I", 0x5A7AB1E5, 192, 92))
    routes = append(table)
    ft_tag = append(struct.pack("<I", 0xA3060B95))
    bias = append(struct.pack("<12h", 90, 60, 200, -32768, 128, -10,
                               100, 170, 80, 32767, 255, 108))
    plane_offsets = []
    for rows in FEATURE_ROWS:
        plane_offsets.append(len(data))
        for _ in range(rows):
            vals = [-128, *(rng.randint(-128, 127) for _ in range(LANES - 2)), 127]
            append(struct.pack("<12b", *vals))
    stacks = []
    for _ in roots:
        start = append(struct.pack("<I", 0x63337116))
        fc0_bias = append(struct.pack("<32i", *(rng.randint(-4000, 4000) for _ in range(32))))
        fc0_weights = len(data)
        for _ in range(32):
            vals = [-128, *(rng.randint(-128, 127) for _ in range(LANES - 2)), 127]
            append(struct.pack("<12b", *vals) + bytes(rng.randrange(1, 256) for _ in range(20)))
        fc1_bias = append(struct.pack("<3i", *(rng.randint(-10000, 10000) for _ in range(3))))
        fc1_weights = append(struct.pack("<192b", *(rng.randint(-128, 127) for _ in range(192))))
        fc2_bias = append(struct.pack("<i", rng.randint(-10000, 10000)))
        fc2_weights = append(struct.pack("<70b", *(rng.randint(-128, 127) for _ in range(70)))
                             + bytes(rng.randrange(1, 256) for _ in range(26)))
        stacks.append((start, fc0_bias, fc0_weights, fc1_bias, fc1_weights, fc2_bias, fc2_weights))
    spec = ed.Geometry(6, FEATURE_ROWS, 32, 3, 192, 92, len(desc), hashlib.sha256(table).hexdigest())
    return spec, bytes(data), (route_header, routes, ft_tag, bias, plane_offsets, stacks)


def reference_transform(data, loc):
    """Permute individual signed scalars by direct offsets, without NumPy or the editor."""
    _, _, _, bias, planes, stacks = loc
    result = bytearray(data)
    order = (*P, *(6 + old for old in P))

    def row(start, fmt):
        width = struct.calcsize(fmt)
        for new, old in enumerate(order):
            value, = struct.unpack_from(fmt, data, start + old * width)
            struct.pack_into(fmt, result, start + new * width, value)

    row(bias, "<h")
    for start, rows in zip(planes, FEATURE_ROWS):
        for i in range(rows):
            row(start + i * LANES, "<b")
    for _, _, weights, *_ in stacks:
        for i in range(32):
            row(weights + 32 * i, "<b")
    return bytes(result)


def scalar(data, loc, raw_route, reverse):
    """Independent integer forward pass incl. clipping/product, both activation
    branches, raw FC0 skip, scaling and C++ signed division. Reads fixture bytes.
    """
    _, routes, _, bias, planes, stacks = loc
    sides = (((0, 2), (1,), (0, 2, 4)), ((1,), (0, 1), (1, 3, 5)))
    if reverse:
        sides = sides[::-1]
    x = []
    for selected in sides:
        acc = list(struct.unpack_from("<12h", data, bias))
        for ids, start in zip(selected, planes):
            for r in ids:
                weights = struct.unpack_from("<12b", data, start + r * LANES)
                acc = [a + w for a, w in zip(acc, weights)]
        acc = [min(255, max(0, (a + 32768) % 65536 - 32768)) for a in acc]
        x.extend(a * b // 512 for a, b in zip(acc[:6], acc[6:]))
    fallback = struct.unpack_from("<192H", data, routes)
    roots = [i for i in range(192) if fallback[i] == i]
    _, b0, w0, b1, w1, b2, w2 = stacks[roots.index(fallback[raw_route])]

    def affine(values, biases, weights, count, stride):
        result = list(struct.unpack_from(f"<{count}i", data, biases))
        for out in range(count):
            ws = struct.unpack_from(f"<{len(values)}b", data, weights + out * stride)
            result[out] += sum(w * a for w, a in zip(ws, values))
        return result

    def activate(values, shift):
        return ([min(127, v * v >> (2 * shift + 7)) for v in values]
                + [min(127, max(0, v >> shift)) for v in values])

    def trunc(n, d):
        return (1 if n >= 0 else -1) * (abs(n) // d)

    f0 = affine(x, b0, w0, 32, 32)
    a0 = activate(f0, 7)
    f1 = affine(a0, b1, w1, 3, 64)
    f2, = affine(a0 + activate(f1, 6), b2, w2, 1, 96)
    scaled = trunc((f2 + f0[30] - f0[31]) * 9600, 16384)
    return x, f0, f1, f2, scaled, trunc(scaled, 16)


class PermutationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec, cls.data, cls.loc = fixture()
        cls.edited = ed.transform(cls.data, P, cls.spec)

    def test_original_geometry_and_readonly_views(self):
        self.assertEqual((ed.TEST1757.bias_offset, ed.TEST1757.stacks_start,
                          ed.TEST1757.stack_bytes, ed.TEST1757.size),
                         (496, 135121392, 35208, 138360528))
        layout = ed.parse_layout(self.data, self.spec)
        self.assertEqual((layout.bias_offset, layout.plane_offsets, layout.stacks_start),
                         (self.loc[3], tuple(self.loc[4]), self.loc[5][0][0]))
        self.assertEqual(len(layout.fallback), 192)
        self.assertEqual(set(layout.raw_to_physical), set(range(92)))
        roots = list(range(0, 184, 2))
        for raw, f in enumerate(layout.fallback):
            self.assertEqual(layout.raw_to_physical[raw], roots.index(f))
        ft = ed.ft_views(bytearray(self.data), layout)
        self.assertEqual((ft.ft_biases.shape, ft.ft_biases.min(), ft.ft_biases.max()),
                         ((12,), -32768, 32767))
        for arr, rows in zip((ft.threat_weights, ft.pair_weights, ft.psq_weights), FEATURE_ROWS):
            self.assertEqual((arr.shape, arr.min(), arr.max(), arr.flags.writeable),
                             ((rows, 12), -128, 127, False))

    def test_exact_bytes_padding_and_inverse(self):
        self.assertNotEqual(self.edited, self.data)
        self.assertEqual(self.edited, reference_transform(self.data, self.loc))
        recovered = ed.transform(self.edited, ed.inverse_permutation(P, 6), self.spec)
        self.assertEqual(recovered, self.data)
        self.assertEqual(hashlib.sha256(recovered).digest(), hashlib.sha256(self.data).digest())
        self.assertEqual(ed.transform(self.data, range(6), self.spec), self.data)

    def test_scalar_output_every_route_and_both_perspectives(self):
        scores = set()
        order = (*P, *(i + 6 for i in P))
        for route in range(192):
            for reverse in (False, True):
                x, *before = scalar(self.data, self.loc, route, reverse)
                y, *after = scalar(self.edited, self.loc, route, reverse)
                self.assertEqual(y, [x[old] for old in order])
                self.assertEqual(before, after)
                scores.add(before[-1])
        self.assertGreater(len(scores), 92)
        self.assertLess(min(scores), 0)
        self.assertGreater(max(scores), 0)
        # Changing only the FT is observable in the independent final evaluator.
        dense = self.loc[5][0][0]
        uncoupled = self.edited[:dense] + self.data[dense:]
        self.assertTrue(any(scalar(uncoupled, self.loc, r, False)[-1] !=
                            scalar(self.data, self.loc, r, False)[-1] for r in (0, 2, 4)))

    def test_invalid_permutations(self):
        for p in (P[:-1], (*P, 3), [0, 0, 1, 2, 3, 4], [-1, *P[1:]], [6, *P[1:]],
                  [2.0, *P[1:]], [True, *P[1:]], [np.bool_(False), *P[1:]], ["2", *P[1:]]):
            with self.subTest(p=p), self.assertRaises(ValueError):
                ed.transform(self.data, p, self.spec)

    def test_malformed_format_including_aliases_and_last_stack(self):
        header, routes, ft, _, _, stacks = self.loc
        locations = [(0, "<I", 0), (4, "<I", 0), (8, "<I", 0), (header, "<I", 0),
                     (header + 4, "<I", 191), (header + 8, "<I", 91), (routes + 2, "<H", 192),
                     (routes + 2, "<H", 3),  # non-root targets are not followed
                     (routes, "<H", 2),  # removes a root
                     (routes + 2, "<H", 0),  # valid alias, but wrong pinned fallback table
                     (ft, "<I", 0), (stacks[0][0], "<I", 0), (stacks[-1][0], "<I", 0)]
        for start, fmt, value in locations:
            with self.subTest(start=start, value=value), self.assertRaises(ValueError):
                bad = bytearray(self.data)
                struct.pack_into(fmt, bad, start, value)
                spec = self.spec
                if (start, value) in ((routes + 2, 192), (routes + 2, 3), (routes, 2)):
                    # Supply the modified table's own digest, so rejection actually
                    # depends on checking self-roots/aliases and not merely the SHA.
                    spec = replace(spec, fallback_sha256=hashlib.sha256(bad[routes:routes + 384]).hexdigest())
                ed.parse_layout(bad, spec)
        for bad in (self.data[:-1], self.data + b"!", b""):
            with self.assertRaises(ValueError):
                ed.parse_layout(bad, self.spec)

    def test_verified_disk_output_and_pin_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, out, receipt = [Path(tmp) / name for name in ("original", "candidate", "receipt")]
            src.write_bytes(self.data)
            sha = hashlib.sha256(self.data).hexdigest()
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                ed._write_verified(src, P, out, receipt, self.spec, "0" * 64)
            self.assertFalse(out.exists() or receipt.exists())
            result = ed._write_verified(src, P, out, receipt, self.spec, sha)
            self.assertEqual(out.read_bytes(), self.edited)
            self.assertEqual(src.read_bytes(), self.data)
            self.assertEqual(json.loads(receipt.read_bytes()), result)
            self.assertEqual(result["inverse_sha256"], sha)
            self.assertEqual(result["candidate_sha256"], hashlib.sha256(self.edited).hexdigest())
            with self.assertRaises(FileExistsError):
                ed._write_verified(src, P, out, None, self.spec, sha)
            uncommitted = Path(tmp) / "uncommitted"
            with self.assertRaises(FileNotFoundError):
                ed._write_verified(src, P, uncommitted, Path(tmp) / "missing" / "receipt", self.spec, sha)
            self.assertFalse(uncommitted.exists())
            with self.assertRaises(ValueError):
                ed.write_candidate(src, range(512), Path(tmp) / "wrong-parent")


if __name__ == "__main__":
    unittest.main()
