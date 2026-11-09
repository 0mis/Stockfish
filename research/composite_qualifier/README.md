# Local composite hosted readiness

This workflow qualifies the fixed history-floor Windows BMI2 executable with the frozen readout-ridge network as an **external EvalFile**. It is preparation for a later local combined-versus-official comparison. It does not submit or qualify a mixed public Fishtest proposal, rebuild an engine, play games, or establish a gain over official Stockfish. The independent public Fishtest idea remains the nine-bias edit on unchanged prefetch source.

The branch is `research/test1757-composite-qualifier-ci-v1`, with single parent `39fd08831c65a61df4da6a4102f071a4320e37bd`. The workflow is `.github/workflows/test1757-composite-qualifier-v1.yml`. `plan.json` is immutable SHA-256 `0b85f4d61224bc5a7852bd920539115487228ed112fabace78602a043bb7cb88`.

## Exact inputs

All artifacts come from `0mis/Stockfish`. The workflow uses pinned `actions/download-artifact` with exact run and artifact name. The hosted driver independently checks artifact ID, run/repository/head identity, digest, archive size and expiry through GitHub's artifact API, then checks every consumed member's bytes and full SHA-256. Only the listed members are opened by the driver; other parent evidence is not interpreted or uploaded.

| Role | Run / artifact ID | Artifact name | Member | Bytes / SHA-256 |
| --- | --- | --- | --- | --- |
| History executable | 42579129686 / 13656641851 | test1757-history-floor-windows-bmi2-0b36bc418a68c8e280dcc8db08fc3e2226d61ab2 | stockfish-windows-x86-64-bmi2.exe | 142322184 / ae1d4772a71529085b26d7460e76fcf65dbe09782e55ea67ecef2e4f033c5ba5 |
| Ridge network | 42583426891 / 13658147479 | test1757-readout-ridge-v1-08abd7bd1c3fa9a504a0fa8556a6bef6633933cc | verified-readout-ridge-candidate/candidate.nnue | 138360528 / d5c257728ba53cb9808543ce2800080c860f1c878896cd1b17f75ed4b20aafee |
| Accepted comparator | 42543250921 / 13642730885 | test1757-prefetch-windows-bmi2-49672d7b3c2b0cb544f4bf7cc70515249fe047e3 | stockfish-windows-x86-64-bmi2.exe | 142322823 / 12a8e7bfdd9b1189b4350bd9ebef3501be1e89bd5fa41dcb56921f85ba23869a |

The history artifact additionally supplies `probes-v1.json` (4783 bytes, SHA-256 `60237d711345475e7aeb846888faee49efc1ec4c9f4a3f17a1781332389e8349`) and `source-pin.json` (467 bytes, SHA-256 `840fa742e16a9c080a5ae2e77313849db27f3816a6d78dd4133761542157824f`). Those bind the established 22 retired probes and history source commit `d19047cc44fae773a590be3b0b9f08fdd75acdc7`, tree `6c3cd141dcc3cac20b84469b729fbbd1141c0d60`, on baseline `7a17b53bcaf93f54a23ad65f309c413585500c82`.

## Required checks

One sequential job on standard `windows-2022` performs at most five fresh native processes:

1. Run canonical `bench 16 1 13 default depth` twice with the history executable and explicit d5 network, T1/H16, no tablebases or strength limiting. Parse all 51 exact canonical FEN markers, both terminal entries, final scores/PVs/bestmoves and node counts through the inherited pinned parser. Legally replay every emitted PV and bestmove/ponder, including the two Chess960 benchmark positions. Require per-position final records, node vectors and search signatures to repeat exactly. Timing/NPS remain in raw logs and are excluded from deterministic signatures.
2. Run start-position perft4 and require 197281 nodes plus the complete 20-move divide.
3. Capture all 92 printed buckets, used physical heads, raw STM integers and White/final static output on the established 22 roots, once with the history executable and once with accepted prefetch, both using the same external d5 network. Require complete numeric and bucket parity and the established physical routes.

There is **no expected mixed benchmark total** before this job runs. The component totals 3230034 and 2376690 are not mixed signatures and are not substituted. The benchmark roster is bound independently to compact-JSON SHA-256 `a81b4a8e2f81b261b9cf75e35c1fcb35add748f5f4b6dce903930fa4f9f6e272`. All executable/network/input files are rehashed after capture. No later rebuilt executable is asserted byte-equivalent to this downloaded artifact.

The component screen context is descriptive retired development only: history 68/128 audited; ridge 66/128 reported with its independent final raw audit pending at the time this qualifier was requested. Their published PGN commit and hashes are context metadata in the plan; this job does not open PGNs, current trials or held-out corpora. It creates no combined match result or strength estimate.

## Launch and evidence contract

After the overlay is reviewed and placed on the exact parent, a push by the owner to the named branch triggers the job. This draft performs no push or dispatch. The workflow contains no build step. The Python entry point requires Windows GitHub Actions and explicit `--execute` for native work; omitting it only emits plan/source receipts.

```powershell
$env:PYTHONPATH = @('research/composite_qualifier', 'research/layout') -join [System.IO.Path]::PathSeparator
python -B -m unittest discover -s research/composite_qualifier -p test_*.py -v
python -B research/composite_qualifier/qualify.py --workspace . --output evidence --execute
```

The output directory must exist and be fresh. Workflow timeout is 20 minutes; inherited native capture timeout is 240 seconds per process. `RESULT.json` is written on success or failure; every raw capture retains command, stdout, stderr, exit/timeout record and hashes. Failed captures are not replaced or retried. Standard GitHub runner cancellation/timeout can still prevent an upload from completing.

Expected hosted artifact name: **`test1757-local-composite-qualifier-v1-<github.sha>`**. It includes only JSON, TXT and UCI under `evidence/`: plan/source/artifact/input receipts, retired-probe projection, five raw captures and parsed results, post-input hashes and `RESULT.json`. It includes no executable, NNUE, downloaded parent artifact tree, PGN or model candidate. Successful status is `hosted_local_composite_readiness_passed`, with the new mixed bench node total, node-vector/search digests, perft4 and parity receipt; `verified_elo_gain`, `official_superiority_claimed`, `mixed_match_played` and `local_same_host_launch_qualified` remain false.

Local preparation ran seven synthetic tests with `subprocess.Popen` patched to reject native execution. A passive check on the already archived history-only benchmark reconstructed all 51 positions and 637 PV lines, confirming parser compatibility; it is not a mixed qualification result.
