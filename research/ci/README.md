# Test1757 remote build draft v1

This draft has not been published or run. It requires permission to publish workflow files and a check of available artifact storage before its first triggering push.

The only trigger is a push to `research/test1757-prefetch-ci` in `0mis/Stockfish`. One standard Windows 2022 job has a 45-minute limit, read-only contents permission, no configured secrets, no cache and no matrix. The driver comes from that push's commit. Engine source comes from commit `7a17b53bcaf93f54a23ad65f309c413585500c82`, and both HEAD and tree `8e3164caf60d281cb4b1d00a699364e04b354451` are checked before acquisition/build.

The public donor is downloaded with `curl --compressed` and checked against its complete SHA-256 and 138,360,528-byte size. The sparse public projection validates 271 ordered non-overlapping byte runs, every old byte, 439 changes and the final edited hash. The donor is preserved. No local private paths or complete private receipts are included.

MinGW64 GCC builds BMI2 PGO with `-j2` and `EXTRAPROFILEFLAGS=-mmove-max=128`. Compiler identity and build output are retained. The same final executable runs in fresh UCI processes for two ordinary H16/T1/depth13 benches per network, start-position perft4 per network and the frozen 22-row synthetic eval request per network. Every process has commands/argv, return code and output hashes; captures bind the executable and network hashes. Bench repetitions must agree within each arm, and both perft totals must be 197281.

Eval captures remain **pending independent passive verification**. The driver checks their count and network names but does not compare raw integers or 92-row traces with scalar expectations. After authenticating all hashes and process records, the root researcher can load `probes-v1.json` and pass it to the existing private `verify.transcript(request, role, stdout, stderr)` for each arm. Its `arms[*].external_evalfile.path` values use relative Windows paths such as `..\src\nn-18cd32e11a9c.nnue`, matching these Windows captures. This spelling forces an external load even for the donor whose basename equals the compiled default. Original private command-file hashes differ because their private absolute EvalFile paths have been replaced; use the captured `.uci` files and their recorded hashes.

The single one-day artifact contains the final executable, logs/commands/JSON, GPL text, AUTHORS and `corresponding-source.tar`. That archive includes the exact pinned engine tree and the reviewed driver files. Neither external NNUE file is uploaded. The executable still embeds the approximately 138 MB donor, so excluding external nets does not make artifact storage negligible. The MSYS2 compiler package is recorded at runtime rather than version-pinned; this is a new build, with no claim of matching a previous executable or of improved strength.

The local tests use only short synthetic byte strings and output examples. They start no native programs or network requests.
