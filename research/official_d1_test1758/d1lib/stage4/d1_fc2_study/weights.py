"""D1-only clipped fc2 grid and bounded stream serializer. No fitting/launching.

Importing this module does not read a network. The CLI runs only tiny in-memory
self-tests. A caller may serialize one selected candidate later through
write_candidate; this module never serializes the grid automatically.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import Path
import struct
from typing import BinaryIO, Callable, Iterator

SOURCE_COMMIT = "ff7cee4244ec96ea488c402669e3674c0574dce6"
PARENT_SHA256 = "d1ee674a62ca754648fb8c2907ff4e92f0c3acff28f59afa0f5bd4e3949538f1"
ENGINE_SHA256 = "c0092fd059ae81d9fad61f9372e985f5b0d99b934bd8639867590cf988fa4cd9"
FILE_BYTES = 136247652
VERSION, HEADER_HASH, FT_HASH, ARCH_HASH = 0x6A448AFA, 0xC0357A83, 0xA3060B95, 0x63337116
DESCRIPTION_BYTES, HEADER_BYTES = 84, 100
HEADS, INPUTS, STACK_BYTES, STACKS_OFFSET = 32, 128, 35208, 135120996
FC2_IN_STACK, FC2_BYTES = 35076, 132
S_NUMERATORS, S_DENOMINATOR = (98, 99, 100, 101, 102), 100
T_VALUES = (-128, -96, -64, -32, 0, 32, 64, 96, 128)
I32_MIN, I32_MAX = -(1 << 31), (1 << 31) - 1
CHUNK_BYTES = 1024 * 1024


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: str | Path, check: Callable[[], None] = lambda: None) -> str:
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while block := stream.read(CHUNK_BYTES):
            check(); value.update(block)
    return value.hexdigest()


def round_even_ratio(numerator: int, denominator: int = 100) -> int:
    """Exact signed rational rounding; no floating-point parameter arithmetic."""
    require(type(numerator) is int and type(denominator) is int and denominator > 0, "Integer ratio required")
    quotient, remainder = divmod(abs(numerator), denominator)
    rounded = quotient + int(2 * remainder > denominator or (2 * remainder == denominator and quotient % 2 != 0))
    return -rounded if numerator < 0 else rounded


@dataclass(frozen=True)
class Head:
    bias: int
    weights: tuple[int, ...]

    def __post_init__(self):
        require(type(self.bias) is int and I32_MIN <= self.bias <= I32_MAX, "fc2 bias outside int32")
        require(type(self.weights) is tuple and len(self.weights) == INPUTS
                and all(type(value) is int and -128 <= value <= 127 for value in self.weights), "Exactly128 native int8 coefficients required")


@dataclass(frozen=True)
class Candidate:
    s_numerator: int
    t: int
    heads: tuple[Head, ...]
    parent_tail_sha256: str
    tail_sha256: str
    clipped_coordinates: tuple[dict, ...]
    changed_coordinates: tuple[dict, ...]
    changed_bytes: int

    def report(self) -> dict:
        return {"s_numerator": self.s_numerator, "s_denominator": S_DENOMINATOR, "t": self.t,
                "operator": "clip_int8_symmetric_after_exact_round_even_then_int32_bias_round_even",
                "parent_tail_sha256": self.parent_tail_sha256, "tail_sha256": self.tail_sha256,
                "clipped_coordinates": list(self.clipped_coordinates), "changed_coordinates": list(self.changed_coordinates),
                "clipped_coordinate_count": len(self.clipped_coordinates), "changed_coordinate_count": len(self.changed_coordinates),
                "changed_byte_count": self.changed_bytes, "unclipped_safe": not self.clipped_coordinates,
                "is_zero_edit_point": self.s_numerator == 100 and self.t == 0}


def pack_heads(heads: tuple[Head, ...]) -> bytes:
    require(type(heads) is tuple and len(heads) == HEADS and all(isinstance(head, Head) for head in heads), "Exactly32 fc2 heads required")
    return b"".join(struct.pack("<i128b", head.bias, *head.weights) for head in heads)


def unpack_heads(raw: bytes) -> tuple[Head, ...]:
    require(len(raw) == HEADS * FC2_BYTES, "Exact4224-byte tail payload required")
    rows = [struct.unpack_from("<i128b", raw, index * FC2_BYTES) for index in range(HEADS)]
    return tuple(Head(int(row[0]), tuple(map(int, row[1:]))) for row in rows)


def make_candidate(parent: tuple[Head, ...], s_numerator: int, t: int) -> Candidate:
    require(type(s_numerator) is int and s_numerator in S_NUMERATORS and type(t) is int and t in T_VALUES, "Point outside frozen45-point grid")
    before = pack_heads(parent)
    # Actual D1 fc2 has no -128 values. Refuse a different parent rather than
    # silently changing -128 during the nominal zero edit under this operator.
    require(all(-127 <= value <= 127 for head in parent for value in head.weights), "D1 symmetric parent fc2 range required")
    heads, clipped, changed = [], [], []
    for index, head in enumerate(parent):
        bias = round_even_ratio(s_numerator * head.bias + 100 * t)
        require(I32_MIN <= bias <= I32_MAX, "Bias overflow; bias clipping is forbidden")
        if bias != head.bias:
            changed.append({"head": index, "parameter": "fc2_bias", "index": 0, "old": head.bias,
                            "rounded_preclip": bias, "new": bias, "clipped": False})
        values = []
        for column, original in enumerate(head.weights):
            rounded = round_even_ratio(s_numerator * original)
            value = min(127, max(-127, rounded))
            coordinate = {"head": index, "parameter": "fc2_weights", "index": column, "old": original,
                          "rounded_preclip": rounded, "new": value, "clipped": rounded != value}
            if rounded != value:
                clipped.append(coordinate)
            if value != original:
                changed.append(coordinate)
            values.append(value)
        heads.append(Head(bias, tuple(values)))
    result = tuple(heads)
    after = pack_heads(result)
    if s_numerator == 100 and t == 0:
        require(after == before and not clipped and not changed, "Zero edit is not bit-identical")
    return Candidate(s_numerator, t, result, digest_bytes(before), digest_bytes(after), tuple(clipped), tuple(changed),
                     sum(left != right for left, right in zip(before, after)))


def iter_grid(parent: tuple[Head, ...]) -> Iterator[Candidate]:
    """Evaluate/report one small tail at a time; never write45 full networks."""
    for scale in S_NUMERATORS:
        for offset in T_VALUES:
            yield make_candidate(parent, scale, offset)


def _header(stream: BinaryIO) -> bytes:
    stream.seek(0)
    raw = stream.read(HEADER_BYTES)
    require(len(raw) == HEADER_BYTES, "Truncated D1 header")
    require(struct.unpack_from("<III", raw) == (VERSION, HEADER_HASH, DESCRIPTION_BYTES)
            and struct.unpack_from("<I", raw, 12 + DESCRIPTION_BYTES)[0] == FT_HASH, "Exact D1 header/layout required")
    return raw


def _read_tail_stream(stream: BinaryIO) -> tuple[bytes, tuple[Head, ...]]:
    header, chunks = _header(stream), []
    for index in range(HEADS):
        stream.seek(STACKS_OFFSET + index * STACK_BYTES)
        raw_hash = stream.read(4)
        require(len(raw_hash) == 4 and struct.unpack("<I", raw_hash)[0] == ARCH_HASH, "Wrong dense stack architecture hash")
        stream.seek(STACKS_OFFSET + index * STACK_BYTES + FC2_IN_STACK)
        chunks.append(stream.read(FC2_BYTES))
    stream.seek(FILE_BYTES)
    require(stream.read(1) == b"", "Trailing bytes beyond D1 EOF")
    return header, unpack_heads(b"".join(chunks))


def read_parent_tail(path: str | Path, check: Callable[[], None] = lambda: None) -> tuple[Head, ...]:
    """Future explicit call: stream-hash parent, decode only4224 fc2 bytes."""
    source = Path(path).resolve(strict=True)
    require(source.is_file() and source.stat().st_size == FILE_BYTES, "Wrong D1 byte length")
    require(file_sha256(source, check) == PARENT_SHA256, "Parent D1 full SHA256 differs")
    with source.open("rb") as stream:
        _, heads = _read_tail_stream(stream)
    require(all(-127 <= value <= 127 for head in heads for value in head.weights), "Unexpected D1 fc2 native -128")
    return heads


def _regions(parent: tuple[Head, ...], candidate: Candidate) -> list[tuple[int, bytes, bytes]]:
    original, changed = pack_heads(parent), pack_heads(candidate.heads)
    return [(STACKS_OFFSET + index * STACK_BYTES + FC2_IN_STACK,
             original[index * FC2_BYTES:(index + 1) * FC2_BYTES], changed[index * FC2_BYTES:(index + 1) * FC2_BYTES])
            for index in range(HEADS)]


def _copy_patched(source: BinaryIO, target: BinaryIO, size: int, regions: list[tuple[int, bytes, bytes]],
                  chunk_bytes: int = CHUNK_BYTES, check: Callable[[], None] = lambda: None) -> dict:
    """Private stream core, also tested with tiny artificial spans."""
    require(type(size) is int and size >= 0 and type(chunk_bytes) is int and chunk_bytes > 0, "Invalid stream bound")
    end = 0
    for start, old, new in regions:
        require(type(start) is int and start >= end and len(old) == len(new) > 0 and start + len(old) <= size, "Overlapping/out-of-range patch regions")
        end = start + len(old)
    source.seek(0)
    original_hash, output_hash, spans = hashlib.sha256(), hashlib.sha256(), []
    cursor = changed_bytes = 0
    def copy_span(stop):
        nonlocal cursor
        start, value = cursor, hashlib.sha256()
        while cursor < stop:
            check()
            block = source.read(min(chunk_bytes, stop - cursor))
            require(block and cursor + len(block) <= stop, "Truncated/overlong source span")
            require(target.write(block) == len(block), "Short output write")
            value.update(block); original_hash.update(block); output_hash.update(block); cursor += len(block)
        spans.append({"start": start, "end": stop, "bytes": stop - start, "sha256": value.hexdigest()})
    for start, old, new in regions:
        copy_span(start); check()
        actual = source.read(len(old))
        require(actual == old, "Source fc2 bytes differ from authenticated parent tail")
        require(target.write(new) == len(new), "Short fc2 write")
        original_hash.update(actual); output_hash.update(new); cursor += len(old)
        changed_bytes += sum(a != b for a, b in zip(actual, new))
    copy_span(size)
    require(source.read(1) == b"", "Source EOF differs")
    return {"bytes": size, "parent_sha256": original_hash.hexdigest(), "candidate_sha256": output_hash.hexdigest(),
            "untouched_spans": spans, "changed_byte_count": changed_bytes}


def _verify_output(stream: BinaryIO, audit: dict, regions: list[tuple[int, bytes, bytes]],
                   chunk_bytes: int = CHUNK_BYTES, check: Callable[[], None] = lambda: None) -> None:
    stream.seek(0)
    whole = hashlib.sha256()
    for index, span in enumerate(audit["untouched_spans"]):
        remain, value = span["bytes"], hashlib.sha256()
        require(stream.tell() == span["start"], "Output span offset differs")
        while remain:
            check(); block = stream.read(min(chunk_bytes, remain))
            require(block and len(block) <= remain, "Truncated output span")
            value.update(block); whole.update(block); remain -= len(block)
        require(value.hexdigest() == span["sha256"], "Untouched output span changed")
        if index < len(regions):
            start, old, new = regions[index]
            require(stream.tell() == start, "Output fc2 offset differs")
            actual = stream.read(len(new)); require(actual == new, "Output fc2 payload differs")
            whole.update(actual)
    require(stream.tell() == audit["bytes"] and stream.read(1) == b"" and whole.hexdigest() == audit["candidate_sha256"], "Output hash/EOF differs")


def write_candidate(parent_path: str | Path, destination: str | Path, candidate: Candidate,
                    check: Callable[[], None] = lambda: None) -> dict:
    """Serialize a later selected point once; never overwrite/delete any path.

    On failure an incomplete destination is deliberately preserved and must not
    be admitted as a model. Caller owns timing, storage allocation and receipt.
    """
    source, target = Path(parent_path).resolve(strict=True), Path(destination).resolve()
    require(source != target and not target.exists(), "Parent overwrite or existing destination forbidden")
    require(target.suffix.casefold() == ".nnue" and target.parent.is_dir(), "Existing explicit output directory and .nnue name required")
    parent = read_parent_tail(source, check)
    expected = make_candidate(parent, candidate.s_numerator, candidate.t)
    require(expected == candidate, "Candidate does not equal the frozen operator applied to D1")
    regions = _regions(parent, candidate)
    with source.open("rb") as original:
        header, repeated = _read_tail_stream(original)
        require(repeated == parent, "Parent tail changed before serialization")
        with target.open("xb") as output:
            audit = _copy_patched(original, output, FILE_BYTES, regions, check=check)
    require(audit["parent_sha256"] == PARENT_SHA256 and audit["changed_byte_count"] == candidate.changed_bytes, "Parent changed during stream patch")
    with target.open("rb") as output:
        _verify_output(output, audit, regions, check=check)
        changed_header, changed_heads = _read_tail_stream(output)
        require(changed_header == header and changed_heads == candidate.heads, "Output header/stack hashes/tails differ")
    require(file_sha256(source, check) == PARENT_SHA256, "Parent hash changed after serialization")
    if candidate.s_numerator == 100 and candidate.t == 0:
        require(audit["candidate_sha256"] == PARENT_SHA256, "Whole-file zero edit is not identical")
    return {**audit, "source": str(source), "destination": str(target), "candidate": candidate.report(),
            "allowed_fc2_regions": [{"start": start, "bytes": len(old)} for start, old, new in regions],
            "allowed_parameter_bytes": HEADS * FC2_BYTES, "all_untouched_spans_rehashed": True,
            "header_and_every_stack_hash_preserved": True, "parent_rehashed_after_write": True,
            "output_eof_verified": True, "parent_overwritten": False, "native_conformance_claim": False,
            "fit_or_strength_claim": False}


def self_test() -> dict:
    cases = []
    assert [round_even_ratio(n, 2) for n in (1, 3, 5, -1, -3, -5)] == [0, 2, 2, 0, -2, -2]
    cases.append("exact_signed_ties_even")
    values = tuple((-127, -126, -50, -1, 0, 1, 50, 126, 127)[i % 9] for i in range(INPUTS))
    parent = tuple(Head(index * 31 - 400, values) for index in range(HEADS))
    assert unpack_heads(pack_heads(parent)) == parent
    identity = make_candidate(parent, 100, 0)
    assert pack_heads(identity.heads) == pack_heads(parent) and not identity.clipped_coordinates and not identity.changed_coordinates
    cases.append("zero_edit_tail_identity_and_exact_layout")
    grid = list(iter_grid(parent))
    assert len(grid) == 45 and sum(not item.clipped_coordinates for item in grid) == 27
    up = make_candidate(parent, 102, 0)
    assert up.clipped_coordinates and all(-127 <= w <= 127 for head in up.heads for w in head.weights)
    assert any(item["old"] == item["new"] and item["clipped"] for item in up.clipped_coordinates)
    assert any(item["clipped"] and item["old"] != item["new"] for item in up.changed_coordinates)
    cases.append("explicit_clipping_including_clipped_but_unchanged_coordinates")
    try:
        make_candidate(tuple(Head(I32_MAX, values) for _ in range(HEADS)), 102, 128)
    except ValueError: pass
    else: raise AssertionError("Bias overflow accepted")
    cases.append("bias_overflow_refused")
    original = b"HEADER-" + bytes(range(64)) + b"EOF"
    regions = [(9, original[9:13], b"abcd"), (35, original[35:38], b"xyz")]
    output = io.BytesIO(); audit = _copy_patched(io.BytesIO(original), output, len(original), regions, chunk_bytes=7)
    _verify_output(io.BytesIO(output.getvalue()), audit, regions, chunk_bytes=5)
    assert output.getvalue() == original[:9] + b"abcd" + original[13:35] + b"xyz" + original[38:]
    assert len(audit["untouched_spans"]) == 3 and audit["parent_sha256"] == digest_bytes(original)
    cases.append("tiny_stream_patch_every_untouched_span_and_eof")
    zero = [(start, old, old) for start, old, new in regions]
    output = io.BytesIO(); audit_zero = _copy_patched(io.BytesIO(original), output, len(original), zero, chunk_bytes=3)
    assert output.getvalue() == original and audit_zero["candidate_sha256"] == digest_bytes(original)
    cases.append("tiny_whole_stream_zero_edit_identity")
    corrupt = bytearray(audit["bytes"]); correct = original[:9] + b"abcd" + original[13:35] + b"xyz" + original[38:]
    corrupt[:] = correct; corrupt[0] ^= 1
    try: _verify_output(io.BytesIO(corrupt), audit, regions)
    except ValueError: pass
    else: raise AssertionError("Untouched-span corruption accepted")
    for bad in (original[:-1], original + b"extra"):
        try: _copy_patched(io.BytesIO(bad), io.BytesIO(), len(original), regions)
        except ValueError: pass
        else: raise AssertionError("Wrong source EOF accepted")
    cases.append("untouched_corruption_and_truncated_or_extra_eof_refused")
    return {"status": "PASS", "cases": cases, "case_count": len(cases), "actual_network_read": False,
            "files_written": False, "engines_started": False, "fit_performed": False}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", required=True)
    parser.parse_args()
    print(json.dumps(self_test(), indent=2))
