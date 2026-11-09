"""Finite interactive UCI differential checks; importing starts no process.
SPDX-License-Identifier: GPL-3.0-or-later
"""
import json
from pathlib import Path
import queue
import re
import subprocess
import threading
import time

import chess

from build_and_capture_v1 import ERROR, NET_MESSAGE, EVALFILES, file_hash, require, setup_commands, write_json

REFERENCE_SHA = "12a8e7bfdd9b1189b4350bd9ebef3501be1e89bd5fa41dcb56921f85ba23869a"
REQUIRED_COVERAGE = {kind + "_" + side for kind in ("quiet", "capture", "castle", "ep", "promotion", "underpromotion") for side in ("white", "black")}


def validate_histories(document):
    require(chess.__version__ == "1.11.2", "Pinned chess version differs")
    require(document["schema"] == "accumulator-history-cases-v1" and document["depth"] == 6, "Frozen history contract differs")
    require(len(document["cases"]) == 8, "History case roster differs")
    ids, coverage = set(), set()
    for case in document["cases"]:
        require(re.fullmatch(r"[a-z0-9-]+", case["id"]) and case["id"] not in ids, "Invalid or duplicate case ID")
        ids.add(case["id"])
        board = chess.Board(case["fen"])
        require(board.is_valid() and not board.is_game_over(), "Invalid or terminal synthetic root")
        for token in case["moves"]:
            move = chess.Move.from_uci(token)
            require(move in board.legal_moves, "Illegal frozen history move")
            side = "white" if board.turn else "black"
            if board.is_capture(move): coverage.add("capture_" + side)
            if board.is_castling(move): coverage.add("castle_" + side)
            if board.is_en_passant(move): coverage.add("ep_" + side)
            if move.promotion:
                coverage.add("promotion_" + side)
                if move.promotion != chess.QUEEN: coverage.add("underpromotion_" + side)
            elif not board.is_capture(move) and not board.is_castling(move):
                coverage.add("quiet_" + side)
            board.push(move)
            require(board.is_valid() and not board.is_game_over(), "History reaches a terminal board")
    require(REQUIRED_COVERAGE <= coverage, "History coverage is incomplete")
    return sorted(coverage)


def checkpoints(case):
    count = len(case["moves"])
    return [{"kind": "forward", "prefix": value} for value in range(count + 1)] + [
        {"kind": "repeat", "prefix": count}, {"kind": "undo", "prefix": count - 1},
        {"kind": "reuse", "prefix": count}]


def parse_search(lines, board, expected_network):
    require(NET_MESSAGE.findall("\n".join(lines)) == [expected_network], "Search network acknowledgment differs")
    rows, best = {}, []
    for line in lines:
        if line.startswith("bestmove "):
            match = re.fullmatch(r"bestmove ([a-h][1-8][a-h][1-8][qrbn]?)(?: ponder ([a-h][1-8][a-h][1-8][qrbn]?))?", line)
            require(match is not None, "Invalid bestmove reply")
            replay = board.copy(); move = chess.Move.from_uci(match[1]); require(move in replay.legal_moves, "Illegal bestmove")
            replay.push(move)
            if match[2]: require(chess.Move.from_uci(match[2]) in replay.legal_moves, "Illegal ponder")
            best.append({"bestmove": match[1], "ponder": match[2]})
        if not line.startswith("info depth "): continue
        tokens = line.split()
        if "score" not in tokens or "pv" not in tokens: continue
        value = lambda key: tokens[tokens.index(key) + 1]
        score_index = tokens.index("score")
        score_kind, score_value = tokens[score_index + 1:score_index + 3]
        require(score_kind in ("cp", "mate"), "Unknown score scale")
        pv = tokens[tokens.index("pv") + 1:]
        require(pv, "Empty search PV")
        replay = board.copy()
        for token in pv:
            move = chess.Move.from_uci(token); require(move in replay.legal_moves, "Illegal PV move"); replay.push(move)
        depth = int(value("depth"))
        rows[depth] = {"depth": depth, "seldepth": int(value("seldepth")) if "seldepth" in tokens else None,
                       "score": {"kind": score_kind, "value": int(score_value),
                                 "bound": "lowerbound" if "lowerbound" in tokens else "upperbound" if "upperbound" in tokens else None},
                       "nodes": int(value("nodes")), "pv": pv}
    require(len(best) == 1 and set(rows) == set(range(1, 7)) and rows[6]["nodes"] > 0, "Incomplete deterministic depth6 search")
    return {"per_depth": [rows[key] for key in sorted(rows)], **best[0]}


class Session:
    """One owned engine per case; go always finishes before another command."""
    def __init__(self, executable, cwd, output, stem):
        self.output, self.stem = output, stem
        self.argv, self.cwd = [str(executable.resolve(strict=True))], str(cwd.resolve(strict=True))
        self.events = queue.Queue(); self.searching = False; self.forced = False
        self.commands = (output / (stem + ".uci")).open("xb")
        try:
            self.process = subprocess.Popen(self.argv, cwd=self.cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except Exception as exc:
            self.commands.close()
            write_json(output / f"{stem}.record.json", {"argv": self.argv, "cwd": self.cwd,
                       "returncode": None, "startup_failure": {"type": type(exc).__name__, "message": str(exc)}})
            raise
        self.threads = []
        for channel in ("stdout", "stderr"):
            thread = threading.Thread(target=self.read_pipe, args=(channel,), daemon=True)
            thread.start(); self.threads.append(thread)

    def read_pipe(self, channel):
        total = 0
        try:
            with (self.output / f"{self.stem}.{channel}.txt").open("xb") as log:
                for raw in iter(getattr(self.process, channel).readline, b""):
                    total += len(raw)
                    if total > 16 * 1024 * 1024: raise ValueError("History output limit exceeded")
                    log.write(raw); log.flush()
                    self.events.put((channel, raw.decode("utf-8").rstrip("\r\n")))
            self.events.put(("eof", channel))
        except Exception as exc:
            self.events.put(("fatal", type(exc).__name__ + ": " + str(exc)))

    def send(self, command):
        require("\n" not in command and "\r" not in command, "Multiline interactive command")
        raw = (command + "\n").encode(); self.commands.write(raw); self.commands.flush()
        self.process.stdin.write(raw); self.process.stdin.flush()

    def wait_for(self, predicate, seconds=60):
        deadline, lines = time.monotonic() + seconds, []
        while True:
            remaining = deadline - time.monotonic()
            require(remaining > 0, "Interactive UCI reply timed out")
            try: channel, line = self.events.get(timeout=remaining)
            except queue.Empty: raise ValueError("Interactive UCI reply timed out")
            require(channel != "fatal" and not ERROR.search(line), "Interactive engine error: " + line)
            require(not (channel == "eof" and line == "stdout"), "Engine exited before required reply")
            if channel == "stdout":
                lines.append(line)
                if predicate(line): return lines

    def configure(self, network):
        commands = setup_commands(network)
        self.send(commands[0]); self.wait_for(lambda line: line == "uciok")
        for command in commands[1:-1]: self.send(command)
        self.send("isready"); self.wait_for(lambda line: line == "readyok")
        self.send("ucinewgame"); self.send("isready"); self.wait_for(lambda line: line == "readyok")

    def search(self, case, prefix, network):
        board = chess.Board(case["fen"])
        for token in case["moves"][:prefix]: board.push_uci(token)
        position = "position fen " + case["fen"]
        if prefix: position += " moves " + " ".join(case["moves"][:prefix])
        self.send(position)
        self.searching = True; self.send("go depth 6")
        lines = self.wait_for(lambda line: line.startswith("bestmove "))
        self.searching = False
        result = parse_search(lines, board, network)
        self.send("isready"); self.wait_for(lambda line: line == "readyok")
        return result

    def close(self):
        try:
            if self.process.poll() is None and not self.searching:
                self.send("quit")
                try: self.process.wait(timeout=10)
                except subprocess.TimeoutExpired: self.forced = True; self.process.kill(); self.process.wait(timeout=10)
            elif self.process.poll() is None:
                self.forced = True; self.process.kill(); self.process.wait(timeout=10)
        finally:
            self.commands.close()
            for thread in self.threads: thread.join(timeout=3)
            record = {"argv": self.argv, "cwd": self.cwd, "returncode": self.process.returncode,
                      "forced_cleanup": self.forced, "log_readers_closed": all(not t.is_alive() for t in self.threads)}
            record["files"] = {suffix: {"path": f"{self.stem}.{suffix}", "sha256": file_hash(self.output / f"{self.stem}.{suffix}")}
                               for suffix in ("uci", "stdout.txt", "stderr.txt")}
            write_json(self.output / f"{self.stem}.record.json", record)
        require(record["returncode"] == 0 and not record["forced_cleanup"] and record["log_readers_closed"], "History engine did not exit cleanly")


def run(candidate, candidate_sha, reference, cwd, output, document):
    coverage = validate_histories(document)
    require(file_hash(reference) == REFERENCE_SHA, "Reference executable differs")
    results = {"schema": "accumulator-history-differential-v1", "status": "incomplete",
               "reference_sha256": REFERENCE_SHA, "candidate_sha256": candidate_sha,
               "depth": 6, "coverage": coverage, "cases": [], "strength_claim": False,
               "independent_native_acceptance": "pending_real_CI_and_independent_review"}
    try:
        for role in ("donor", "edited"):
            for case in document["cases"]:
                observations = {}
                for identity, executable, expected in (("reference", reference, REFERENCE_SHA), ("candidate", candidate, candidate_sha)):
                    require(file_hash(executable) == expected, "History executable changed before case")
                    session = Session(executable, cwd, output, f"history-{role}-{identity}-{case['id']}")
                    try:
                        session.configure(EVALFILES[role])
                        observations[identity] = [{**point, "search": session.search(case, point["prefix"], EVALFILES[role])}
                                                  for point in checkpoints(case)]
                    except Exception:
                        # close writes its own failure record; keep the original
                        # protocol/parser error as the result's primary cause.
                        try: session.close()
                        except Exception: pass
                        raise
                    else: session.close()
                    require(file_hash(executable) == expected, "History executable changed after case")
                result = {"network": role, "case": case["id"], "searches": len(observations["reference"]),
                          "reference": observations["reference"], "candidate": observations["candidate"],
                          "equal": observations["reference"] == observations["candidate"]}
                results["cases"].append(result)
                require(result["equal"], "History nodes/scores/PVs differ: " + role + "/" + case["id"])
        require(file_hash(reference) == REFERENCE_SHA and file_hash(candidate) == candidate_sha, "Final history executable hash differs")
        results["status"] = "differential_checks_passed_pending_independent_review"
    except Exception as exc:
        results["error"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally: write_json(output / "HISTORY-RESULT.json", results)
    return {"status": results["status"], "cases": len(results["cases"]),
            "reference_sha256": REFERENCE_SHA, "sha256": file_hash(output / "HISTORY-RESULT.json")}
