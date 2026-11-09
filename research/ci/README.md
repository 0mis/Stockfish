# Test1757 history-floor Windows build

One Windows 2022 job builds the candidate on pushes to `research/test1757-history-floor-ci-v1` in `0mis/Stockfish`. Its time limit is 45 minutes. The workflow reads repository contents and the previously accepted Actions artifact.

`source-pin.json` specifies the public candidate commit and tree. The workflow checks out the driver at its triggering SHA, the candidate at the recorded SHA, and the selected prefetch baseline at its pinned SHA. It compares both root trees and both `src` trees, verifies the old and new `search.cpp` blobs and checks the working copies. Before building, it archives every Git blob of the candidate with its original mode and includes the driver, workflow, Stockfish authors and GPL license.

The runner obtains artifact 13642730885 from run 42543250921. It verifies the 142,322,823-byte reference executable, finds the unique embedded network prefix and verifies the 138,360,528-byte original 18cd network before installing it under its default filename. The finished executable must embed those same bytes. The accepted reference transcript and commands are individually hash-pinned.

The reviewed MinGW64 profile-build recipe uses `ARCH=x86-64-bmi2`, two build jobs, and the original default PGO bench. The PGO wrapper retains both complete output streams and the instrumented executable hashes. The resulting candidate runs the canonical 51-position bench twice with H16, T1, depth 13. Each run must have the same node count at every position. The expected signature is established by this candidate’s own repetitions.

Qualification also checks start-position perft at depth four for 197,281 nodes and compares the exact native integer, active bucket, and all 92 displayed values for each of the 22 public probes. The original transcript and the new commands, raw pipes, exit records, source manifest and summary travel with the executable in a one-day artifact. These checks cover build and static native behavior. Playing strength requires separate games.

Use `python -B -m unittest discover -s research/ci -p "test_*.py"` for the small source, transcript, benchmark and extraction tests. The workflow is the normal build entry point. GitHub-hosted MinGW64 supplies the current compiler, and the job captures its version and identity.
