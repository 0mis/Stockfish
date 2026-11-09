"""Canonical decoder and exact FT-channel permutation for pinned SFNNv19.

Source: Stockfish ff7cee4244ec96ea488c402669e3674c0574dce6, specifically
nnue_feature_transformer.h, nnue_architecture.h, and layers/affine_transform*.h.
This K32H3 format has RAW int16 biases and RAW int8 FT weights: there are no
LEB128 sections or PSQT weights. The version word alone does not identify it;
the header, transformer, and every layer-stack hash must also match.

Arrays use file/canonical order, before the engine's architecture-dependent
PackusEpi16Order/WideningPairOrder and affine load-time permutations.

Permutation convention: p[new_channel] = old_channel, len(p) == 512. Apply
p to BOTH 512-element accumulator halves of every FT feature row and bias.
The clipped product then changes from x[j] to x[p[j]]. Apply the SAME p to
both 512-column perspective blocks in fc0, in every one of the 32 stacks.
All other parameters stay byte-identical. Changing only one FT half, one
feature family, one K32H3 block, or one perspective is not this symmetry.

SIMD safety: each FT output is in [0, floor(255**2 / 512)] = [0,127]. Thus
even arbitrary regrouping of signed-int8 fc0 weights cannot saturate an
SSSE3/AVX2 maddubs int16 pair: its range is [-32512,32258]. fc0 int32
accumulation bounds, including bias, are separately checked before editing.
Accumulator additions stay in the same feature order within each permuted
lane; paired widening sums at most two int8 values into an int16 lane.
Native signature/evaluation verification remains an independent requirement.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import os
from pathlib import Path
import struct
import tempfile
from typing import BinaryIO, Iterable

import numpy as np

VERSION = 0x6A448AFA
L1, L2, L3 = 1024, 32, 32
CHANNELS = L1 // 2
STACKS = 32
THREATS = 59808
PAIRS = 96 * 95 // 2
PSQ_BLOCKS = 3
PSQ_BLOCK_DIMENSIONS = 32 * 11 * 64
PSQ = PSQ_BLOCKS * PSQ_BLOCK_DIMENSIONS
STACK_BYTES = 4 + 4 * L2 + L1 * L2 + 4 * L3 + 2 * L2 * L3 + 4 + 128
FT_BYTES = 2 * L1 + (THREATS + PAIRS + PSQ) * L1
BASELINE_SHA256 = "d1ee674a62ca754648fb8c2907ff4e92f0c3acff28f59afa0f5bd4e3949538f1"
SOURCE_COMMIT = "ff7cee4244ec96ea488c402669e3674c0574dce6"
MAX_DESCRIPTION_BYTES = 1024 * 1024
FT_OUTPUT_MAX = (255 * 255) // 512


def _affine_hash(previous: int, outputs: int) -> int:
    return ((0xCC03DAE4 + outputs) ^ (previous >> 1) ^ (previous << 31)) & 0xFFFFFFFF


def architecture_hash() -> int:
    h = _affine_hash(0xEC42E90D ^ (L1 * 2), L2)
    h = (h + 0x538D24C7) & 0xFFFFFFFF
    h = _affine_hash(h, L3)
    h = (h + 0x538D24C7) & 0xFFFFFFFF
    return _affine_hash(h, 1)


def transformer_hash() -> int:
    h = 0
    for component in (0x2C41EAEA, 0x5C80FA69, 0xAB005CEF):
        h = (((h << 1) | (h >> 31)) ^ component) & 0xFFFFFFFF
    return h ^ (L1 * 2)


def validate_permutation(permutation: Iterable[int]) -> np.ndarray:
    """Return an owned int64 new-to-old permutation; never truncate floats."""
    p = np.asarray(list(permutation))
    if p.shape != (CHANNELS,) or p.dtype.kind not in "iu":
        raise ValueError("Permutation must contain exactly 512 integer indices")
    if np.any(p < 0) or np.any(p >= CHANNELS):
        raise ValueError("Permutation indices must be in [0, 511]")
    p = p.astype(np.int64, copy=True)
    if not np.array_equal(np.sort(p), np.arange(CHANNELS)):
        raise ValueError("Permutation must contain each channel exactly once")
    return p


def full_permutation(permutation: Iterable[int]) -> np.ndarray:
    """The corresponding 1024-column accumulator/fc0 permutation."""
    p = validate_permutation(permutation)
    return np.concatenate((p, p + CHANNELS))


@dataclass(frozen=True)
class Stack:
    bucket: int
    offset: int
    fc0_bias: np.ndarray
    fc0_weights: np.ndarray
    fc1_bias: np.ndarray
    fc1_weights: np.ndarray
    fc2_bias: np.ndarray
    fc2_weights: np.ndarray


class Network:
    """Validated immutable bytes with zero-copy, read-only NumPy views."""

    def __init__(self, data: bytes, *, expected_sha256: str | None = None):
        if not isinstance(data, bytes):
            data = bytes(data)
        self.data = data
        if len(data) < 16:
            raise ValueError("Truncated NNUE header")
        version, header_hash, description_size = struct.unpack_from("<III", data)
        if version != VERSION:
            raise ValueError("Unsupported NNUE version")
        if header_hash != (architecture_hash() ^ transformer_hash()):
            raise ValueError("Unsupported NNUE architecture; expected pinned SFNNv19 K32H3")
        if description_size > MAX_DESCRIPTION_BYTES:
            raise ValueError("NNUE description exceeds supported size limit")
        expected_bytes = 16 + description_size + FT_BYTES + STACKS * STACK_BYTES
        if len(data) != expected_bytes:
            raise ValueError(f"Wrong NNUE length: expected {expected_bytes}, got {len(data)}")
        if expected_sha256 is not None and self.sha256 != expected_sha256.lower():
            raise ValueError("NNUE SHA256 does not match the requested baseline")
        self.description_bytes = data[12:12 + description_size]
        self.description = self.description_bytes.decode("utf-8", errors="replace")
        pos = 12 + description_size
        if struct.unpack_from("<I", data, pos)[0] != transformer_hash():
            raise ValueError("Wrong feature-transformer hash")
        self.ft_offset = pos + 4
        self.sections: list[dict] = []
        pos = self.ft_offset

        def array(name: str, dtype: str, shape: tuple[int, ...]) -> np.ndarray:
            nonlocal pos
            count = int(np.prod(shape))
            result = np.frombuffer(data, dtype=dtype, count=count, offset=pos).reshape(shape)
            self.sections.append({"name": name, "offset": pos, "bytes": result.nbytes,
                                  "dtype": dtype, "shape": list(shape)})
            pos += result.nbytes
            return result

        self.ft_biases = array("ft.biases", "<i2", (L1,))
        self.threat_weights = array("ft.threat_weights", "i1", (THREATS, L1))
        self.pair_weights = array("ft.pair_weights", "i1", (PAIRS, L1))
        self.psq_weights = array("ft.psq_weights", "i1", (PSQ, L1))
        self.stacks_offset = pos
        self.stacks: list[Stack] = []
        for bucket in range(STACKS):
            start = pos
            if struct.unpack_from("<I", data, pos)[0] != architecture_hash():
                raise ValueError(f"Wrong architecture hash in stack {bucket}")
            pos += 4
            prefix = f"stack[{bucket}]"
            self.stacks.append(Stack(
                bucket=bucket, offset=start,
                fc0_bias=array(prefix + ".fc0.bias", "<i4", (L2,)),
                fc0_weights=array(prefix + ".fc0.weights", "i1", (L2, L1)),
                fc1_bias=array(prefix + ".fc1.bias", "<i4", (L3,)),
                fc1_weights=array(prefix + ".fc1.weights", "i1", (L3, 2 * L2)),
                fc2_bias=array(prefix + ".fc2.bias", "<i4", (1,)),
                fc2_weights=array(prefix + ".fc2.weights", "i1", (1, 2 * L2 + 2 * L3)),
            ))
            if pos - start != STACK_BYTES:
                raise AssertionError("Internal stack-size mismatch")
        if pos != len(data):
            raise ValueError("Trailing NNUE bytes")

    @classmethod
    def load(cls, path: str | Path, *, expected_sha256: str | None = None) -> Network:
        return cls(Path(path).read_bytes(), expected_sha256=expected_sha256)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()

    @property
    def psq_blocks(self) -> np.ndarray:
        return self.psq_weights.reshape(PSQ_BLOCKS, PSQ_BLOCK_DIMENSIONS, L1)

    def to_bytes(self) -> bytes:
        """Exact original serialization, with no copy for an unchanged network."""
        return self.data

    def write(self, destination: str | Path | BinaryIO) -> None:
        """Re-encode every raw array and hash; identity is checked by tests."""
        if hasattr(destination, "write"):
            self._write(destination)
        else:
            with Path(destination).open("wb") as stream:
                self._write(stream)

    def _write(self, stream: BinaryIO, p: np.ndarray | None = None,
               chunk_rows: int = 2048) -> None:
        if type(chunk_rows) is not int or chunk_rows <= 0:
            raise ValueError("chunk_rows must be a positive integer")
        stream.write(struct.pack("<III", VERSION, architecture_hash() ^ transformer_hash(),
                                 len(self.description_bytes)))
        stream.write(self.description_bytes)
        stream.write(struct.pack("<I", transformer_hash()))
        if p is None:
            stream.write(memoryview(self.ft_biases).cast("B"))
        else:
            stream.write(self.ft_biases[p].tobytes())
        for weights in (self.threat_weights, self.pair_weights, self.psq_weights):
            for start in range(0, weights.shape[0], chunk_rows):
                block = weights[start:start + chunk_rows]
                if p is None:
                    stream.write(memoryview(block).cast("B"))
                else:
                    stream.write(np.ascontiguousarray(block[:, p]).tobytes())
        for stack in self.stacks:
            stream.write(struct.pack("<I", architecture_hash()))
            for field in ("fc0_bias", "fc0_weights", "fc1_bias", "fc1_weights",
                          "fc2_bias", "fc2_weights"):
                values = getattr(stack, field)
                if p is not None and field == "fc0_weights":
                    stream.write(np.ascontiguousarray(values[:, p]).tobytes())
                else:
                    stream.write(memoryview(values).cast("B"))

    def simd_safety(self) -> dict:
        """Bounds valid for every permutation, not just sampled positions."""
        min_fc0, max_fc0 = 2**63 - 1, -(2**63)
        for stack in self.stacks:
            weights = stack.fc0_weights.astype(np.int64)
            lows = stack.fc0_bias.astype(np.int64) + FT_OUTPUT_MAX * np.minimum(weights, 0).sum(axis=1)
            highs = stack.fc0_bias.astype(np.int64) + FT_OUTPUT_MAX * np.maximum(weights, 0).sum(axis=1)
            min_fc0 = min(min_fc0, int(lows.min()))
            max_fc0 = max(max_fc0, int(highs.max()))
        int16_pair_min = 2 * FT_OUTPUT_MAX * -128
        int16_pair_max = 2 * FT_OUTPUT_MAX * 127
        return {
            "ft_output_range": [0, FT_OUTPUT_MAX],
            "maddubs_int16_pair_range": [int16_pair_min, int16_pair_max],
            "maddubs_saturation_impossible": -32768 <= int16_pair_min and int16_pair_max <= 32767,
            "fc0_int32_range": [min_fc0, max_fc0],
            "fc0_int32_overflow_impossible": -(2**31) <= min_fc0 and max_fc0 < 2**31,
        }

    def _checked_full_permutation(self, permutation: Iterable[int]) -> np.ndarray:
        p = full_permutation(permutation)
        safety = self.simd_safety()
        if not safety["maddubs_saturation_impossible"] or not safety["fc0_int32_overflow_impossible"]:
            raise ValueError("Cannot establish permutation safety for this network")
        return p

    def permute_ft(self, permutation: Iterable[int]) -> Network:
        """Return an immutable, structurally validated compensated network."""
        p = self._checked_full_permutation(permutation)
        stream = io.BytesIO()
        self._write(stream, p)
        return Network(stream.getvalue())

    def write_permuted_ft(self, destination: str | Path, permutation: Iterable[int],
                          *, chunk_rows: int = 2048) -> dict:
        """Stream to a temporary sibling, validate it, then atomically replace.

        The source data is immutable and never changed. Auxiliary column-copy
        buffers use at most chunk_rows * 1024 bytes each.
        """
        p = self._checked_full_permutation(permutation)
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                self._write(stream, p, chunk_rows)
            edited = Network.load(temporary)
            audit = {
                "source_commit": SOURCE_COMMIT,
                "source_sha256": self.sha256,
                "output_sha256": edited.sha256,
                "output_bytes": len(edited.data),
                "permutation_convention": "new_channel_to_old_channel",
                "permutation": p[:CHANNELS].tolist(),
                "moved_channels": int(np.count_nonzero(p[:CHANNELS] != np.arange(CHANNELS))),
                "compensated_stacks": STACKS,
                "simd_safety": self.simd_safety(),
            }
            os.replace(temporary, path)
            return audit
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def inspect(self) -> dict:
        return {"format": "SFNNv19 K32H3", "source_commit": SOURCE_COMMIT,
                "sha256": self.sha256, "bytes": len(self.data),
                "description": self.description,
                "architecture_hash": hex(architecture_hash()),
                "transformer_hash": hex(transformer_hash()),
                "stacks": STACKS, "stacks_offset": self.stacks_offset,
                "sections": self.sections, "simd_safety": self.simd_safety()}
