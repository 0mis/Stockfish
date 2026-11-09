"""Invented vectors and tiny byte files only; contains no real positions/labels."""
from copy import deepcopy
import hashlib
import struct

try:
    from . import core
except ImportError:
    import core


SPLIT = "a" * 64


def mapping_fixture(*, bias=0, reverse_permutation=False):
    data = bytearray(b"synthetic-header")
    heads = []
    for h in range(92):
        offset = len(data)
        weights = [0] * 128
        head_bias = bias if h == 0 else 0
        data.extend(struct.pack("<i", head_bias) + struct.pack("<128b", *weights))
        heads.append({"physical_head": h, "bias_offset": offset, "bias_before": head_bias,
                      "weight_offsets": list(range(offset + 4, offset + 132)), "weights": weights,
                      "serialized_to_native": list(reversed(range(128))) if reverse_permutation and h == 0 else list(range(128))})
    parent = bytes(data)
    mapping = {"schema": "test1757-fc2-byte-map/1", "verified": True, "parent_sha256": hashlib.sha256(parent).hexdigest(),
               "parent_bytes": len(parent), "stored_heads": 92, "head_width": 128, "raw_to_physical": [i % 92 for i in range(192)],
               "quantizer": deepcopy(core.QUANTIZER), "heads": heads}
    return parent, mapping


def capture(mapping, head_index=0, *, active_lanes=None, base_cp=0, root_pov="white", cp_function=None):
    head = mapping["heads"][head_index]
    serialized = [0] * 128
    for lane, value in (active_lanes or {}).items():
        serialized[lane] = value
    native = [0] * 128
    for lane, index in enumerate(head["serialized_to_native"]):
        native[index] = serialized[lane]
    affine = head["bias_before"] + sum(w * x for w, x in zip(head["weights"], serialized))
    lookup = [base_cp + (cp_function(offset) if cp_function else offset) for offset in range(-24, 25)]
    return {"schema": "test1757-fc2-activation/1", "physical_head": head_index, "stored_heads": 92,
            "fc0": [0] * 32, "fc1": [0] * 32, "input_native": native, "input_serialized": serialized,
            "serialized_to_native": head["serialized_to_native"][:], "output_weights": head["weights"][:], "output_bias": head["bias_before"],
            "fc2_affine": affine, "skip": 0, "combined": affine, "scaled": core.trunc0(affine * 9600, 16384),
            "raw_nnue": core.raw_from_combined(affine), "ordinary_raw_nnue": core.raw_from_combined(affine),
            "side_to_move": 1 if root_pov == "white" else 0, "static_loss_available": True,
            "ordinary_static_cp": lookup[24], "ordinary_static_zero": lookup[24] * 16,
            "raw_offset_min": -24, "raw_offset_max": 24, "raw_offset_cp": lookup,
            "raw_offset_static_zero": [cp * 16 for cp in lookup]}


def paired_row(mapping, identifier, component, *, head_a=0, head_b=None, split="train", root_pov="white", target=8,
               active_a=None, active_b=None, cp_function=None, cp_a=0, cp_b=0):
    head_b = head_a if head_b is None else head_b
    children = []
    for rank, head, active, cp in ((1, head_a, active_a, cp_a), (2, head_b, active_b, cp_b)):
        children.append({"rank": rank, "position_id": f"child-{identifier}-{rank}", "move_uci": "synthetic",
                         "in_check": False, "terminal": False,
                         "capture": capture(mapping, head, active_lanes=active, base_cp=cp, root_pov=root_pov, cp_function=cp_function if rank == 1 else None)})
    return {"position_id": str(identifier), "component_id": str(component), "component_split_sha256": SPLIT, "split": split,
            "root_pov": root_pov, "teacher_margin_cp": target, "children": children}


def training_rows(mapping, *, head=0, count=24, components=8, root_pov="white", target=8, active_a=None, cp_function=None):
    return [paired_row(mapping, f"h{head}-root{i}", f"h{head}-component{i % components}", head_a=head, root_pov=root_pov, target=target,
                       active_a={0: 64} if active_a is None else active_a, cp_function=cp_function) for i in range(count)]
