# Fixed January 2013 data-only feasibility

The prospectively fixed first 20,000 January 2013 standard-rated records provide
enough structural capacity under the frozen rule. They do not establish future
stable-teacher-pair yield, learner reproducibility, statistical power, or a gain.
No native labels, engines, fitting, private inputs or private held-out labels
were used. Private collision clearance remains required before any training.

| Fixed partition | Selected roots | Components |
| --- | ---: | ---: |
| TRAIN | 16,000 | 4,711 |
| DEV | 3,000 | 1,102 |
| FINAL | 3,000 | 679 |
| SCREEN | 3,000 | 700 |

All 20,000 records were legal standard games. The fixed length rule retained
9,488 unique quality-controlled games; 7,192 anchor families had eligible roots.
There were 44,436 unique eligible later roots and 38,787 preliminary parents.
The all-legal-child guard contains 1,455,985 unique child IDs. No preliminary
parent/child guard ID crosses partitions. The material selector gives no
coverage of the earlier 9--12-piece/no-queen head-8 setting.

The [CC0 standard-game source](https://database.lichess.org/#standard) is
`lichess_db_standard_rated_2013-01.pgn.zst`, 17,761,302 bytes, SHA256
`aa40b3671fa3cf1072eb182892cd90b0e1e003a4a5943492f64b77e7f3fd1635`,
verified against the [primary checksum list](https://database.lichess.org/standard/sha256sums.txt).
February 2013 was not opened. Broadcasts were abandoned before archive access
because their specific license is CC BY-SA 4.0.

Method commit: `87e0d14e8c8e1c0510865acef2b1ebf04b00f816`.
Frozen plan SHA256:
`238cbe1c36df282b61bcaddcd26afc0529b5ca60f2280b1babd8d94d01e7565b`.
[Discovery CI](https://github.com/0mis/Stockfish/actions/runs/42706302113)
succeeded in 97.74 script seconds, 83.26 CPU seconds and 497,276 KiB peak RSS.

[Independent saved-product audit](https://github.com/0mis/Stockfish/actions/runs/42707284461)
also passed. A separate implementation verified the five saved canonical
streams, all 9,488 source-origin rows, all 38,787 preliminary manifest rows,
component commitments, exact preliminary/partition selection and split
disjointness. It reconstructed all 25,000 selected legal prefixes and roots,
then all 937,122 selected legal child boards in about 60 audit seconds. Eleven
synthetic tests passed. Unselected preliminary child lists are bound by the
original generator and exact manifest hashes; they were not independently
replayed in this audit. Original PGNs and large products remain on hosted CI;
only small hash-only receipts were downloaded locally.

`ARTIFACTS.json` binds exact public run/artifact IDs, bytes and ZIP SHA256.
`RESULT.json` binds each canonical stream, sanitized partition file and
reversible exposure manifest. `AUDIT.json` and `AUDIT-BINDINGS.json` bind the
independent audit. All canonical streams contain strictly sorted, unique
lowercase SHA256 FEN4 IDs plus LF, exactly 65 bytes/ID. They use ordinary
Standard castling with legal EP only, and contain no alternate hash domains.

The hosted collision artifact contains `parents.ids`, `children.ids`,
`selected-parents.ids`, `census-roots.ids`, `anchor16.ids`, the complete
preliminary root/child/component/split manifest, and source-game origin index.
`parents.ids` and `children.ids` conservatively include every selected root and
all its legal one-ply candidates. The separate sanitized product artifact has
fixed selected root FENs, legal prefixes and public provenance only. No names,
ratings, annotations or results are published. No private IDs are supplied to
the public workflow. The private gate must use these unchanged streams; public
sampling will not be redrawn or conditioned on reserved positions.
