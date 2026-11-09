# Training-only sibling baseline diagnostic

This measures the original Test1757 network's static ranking of two actual legal child boards. It creates no network candidate, fits no parameter, selects no weight and plays no game. It queries only the original published optimization population, with no previous held-out search labels.

The immutable plan is `plan.json`, SHA-256 `554ea5a418980ea9b21dc72e2835e9e367534d321052b67c3d9686f825b566ac`. The required source base is `0mis/Stockfish` commit `08abd7bd1c3fa9a504a0fa8556a6bef6633933cc`. The driver verifies inherited source hashes, the plan, original public corpus membership, and all input hashes.

## Inputs and launch

Use the existing `research/layout/fetch_inputs.py` workflow, including its authenticated artifact retrieval, to supply the same four verified files in `inputs/`:

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| reference.exe | 142322823 | 12a8e7bfdd9b1189b4350bd9ebef3501be1e89bd5fa41dcb56921f85ba23869a |
| parent.nnue | 138360528 | 18cd32e11a9cac7c595bc6ebf1301088da8e1f2c7b31efda88dbc74c71f5d2cf |
| edited.pgn | 918097 | 8bd56ec5f58ef65881ed5ae2d7ef6683683893c34c10b94b3bb0f2d5fc83dde9 |
| parent.pgn | 935475 | 1acfc5b48a1c20aa43947fbde0ec0ea6c413f8bf76a7265dbe3d8e47c711de5d |

Keep the pinned layout Python requirements, including python-chess 1.11.2 and the baseline numpy dependency. Set `PYTHONPATH` to the sibling, head-calibration and layout directories. The output directory must exist, with none of this diagnostic's output filenames already present. The driver writes exclusively into that output directory, including a fresh `work/` subdirectory.

```powershell
$env:PYTHONPATH = @('research/sibling_diagnostic', 'research/head_calibration', 'research/layout') -join [System.IO.Path]::PathSeparator
python -B -m unittest discover -s research/sibling_diagnostic -p test_*.py -v
python -B research/sibling_diagnostic/experiment.py --inputs inputs --output evidence --execute
```

`--plan` and `--plan-sha256` may be supplied explicitly; both must match the immutable plan. Omitting `--execute` freezes the selection and writes a `prepared_not_executed` result, without reading the engine/network or starting a process. Native execution additionally requires Windows and `GITHUB_ACTIONS=true`. Tests patch `subprocess.Popen` to raise if any process launch is attempted.

## Selection and measurements

The unchanged public corpus builder must reproduce the exact original membership hash `f70679592a71b4803c3a2ba7bc80ac7c120780c83f29ed742abb9e4414ab211c`. Only its 1,536 optimization positions in 48 families enter selection. For each family, legally eligible nonterminal roots with at least two legal moves are ordered by ascending SHA-256 of ASCII `sibling-diagnostic-v1|` plus position ID, then position ID. At most four per family are fixed in `SELECTION.json` before any search. A local no-native check found all 1,536 eligible and selected exactly 192 roots, with canonical selection hash `96da81e39776c821c21c0101fcd73942f1f9a34175bb992fe70dcba25ffb1ffe`.

One sequential engine process runs T1/H16, MultiPV2, with tablebases, pondering and strength limiting disabled. Each depth-9 and depth-13 search starts with `ucinewgame` and a ready barrier, followed by its own bare root FEN. The parser examines the last two scored records, requiring ordered ranks 1/2 at the exact requested depth. It never substitutes an earlier exact score for a later bounded/mate/malformed/incomplete final record. Both final PVs, every earlier reported PV and bestmove/ponder are legally replayed and exposed in the public-position ledger.

Every root keeps the two signed root-perspective margins. A clear pair requires unbounded integer CP scores at both completed depths; the same ordered top-two first moves; margins >=20 CP at both depths and within 20 CP of each other; and both depth-13 scores in [-250,250]. The two actual depth-13 candidate child boards are then constructed and their original static scores measured even if the pair is not clear. Captures and zeroing counters follow actual legal moves. The existing router predicts raw/physical routing; the captured used bucket must agree whenever measured. In-check, checkmate, stalemate, other terminal and absent-numeric-static cases retain explicit exclusions. No mate, check or absent value is converted to a substitute zero/CP target.

The existing eval parser converts printed White CP into **child side-to-move** CP. The diagnostic then negates it to obtain the parent's perspective. Static margin is parent score of teacher rank 1 minus parent score of teacher rank 2. A gross inversion is a clear pair with both child statics available and margin <=-12 CP. Missing children or scores are excluded from assessable counts and remain in the report.

## Budget and retained evidence

The process has a **2,400-second total wall budget**, including preparation, and a **60-second receive/search budget**. There is at most one engine process with one search thread and 16 MiB hash. The deterministic maximum is 192 roots, 384 searches and 384 child eval attempts; exclusions may reduce child attempts. The native search cannot continue past a receive timeout: the reused session cleanup kills and reaps it, drains readers and records forced cleanup. This cleanup and input/output hashing may take a small additional interval after a timeout. Workflow timeout should allow a cleanup/upload margin beyond 40 minutes. Any partial capture is failed evidence, never successful completion; there is no replacement root, reselection, adaptive extension or retry in this driver.

Retain all generated JSON and raw text/command files:

| Output | Purpose |
| --- | --- |
| RESULT.json | Explicit completion/failure/preparation status, counts, all 48 families and all 92 physical heads on capture, coverage, truncations, artifact receipts; candidate_bins_created=false and verified_elo_gain=false |
| SOURCES.json, PREPARE.json, SELECTION.json | Plan/source/input bindings and complete training-only eligibility/selection audit |
| sibling-original.uci, sibling-original.stdout.txt, sibling-original.stderr.txt | Every native command and complete unedited output streams |
| CAPTURE.json | Executable/network pre/post receipts, clean-exit/reader evidence, completed counts, raw-stream hashes and per-search/per-child timing |
| ROWS.json | Every completed selected root, both teacher rank records/margins, child boards/routes/static scores or exclusions, inversion disposition |
| EXPOSURE.json | All distinct public root/PV/child IDs and canonical FEN4, with root/depth/rank/line/ply request provenance; incomplete on failed capture |

There is no implication of Elo or a verified model defect from these descriptive counts. This is a baseline cause-finding experiment on already retired public optimization openings.
