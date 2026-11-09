"""Runner-only v1 build/capture driver. Importing this module starts no process.

Native eval transcripts await the existing independent passive verifier. No
scalar/native equality, performance improvement, or Elo claim is made here.
SPDX-License-Identifier: GPL-3.0-or-later
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
SOURCE_COMMIT = "7a17b53bcaf93f54a23ad65f309c413585500c82"
SOURCE_TREE = "8e3164caf60d281cb4b1d00a699364e04b354451"
DONOR = "18cd32e11a9cac7c595bc6ebf1301088da8e1f2c7b31efda88dbc74c71f5d2cf"
EDITED = "034f47e652a6a0354f9099993535a0405f4933146fc6d0b913583696bfbdacbd"
NETWORK_BYTES = 138360528
NETS = {"donor": f"nn-{DONOR[:12]}.nnue", "edited": f"nn-{EDITED[:12]}.nnue"}
EVALFILES = {role: "..\\src\\" + name for role, name in NETS.items()}
INPUT_HASHES = {"patch-v1.json": "e2b5779282e261edfa3c4deb227ca09f398a1fef98029ba4235f72ea91626631",
                "probes-v1.json": "60237d711345475e7aeb846888faee49efc1ec4c9f4a3f17a1781332389e8349"}
BUILD_ARGS = ["make", "-j2", "profile-build", "ARCH=x86-64-bmi2", "COMP=mingw",
              "COMPCXX=g++", "EXTRAPROFILEFLAGS=-mmove-max=128"]
ERROR = re.compile(r"ERROR:|CRITICAL ERROR|No such option|Unknown command|Unable to open file|network evaluation parameters compatible", re.I)
NET_MESSAGE = re.compile(r"^info string NNUE evaluation using (.+?) \(\d+MiB,", re.M)
BENCH = re.compile(r"Total time \(ms\)\s*:\s*(\d+)\s+Nodes searched\s*:\s*(\d+)\s+Nodes/second\s*:\s*(\d+)")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_hash(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def write_json(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def sparse_patch(parent, patch):
    require(type(patch["bytes"]) is int and len(parent) == patch["bytes"], "Parent size differs")
    require(digest(parent) == patch["parent_sha256"], "Parent hash differs")
    edited, end, changed = bytearray(parent), 0, 0
    for run in patch["runs"]:
        offset = run["offset"]
        require(type(offset) is int and offset >= end, "Patch offsets overlap or are out of order")
        require(all(isinstance(run[key], str) and re.fullmatch(r"(?:[0-9a-f]{2})+", run[key])
                    for key in ("before", "after")), "Invalid patch hex")
        before, after = bytes.fromhex(run["before"]), bytes.fromhex(run["after"])
        require(len(before) == len(after) and offset + len(before) <= len(parent), "Patch length or bounds differ")
        end = offset + len(before)
        require(parent[offset:end] == before, "Patch old bytes differ")
        changed += sum(a != b for a, b in zip(before, after))
        edited[offset:end] = after
    require(type(patch["changed_bytes"]) is int and changed == patch["changed_bytes"], "Changed-byte total differs")
    require(digest(edited) == patch["candidate_sha256"], "Edited hash differs")
    return edited


def setup_commands(filename):
    require(re.fullmatch(r"\.\.\\src\\nn-[0-9a-f]{12}\.nnue", filename), "Expected an explicit safe external network path")
    return ["uci", "setoption name NumaPolicy value none", "setoption name Threads value 1",
            "setoption name Hash value 64", "setoption name MultiPV value 1",
            "setoption name UCI_Chess960 value false", "setoption name UCI_LimitStrength value false",
            "setoption name Ponder value false", "setoption name SyzygyPath value <empty>",
            f"setoption name EvalFile value {filename}", "setoption name SyzygyProbeLimit value 0", "isready"]


def commands_for(filename, mode, rows=()):
    commands = setup_commands(filename)
    if mode == "bench":
        # Explicit ordinary default bench: H16/T1/depth13, default position set.
        commands += ["bench 16 1 13 default depth", "isready"]
    elif mode == "perft":
        commands += ["position startpos", "go perft 4", "isready"]
    elif mode == "eval":
        for row in rows:
            require("\n" not in row["fen"] and "\r" not in row["fen"], "Invalid synthetic FEN command")
            commands += ["position fen " + row["fen"], "eval", "isready"]
    else:
        raise ValueError("Unknown capture mode")
    return "\n".join(commands + ["quit"]) + "\n"


def parse_bench(stdout, stderr):
    summaries = BENCH.findall(stderr)
    markers = [(int(a), int(b)) for a, b in re.findall(r"^Position:\s*(\d+)/(\d+)\s", stderr, re.M)]
    require(len(summaries) == 1 and markers, "Incomplete bench summary or position roster")
    positions = markers[0][1]
    require(markers == [(index, positions) for index in range(1, positions + 1)], "Bench positions are incomplete")
    nodes, last = [], None
    for line in stdout.splitlines():
        if line.startswith("info depth ") and " score " in line:
            last = line
        elif line.startswith("bestmove "):
            require(last is not None, "Bestmove has no final score record")
            match = re.search(r"\bnodes (\d+)\b", last)
            require(match is not None or last.startswith("info depth 0 score "), "Final node count is missing")
            nodes.append(int(match[1]) if match else 0)
            last = None
    elapsed, total, nps = map(int, summaries[0])
    require(elapsed > 0 and total > 0 and len(nodes) == positions and sum(nodes) == total, "Bench search node totals differ")
    return {"positions": positions, "nodes": total, "per_position_nodes": nodes, "elapsed_ms": elapsed, "nodes_per_second": nps}


def capture(argv, cwd, output, stem, commands=None, timeout=600):
    paths = {key: output / f"{stem}.{suffix}" for key, suffix in (("stdout", "stdout.txt"), ("stderr", "stderr.txt"))}
    record = {"argv": argv, "cwd": str(cwd), "returncode": None, "timed_out": False}
    if commands is not None:
        path = output / f"{stem}.uci"
        path.write_bytes(commands.encode())
        record["commands"] = {"path": path.name, "sha256": file_hash(path)}
    try:
        with paths["stdout"].open("xb") as stdout, paths["stderr"].open("xb") as stderr:
            result = subprocess.run(argv, cwd=cwd, input=commands.encode() if commands is not None else None,
                                    stdout=stdout, stderr=stderr, timeout=timeout, check=False)
            record["returncode"] = result.returncode
    except subprocess.TimeoutExpired:
        record["timed_out"] = True
    finally:
        record["logs"] = {key: {"path": path.name, "sha256": file_hash(path)} for key, path in paths.items() if path.exists()}
        write_json(output / f"{stem}.record.json", record)
    require(record["returncode"] == 0, f"{stem} did not exit cleanly")
    return tuple(paths[key].read_text(encoding="utf-8", errors="replace") for key in ("stdout", "stderr"))


def run(driver_commit):
    require(re.fullmatch(r"[0-9a-f]{40}", driver_commit), "Expected driver commit SHA")
    output, source, driver = Path("artifact"), Path("source"), Path("driver")
    output.mkdir(exist_ok=False)
    summary = {"schema": "test1757-remote-evidence-v1", "status": "incomplete",
               "source_commit": SOURCE_COMMIT, "source_tree": SOURCE_TREE,
               "driver_commit": driver_commit, "native_scalar_equality": "pending_independent_passive_verification",
               "strength_claim": False, "captures": {}}
    try:
        for label, directory, revision, expected in (("source-head", source, "HEAD", SOURCE_COMMIT),
                ("source-tree", source, "HEAD:", SOURCE_TREE), ("driver-head", driver, "HEAD", driver_commit)):
            stdout, _ = capture(["git", "rev-parse", revision], directory, output, label)
            require(stdout.strip() == expected, label + " differs")
        capture(["git", "diff", "--exit-code", "HEAD", "--"], source, output, "source-clean-before")
        patch, probes = (json.loads((HERE / name).read_bytes()) for name in INPUT_HASHES)
        require(all(file_hash(HERE / name) == expected for name, expected in INPUT_HASHES.items()), "Driver input hash differs")
        require((patch["parent_sha256"], patch["candidate_sha256"], patch["bytes"], len(patch["runs"]), patch["changed_bytes"])
                == (DONOR, EDITED, NETWORK_BYTES, 271, 439), "Frozen patch identity differs")
        require(len(probes["rows"]) == 22 and probes["buckets_per_eval"] == 92, "Frozen synthetic roster differs")
        driver_files = [HERE / "build_and_capture_v1.py", HERE / "test_build_and_capture_v1.py",
                        *(HERE / name for name in INPUT_HASHES), driver / ".github/workflows/test1757-prefetch-build-v1.yml"]
        require(all(path.is_file() for path in driver_files), "A reviewed driver file is missing")
        summary["driver_files"] = {path.resolve().relative_to(driver.resolve()).as_posix(): file_hash(path) for path in driver_files}
        capture(["git", "archive", "--format=tar", "--prefix=stockfish-source/", "--output=../artifact/corresponding-source.tar", SOURCE_COMMIT],
                source, output, "archive-source")
        with tarfile.open(output / "corresponding-source.tar", "a") as archive:
            for relative in summary["driver_files"]:
                archive.add(driver / relative, arcname="ci-driver/" + relative, recursive=False)
        for name in ("AUTHORS", "Copying.txt"):
            shutil.copyfile(source / name, output / name)
        summary["source_archive"] = {"path": "corresponding-source.tar", "sha256": file_hash(output / "corresponding-source.tar")}
        for name in INPUT_HASHES:
            shutil.copyfile(HERE / name, output / name)
        net = source / "src" / NETS["donor"]
        require(not net.exists(), "Donor destination already exists")
        capture(["curl", "--compressed", "--fail", "--location", "--proto", "=https", "--proto-redir", "=https",
                 "--retry", "3", "--output", net.as_posix(), "https://data.stockfishchess.org/nn/" + net.name], Path("."), output, "download-donor")
        require(net.stat().st_size == NETWORK_BYTES and file_hash(net) == DONOR, "Downloaded donor hash or size differs")
        edited = source / "src" / NETS["edited"]
        with edited.open("xb") as stream:
            stream.write(sparse_patch(net.read_bytes(), patch))
        require(edited.stat().st_size == NETWORK_BYTES and file_hash(edited) == EDITED, "Written edited network differs")
        summary["networks"] = {role: {"filename": NETS[role], "sha256": digest_value, "bytes": NETWORK_BYTES}
                               for role, digest_value in (("donor", DONOR), ("edited", EDITED))}
        capture(["g++", "--version"], source / "src", output, "compiler-version")
        capture(["g++", "-v"], source / "src", output, "compiler-identity")
        capture(BUILD_ARGS, source / "src", output, "profile-build", timeout=1800)
        executable = output / "stockfish-windows-x86-64-bmi2.exe"
        shutil.copyfile(source / "src/stockfish.exe", executable)
        engine_hash = file_hash(executable)
        summary["engine"] = {"path": executable.name, "sha256": engine_hash, "bytes": executable.stat().st_size}
        argv = [str(executable.resolve(strict=True))]
        capture(argv, source / "src", output, "engine-compiler", "uci\ncompiler\nisready\nquit\n")
        for role in ("donor", "edited"):
            benches = []
            for stem, mode in ((f"{role}-bench-1", "bench"), (f"{role}-bench-2", "bench"),
                               (f"{role}-perft", "perft"), (f"{role}-eval", "eval")):
                require(file_hash(executable) == engine_hash, "Finished executable changed")
                text = commands_for(EVALFILES[role], mode, probes["rows"])
                stdout, stderr = capture(argv, source / "src", output, stem, text)
                require(not ERROR.search(stdout + "\n" + stderr), stem + " reported an engine error")
                names = NET_MESSAGE.findall(stdout)
                require(all(name == EVALFILES[role] for name in names), "An unexpected network was acknowledged")
                evidence = {"engine_sha256": engine_hash, "network_sha256": summary["networks"][role]["sha256"],
                            "network_filename": NETS[role], "external_evalfile": EVALFILES[role], "network_acknowledgments": names}
                if mode == "bench":
                    require(names, "Bench did not acknowledge its external network")
                    evidence["bench"] = parse_bench(stdout, stderr)
                    benches.append(evidence["bench"]["nodes"])
                elif mode == "perft":
                    totals = re.findall(r"^Nodes searched\s*:\s*(\d+)\s*$", stdout, re.M)
                    require(totals == ["197281"], "Start-position perft4 differs")
                    evidence["perft4_nodes"] = 197281
                else:
                    values = re.findall(r"NNUE evaluation\s+([+-]?\d+)\s+\(side to move, internal units\)", stdout)
                    require(len(names) == len(values) == 22, "Eval capture is incomplete")
                    evidence["native_integers"] = list(map(int, values))
                    evidence["passive_verification"] = "pending"
                summary["captures"][stem] = evidence
            require(benches[0] == benches[1], role + " repeated bench node totals differ")
        require(file_hash(executable) == engine_hash and file_hash(net) == DONOR and file_hash(edited) == EDITED,
                "Executable or networks changed during capture")
        capture(["git", "diff", "--exit-code", "HEAD", "--"], source, output, "source-clean-after")
        summary["status"] = "captured_pending_independent_passive_verification"
    except Exception as exc:
        summary["error"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        write_json(output / "SUMMARY.json", summary)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--driver-commit", required=True)
    run(parser.parse_args().driver_commit)
