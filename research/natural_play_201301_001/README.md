# January 2013 standard-rated natural-play feasibility

This prospective data-only study uses the fixed first 20,000 source records of
the January 2013 Lichess standard-rated archive. It does not collect engine
labels, fit weights, play games, or inspect private trials. The original fixed
retired-engine D1 study and its held-out partitions are separate.

The [standard database](https://database.lichess.org/#standard) is CC0. The exact
archive SHA256 and bytes are fixed in `plan.json`, using Lichess's
[primary checksum list](https://database.lichess.org/standard/sha256sums.txt).
An originally proposed broadcast cohort was abandoned before any archive was
downloaded or parsed because its specific license is CC BY-SA 4.0. February 2013
is identified as a possible separate replication cohort and is never opened.

The immutable plan defines all quality, root, family, split, quota and resource
rules before the archive is parsed. Results, comments, ratings and names never
enter selection, splitting, products or summaries. The PGN parser must lex
source text to recover legal moves; it does not retain those labels.

Roots are at plies 32..80 in steps of 3. Anchor families use the canonical
position after ply 16. Shared later roots and every legal one-ply child of
preliminary roots join connected components before partition assignment.
Components receive prospective TRAIN/DEV/FINAL/SCREEN assignments. A fixed
round-robin cap balances selected roots across components. Minimum capacity is
12,000 TRAIN roots from 1,500 components and 2,000 roots from 250 components in
each other partition. These are structural counts, not a power or stable-label
yield guarantee. The >=18-piece, queen-present filter gives no coverage of an
earlier 9--12-piece/no-queen physical head-8 setting.

Only small source/partition/count/hash receipts return locally. Canonical
`parents.ids`, `children.ids`, `selected-parents.ids`, `census-roots.ids` and
`anchor16.ids` remain hosted and contain sorted unique 64-hex SHA256 IDs plus LF
(65 bytes/ID). The canonical domain is ordinary standard FEN4 with *legal* EP.
Do not mix alternate Shredder/FEN-EP hashes into these streams. The parent and
child streams cover all preliminary roots, including every selected root and
every legal candidate child. The compressed hash-only reversible manifest binds
each preliminary parent to family, component, split, selected flag and all its
children. `source-game-origins.jsonl` binds source-record ordinal and legal
mainline hash to anchor, eligible roots, component and split. Study component
and game hashes are metadata domains, never canonical-position streams.

Sanitized selected FEN/prefix products remain in a separate hosted artifact;
there are no player names, results, ratings or annotations. Original PGNs and
the compressed archive remain runner-only and are not uploaded. All such data
is public-source data. No private reserved IDs or FENs enter this workflow.

Root must independently compare the canonical collision streams to *every*
prior/current reserved starting pool privately before any training or native
evaluation. Sampling is never conditioned on that check and is never redrawn.
No collision clearance or training admission is claimed by this study.

The standard hosted job has a 30-minute timeout; script work is capped at
25 minutes, 20,000 records, 80,000 preliminary roots and 4,000,000 legal child
boards. Full-source compressed bytes/SHA, decompression bounds, exact canonical
stream counts/SHA and cross-partition disjointness are checked. Local tests use
only tiny synthetic positions and PGNs. New code is GPL-3.0-or-later; source
game data is CC0.

Run synthetic checks: `python -B -m unittest -v test_discover.py` in this folder.
The workflow supplies the exact precommitted plan hash to `discover.py` and
uploads separate receipt, hash-only collision-index and sanitized-product
artifacts. Failure receipts are preserved. No native executable is invoked.
