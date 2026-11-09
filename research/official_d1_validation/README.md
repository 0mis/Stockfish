# Official D1 prospective heldout verifier

This overlay is for the **official 32-head D1** study only. It inherits `research/official_d1` from the reviewed training driver and reuses its unchanged `heldout_design.py`, `study.py`, `CacheBuilder`, source plan and D1 forward/byte modules. It does not use the test1757 92-head wrapper, its routing, its quantizer or its fitted network. Original source is `7cdd5e89a39fe3071b67d91c7bb07d712044bf76`, tree `7086bc94bdb39564b678ec4812ecde485de58fb5`; the only oracle is the pinned official executable `b67c37d0…`, with original 136,247,652-byte `d1ee674a…` D1.

Preparation uses source inspections and synthetic fixtures only. No real model, training cache, validation label or prediction is opened here. No local engine, public push or CI dispatch occurs. The staged workflow has **manual dispatch only** and cannot run without Root's subsequently supplied acceptance file and its exact SHA.

## Gates before validation

Root must add the actual `ROOT-ACCEPTANCE.json` and byte-identical `inputs/TRAIN-ACCEPTANCE.json` only after independent acceptance of training run `42608176797` and artifact `13668397917`. The latter must have schema `official-d1-sibling-train-ci-acceptance/1` and exact status `OFFICIAL_D1_TRAIN_CI_ACCEPTED`, with the original official source/inverse and no-validation dispositions. A successful producer CI job is insufficient. No successful acceptance placeholder is included in this overlay.

The Root object schema is `official-d1-heldout-root-acceptance/1`. Required fields are `accepted=true`, `scope="one_fixed_official_d1_validation"`, exact source/tree/engine/parent/plan identities, `training_run_id`, `training_driver_commit`, and `training_artifact={id,bytes,sha256}`. It binds the independent `training_acceptance={bytes,sha256}` and explicit `training_acceptance_assertions=[{path:[...],equals:...}]`, including the real accepted disposition. `no_performance_gate` must be true. The fixed validation fields are 68 roots, 162,650 bytes and SHA `f542d111aa1c850c00da08beb6d45a8fa5b0447a6aa9df93d06350ee4ba5cd24`.

`files` maps each following logical key to the accepted archive's exact `{member,bytes,sha256}`: `frozen`, `recipe`, `candidate`, `cache_manifest`, `mapping`, `train_cache`, `original_train_parity`, `edited_train_parity`. Identity may omit candidate/recipe/edited-parity references and exits before heldout access. The runtime verifies the real GitHub run/artifact identities and archive digest, confines archive paths, and verifies every referenced file before use.

The nonidentity gate validates the official source freeze/map/recipe, both full 136,247,652-byte models, the exact sparse signed coefficient bytes, all other bytes and biases, and the complete inverse parent hash. It then applies the original `heldout_design.gate` and verifies both accepted 586-child training parity receipts, including the exact official source, engine, networks and cache manifest. An identity or any mismatch stops before downloading/opening validation.

Only after the durable `BEFORE-HELDOUT-GATE.json` is written does the job fetch the one fixed heldout file from an immutable `raw.githubusercontent.com/0mis/.../<40-hex-commit>/...` URL. All original per-component hashes and modulo-five assignments are recomputed, and training components/roots remain disjoint. There is no global opening-book closure requirement added to development validation.

## Measurements and reports

The source-plan paired-loss population remains exact. Every ordinary-eligible child is measured, including a valid counterpart whose pair is unavailable. All 68 parents and all 136 child dispositions remain accounted for; in-check and terminal children receive reasons, never counterfactual ordinary scores.

One unchanged original-D1 `CacheBuilder` produces the finite child records. The source study's int64 multiply/truncation and zero-optimism CP wrapper generate expected original/candidate values. The job runs the same exact official executable with each actual NNUE and checks **every measurable raw integer and ordinary CP** using the inherited numeric transcript verifier. Direct numeric observations are retained separately. No Huber loss is aggregated before both model comparisons pass.

`VALIDATION-REPORT.json` contains predicted and directly measured matched-component losses, all row and child dispositions, overlapping parent exclusions, child exclusions, changed/unchanged-head coverage and cross-head pairs. Its exact objective is the source-plan Huber20 target-deficit loss. The verifier never trains, tunes, reselects, chooses a winner or requires improvement. Worse heldout proxy loss is a reported result, not a qualification failure.

Each model additionally runs **two complete 51-position T1/H16 bench signatures**, with all emitted PVs and bestmoves legally replayed, and a complete start-position perft4 of **197281**. Repetition compares complete normalized per-position search vectors, not just a node total. These are structural playing qualifications; no speed, Elo or heldout-improvement threshold is imposed. Raw commands, stdout/stderr, parsed vectors, perft divides and before/after input hashes are retained, including failed processes.

Producer `SUMMARY.json` uses schema `official-d1-heldout-hosted-result/1`. Success status is `HELDOUT_NUMERIC_AND_PLAYING_QUALIFICATION_PASSED_NO_STRENGTH_DECISION`; this is not Root's independent CI acceptance. `PLAYING-QUALIFICATION.json` has `original`/`candidate` entries containing network SHA, two bench totals, two full-signature hashes, position count and perft divides. A later independent Root audit may create `validation-audit-001/CI-ACCEPTANCE.json`; this preparation does not fabricate that receipt.

## Local synthetic check

Run only this overlay's `test_*.py`; do not run the inherited training test suite, which contains a real training-routing check. The local check used an existing NumPy-enabled environment at BelowNormal priority on CPU4. Tests cover the distinct official32/int64 path, closed Root and byte gates, identity stop, exact forward/inverse recovery, all-parent accounting and valid counterparts, native raw/White/STM signs, refusal to aggregate before parity, acceptance of a worse proxy without a winner decision, full benchmark signatures and perft framing. All fixtures are invented; actual heldout/model data is not needed.

The source and libraries remain GPL-3.0-or-later; upstream D1 network authors and Stockfish contributors retain credit and the network's CC0 provenance. The resulting measurements remain validation proxies and functional qualification, not verified Elo.
