"""Measure static scores and fixed-node search scores on the supplied roots.

The first move of the final primary PV must match the engine's bestmove. Only
that final record can supply a teacher score. Failed eligibility keeps both
search records and the reason. A failed UCI session raises and keeps its logs.

The interactive barriers follow the Test1757 history_compare_v1.py driver.
SPDX-License-Identifier: GPL-3.0-or-later
"""

from contextlib import ExitStack
from decimal import Decimal
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import time

import chess
import native_check as native
from fetch_inputs import NETWORK, REFERENCE


SHALLOW_NODES = 16384
DEEP_NODES = 65536
INFO = re.compile(
    r"info depth (\d+) seldepth (\d+) multipv (\d+) score (cp|mate) ([+-]?\d+)"
    r"(?: (upperbound|lowerbound))? nodes (\d+) nps \d+ hashfull \d+ tbhits (\d+) time \d+ pv(?: (.*))?"
)


def _validate_rows(rows):
    native._require(isinstance(rows, list), "Expected an ordered list of roots")
    ids = set()
    for row in rows:
        fen = row["fen"]
        native._require(isinstance(fen, str) and fen.isascii() and fen.isprintable() and len(fen.split(" ")) == 6,
                        "Invalid FEN command")
        board = chess.Board(fen)
        native._require(board.is_valid() and not board.is_check() and not board.is_game_over(), "Invalid static-eval root")
        native._require(type(row["physical_head"]) is int and 0 <= row["physical_head"] < native.BUCKET_COUNT,
                        "Invalid physical head")
        native._require(all(isinstance(row[key], str) and row[key] for key in ("position_id", "family", "role")),
                        "Incomplete root identity")
        native._require(row["position_id"] not in ids, "Duplicate position_id")
        ids.add(row["position_id"])


def _parse_static(prefix, block, net, row):
    # Both parts come from the live session: its actual UCI/setup replies and
    # this position's eval through its own ready barrier.
    trace = native._parse_eval("\n".join(prefix + block), "", net, [row])["rows"][0]
    native._require(trace["used_bucket"] == row["physical_head"], "Static trace selects a different physical head")
    white_cp = int(Decimal(trace["final_score"]) * 100)
    return {**{key: row[key] for key in ("position_id", "family", "physical_head", "role", "fen")},
            "static_cp": white_cp if row["fen"].split()[1] == "w" else -white_cp,
            "final_score_white_pawns": trace["final_score"], "nnue_score_white_pawns": trace["white_score"],
            "raw_score": trace["raw_score"], "used_bucket": trace["used_bucket"], "buckets": trace["buckets"]}


def _parse_search(lines, board, net, budget):
    body, = native._ack_blocks(lines, net, 1)
    best = [native.BESTMOVE.fullmatch(line) for line in body if line.startswith("bestmove")]
    native._require(len(best) == 1 and best[0] and body[-1] == best[0][0], "Missing or malformed bestmove")
    bestmove, ponder = best[0][1], best[0][2]
    replay = board.copy(stack=False)
    replay.push_uci(bestmove)
    if ponder is not None:
        replay.push_uci(ponder)
    result = {"requested_nodes": budget, "bestmove": bestmove, "ponder": ponder, "depth": None,
              "seldepth": None, "score_kind": None, "score_value": None, "bound": None,
              "reported_nodes": None, "pv": [], "cp": None, "reason": None}
    marked = [line for line in body if line.startswith("info depth ") and " score " in line]
    record = INFO.fullmatch(marked[-1]) if marked else None
    if not record:
        result["reason"] = "no_final_score_marker"
        return result
    depth, seldepth, multipv, kind, value, bound, nodes, tb_hits, moves = record.groups()
    native._require(int(tb_hits) == 0, "Unexpected tablebase hit")
    pv = moves.split() if moves else []
    replay = board.copy(stack=False)
    for move in pv:
        native._require(re.fullmatch(native.MOVE, move), "Invalid UCI move in teacher PV")
        replay.push_uci(move)
    result.update(depth=int(depth), seldepth=int(seldepth), score_kind=kind, score_value=int(value),
                  bound=bound, reported_nodes=int(nodes), pv=pv)
    if int(multipv) != 1 or not pv or pv[0] != bestmove or int(nodes) <= 0 or int(depth) <= 0:
        result["reason"] = "no_matching_primary_pv"
    elif bound is not None:
        result["reason"] = "bounded_score"
    elif kind != "cp":
        result["reason"] = "mate_score"
    else:
        result["cp"] = int(value)
    return result


def _stability(shallow, deep):
    for label, search in (("shallow", shallow), ("deep", deep)):
        if search["reason"]:
            return False, label + "_" + search["reason"]
    if shallow["bestmove"] != deep["bestmove"]:
        return False, "bestmove_disagreement"
    if max(abs(shallow["cp"]), abs(deep["cp"])) > 250:
        return False, "score_out_of_range"
    if abs(deep["cp"] - shallow["cp"]) > 20:
        return False, "budget_score_difference"
    return True, "stable"


class _Session:
    def __init__(self, exe, work, logs):
        self.exe, self.work, self.logs = exe, work, logs
        self.process = None
        self.readers = []
        self.events = queue.Queue()
        self.reader_errors = []
        self.forced = False

    def start(self):
        self.process = subprocess.Popen([str(self.exe)], cwd=self.work, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for channel, pipe in (("stdout", self.process.stdout), ("stderr", self.process.stderr)):
            thread = threading.Thread(target=self._read, args=(channel, pipe), daemon=True)
            thread.start()
            self.readers.append(thread)

    def _read(self, channel, pipe):
        try:
            with pipe:
                for raw in iter(pipe.readline, b""):
                    self.logs[channel].write(raw)
                    self.logs[channel].flush()
                    line = raw.decode("utf-8").rstrip("\r\n")
                    if native.ERROR.search(line) or (channel == "stderr" and line.startswith(native.ACK_PREFIX)):
                        message = "Native protocol error: " + line
                        self.reader_errors.append(message)
                        self.events.put(("fatal", message))
                    if channel == "stdout":
                        self.events.put(("line", line))
        except Exception as exc:
            self.reader_errors.append(str(exc))
            self.events.put(("fatal", str(exc)))
        finally:
            if channel == "stdout":
                self.events.put(("eof", "Engine stdout closed"))

    def send(self, command):
        native._require(command.isascii() and command.isprintable(), "Invalid interactive UCI command")
        data = (command + "\n").encode("ascii")
        self.logs["uci"].write(data)
        self.logs["uci"].flush()
        self.process.stdin.write(data)
        self.process.stdin.flush()

    def receive(self, predicate):
        deadline = time.monotonic() + 120
        lines = []
        while True:
            try:
                kind, line = self.events.get(timeout=max(0, deadline - time.monotonic()))
            except queue.Empty as exc:
                raise TimeoutError("Timed out waiting for an engine reply") from exc
            if kind != "line":
                raise RuntimeError(line)
            lines.append(line)
            if predicate(line):
                return lines

    def ready(self):
        self.send("isready")
        return self.receive(lambda line: line == "readyok")

    def quiet_ready(self):
        block = self.ready()
        native._require(not any(line.startswith((native.ACK_PREFIX, "bestmove", "info depth ", "NNUE evaluation", "|"))
                                for line in block), "Unexpected result crossing a UCI barrier")

    def configure(self, net):
        template = native._commands(net, "eval", []).decode("utf-8").splitlines()
        self.send(template[0])
        prefix = self.receive(lambda line: line == "uciok")
        for command in template[1:-2]:
            self.send(command)
        self.prefix = prefix + self.ready()

    def evaluate(self, net, row):
        self.send("position fen " + row["fen"])
        self.send("eval")
        return _parse_static(self.prefix, self.ready(), net, row)

    def search(self, net, row, budget):
        self.send("ucinewgame")
        self.quiet_ready()
        self.send("position fen " + row["fen"])
        self.send(f"go nodes {budget}")
        lines = self.receive(lambda line: line.startswith("bestmove"))
        self.quiet_ready()
        return _parse_search(lines, chess.Board(row["fen"]), net, budget)

    def close(self, normal):
        if self.process is None:
            return
        try:
            if normal and self.process.poll() is None:
                self.send("quit")
                self.process.wait(timeout=15)
        finally:
            if self.process.poll() is None:
                self.forced = True
                self.process.kill()
                self.process.wait(timeout=15)
            try:
                self.process.stdin.close()
            except BrokenPipeError:
                pass
            finally:
                for reader in self.readers:
                    reader.join(timeout=5)


def _collect(exe, net, rows, output, work, stem, teacher):
    native._require(re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]*", stem), "Invalid capture filename stem")
    output = Path(output).resolve(strict=True)
    record = {"schema": "test1757-static-teacher-v1", "status": "incomplete", "teacher": teacher,
              "returncode": None, "forced_cleanup": False, "requested_rows": len(rows)}
    results = []
    paths = {kind: output / f"{stem}.{suffix}" for kind, suffix in
             (("uci", "uci"), ("stdout", "stdout.txt"), ("stderr", "stderr.txt"), ("inputs", "inputs.json"), ("rows", "rows.json"))}
    try:
        native._require(os.name == "nt" and os.environ.get("GITHUB_ACTIONS") == "true", "Labels require Windows GitHub Actions")
        exe, net, work = (Path(p).resolve(strict=True) for p in (exe, net, work))
        _validate_rows(rows)
        native._json(paths["inputs"], rows)
        files = {"executable": exe, "network": net, "inputs": paths["inputs"]}
        before = {key: native._receipt(path) for key, path in files.items()}
        record.update(inputs_before=before, argv=[str(exe)], cwd=str(work))
        REFERENCE.verify(before["executable"]["bytes"], before["executable"]["sha256"])
        if teacher:
            NETWORK.verify(before["network"]["bytes"], before["network"]["sha256"])
        else:
            native._require(before["network"]["bytes"] == NETWORK.size, "Unexpected static evaluation network size")
        relative = os.path.relpath(net, work)
        if not relative.startswith("."):
            relative = ".\\" + relative
        native._require(relative.isascii() and relative.isprintable() and not Path(relative).is_absolute()
                        and (work / relative).resolve(strict=True) == net, "Invalid external EvalFile path")
        record["evalfile"] = relative
        with ExitStack() as stack:
            logs = {key: stack.enter_context(paths[key].open("xb")) for key in ("uci", "stdout", "stderr")}
            session = _Session(exe, work, logs)
            complete = False
            try:
                if rows:
                    session.start()
                    session.configure(relative)
                for row in rows:
                    observed = session.evaluate(relative, row)
                    if teacher:
                        shallow = session.search(relative, row, SHALLOW_NODES)
                        deep = session.search(relative, row, DEEP_NODES)
                        stable, reason = _stability(shallow, deep)
                        observed.update(parent_static_cp=observed["static_cp"], shallow_cp=shallow["cp"], deep_cp=deep["cp"],
                                        shallow_search=shallow, deep_search=deep, stable=stable, reason=reason)
                    results.append(observed)
                complete = True
            finally:
                try:
                    session.close(complete)
                finally:
                    record.update(returncode=session.process.returncode if session.process is not None else None,
                                  forced_cleanup=session.forced, reader_errors=session.reader_errors,
                                  log_readers_closed=all(not thread.is_alive() for thread in session.readers))
        native._require((not rows or record["returncode"] == 0) and not record["forced_cleanup"]
                        and not record["reader_errors"] and record["log_readers_closed"], "Unclean native capture")
        lines = paths["stdout"].read_text(encoding="utf-8").splitlines()
        acks = [line for line in lines if line.startswith(native.ACK_PREFIX)]
        native._require(acks == [native.ACK_PREFIX + relative + native.ACK_SUFFIX] * (len(rows) * (3 if teacher else 1)),
                        "Unbound or missing network acknowledgement in complete transcript")
        commands = paths["uci"].read_text(encoding="utf-8").splitlines()
        native._require(lines.count("readyok") == commands.count("isready") and lines.count("uciok") == commands.count("uci"),
                        "Unbound UCI or ready acknowledgement")
        native._require(not rows or commands[-1:] == ["quit"], "Missing clean quit")
        native._require(sum(line.startswith("bestmove") for line in lines) == (2 * len(rows) if teacher else 0),
                        "Unexpected number of completed searches")
        record["inputs_after"] = {key: native._receipt(path) for key, path in files.items()}
        native._require(record["inputs_after"] == before, "Executable, network or supplied roster changed during capture")
        record["status"] = "complete"
    except Exception as exc:
        record["error"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        native._json(paths["rows"], results)
        record["completed_rows"] = len(results)
        record["files"] = {key: native._receipt(path) for key, path in paths.items() if path.exists()}
        native._json(output / f"{stem}.record.json", record)
    return results


def collect_labels(exe: Path, net: Path, rows: list[dict], output: Path, work: Path, stem: str) -> list[dict]:
    """Get original static and fixed-node search scores for each supplied root, in order."""
    return _collect(exe, net, rows, output, work, stem, teacher=True)


def static_evals(exe: Path, net: Path, rows: list[dict], output: Path, work: Path, stem: str) -> list[dict]:
    """Get static scores and complete printed tables for the supplied external network."""
    return _collect(exe, net, rows, output, work, stem, teacher=False)
