# Quantized identity-alignment pilot

This isolated public experiment compares Shawn Xu's original test1757 and test1758 checkpoints. It does not change the ongoing test1758 bias experiment or its follow-up, and reads none of their scores.

The prospective method is `plan.json`. Both full network hashes, source commits, route/stack geometry and the existing audited serialized reader are pinned before measurement. Geometry is checked independently of trained-coordinate alignment. The screen centers each comparison on joint nonzero support, excludes padding/constant vectors, tests all paired FT permutations and factor swaps, and reports all92 physical heads. No permutation is applied and no model is fitted.

[Wortsman et al., Model soups](https://arxiv.org/abs/2203.05482) motivates testing averages of related trained checkpoints. [Ainsworth et al., Git Re-Basin](https://arxiv.org/abs/2209.04836) motivates checking permutation symmetries. Neither paper establishes that these two quantized checkpoints share a trained coordinate system or that averaging improves chess strength.

The initial standard free GitHub Actions job performs alignment only. A valid negative result is a successful completed experiment and stops the interpolation route. A pass permits exactly one predeclared3:1 interpolation toward unedited test1758, followed by independent reconstruction and native CI qualification. Additional games require a separately frozen, viable independent public retired root set; there are no games in this first job.

The implementation reuses `research/layout/permute_nnue.py` at immutable public commit7bef16fe1d9eb0ec8091f9c81b51afb410e62428. The canonical feature transformer pairs raw lanes j and j+512 through clipped multiplication; both factor orientations are therefore included in the diagnostic alternatives. All metadata and input checksums are retained in the evidence. Original authors and Stockfish contributors retain their credits/licenses. New code is GPL-3.0-or-later.
