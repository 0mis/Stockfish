"""Capture finite native layout comparisons on a Windows GitHub Actions runner.

The command and capture format follows Test1757 build_and_capture_v1.py. The
parsed comparisons use printed bucket values, raw integers and search records.
The original streams retain timing fields.

SPDX-License-Identifier: GPL-3.0-or-later
"""

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from fetch_inputs import NETWORK, REFERENCE


BUCKET_COUNT = 92
BENCH_NODES = 3464885
PERFT_NODES = 197281
BORDER = "+------------+------------+"
ACK_PREFIX = "info string NNUE evaluation using "
ACK_SUFFIX = " (131MiB, (131952, 1024, 32, 32, 1))"
ERROR = re.compile(r"ERROR:|CRITICAL ERROR|No such option|Unknown command|Unable to open file|network evaluation parameters compatible", re.I)
BUCKET_ROW = re.compile(r"\|\s*(\d+)\s*\|\s*([+-]?)\s*(\d+\.\d{2})\s*\|\s*(<-- this bucket is used)?")
RAW_SCORE = re.compile(r"NNUE evaluation\s+([+-]?\d+)\s+\(side to move, internal units\)")
WHITE_SCORE = re.compile(r"NNUE evaluation\s+([+-]?\d+\.\d{2})\s+\(white side\)")
FINAL_EVAL = re.compile(r"Final evaluation\s+([+-]?\d+\.\d{2})\s+\(white side\) \[with scaled NNUE, \.\.\.\]")
MOVE = r"[a-h][1-8][a-h][1-8][qrbn]?"
FINAL_INFO = re.compile(
    rf"info depth (13) seldepth (\d+) multipv 1 score (cp|mate) ([+-]?\d+)"
    rf"(?: (lowerbound|upperbound))? nodes (\d+) nps \d+ hashfull \d+ tbhits 0 time \d+ pv ({MOVE}(?: {MOVE})*)"
)
BESTMOVE = re.compile(rf"bestmove ({MOVE}|\(none\))(?: ponder ({MOVE}))?")
TERMINAL_INFO = re.compile(r"info depth 0 score (cp|mate) ([+-]?\d+)")
ROOT_MOVES = {f"{file}2{file}{rank}" for file in "abcdefgh" for rank in (3, 4)} | {"b1a3", "b1c3", "g1f3", "g1h3"}


def _require(ok, message):
    if not ok:
        raise ValueError(message)


def _receipt(path):
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"path": path.name, "bytes": path.stat().st_size, "sha256": digest}


def _json(path, document):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(document, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def _commands(net, mode, rows):
    options = [("NumaPolicy", "none"), ("Threads", "1"), ("Hash", "16"), ("MultiPV", "1"),
               ("Skill Level", "20"), ("UCI_Chess960", "false"), ("UCI_LimitStrength", "false"),
               ("Ponder", "false"), ("SyzygyPath", "<empty>"), ("SyzygyProbeLimit", "0"), ("EvalFile", net)]
    commands = ["uci", *(f"setoption name {key} value {value}" for key, value in options), "isready"]
    if mode == "eval":
        for row in rows:
            commands.extend(["position fen " + row["fen"], "eval", "isready"])
    elif mode == "bench":
        commands.extend(["bench 16 1 13 default depth", "isready"])
    elif mode == "perft":
        commands.extend(["position startpos", "go perft 4", "isready"])
    else:
        raise ValueError(f"Unexpected native mode {mode}")
    return ("\n".join(commands + ["quit"]) + "\n").encode("utf-8")


def _capture(exe, work, output, stem, commands, net_path, exe_sha, net_sha):
    inputs = output / f"{stem}.uci"
    with inputs.open("xb") as stream:
        stream.write(commands)
    stdout, stderr = (output / f"{stem}.{key}.txt" for key in ("stdout", "stderr"))
    record = {"argv": [str(exe)], "cwd": str(work), "commands": _receipt(inputs), "executable_sha256": exe_sha,
              "evalfile": net_path, "network_sha256": net_sha, "returncode": None, "timed_out": False}
    try:
        with stdout.open("xb") as out, stderr.open("xb") as err, inputs.open("rb") as control:
            result = subprocess.run([str(exe)], cwd=work, stdin=control, stdout=out, stderr=err, timeout=240, check=False)
        record["returncode"] = result.returncode
        _require(result.returncode == 0, f"{stem} exited with status {result.returncode}")
    except Exception as exc:
        record["timed_out"] = isinstance(exc, subprocess.TimeoutExpired)
        record["error"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        record["logs"] = {key: _receipt(path) for key, path in (("stdout", stdout), ("stderr", stderr)) if path.exists()}
        _json(output / f"{stem}.record.json", record)
    return stdout.read_text(encoding="utf-8"), stderr.read_text(encoding="utf-8")


def _ready_blocks(stdout, stderr, expected):
    _require(not ERROR.search(stdout + "\n" + stderr), "Native error appeared in the control or data streams")
    _require(not any(line.startswith(ACK_PREFIX) for line in stderr.splitlines()), "Unexpected network acknowledgement on stderr")
    lines = stdout.splitlines()
    ready = [idx for idx, line in enumerate(lines) if line == "readyok"]
    _require(len(ready) == expected + 1 and lines.count("uciok") == 1 and lines.index("uciok") < ready[0],
             "Incomplete UCI or ready acknowledgements")
    _require(not any(line.strip() for line in lines[ready[-1] + 1:]), "Output after final ready acknowledgement")
    _require(not any(line.startswith(ACK_PREFIX) for line in lines[:ready[0]]), "Unbound network acknowledgement")
    return [lines[start + 1:end] for start, end in zip(ready, ready[1:])]


def _ack_blocks(lines, net, expected):
    starts = [idx for idx, line in enumerate(lines) if line.startswith(ACK_PREFIX)]
    _require(len(starts) == expected and all(lines[idx] == ACK_PREFIX + net + ACK_SUFFIX for idx in starts),
             f"Expected {expected} explicit acknowledgements of {net}")
    _require(not any(line.startswith(("info depth ", "bestmove ", "NNUE network contributions", "|", "Nodes searched"))
                     for line in lines[:starts[0]]), "Native result before its network acknowledgement")
    return [lines[start + 1:end] for start, end in zip(starts, starts[1:] + [len(lines)])]


def _parse_eval(stdout, stderr, net, rows):
    records = []
    for block, row in zip(_ready_blocks(stdout, stderr, len(rows)), rows):
        lines, = _ack_blocks(block, net, 1)
        side = {"w": "White", "b": "Black"}[row["fen"].split()[1]]
        headings = [i for i, line in enumerate(lines) if line.startswith("NNUE network contributions")]
        _require(len(headings) == 1 and lines[headings[0]] == f"NNUE network contributions (Normalized, {side} to move)",
                 "Eval trace heading differs from the requested FEN")
        start = headings[0] + 4
        end = start + BUCKET_COUNT
        _require(lines[start - 3:start] == [BORDER, "|   Bucket   | Evaluation |", BORDER] and lines[end:end + 1] == [BORDER],
                 "Incomplete 92-row bucket table")
        _require(sum(line.startswith("|") for line in lines) == BUCKET_COUNT + 1, "Extra or missing printed bucket")
        buckets = []
        for index, line in enumerate(lines[start:end]):
            match = BUCKET_ROW.fullmatch(line)
            _require(match and int(match[1]) == index, f"Invalid bucket row {index}")
            # Preserve the displayed sign, including the sign of rounded zero.
            buckets.append({"index": index, "printed_value": match[2] + match[3], "used": bool(match[4])})
        active = [item["index"] for item in buckets if item["used"]]
        _require(len(active) == 1, "Expected exactly one selected bucket")
        tail = [line for line in lines[end + 1:] if line.strip()]
        _require(len(tail) == 3 and sum(line.startswith(("NNUE evaluation", "Final evaluation")) for line in lines) == 3,
                 "Incomplete or unexpected eval score output")
        raw, white, final = RAW_SCORE.fullmatch(tail[0]), WHITE_SCORE.fullmatch(tail[1]), FINAL_EVAL.fullmatch(tail[2])
        _require(raw and white and final, "Missing exact selected or displayed eval score")
        records.append({"fen": row["fen"], "buckets": buckets, "used_bucket": active[0], "raw_score": int(raw[1]),
                        "white_score": white[1], "final_score": final[1]})
    return {"network_acknowledgements": len(records), "rows": records}


def _bench_search(lines, index, fen):
    best_indices = [i for i, line in enumerate(lines) if line.startswith("bestmove")]
    _require(len(best_indices) == 1 and not any(line.strip() for line in lines[best_indices[0] + 1:]),
             f"Incomplete bestmove or unexpected output at bench position {index}")
    best = BESTMOVE.fullmatch(lines[best_indices[0]])
    _require(best is not None, f"Malformed bestmove at bench position {index}")
    scored = [line for line in lines[:best_indices[0]] if line.startswith("info depth ") and " score " in line]
    _require(scored, f"Missing scored search at bench position {index}")
    if index in (48, 49):
        terminal = TERMINAL_INFO.fullmatch(scored[-1])
        _require(len(scored) == 1 and terminal and best[1] == "(none)" and best[2] is None, "Invalid terminal search")
        depth, seldepth, kind, score, bound, nodes, pv = 0, None, terminal[1], int(terminal[2]), None, 0, []
    else:
        final = FINAL_INFO.fullmatch(scored[-1])
        _require(final and set(int(line.split()[2]) for line in scored) == set(range(1, 14)), "Incomplete depth-13 search")
        depth, seldepth, kind, score, bound, nodes, pv = int(final[1]), int(final[2]), final[3], int(final[4]), final[5], int(final[6]), final[7].split()
        _require(nodes > 0 and best[1] == pv[0], f"Final PV or nodes disagree at bench position {index}")
    return {"position": index, "fen": fen, "depth": depth, "seldepth": seldepth, "score_kind": kind,
            "score": score, "bound": bound, "nodes": nodes, "pv": pv, "bestmove": best[1], "ponder": best[2]}


def _parse_bench(stdout, stderr, net):
    block, = _ready_blocks(stdout, stderr, 1)
    searches = _ack_blocks(block, net, 51)
    markers = [re.fullmatch(r"Position: (\d+)/(\d+) \(([^()]+)\)", line)
               for line in stderr.splitlines() if line.startswith("Position:")]
    _require(len(markers) == 51 and all(m and int(m[1]) == i and int(m[2]) == 51 for i, m in enumerate(markers, 1)),
             "Bench position roster or ordering differs")
    totals = [re.fullmatch(r"Nodes searched\s*:\s*(\d+)\s*", line)
              for line in stderr.splitlines() if line.startswith("Nodes searched")]
    _require(len(totals) == 1 and totals[0] and int(totals[0][1]) == BENCH_NODES, "Canonical bench total differs")
    positions = [_bench_search(chunk, int(marker[1]), marker[3]) for chunk, marker in zip(searches, markers)]
    _require(sum(row["nodes"] for row in positions) == BENCH_NODES, "Bench position node vector differs from its total")
    return {"network_acknowledgements": len(positions), "nodes": BENCH_NODES, "positions": positions}


def _parse_perft(stdout, stderr, net):
    block, = _ready_blocks(stdout, stderr, 1)
    lines, = _ack_blocks(block, net, 1)
    divides = {}
    totals = []
    for line in lines:
        if not line.strip() or line.startswith("info string Network replica "):
            continue
        total = re.fullmatch(r"Nodes searched\s*:\s*(\d+)", line)
        if total:
            totals.append(int(total[1]))
            continue
        match = re.fullmatch(rf"({MOVE}): (\d+)", line)
        _require(match and match[1] not in divides and not totals, "Unexpected or duplicate perft divide")
        divides[match[1]] = int(match[2])
    _require(totals == [PERFT_NODES] and set(divides) == ROOT_MOVES and sum(divides.values()) == PERFT_NODES,
             "Incomplete or incorrect start-position perft4")
    return {"network_acknowledgements": 1, "nodes": PERFT_NODES, "divides": divides}


def _validated_rows(rows):
    # chess is installed in the CI environment that produced the sampled FENs.
    import chess

    _require(isinstance(rows, list) and 0 < len(rows) <= 30, "Expected 1 to 30 native positions")
    seen = set()
    for row in rows:
        fen = row["fen"]
        _require(isinstance(fen, str) and len(fen.split(" ")) == 6 and fen.isascii() and fen.isprintable(), "Invalid FEN command")
        board = chess.Board(fen)
        _require(board.is_valid() and not board.is_check() and not board.is_game_over() and not board.chess960,
                 "Expected a legal nonterminal standard position with the side to move out of check")
        _require(fen not in seen, "Duplicate native eval position")
        seen.add(fen)


def check_native(executable: Path, parent_net: Path, candidate_net: Path, rows: list[dict], output: Path, work: Path) -> dict:
    """Run six fresh processes and save evidence. Raise on any failed comparison."""
    output = output.resolve(strict=True)
    summary = {"schema": "test1757-layout-native-v1", "status": "incomplete", "equality_verified": False, "captures": {}}
    try:
        _require(os.name == "nt" and os.environ.get("GITHUB_ACTIONS") == "true", "Native captures require a Windows GitHub Actions runner")
        exe, parent, candidate, work = (path.resolve(strict=True) for path in (executable, parent_net, candidate_net, work))
        _require(work.is_dir() and parent != candidate, "Invalid working directory or repeated network path")
        _validated_rows(rows)
        row_file = output / "native-rows.json"
        _json(row_file, rows)
        files = {"executable": exe, "parent": parent, "candidate": candidate, "rows": row_file}
        before = {role: _receipt(path) for role, path in files.items()}
        summary["inputs"] = before
        for role, spec in (("executable", REFERENCE), ("parent", NETWORK)):
            spec.verify(before[role]["bytes"], before[role]["sha256"])
        _require(before["candidate"]["bytes"] == NETWORK.size and before["candidate"]["sha256"] != NETWORK.sha256,
                 "Unexpected candidate network size or identity")
        parsed = {}
        for role, net in (("parent", parent), ("candidate", candidate)):
            parsed[role] = {}
            relative = os.path.relpath(net, work)
            if not relative.startswith("."):
                relative = ".\\" + relative
            _require(relative.isprintable() and not Path(relative).is_absolute() and (work / relative).resolve(strict=True) == net,
                     "Invalid external EvalFile path")
            for mode in ("eval", "bench", "perft"):
                stem = f"native-{role}-{mode}"
                stdout, stderr = _capture(exe, work, output, stem, _commands(relative, mode, rows), relative,
                                          before["executable"]["sha256"], before[role]["sha256"])
                if mode == "eval":
                    result = _parse_eval(stdout, stderr, relative, rows)
                elif mode == "bench":
                    result = _parse_bench(stdout, stderr, relative)
                else:
                    result = _parse_perft(stdout, stderr, relative)
                parsed[role][mode] = result
                path = output / f"{stem}.parsed.json"
                _json(path, result)
                summary["captures"][stem] = {"parsed": _receipt(path), "process": _receipt(output / f"{stem}.record.json")}
        after = {role: _receipt(path) for role, path in files.items()}
        summary["inputs_after"] = after
        _require(after == before, "Native executable, network or input roster changed during capture")
        for mode in ("eval", "bench", "perft"):
            _require(parsed["parent"][mode] == parsed["candidate"][mode], f"Parent and candidate {mode} records differ")
        summary.update(status="passed", equality_verified=True, eval_positions=len(rows), buckets_per_eval=BUCKET_COUNT,
                       bench_positions=51, bench_nodes=BENCH_NODES, perft4_nodes=PERFT_NODES, perft_root_moves=20)
    except Exception as exc:
        summary["error"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        _json(output / "native-check.json", summary)
    return summary
