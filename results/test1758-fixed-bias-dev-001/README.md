# Fixed test1758 bias-transfer development result

The transferred f92 network scored **127.5–128.5** against the unedited Shawn Xu test1758 parent68752 in **256 games / 128 reversed pairs**, at **10+0.1 / T1 / H64** on a standard public Windows Actions runner. Both sides used the same released history executable, SHA256 `ae1d4772a71529085b26d7460e76fcf65dbe09782e55ea67ecef2e4f033c5ba5`.

The exact empirical paired-bootstrap central 95% percentile interval, using 128 iid empirical pair draws, is **[-21.74, +19.02]** on the descriptive logistic Elo scale. This fixed development sample contains 128 retired-opening occurrences (110 distinct canonical roots). It is separate from private trial4 and does not establish a confirmed strength gain.

[Frozen plan](../../public-dev/plan.json) · [Hosted run](https://github.com/0mis/Stockfish/actions/runs/42675059274) · [Raw PGNs](games.pgn) · [Verified receipt](PRIMARY-VERIFIED.json) · [Independent operator audit](OPERATOR-INDEPENDENT-AUDIT.json).

The receipt binds the actual evidence ZIP (38676423 bytes, SHA256 `3cd86dab43b4931fecd9425560cff9d1565d72fa43d9644f5281c20cf111666a`), every member, the exact runtime inputs and all 128 native command/process/clock receipts. The full referee logs and per-pair PGNs are in the [run artifact](https://github.com/0mis/Stockfish/actions/runs/42675059274/artifacts/13710238515); its recorded expiry is 2026-11-16T21:07:33Z. The immutable source and raw result files above remain on this branch.

One zero-game Windows executable-resolution startup failure was preserved before the successful run. There were no outcome-based retries or schedule changes. An operator reader's strict file list was corrected to include the referee's automatically saved configurations; all 128 configurations were then independently verified. The [reader correction receipt](POSTTERMINAL-AUDIT-READER-CORRECTION.json) preserves that distinction. The prospective follow-up selection rule and openings were frozen publicly before the primary result was read; this receipt records that chronology.
