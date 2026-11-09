"""Windows BMI2 capture routines from the reviewed test1757 v1 driver.
SPDX-License-Identifier: GPL-3.0-or-later
"""
import hashlib
import json
from pathlib import Path
import re
import subprocess


BUILD_ARGS = ["make", "-j2", "profile-build", "ARCH=x86-64-bmi2", "COMP=mingw",
              "COMPCXX=g++", "EXTRAPROFILEFLAGS=-mmove-max=128"]


ERROR = re.compile(r"ERROR:|CRITICAL ERROR|No such option|Unknown command|Unable to open file|network evaluation parameters compatible", re.I)


NET_MESSAGE = re.compile(r"^info string NNUE evaluation using (.+?) \(\d+MiB,", re.M)


BENCH = re.compile(r"Total time \(ms\)\s*:\s*(\d+)\s+Nodes searched\s*:\s*(\d+)\s+Nodes/second\s*:\s*(\d+)")


def require(condition, message):
    if not condition:
        raise ValueError(message)


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
