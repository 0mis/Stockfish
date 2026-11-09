# PR7294 current-source port: timing gate failed

The fixed hosted diagnostic rejects this port route under its predeclared gate.
Median engine-reported speed was **+1.0073762875%**; median monotonic whole-process
wall speed was **+0.9910493538%**. The rule required at least 1% on both clocks.
Seven of eight blocks were faster on both. The wall threshold failed. No
rounding-up, outlier removal, extra blocks, alternative policy or games follow.

| Block | Order | A engine ms | B engine ms | Engine speed % | A wall s | B wall s | Wall speed % |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | ABBA | 4478 | 4456 | +0.493716 | 5.230034781 | 5.153347799 | +1.488100 |
| 2 | BAAB | 4423.5 | 4420.5 | +0.067866 | 5.128877133 | 5.155014715 | -0.507032 |
| 3 | ABBA | 4453.5 | 4424.5 | +0.655441 | 5.180363321 | 5.129170282 | +0.998076 |
| 4 | BAAB | 4474 | 4414 | +1.359311 | 5.205627927 | 5.154902536 | +0.984022 |
| 5 | ABBA | 4487.5 | 4413 | +1.688194 | 5.206309470 | 5.130077910 | +1.485973 |
| 6 | BAAB | 4513.5 | 4442 | +1.609635 | 5.230478286 | 5.180367815 | +0.967315 |
| 7 | ABBA | 4469.5 | 4452.5 | +0.381808 | 5.204807972 | 5.179752035 | +0.483728 |
| 8 | BAAB | 4500.5 | 4383.5 | +2.669100 | 5.230363294 | 5.104289605 | +2.469956 |

A is unedited official `43e01e07210948e1130dc687c065d7a3742b2793`;
B is exact attributed port `709bddfb2d10b9da1ecb0b97fff9452cd5a8ffc5`.
The policy comes from [PR7294](https://github.com/official-stockfish/Stockfish/pull/7294),
by **MxTriX77 / SanityEclipse000**, pinned original commit
`5b559f0b7e37822d04dffe9e559d8a716705ccf5`. The original author is credited
in AUTHORS. 0mis made this current-source adaptation and diagnostic. Mstembera's
specialized arrays and all current unrolls remain byte-for-byte after the
mechanical inverse removes only the prefetch helper/calls and index plumbing.
The redundant sawmaker policy was not stacked.

Both sides used unchanged stock18cd, full SHA256
`18cd32e11a9cac7c595bc6ebf1301088da8e1f2c7b31efda88dbc74c71f5d2cf`.
There were identical fresh x86-64-bmi2 GCC profile-build procedures, default
PGO workload, one unmeasured warmup each, then 32 serial measured fresh-process
benches in the eight fixed blocks. No local build or engine execution occurred.

The standard hosted runner was Ubuntu 24.04 image 20261101.360.1, AMD EPYC 7763,
four allowed logical CPUs, native affinity fixed to CPU 0. Both builds used
GCC 13.3.0; compiler-driver SHA256
`1353e9bdd29a7295c7226bf6c63abccce056d8cac31f112e5cdbecc3f28c2769`.
Actual flags, compiler/host metadata, all 32 individual timings and command records
are retained. This result applies to this hosted target and workload; no laptop
speed or Elo inference is made.

Every one of 38 raw PGO/qualification/warmup/timed benches matched 3,464,885 nodes
and the full 51 final PV/bestmove/ponder/search signature
`39b67104cddc03fb7030b71f6a0ee9b041b90f39d920d7091b830a2492272fbd`.
The passive hosted audit legally replayed 24,206 reported PV rows and 1,938 final
positions, including the public benchmark's Chess960 cases. Both start-position
perft4 runs were 197,281. Both complete embedded networks matched stock18cd.
The audit independently reread raw clocks; a second passive readback used exact
rational arithmetic and confirmed the strict gate failure. Fifteen pure tests
passed. No private/formal inputs or current games/datasets were used.

The immutable original plan SHA256 remains
`201bc0b8c03fdf4cb837a87b4a8c6ca0dfeefe979a220d0e093eed615ce657dc`.
[Completed hosted run](https://github.com/0mis/Stockfish/actions/runs/42716460040)
used explicit recovery driver `1f77125baf18626f7a6a6f206c6aec3c491a68f5`.
Main diagnostic wall time was 405.27 seconds.

The [initial attempt](https://github.com/0mis/Stockfish/actions/runs/42715339745)
failed before B qualification because shared temporary capture names collided.
Its authenticated artifact contains zero warmups and zero measured blocks.
`../readout-failure-001/` preserves its phase audit, recovery record and complete
non-executable raw-text/source bundle. The explicit path-only recovery gives
each engine a separate work parent while leaving every original driver file,
plan byte, workload, source pin, clock and decision rule unchanged. No measured
sample was retried or discarded.

`ARTIFACTS.json` pins the exact full successful and failed artifact ZIPs, both
independently authenticated locally without executing their binaries.
`RAW-TEXT.tar.gz` and `RAW-TEXT-MEMBERS.json` durably retain all non-executable
artifact members: raw commands/pipes/clocks, source archives, build/PGO logs,
symbol tables and receipts. Native binaries remain in the original pinned
hosted artifacts, with their hashes below and in the full member manifests.

| Side | Executable bytes | Executable SHA256 |
| --- | ---: | --- |
| A | 138978352 | `46d484a01974a289a5e8b5dbd3921b23dc791e03db3c7d3ecddf5f4d60619b92` |
| B | 138978352 | `06be0f7a0b59db1d1f2bc2b501178b258a75926c756208278c346a802af271fe` |

`RESULT.json`, `INDEPENDENT-AUDIT.json` and `PASSIVE-READBACK.json` are the
machine-readable decision/evidence records. The final outcome is
`TIMING_GATE_FAILED_REJECT_PORT_ROUTE`. No further timing or games are launched.
