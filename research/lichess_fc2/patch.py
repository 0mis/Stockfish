"""Reversible, byte-exact application of a jointly frozen sparse FC2 proposal.

The mapping must come from independent parent serialization/native verification.
No architecture offsets are guessed here. SPDX-License-Identifier: GPL-3.0-or-later
"""
import hashlib
import os
from pathlib import Path
import struct
import tempfile

try:
    from .core import ContractError, canonical, digest, hash_string, require, validate_mapping, verify_freeze
except ImportError:
    from core import ContractError, canonical, digest, hash_string, require, validate_mapping, verify_freeze


I32 = struct.Struct("<i")
I8 = struct.Struct("<b")


def write_joint_freeze(path, mapping, frozen, *, allow_synthetic=False):
    """Persist the complete choices exclusively before any validation is opened."""
    validate_mapping(mapping, allow_synthetic=allow_synthetic)
    verify_freeze(frozen, mapping)
    data = canonical(frozen) + b"\n"
    with Path(path).open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    return {"freeze_sha256": frozen["freeze_sha256"], "file_sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def _parent_records(data, mapping, edits=(), reverse=False):
    changed = {edit["offset"]: edit for edit in edits}
    for head in mapping["heads"]:
        require(I32.unpack_from(data, head["bias_offset"])[0] == head["bias_before"], "An FC2 bias changed")
        for lane, offset in enumerate(head["weight_offsets"]):
            expected = changed[offset]["after"] if reverse and offset in changed else head["weights"][lane]
            require(I8.unpack_from(data, offset)[0] == expected, "Serialized FC2 coefficient differs")


def _only_edits(source, target, edits):
    cursor = 0
    for edit in sorted(edits, key=lambda e: e["offset"]):
        offset = edit["offset"]
        require(source[cursor:offset] == target[cursor:offset], "Bytes outside intended int8 coefficients changed")
        cursor = offset + 1
    require(source[cursor:] == target[cursor:], "Bytes after the intended edits changed")


def bind_patch(parent, mapping, frozen, *, allow_synthetic=False):
    """Return candidate bytes and a hash-bound reversible byte recipe."""
    validate_mapping(mapping, allow_synthetic=allow_synthetic)
    verify_freeze(frozen, mapping)
    require(len(parent) == mapping["parent_bytes"] and hashlib.sha256(parent).hexdigest() == mapping["parent_sha256"], "Exact parent file differs")
    _parent_records(parent, mapping)
    target = bytearray(parent)
    for edit in frozen["edits"]:
        require(edit["before"] != edit["after"] and edit["after"] - edit["before"] == edit["delta"], "Invalid coefficient delta")
        require(I8.unpack_from(parent, edit["offset"])[0] == edit["before"], "Coefficient before byte differs")
        I8.pack_into(target, edit["offset"], edit["after"])
    candidate = bytes(target)
    _only_edits(parent, candidate, frozen["edits"])
    _parent_records(candidate, mapping, frozen["edits"], reverse=True)
    inverse = bytearray(candidate)
    for edit in frozen["edits"]:
        I8.pack_into(inverse, edit["offset"], edit["before"])
    require(bytes(inverse) == parent and hashlib.sha256(inverse).hexdigest() == mapping["parent_sha256"], "Inverse does not recover exact original bytes")
    recipe = {"schema": "test1757-fc2-byte-patch/1", "freeze_sha256": frozen["freeze_sha256"], "mapping_sha256": digest(mapping),
              "parent_sha256": mapping["parent_sha256"], "candidate_sha256": hashlib.sha256(candidate).hexdigest(),
              "bytes": len(parent), "edits": frozen["edits"], "changed_bytes": len(frozen["edits"]), "biases_unchanged": True,
              "inverse_sha256": mapping["parent_sha256"], "identity": frozen["identity"], "verified_gain": False}
    recipe["patch_sha256"] = digest(recipe)
    return candidate, recipe


def verify_patch(recipe, mapping):
    require(recipe["schema"] == "test1757-fc2-byte-patch/1" and recipe["patch_sha256"] == digest({k: v for k, v in recipe.items() if k != "patch_sha256"}), "Byte recipe digest differs")
    require(recipe["mapping_sha256"] == digest(mapping) and recipe["parent_sha256"] == recipe["inverse_sha256"] == mapping["parent_sha256"]
            and recipe["bytes"] == mapping["parent_bytes"] and hash_string(recipe["candidate_sha256"])
            and recipe["biases_unchanged"] is True and recipe["changed_bytes"] == len(recipe["edits"]) <= 4, "Byte recipe source/count binding differs")
    seen = set()
    for edit in recipe["edits"]:
        head, lane = edit["physical_head"], edit["lane"]
        require(type(head) is int and 0 <= head < 92 and head not in seen and type(lane) is int and 0 <= lane < 128, "Repeated or invalid FC2 edit")
        spec = mapping["heads"][head]
        require(edit["offset"] == spec["weight_offsets"][lane] and edit["before"] == spec["weights"][lane]
                and type(edit["delta"]) is int and edit["delta"] in (-4, -2, -1, 1, 2, 4)
                and edit["after"] == edit["before"] + edit["delta"] and -128 <= edit["after"] <= 127, "Byte recipe lane/value differs")
        seen.add(head)
    require(recipe["identity"] is (not recipe["edits"]), "Identity byte recipe differs")


def apply_patch_bytes(source, mapping, recipe, *, reverse=False, allow_synthetic=False):
    validate_mapping(mapping, allow_synthetic=allow_synthetic)
    verify_patch(recipe, mapping)
    source_sha = recipe["candidate_sha256" if reverse else "parent_sha256"]
    target_sha = recipe["parent_sha256" if reverse else "candidate_sha256"]
    require(len(source) == recipe["bytes"] and hashlib.sha256(source).hexdigest() == source_sha, "Patch source bytes/SHA differ")
    _parent_records(source, mapping, recipe["edits"], reverse=reverse)
    out = bytearray(source)
    for edit in recipe["edits"]:
        before, after = (edit["after"], edit["before"]) if reverse else (edit["before"], edit["after"])
        require(I8.unpack_from(source, edit["offset"])[0] == before, "Patch before byte differs")
        I8.pack_into(out, edit["offset"], after)
    target = bytes(out)
    _only_edits(source, target, recipe["edits"])
    require(hashlib.sha256(target).hexdigest() == target_sha, "Patch target SHA differs")
    _parent_records(target, mapping, recipe["edits"], reverse=not reverse)
    return target


def _publish_new(output, data):
    output = Path(output)
    require(not output.exists() and not output.is_symlink(), "Output already exists")
    with tempfile.TemporaryDirectory(dir=output.parent, prefix=".fc2-edit-") as directory:
        staged = Path(directory) / "network"
        with staged.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        with staged.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        require(staged.stat().st_size == len(data) and actual == hashlib.sha256(data).hexdigest(), "Saved candidate/inverse file differs")
        os.link(staged, output)


def write_candidate(parent_path, output_path, mapping, frozen, *, allow_synthetic=False):
    """File wrapper; never overwrite a source/output or write an identity copy."""
    output = Path(output_path)
    require(not output.exists() and not output.is_symlink(), "Output already exists")
    candidate, recipe = bind_patch(Path(parent_path).read_bytes(), mapping, frozen, allow_synthetic=allow_synthetic)
    if not recipe["identity"]:
        _publish_new(output, candidate)
    return {"recipe": recipe, "output_written": not recipe["identity"]}


def write_inverse(candidate_path, output_path, mapping, recipe, *, allow_synthetic=False):
    output = Path(output_path)
    require(not output.exists() and not output.is_symlink(), "Output already exists")
    original = apply_patch_bytes(Path(candidate_path).read_bytes(), mapping, recipe, reverse=True, allow_synthetic=allow_synthetic)
    if not recipe["identity"]:
        _publish_new(output, original)
    return {"inverse_sha256": hashlib.sha256(original).hexdigest(), "output_written": not recipe["identity"]}
