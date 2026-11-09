"""Unchanged64Ki/256Ki teacher method, ported to current v20/component metadata."""
import hashlib
import json
from pathlib import Path
import queue
import re
import subprocess
import threading
import time
import chess
from common import canonical, position_id, need, put, ref as reference


def identity(board):
    # Admission remains canonical-only; this is a separately named usage projection.
    aliases=sorted({hashlib.sha256(" ".join(board.fen(shredder=shredder,en_passant=ep).split()[:4]).encode()).hexdigest()
                    for shredder in (False,True) for ep in ("legal","fen")})
    return position_id(board),aliases


class UCI:
    def __init__(self, executable, network, out, name, *, multipv=2):
        self.exe, self.network = Path(executable), Path(network)
        self.out, self.name = Path(out), name
        self.paths = {kind: self.out / (name + "." + kind) for kind in ("stdin", "stdout", "stderr")}
        self.handles = {kind: path.open("xb") for kind, path in self.paths.items()}
        self.queue = queue.Queue(); self.consumed = 0; self.sent = 0
        self.record = {"schema":"sfnnv20-natural-native-process/1",
                       "engine":reference(self.exe),"network":reference(self.network),"started_utc_epoch":time.time(),
                       "argv":[str(self.exe)],"threads":1,"hash_mib":16,"pid":None,"returncode":None}
        self.process = subprocess.Popen([str(self.exe)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=self.handles["stderr"], cwd=self.exe.parent)
        self.record["pid"] = self.process.pid
        def read():
            try:
                for raw in iter(self.process.stdout.readline, b""):
                    self.handles["stdout"].write(raw); self.handles["stdout"].flush()
                    self.queue.put(raw)
            finally:
                self.queue.put(None)
        self.thread = threading.Thread(target=read, daemon=True); self.thread.start()
        self.send("uci"); self.until("uciok", 30)
        for command in ("setoption name NumaPolicy value none","setoption name Threads value 1",
                        "setoption name Hash value 16","setoption name UCI_Chess960 value false",
                        "setoption name SyzygyPath value <empty>",
                        "setoption name EvalFile value " + str(self.network),
                        "setoption name MultiPV value " + str(multipv)):
            self.send(command)
        self.send("isready"); self.until("readyok", 30)

    def send(self, line):
        need("\n" not in line and "\r" not in line, "Unsafe native command")
        raw = (line + "\n").encode()
        self.handles["stdin"].write(raw); self.handles["stdin"].flush()
        self.process.stdin.write(raw); self.process.stdin.flush(); self.sent += 1

    def until(self, marker, timeout):
        rows, deadline = [], time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            need(remaining > 0, "Bounded native response timeout")
            raw = self.queue.get(timeout=remaining)
            need(raw is not None, "Native process ended before " + marker)
            self.consumed += 1
            line = raw.decode("utf-8").rstrip("\r\n")
            rows.append(line)
            need(not re.search(r"\b(?:ERROR|CRITICAL)\b",line,re.I), "Native error: " + line)
            if line == marker or line.startswith(marker + " "):
                return rows

    def search(self, root, nodes, moves=None):
        self.send("ucinewgame"); self.send("isready"); self.until("readyok",30)
        self.send("position fen " + root["fen6"])
        command = "go nodes " + str(nodes)
        if moves:
            command += " searchmoves " + " ".join(moves)
        start_input, start_output = self.sent + 1, self.consumed + 1
        self.send(command); started = time.monotonic()
        lines = self.until("bestmove",30)
        elapsed = time.monotonic() - started
        return {"budget_nodes":nodes,"searchmoves":moves,"elapsed_seconds":elapsed,
                "eval_file":str(self.network),
                "input_line":start_input,"stdout_line_start":start_output,
                "stdout_line_stop":self.consumed,"lines":lines}

    def close(self):
        if self.process.poll() is None:
            self.send("quit")
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill(); self.process.wait()
                self.record["close_timeout"] = True
        self.thread.join(timeout=10)
        for handle in self.handles.values():
            handle.close()
        self.record.update({"finished_utc_epoch":time.time(),"returncode":self.process.returncode,
                            "streams":{kind:reference(path) for kind,path in self.paths.items()},
                            "engine_rehashed":reference(self.exe),"network_rehashed":reference(self.network)})
        put(self.out/(self.name+".process.json"),self.record)
        need(self.process.returncode == 0 and self.paths["stderr"].stat().st_size == 0, "Native process failed/stderr")
        need(self.record["engine"] == self.record["engine_rehashed"]
             and self.record["network"] == self.record["network_rehashed"], "Native inputs changed")
        return self.record


def parse_search(root, response):
    board = chess.Board(root["fen6"])
    use_lines = [line for line in response["lines"] if line.startswith("info string NNUE evaluation using ")]
    need(use_lines and all(line.startswith("info string NNUE evaluation using " + response["eval_file"] + " (")
                           for line in use_lines), "Actual teacher EvalFile loaded-path confirmation differs")
    rows, observed = {}, {}
    pv_lines, pv_plies = 0, 0
    def observe(position, witness):
        position_id, aliases = identity(position)
        if position_id not in observed:
            observed[position_id] = {"canonical_id":position_id,"conservative_alias_ids":aliases,"first_witness":witness}
    observe(board,{"kind":"root","ply":0})
    for index, line in enumerate(response["lines"]):
        if " pv " not in line:
            continue
        pv = line.split(" pv ",1)[1].split()
        replay = board.copy(stack=False)
        for ply, token in enumerate(pv,1):
            move = replay.parse_uci(token)
            need(move != chess.Move.null() and replay.is_legal(move), "Illegal actual teacher PV")
            replay.push(move); observe(replay,{"kind":"pv","line":index,"ply":ply})
            pv_plies += 1
        pv_lines += 1
        match = re.search(r"^info depth (\d+).*\bmultipv ([12])\b.*\bscore cp ([+-]?\d+)\b",line)
        if match and pv and "lowerbound" not in line and "upperbound" not in line:
            depth, rank, cp = map(int,match.groups())
            rows.setdefault(depth,{})[rank] = {"move":pv[0],"root_stm_cp":cp,"pv":pv,"depth":depth}
    best = re.fullmatch(r"bestmove (\S+)(?: ponder (\S+))?",response["lines"][-1])
    need(best is not None, "Malformed actual teacher bestmove")
    replay = board.copy(stack=False)
    for ply, token in enumerate((best[1],best[2]),1):
        if token:
            move = replay.parse_uci(token)
            need(move != chess.Move.null() and replay.is_legal(move), "Illegal actual teacher bestmove/ponder")
            replay.push(move); observe(replay,{"kind":"bestmove" if ply==1 else "ponder","ply":ply})
    candidates = None
    for depth in sorted(rows,reverse=True):
        if set(rows[depth]) == {1,2}:
            pair = [rows[depth][1],rows[depth][2]]
            if pair[0]["move"] == best[1] and pair[0]["move"] != pair[1]["move"]:
                candidates = pair
            break
    reported_nodes = [int(match[1]) for line in response["lines"] if (match:=re.search(r"\bnodes (\d+)\b",line))]
    need(reported_nodes and max(reported_nodes) <= response["budget_nodes"]+4096, "Native node cap/overshoot differs")
    exposure = {"root_id":root["root_id"],"component_id":root["component_id"],
                "canonical_id_domain":"ordinary Standard legal-EP FEN4 SHA256",
                "conservative_usage_alias_domain":"ordinary/Shredder x legal/FEN-EP; separate from admission domain",
                "budget_nodes":response["budget_nodes"],"reported_positions":sorted(observed.values(),key=lambda row:row["canonical_id"]),
                "legal_pv_lines":pv_lines,"legal_pv_plies":pv_plies,"bestmove_and_ponder_legal":True,
                "unreported_search_internals":"unknown"}
    return {"root_id":root["root_id"],"component_id":root["component_id"],
            "budget_nodes":response["budget_nodes"],"elapsed_seconds":response["elapsed_seconds"],
            "actual_reported_max_nodes":max(reported_nodes),"complete_exact_top_two":candidates,
            "bestmove":best[1],"process_stdout_lines":[response["stdout_line_start"],response["stdout_line_stop"]],
            "process_input_line":response["input_line"]}, exposure


def consensus(root, low, high):
    reasons = []
    a, b = low["complete_exact_top_two"], high["complete_exact_top_two"] if high else None
    if not a or not b:
        reasons.append("missing_fully_completed_exact_cp_pair")
    elif [r["move"] for r in a] != [r["move"] for r in b]:
        reasons.append("teacher_budget_order_disagreement")
    else:
        margins = [pair[0]["root_stm_cp"] - pair[1]["root_stm_cp"] for pair in (a,b)]
        if min(margins)<16:
            reasons.append("teacher_consensus_margin_below16")
        if any(abs(pair[0]["root_stm_cp"])>600 for pair in (a,b)):
            reasons.append("teacher_top_absolute_cp_above600")
    children = []
    if a:
        board = chess.Board(root["fen6"])
        for rank, candidate in enumerate(a,1):
            move = board.parse_uci(candidate["move"])
            capture = board.is_capture(move) or bool(move.promotion)
            child = board.copy(stack=False); child.push(move)
            position_id, aliases = identity(child)
            held = "in_check" if child.is_check() else "terminal" if child.is_game_over(claim_draw=False) else None
            if held:
                reasons.append("child"+str(rank)+"_"+held)
            children.append({"rank":rank,"move":candidate["move"],"fen6":child.fen(en_passant="fen"),
                             "position_id":position_id,"conservative_alias_ids":aliases,
                             "capture_or_promotion":capture,"ordinary_static_available":held is None,
                             "unavailable_reason":held})
    return {"root_id":root["root_id"],"component_id":root["component_id"],"partition":root["partition"],
            "eligible":not reasons,"reasons":reasons,"low":low,"high":high,
            "target_margin_cp":min(40,*margins) if not reasons else None,"children":children,
            "root_fen6":root["fen6"],"origins":root["origins"]}
