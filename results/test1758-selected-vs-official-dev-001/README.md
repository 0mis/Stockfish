# Unedited test1758 with released history source versus official Nov6

The complete fixed development comparison scored **134.5–121.5**, with **51 wins, 167 draws, 38 losses**, over 128 reversed pairs / 256 games at 10+0.1, T1, H64. Both arms played128 games as White and128 as Black. All 256 terminations were normal; there were **zero reported clock forfeits** and zero game/outcome retries.

| Candidate color | Wins | Draws | Losses |
| --- | ---: | ---: | ---: |
| White |48|78|2|
| Black |3|89|36|

The candidate is the exact released history executable `ae1d4772a71529085b26d7460e76fcf65dbe09782e55ea67ecef2e4f033c5ba5` with Shawn Xu's **unedited test1758** network `68752b755c93e419e714829c4aac1bbd66e9509a96cf175be28a00164b6d7f21`. The reference is official November6 source `7cdd5e89a39fe3071b67d91c7bb07d712044bf76`, executable `b67c37d0d45e3540e311392aeeb699e3a745f16e172bc3fc6f85e0782093842b`, with original `d1ee674a62ca754648fb8c2907ff4e92f0c3acff28f59afa0f5bd4e3949538f1`. The measured effect concerns this whole candidate combination against that specific reference.

The score corresponds to a descriptive logistic estimate of **17.66 Elo**. The exact 128-draw empirical paired-bootstrap nominal central95% interval is **[+1.36,+34.04] Elo**, with pentanomial `[0,14,87,27,0]`. Its polynomial exponent is 128 and normalization is128^128; quantiles use the smallest integer k with40·CDF(k)≥q·128^128 for q=1,39. This describes the fixed retired-opening public-VM sample; it is not formal confirmation or a general strength claim. The 128 distinct public retired roots have no canonical starting-root overlap with the primary’s 110 unique roots, while broader development-family correlation remains.

The selection was fixed in the [prospective plan](https://github.com/0mis/Stockfish/blob/4f0672262b2edd3d9b4c7ce17ac466893b398389/public-dev/plan.json) before the primary outcome was disclosed. The [separate primary](https://github.com/0mis/Stockfish/blob/f6b8d4898be1515be76db88e28987184dd49c7f2/results/test1758-fixed-bias-dev-001/README.md) scored 127.5–128.5 for transferred f92 versus unedited687 on the same history executable. The strict >128/256 rule therefore selected unedited687 for this follow-up. These two estimates are kept separate.

[Actual run42702655777](https://github.com/0mis/Stockfish/actions/runs/42702655777) used a standard public Windows runner and source 366de257b7bdc904dd8999714208d26ef9149e8e. It completed at 2026-11-09T23:41:49Z. Independent operator readback verified all 1,287 raw artifact members, 128 actual configurations and process UTC records, 35,145 real-clock go commands matching 35,145 played plies with raw comments, legal PGNs, both colors, the complete PGN byte index and an independently exponentiated bootstrap polynomial. Two earlier preflight refusals started zero engines/games and are preserved in the source admission history; the actual gameplay execution occurred once. Read-only metadata transport delays postponed archival verification until 2026-11-10T04:34:46Z.

The [full UTC PGN](games-utc.pgn) preserves all source moves and clock/latency comments byte-for-byte. Its engine-only headers were allowlist-checked; every start/end timestamp explicitly has +0000 offset. No player identities, ratings or private paths are present. [Byte index](PGN-BYTE-INDEX.json), [result](RESULT.json), [hosted audit](HOSTED-INDEPENDENT-AUDIT.json), [operator audit](OPERATOR-INDEPENDENT-AUDIT.json), [terminal acceptance](TERMINAL-ACCEPTANCE.json) and [selection readback](PRIMARY-SELECTION.json) retain exact hashes. The original 33,526,156-byte archive SHA256 is `b3e39dfab9a0cfaf8b9b1a0c9379f850513088ea15cf92c4484509925e7e76ef` ([artifact13721112566](https://github.com/0mis/Stockfish/actions/runs/42702655777/artifacts/13721112566)); its temporary hosted retention is separate from the preserved research copy.

## Exact public configuration

- [Released history executable archive](https://github.com/0mis/Stockfish/releases/download/experimental-test1757-history-d5c257728ba5/stockfish-test1757-history-d5-windows-bmi2.zip): SHA256 `23b4df6759e361ea510e28f23abec04daeb14f08969a781653b08a916f0e4e8d`, member `stockfish-history-floor-bmi2.exe`.
- [Unedited test1758 network](https://tests.stockfishchess.org/api/nn/nn-68752b755c93.nnue): 138,360,528 bytes, full SHA256 68752b755c93e419e714829c4aac1bbd66e9509a96cf175be28a00164b6d7f21. Configure `EvalFile` explicitly to these bytes; the release's embedded network is a different configuration.
- [Official Nov6 release](https://github.com/official-stockfish/Stockfish/releases/tag/stockfish-dev-20261106-7cdd5e89), [original reference network](https://tests.stockfishchess.org/api/nn/nn-d1ee674a62ca.nnue), and [fastchess v1.8.3-alpha](https://github.com/Disservin/fastchess/releases/tag/v1.8.3-alpha).

Both arms used Ponder=false, UCI_LimitStrength=false, SyzygyProbeLimit=0, Move Overhead=10 and referee time margin 0, fresh engines, no evaluation adjudication and no outcome stopping. No authenticated canonical bench for the exact ae1d+687 configuration was collected; native network loading, clocks and complete legal play were authenticated.

Credits: Shawn Xu (xu-shawn), original test1758 network, CC0-1.0; Stockfish developers, GPL-3.0-or-later; Shawn Xu test1757 source lineage; SanityEclipse000/MxTriX77 prefetch; Stefan Geschwentner/locutus2 history floor; Disservin/fastchess contributors, MIT; 0mis AI-assisted public development tooling. Original engine attribution and license files remain in the linked release archives.
