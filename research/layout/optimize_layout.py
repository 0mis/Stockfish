"""Fit one CPU channel layout for the original18cd product-zero masks.

Daniel Monroe's Stockfish ftperm technique groups four often-zero channels
together. Upstream also searches three-cycles. This implementation uses only
two-swaps, with the following fixed search policy. Start from the identity.
Score each cross-group swap by its exact change in all-zero groups over both
perspectives. Rank positive swaps by descending gain, then ascending (left,
right) slot. Greedily take swaps whose two groups are unused in this batch.
Stop when no swap improves the count or after 64 batches.

Upstream attribution:
https://github.com/official-stockfish/nnue-pytorch/blob/b26145006e54e6461cb4b51944ab05cc4850c580/ftperm.py
"""

from collections.abc import Sequence

import numpy as np


CHANNELS = 512
GROUP_SIZE = 4
MAX_BATCHES = 64


def _fold_zero_masks(masks):
    if not isinstance(masks, np.ndarray) or np.ma.isMaskedArray(masks):
        raise TypeError("Expected a NumPy array of observed bool or uint8 zero masks")
    masks = np.asarray(masks)
    if masks.ndim != 2 or masks.shape[1] != 2 * CHANNELS or masks.shape[0] == 0:
        raise ValueError("Expected at least one position with 1024 mask entries per position")
    if masks.dtype not in (np.bool_, np.uint8):
        raise TypeError("Zero masks must have dtype bool or uint8")
    if masks.dtype == np.uint8 and masks.max() > 1:
        raise ValueError("Every uint8 mask entry must be 0 or 1, with 1 indicating zero")
    return masks.reshape(-1, CHANNELS)


def _validated_order(permutation):
    if not isinstance(permutation, (Sequence, np.ndarray)) or isinstance(permutation, (str, bytes)):
        raise ValueError("Expected a sequence of 512 integer channel indices")
    if isinstance(permutation, np.ndarray) and (permutation.ndim != 1 or np.ma.isMaskedArray(permutation)):
        raise ValueError("Expected a one-dimensional permutation of observed indices")
    if len(permutation) != CHANNELS or any(
        isinstance(index, (bool, np.bool_)) or not isinstance(index, (int, np.integer))
        for index in permutation
    ):
        raise ValueError("Expected 512 integer channel indices")
    order = [int(index) for index in permutation]
    if sorted(order) != list(range(CHANNELS)):
        raise ValueError("Channel indices must form a bijection of 0 through 511")
    return order


def _count_zero_groups(samples, order=None):
    total = 0
    for start in range(0, len(samples), 2048):
        batch = samples[start : start + 2048]
        if order is not None:
            batch = batch[:, order]
        total += int(np.count_nonzero(batch.reshape(-1, GROUP_SIZE).all(axis=1)))
    return total


def _score_summary(samples, order):
    return {
        "positions": len(samples) // 2,
        "total_groups": len(samples) * (CHANNELS // GROUP_SIZE),
        "identity_zero_groups": _count_zero_groups(samples),
        "permuted_zero_groups": _count_zero_groups(samples, order),
    }


def _channel_bits(samples):
    # Each integer represents all folded sample rows for one original channel.
    packed = np.packbits(samples.T, axis=1, bitorder="little")
    return [int.from_bytes(row.tobytes(), "little") for row in packed]


def _refresh_replacements(replacements, bits, order, changed_groups):
    # Rows address destination slots. Columns address original channels, so
    # only destination groups touched by the last batch need to be refreshed.
    for group in changed_groups:
        start = group * GROUP_SIZE
        a, b, c, d = (bits[channel] for channel in order[start : start + GROUP_SIZE])
        other_three = (b & c & d, a & c & d, a & b & d, a & b & c)
        for slot, common in enumerate(other_three, start):
            if common:
                replacements[slot] = [(common & channel).bit_count() for channel in bits]
            else:
                replacements[slot].fill(0)


def _swap_gains(replacements, order):
    placements = replacements[:, order]
    # The diagonal is the existing count in the slot's group, repeated for
    # all four slots. Swapping across groups combines two independent deltas.
    one_way = placements - placements.diagonal()[:, None]
    gains = one_way + one_way.T
    group_ids = np.arange(len(order)) // GROUP_SIZE
    gains[group_ids[:, None] == group_ids[None, :]] = 0
    return gains


def _disjoint_swaps(gains):
    groups = len(gains) // GROUP_SIZE
    left_groups, right_groups = np.triu_indices(groups, k=1)
    by_group = gains.reshape(groups, GROUP_SIZE, groups, GROUP_SIZE).transpose(0, 2, 1, 3)
    pair_gains = by_group[left_groups, right_groups].reshape(-1, GROUP_SIZE * GROUP_SIZE)

    # All 16 choices for a pair consume the same two groups. Its first maximum
    # is the lexicographically earliest best slot pair in row-major order.
    best = pair_gains.argmax(axis=1)
    values = pair_gains[np.arange(len(best)), best]
    left_slots = left_groups * GROUP_SIZE + best // GROUP_SIZE
    right_slots = right_groups * GROUP_SIZE + best % GROUP_SIZE
    candidates = [
        (-int(values[i]), int(left_slots[i]), int(right_slots[i]))
        for i in np.flatnonzero(values > 0)
    ]
    used_groups = set()
    accepted = []
    for negative_gain, left, right in sorted(candidates):
        left_group, right_group = left // GROUP_SIZE, right // GROUP_SIZE
        if left_group not in used_groups and right_group not in used_groups:
            accepted.append((left, right, -negative_gain))
            used_groups.update((left_group, right_group))
    return accepted


def fit_layout(optimization_masks):
    """Fit a new-to-old order from N x 1024 optimization masks (True/1 = zero).

    Each row contains 512 side-to-move channels followed by 512 opponent
    channels. Both perspectives use the same order. Returns JSON-ready counts,
    the permutation, and the accepted swaps in each batch. Swap slot indices
    refer to the order at the beginning of the corresponding batch.
    """
    samples = _fold_zero_masks(optimization_masks)
    bits = _channel_bits(samples)
    order = list(range(CHANNELS))
    replacements = np.empty((CHANNELS, CHANNELS), dtype=np.int64)
    changed_groups = range(CHANNELS // GROUP_SIZE)
    rounds = []
    stop_reason = "round_limit"

    for round_number in range(1, MAX_BATCHES + 1):
        _refresh_replacements(replacements, bits, order, changed_groups)
        swaps = _disjoint_swaps(_swap_gains(replacements, order))
        if not swaps:
            stop_reason = "no_positive_swap"
            break
        changed_groups = []
        for left, right, _ in swaps:
            order[left], order[right] = order[right], order[left]
            changed_groups.extend((left // GROUP_SIZE, right // GROUP_SIZE))
        rounds.append({
            "round": round_number,
            "gain": sum(gain for _, _, gain in swaps),
            "swaps": [{"left": left, "right": right, "gain": gain} for left, right, gain in swaps],
        })

    scores = _score_summary(samples, order)
    if scores["permuted_zero_groups"] - scores["identity_zero_groups"] != sum(r["gain"] for r in rounds):
        raise RuntimeError("Sum of accepted swap gains disagrees with the direct group count")
    return {
        **scores,
        "permutation": order,
        "max_batches": MAX_BATCHES,
        "rounds_evaluated": round_number,
        "stop_reason": stop_reason,
        "rounds": rounds,
    }


def evaluate_layout(permutation, heldout_masks):
    """Score a frozen new-to-old permutation on independent N x 1024 zero masks."""
    order = _validated_order(permutation)
    return _score_summary(_fold_zero_masks(heldout_masks), order)
