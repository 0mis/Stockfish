# Prospective test1758 selected composite vs official Stockfish

This source-only branch freezes a development comparison before its primary outcome is read. It has no gameplay workflow at this freeze.

The selection rule is fixed: only a fully valid independently verified primary42675059274 score strictly above128/256 selects the fixed f92ca6 transfer; a valid score at or below128 selects Shawn Xu's unedited test1758 parent68752b. Invalid/incomplete primary evidence means no follow-up dispatch. The selected network runs with the exact published history executableae1d4772 against official Stockfish7cdd5e89/b67c37d0 with its original d1ee674 network.

The128 distinct retired public starting roots have no canonical root overlap with the primary110 unique roots. Each pair reverses colors, at10+0.1/T1/H64, full strength, one pair at a time, fresh processes and no outcome retries. All evidence and the full fixed-sample independent audit must be retained. These two selected experimental estimates are reported separately; neither is formal confirmation or a global gain claim.

See `public-dev/plan.json`, `openings.json` and `opening-provenance.json` for byte-pinned inputs and the prospective protocol. Activation requires the actual complete primary receipt and matching actual raw artifact hashes. No primary scores, logs, PGNs or result artifact were read by this planner before this freeze.

Credits: Shawn Xu(xu-shawn), test1758/CC0-1.0; Stockfish developers; SanityEclipse000/MxTriX77 prefetch; Stefan Geschwentner/locutus2 history floor; Disservin/fastchess;0mis AI-assisted fixed readout study and public evidence tooling. The fixed transfer is not new training on test1758.
