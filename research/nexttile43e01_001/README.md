# Attributed PR7294 prefetch port on official43e01

This is one narrow port of [PR7294](https://github.com/official-stockfish/Stockfish/pull/7294),
by **MxTriX77 / SanityEclipse000**, original commit
`5b559f0b7e37822d04dffe9e559d8a716705ccf5` on old `7cdd5e89`.
The original author is also credited in AUTHORS. 0mis supplies the current-source
adaptation and diagnostic, and does not claim original-policy authorship.
The overlapping sawmaker `1f75e865` patch is not applied.

The baseline is exact official `43e01e07210948e1130dc687c065d7a3742b2793`.
It already includes mstembera's specialized updates (`428d7c76`) and current
unrolls. The original next-tile gate is absent there. The port adds the same
paired-widening + USE_PEXT + more-than-two-tiles policy, and threads the existing
tile index through the current single, pair and add/subtract paths. All fixed
row-address arrays, current loop bodies/unrolls, dispatch, search and network
remain. A mechanical inverse must restore the exact original accumulator
bytes. The only other tracked engine-source change is the original author's
AUTHORS entry. No whole-file replacement or second prefetch policy is used.

`plan.json` is committed before any timing. A standard public hosted job builds
both exact trees freshly and sequentially with the same GCC binary/flags,
x86-64-bmi2, default PGO workload and original stock18cd network. Full embedded
NNUE bytes/SHA are checked. Both sides must match 3,464,885 nodes, all 51 final
PV/bestmove/ponder/search signatures and perft4=197,281. Every reported PV is
legally replayed, including the two standard benchmark Chess960 roots.

After qualification, one unmeasured warmup per side precedes eight balanced
blocks, alternating ABBA/BAAB. A is unedited official; B is the port. All native
processes use the same lowest allowed CPU and run one at a time. Both native
reported elapsed milliseconds and monotonic whole-process nanoseconds are
retained. Per-block speed is mean(A time)/mean(B time)-1. The fixed gate requires
median speed >=1% on **both** clocks, with at least seven of eight blocks faster
on both. No outlier deletion, retries, extra blocks, adaptive reorder or
alternative policy is allowed. Raw stdin/stdout/stderr, build/PGO logs, host/CPU
metadata, binaries, source archives and timing receipts are preserved.

`qualify_common.py`, `pgo_capture.py` and their pure tests reuse the previously
published qualification harness, pinned by `REUSED-SOURCES.json`. No additional
heavy framework is introduced. Local execution is limited to pure checks;
`run.py` refuses native execution outside GitHub Actions. `audit.py` independently
rereads all raw outputs and timing arithmetic without launching an engine.

The job is bounded to 40 minutes, two compile jobs, 40 serial engine processes,
32 measured benches and exactly one policy. A failure retains evidence and
rejects the route. A pass is sent to Root before any separately authorized
real-clock development A/B. This is a hosted target-architecture diagnostic,
with no inference about laptop speed or Elo. No games, Fishtest submissions,
private inputs or current matches/datasets are touched. All code is GPL-3.0-or-later.

The first hosted attempt, run `42715339745`, stopped before B qualification:
the two engines shared the `qualify-perft` working-directory name. Its complete
authenticated artifact contains zero warmups and zero measured processes or
blocks. `readout-failure-001` preserves that phase audit and exact artifact pin.
The explicitly reviewed `recover.py` entrypoint gives the unchanged native
capture separate A-stockfish/B-stockfish work parents. Original plan bytes,
all original driver code pins, port source, commands, clocks and fixed gate
remain identical. A mocked regression verifies separate workspaces and
preserves rejection of duplicate captures within one engine. This is a
premeasurement harness recovery, with no timing sample retried or discarded.
