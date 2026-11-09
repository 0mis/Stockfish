"""Legal benchmark replay reused from the reviewed 388011 official-D1 CI driver.

Source: https://github.com/0mis/Stockfish/blob/388011558e440fc93f4aacbbaf4aef41d387578c/research/official_d1_validation/verify.py
Only its standalone benchmark helpers are retained; no model or heldout code.
"""
import hashlib
import json
import re
import chess


def need(ok, reason):
    if not ok:
        raise ValueError(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(doc):
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


BENCHMARK_SOURCE_SHA256 = "0a8d3f29278ad53abbceee284721a3ac4ddd7c768052de928672a2869bc2da80"


def benchmark_specs(raw, expected=51):
    """Review-equivalent of retained_bench_usage.parser.default_specs.

    The exact Defaults source controls mode and pre-root history. A FEN alone
    cannot establish the UCI castling dialect. Do not inspect BenchmarkPositions.
    """
    text = raw.decode("utf-8")
    begin = "const std::vector<std::string> Defaults = {"
    need(text.count(begin) == 1, "Unique Defaults source block required")
    block = text.split(begin, 1)[1].split("\n};", 1)[0]
    specs, mode = [], False
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        match = re.fullmatch(r'("(?:[^"\\]|\\.)*")\s*,?\s*(?://[^\r\n]*)?', line)
        need(match is not None, "Unknown Defaults source grammar")
        value = json.loads(match[1])
        if value.startswith("setoption"):
            need(value in ("setoption name UCI_Chess960 value false", "setoption name UCI_Chess960 value true"), "Unknown benchmark mode")
            mode = value.endswith("true")
        else:
            specs.append({"source": value, "chess960": mode})
    need(len(specs) == expected, "Exact benchmark root count differs")
    return specs


def official_benchmark_specs(raw):
    need(sha(raw) == BENCHMARK_SOURCE_SHA256, "Exact verified7cdd benchmark.cpp required")
    return benchmark_specs(raw)


def benchmark_step(board, token):
    # parse_uci performs the per-board standard/Chess960 castling normalization
    # used by the previously verified retained bench legal-prefix adapter.
    move = board.parse_uci(token)
    need(move != chess.Move.null() and board.is_legal(move), "Illegal benchmark move in declared source mode")
    board.push(move)


def benchmark_root(spec):
    fen, separator, history = spec["source"].partition(" moves ")
    fields = len(fen.split())
    need(fields in (4, 6) and (not separator or history.strip()), "Malformed source benchmark root/history")
    if fields == 4:
        fen += " 0 1"
    board = chess.Board(fen, chess960=spec["chess960"])
    need(board.is_valid(), "Invalid declared source benchmark board")
    for token in history.split():
        benchmark_step(board, token)
    return board


def parse_bench(stdout, stderr, returncode, *, specs):
    need(returncode == 0 and not re.search(rb"\b(?:ERROR|CRITICAL)\b", stdout + stderr, re.I), "Bench process failed")
    markers = re.findall(r"^Position: (\d+)/51 \(([^\r\n]+)\)\s*$", stderr.decode().replace("\r\n", "\n"), re.M)
    need(stdout.decode().splitlines().count("uciok") == 1 and stdout.decode().splitlines().count("readyok") == 2, "Bench UCI/ready handshake differs")
    need([int(i) for i, _ in markers] == list(range(1, 52)), "Complete51-position bench roster required")
    need(len(specs) == 51, "Per-position source mode/history context is mandatory")
    chunks, current = [], []
    for line in stdout.decode().replace("\r\n", "\n").splitlines():
        current.append(line)
        if line.startswith("bestmove "):
            chunks.append(current); current = []
    need(len(chunks) == 51 and not any(line.startswith(("info depth", "bestmove")) for line in current), "Complete51 bench searches required")
    vector = []
    for (index, fen), lines, spec in zip(markers, chunks, specs):
        board = benchmark_root(spec)
        need(fen == board.fen(shredder=spec["chess960"], en_passant="legal"), "Bench marker differs from exact supplied source/mode/history")
        best = re.fullmatch(r"bestmove ([a-h][1-8][a-h][1-8][qrbn]?|\(none\))(?: ponder ([a-h][1-8][a-h][1-8][qrbn]?))?", lines[-1])
        need(best, "Malformed bench bestmove")
        scored = [line for line in lines if line.startswith("info depth ") and " score " in line]
        need(scored, "Missing bench final score")
        pv_lines, pv_plies = 0, 0
        for line in lines:
            if " pv " in line:
                replay = board.copy(stack=False)
                for token in line.split(" pv ", 1)[1].split():
                    benchmark_step(replay, token)
                    pv_plies += 1
                pv_lines += 1
        if best[1] == "(none)":
            need(board.is_game_over(claim_draw=False) and best[2] is None, "Nonterminal bench none move")
        else:
            replay = board.copy(stack=False)
            for token in (best[1], best[2]):
                if token:
                    benchmark_step(replay, token)
        final = scored[-1]
        if best[1] != "(none)":
            need(" pv " in final and final.split(" pv ", 1)[1].split()[0] == best[1], "Bench final PV/bestmove differs")
        nodes = re.search(r"\bnodes (\d+)\b", final)
        count = int(nodes[1]) if nodes else 0
        need(best[1] == "(none)" or count > 0, "Missing bench node count")
        vector.append({"index": int(index), "fen": fen, "chess960": spec["chess960"], "supplied_source": spec["source"],
                       "final_info": re.sub(r"\s+(?:nps|time)\s+\d+", "", final), "bestmove": lines[-1], "nodes": count,
                       "legal_pv_lines": pv_lines, "legal_pv_plies": pv_plies, "bestmove_path_checked": True, "ponder_path_checked": bool(best[2])})
    total = sum(r["nodes"] for r in vector)
    need(total > 0 and re.findall(r"^Nodes searched\s*:\s*(\d+)\s*$", stderr.decode(), re.M) == [str(total)], "Bench aggregate nodes differ")
    return {"nodes": total, "searches": 51, "vector": vector, "search_signature_sha256": sha(canonical(vector)),
            "source_chess960_positions": [i + 1 for i, spec in enumerate(specs) if spec["chess960"]],
            "all_reported_pv_bestmove_and_ponder_paths_checked": True}
