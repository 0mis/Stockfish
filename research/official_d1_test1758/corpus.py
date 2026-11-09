"""Frozen public-development corpus admission. No neural/native calls."""
from collections import Counter, defaultdict
import gzip
import hashlib
import io
import json
from pathlib import Path
import urllib.request
import chess
import chess.pgn

HERE = Path(__file__).resolve().parent
PLAN_SHA = "4c12de51e9acc6bbb63a831d7bd2c6511cf155b2b9d6dca83e2644e5cceb1ca9"


def need(ok, message):
    if not ok:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def put(path, value):
    with Path(path).open("xb") as target:
        target.write(canonical(value) + b"\n")


def reference(path):
    path = Path(path)
    return {"path": path.name, "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def policy():
    raw = (HERE / "plan.json").read_bytes()
    need(sha(raw) == PLAN_SHA, "Frozen diagnostic plan differs")
    plan = json.loads(raw)
    for relative, digest in plan["native_cache"]["unchanged_modules"].items():
        need(sha((HERE / "d1lib" / relative).read_bytes()) == digest, "Unchanged public D1 helper differs: " + relative)
    return plan


def identity(board):
    side = "w" if board.turn else "b"
    legal_ep = chess.square_name(board.ep_square) if board.ep_square is not None and board.has_legal_en_passant() else "-"
    fen_ep = chess.square_name(board.ep_square) if board.ep_square is not None else "-"
    prefix = board.board_fen() + " " + side + " "
    standard, shredder = board.castling_xfen(), board.castling_shredder_fen()
    ordinary = prefix + standard + " " + legal_ep
    aliases = sorted({sha((prefix + castle + " " + ep).encode())
                      for castle in {standard, shredder} for ep in {legal_ep, fen_ep}})
    return sha(ordinary.encode()), aliases


def partition(family):
    remainder = int(sha(("official-d1-test1758-v1/family|" + family).encode()), 16) % 20
    return "train" if remainder <= 10 else "development" if remainder <= 13 else "final" if remainder <= 16 else "screen"


def gzip_rows(path, rows):
    count, digest = 0, hashlib.sha256()
    with Path(path).open("xb") as target, gzip.GzipFile(fileobj=target, mode="wb", mtime=0) as output:
        for row in rows:
            raw = canonical(row) + b"\n"
            output.write(raw); count += 1; digest.update(raw)
    return {**reference(path), "rows": count, "canonical_uncompressed_sha256": digest.hexdigest()}


def build(out):
    plan = policy()
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    source_refs, cases, observed = [], {}, []
    game_total = 0
    for source_index, pin in enumerate(plan["source_catalog"]["allowed_pgns"]):
        url = ("https://raw.githubusercontent.com/0mis/stockfish-weight-space-lab/"
               + plan["source_catalog"]["commit"] + "/results/" + pin["name"] + "/games-utc.pgn")
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "0mis-d1-test1758-corpus"}), timeout=90) as response:
            raw = response.read(pin["bytes"] + 1)
        need(len(raw) == pin["bytes"] and sha(raw) == pin["sha256"], "Approved retired PGN hash differs")
        pgn = out / (pin["name"] + ".pgn")
        pgn.write_bytes(raw); source_refs.append({**reference(pgn), "url": url})
        stream, game_index = io.StringIO(raw.decode("utf-8")), 0
        while (game := chess.pgn.read_game(stream)) is not None:
            need(not game.errors, "Authentic PGN has a legal reconstruction error")
            board = game.board()
            need(board.is_valid() and not board.chess960 and "FEN" in game.headers, "Legal standard original opening FEN required")
            family, _ = identity(board)
            fold = partition(family)
            for ply, move in enumerate([None, *game.mainline_moves()]):
                if move is not None:
                    need(board.is_legal(move), "Illegal authentic PGN move")
                    board.push(move)
                root, aliases = identity(board)
                origin = {"source_index": source_index, "archive": pin["name"], "game_index": game_index, "source_ply": ply}
                observed.append({"canonical_id": root, "alias_ids": aliases, "origin": origin, "role": "observed_retired_pgn_mainline"})
                if 4 <= ply <= 128 and ply % 4 == 0:
                    key = (family, root)
                    if key not in cases:
                        legal = list(board.legal_moves)
                        reasons = [] if len(legal) >= 2 and not board.is_game_over(claim_draw=False) else ["parent_terminal_or_fewer_than_two_legal_moves"]
                        cases[key] = {"root_id": root, "family_id": family, "partition": fold,
                                      "fen6": board.fen(en_passant="fen"), "root_alias_ids": aliases,
                                      "origins": [], "source_reasons": reasons}
                    cases[key]["origins"].append(origin)
            game_index += 1
        game_total += game_index
    need(game_total == 768, "Exact approved retired game count differs")
    families = sorted({family for family, _ in cases})
    need(len(families) <= 286, "Unexpected opening-family increase")
    family_sets = {}
    for fold, cap in plan["corpus"]["family_caps"].items():
        family_sets[fold] = set(sorted(family for family in families if partition(family) == fold)[:cap])
    selected = []
    for family in families:
        fold = partition(family)
        if family in family_sets[fold]:
            selected.extend(sorted((row for (f, _), row in cases.items() if f == family), key=lambda row: row["root_id"])[:40])
    need(len(selected) <= 11440, "Frozen total root cap exceeded")
    owners = defaultdict(set)
    by_case = {}
    bits = {"train": 1, "development": 2, "final": 4, "screen": 8}
    for index, row in enumerate(selected):
        case_id = sha((row["family_id"] + "|" + row["root_id"]).encode())
        row["case_id"] = case_id; by_case[case_id] = row
        board = chess.Board(row["fen6"])
        guards = set(row["root_alias_ids"])
        for move in list(board.legal_moves):
            board.push(move); guards.update(identity(board)[1]); board.pop()
        row["all_legal_one_ply_guard_count"] = len(guards)
        row["all_legal_one_ply_guard_sha256"] = sha("".join(value + "\n" for value in sorted(guards)).encode())
        for alias in guards:
            owners[alias].add(case_id)
        if (index + 1) % 1024 == 0:
            print("CORPUS GUARDS", index + 1, len(selected), flush=True)
    collisions, rejected = set(), set()
    guard_rows = []
    for alias, case_ids in sorted(owners.items()):
        mask = 0
        for case_id in case_ids:
            mask |= bits[by_case[case_id]["partition"]]
        if mask.bit_count() > 1:
            collisions.add(alias); rejected.update(case_ids)
        guard_rows.append({"alias_id": alias, "partition_mask": mask, "case_ids": sorted(case_ids), "role": "admission_all_legal_one_ply_guard"})
    # Cross-family duplicate roots within one surviving partition have one representative.
    surviving = defaultdict(list)
    for row in selected:
        if row["case_id"] not in rejected:
            surviving[(row["partition"], row["root_id"])].append(row)
    admitted = []
    for rows in surviving.values():
        rows.sort(key=lambda row: (min((origin["source_index"], origin["game_index"], origin["source_ply"])
                                      for origin in row["origins"]), row["family_id"]))
        representative = dict(rows[0])
        representative["source_family_ids"] = sorted({row["family_id"] for row in rows})
        representative["origins"] = [origin for row in rows for origin in row["origins"]]
        admitted.append(representative)
    partitions = {}
    for fold in bits:
        rows = sorted((row for row in admitted if row["partition"] == fold), key=lambda row: row["root_id"])
        path = out / (fold + ("-sealed" if fold in ("final", "screen") else "") + ".jsonl")
        with path.open("xb") as target:
            for row in rows:
                target.write(canonical(row) + b"\n")
        partitions[fold] = {**reference(path), "rows": len(rows), "families": len({row["family_id"] for row in rows}),
                            "source_unavailable": sum(bool(row["source_reasons"]) for row in rows)}
    report = {"schema": "official-d1-test1758-admitted-corpus/1", "plan_sha256": PLAN_SHA,
              "source_refs": source_refs, "games": game_total, "families_before_caps": len(families),
              "sampled_cases_before_alias_removal": len(selected), "partitions": partitions,
              "cross_partition_alias_ids": len(collisions), "dropped_cases": len(rejected),
              "same_partition_duplicate_roots_coalesced": len(selected) - len(rejected) - len(admitted),
              "all_root_and_legal_one_ply_cross_partition_aliases_removed": True,
              "no_outcomes_or_predictions_used": True, "final_search_or_evaluation_opened": False,
              "source_observations": gzip_rows(out / "SOURCE-OBSERVATIONS.jsonl.gz", observed),
              "admission_guards": gzip_rows(out / "ADMISSION-GUARDS.jsonl.gz", guard_rows)}
    put(out / "CORPUS.json", report)
    print(json.dumps({"corpus_partitions": partitions, "cross_alias_cases_removed": len(rejected)}), flush=True)
    return report
