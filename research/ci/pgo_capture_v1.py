"""Retain the exact default PGO command's complete pipes; no profiling input edits.
SPDX-License-Identifier: GPL-3.0-or-later
"""
import argparse
from pathlib import Path
import subprocess
import sys

from build_and_capture_v1 import file_hash, write_json


def run(output):
    output = output.resolve(strict=True)
    executable = Path("stockfish.exe").resolve(strict=True)
    paths = {key: output / f"pgo-native.{key}.txt" for key in ("stdout", "stderr")}
    record = {"argv": [str(executable), "bench"], "default_profile_command": "./stockfish.exe bench",
              "argv0_resolution": "Windows absolute resolution of the same cwd/stockfish.exe; benchmark arguments unchanged.", "cwd": str(Path.cwd()),
              "instrumented_sha256_before": file_hash(executable), "returncode": None, "timed_out": False,
              "profiling_inputs": "Unchanged default ./stockfish.exe bench; source Makefile is not edited."}
    try:
        with paths["stdout"].open("xb") as stdout, paths["stderr"].open("xb") as stderr:
            result = subprocess.run(record["argv"], stdout=stdout, stderr=stderr, timeout=900, check=False)
            record["returncode"] = result.returncode
    except subprocess.TimeoutExpired:
        record["timed_out"] = True
    finally:
        record["instrumented_sha256_after"] = file_hash(executable)
        record["logs"] = {key: {"path": path.name, "sha256": file_hash(path)} for key, path in paths.items()}
        write_json(output / "pgo-native.record.json", record)
    # The Makefile still receives a report and tails its last four lines. The
    # original separate pipes remain intact outside deleted PGOBENCH.out.
    for key in ("stdout", "stderr"):
        with paths[key].open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                sys.stdout.buffer.write(block)
    sys.stdout.buffer.flush()
    return 0 if record["returncode"] == 0 and record["instrumented_sha256_before"] == record["instrumented_sha256_after"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    raise SystemExit(run(parser.parse_args().output))
