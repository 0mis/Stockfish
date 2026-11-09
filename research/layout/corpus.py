"""Build the fixed family split from two retired public development PGNs."""

import hashlib
import io
from collections import Counter
from pathlib import Path

import chess
import chess.pgn


COMMIT = "6831500a4c6b22d64109a38bb0dc5bbf5b6f8fe0"
SOURCES = {
    "edited": ("results/test1757-edited-screen/games-utc.pgn", 918097,
               "8bd56ec5f58ef65881ed5ae2d7ef6683683893c34c10b94b3bb0f2d5fc83dde9"),
    "parent": ("results/test1757-parent-screen/games-utc.pgn", 935475,
               "1acfc5b48a1c20aa43947fbde0ec0ea6c413f8bf76a7265dbe3d8e47c711de5d"),
}
FAMILY_DOMAIN = b"stockfish-layout-public-games-v1/family\0"
POSITION_DOMAIN = b"stockfish-layout-public-games-v1/position\0"
SAMPLE_PLIES = range(8, 97, 8)
CAP = 32


def _sha(text):
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def _rank(domain, identifier):
    return hashlib.sha256(domain + identifier.encode("ascii")).hexdigest()


def _fen4(fen):
    return " ".join(fen.split()[:4])


def _identity(board):
    fen = board.fen(en_passant="legal")
    standard = _sha(_fen4(fen))
    shredder = _sha(_fen4(board.shredder_fen(en_passant="legal")))
    return {"fen": fen, "position_id": standard, "alias_ids": sorted({standard, shredder})}


def _ineligible(board):
    if type(board) is not chess.Board or board.chess960 or not board.is_valid():
        return "invalid_or_nonstandard"
    if board.is_check():
        return "in_check"
    if board.is_game_over(claim_draw=False):
        return "terminal"
    return None


class _Mainline(chess.pgn.BaseVisitor):
    """Use White/Black only to verify reversed pairs. Ignore annotation contents."""

    def __init__(self):
        self.headers = chess.pgn.Headers({})
        self.root = None
        self.plies = 0
        self.eligible = []
        self.skipped = []

    def begin_headers(self):
        return self.headers

    def visit_header(self, tagname, tagvalue):
        if tagname in ("FEN", "SetUp", "Variant", "White", "Black"):
            if tagname in self.headers:
                raise ValueError(f"Duplicate structural PGN header {tagname}")
            self.headers[tagname] = tagvalue

    def begin_variation(self):
        return chess.pgn.SKIP

    def visit_move(self, board, move):
        if not board.is_legal(move):
            raise ValueError("Illegal move on the original PGN mainline")

    def visit_board(self, board):
        self.plies = len(board.move_stack)
        if self.plies == 0:
            if type(board) is not chess.Board or board.chess960 or not board.is_valid():
                raise ValueError("A screen game must start at a valid standard root")
            self.root = board.fen(en_passant="legal")
        elif self.plies in SAMPLE_PLIES:
            reason = _ineligible(board)
            if reason:
                self.skipped.append({"relative_ply": self.plies, "reason": reason})
            else:
                self.eligible.append({"relative_ply": self.plies, **_identity(board)})

    def result(self):
        players = (self.headers.get("White"), self.headers.get("Black"))
        if self.root is None or any(not name or name == "?" for name in players) or players[0] == players[1]:
            raise ValueError("Each paired game needs a root and two distinct color identities")
        return {"root": self.root, "players": players, "plies": self.plies, "eligible": self.eligible, "skipped": self.skipped}


def _games(text, arm):
    games = []
    handle = io.StringIO(text)
    while True:
        try:
            game = chess.pgn.read_game(handle, Visitor=_Mainline)
        except ValueError as exc:
            raise ValueError(f"{arm} PGN game {len(games)} failed legal replay") from exc
        if game is None:
            break
        games.append(game)
    if len(games) != 128:
        raise ValueError(f"{arm} has {len(games)} games; expected 128")
    for index in range(0, 128, 2):
        first, second = games[index:index + 2]
        if first["root"] != second["root"] or first["players"] != tuple(reversed(second["players"])):
            raise ValueError(f"{arm} pair {index // 2} must share the root and reverse colors")
    return games


def _read_pinned(path, arm):
    _, size, digest = SOURCES[arm]
    with Path(path).open("rb") as stream:
        raw = stream.read(size + 1)
    if len(raw) != size or hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError(f"{arm} PGN differs from the published byte count or SHA256")
    return raw.decode("utf-8-sig")


def _collect(arms):
    if [game["root"] for game in arms["edited"]] != [game["root"] for game in arms["parent"]]:
        raise ValueError("Edited and parent PGNs have different ordered starting roots")
    families = {}
    for arm in ("edited", "parent"):
        for index, game in enumerate(arms[arm]):
            root4 = _fen4(game["root"])
            family = _sha(root4)
            item = families.setdefault(family, {"family": family, "root_fen4": root4, "games": [], "positions": {}})
            if item["root_fen4"] != root4:
                raise ValueError("Root identity collision")
            source = {"source_arm": arm, "game_index": index}
            item["games"].append({**source, "root_fen": game["root"], "plies": game["plies"], "skipped": game["skipped"]})
            for row in game["eligible"]:
                key = row["position_id"]
                positions = item["positions"]
                if key not in positions:
                    positions[key] = {"position_id": key, "alias_ids": row["alias_ids"],
                                      "position_rank_sha256": _rank(POSITION_DOMAIN, key), "occurrences": []}
                position = positions[key]
                if position["alias_ids"] != row["alias_ids"]:
                    raise ValueError("Position identity has inconsistent castling aliases")
                position["occurrences"].append({**source, "relative_ply": row["relative_ply"], "fen": row["fen"]})
    if len(families) != 63:
        raise ValueError(f"Expected 63 unique starting families, found {len(families)}")
    return families


def _select(families):
    ordered = sorted(families, key=lambda key: (_rank(FAMILY_DOMAIN, key), key))
    optimization_families = set(ordered[:48])
    eligible_opt_aliases = {alias for key in optimization_families for pos in families[key]["positions"].values()
                            for alias in pos["alias_ids"]}
    selected = {"optimization": [], "holdout": []}
    seen = {partition: set() for partition in selected}
    eligible_holdout_aliases = set()
    reports, empty_holdout = [], []
    for key in ordered:
        family = families[key]
        partition = "optimization" if key in optimization_families else "holdout"
        ranked = sorted(family["positions"].values(), key=lambda pos: (pos["position_rank_sha256"], pos["position_id"]))
        accepted = 0
        records = []
        for position in ranked:
            aliases = set(position["alias_ids"])
            if partition == "holdout":
                eligible_holdout_aliases.update(aliases)
            if partition == "holdout" and aliases & eligible_opt_aliases:
                disposition = "eligible_optimization_alias"
            elif aliases & seen[partition]:
                disposition = "already_selected_alias"
            elif accepted >= CAP:
                disposition = "family_cap"
            else:
                disposition = "selected"
                first = position["occurrences"][0]
                selected[partition].append({**first, "family": key, "position_id": position["position_id"],
                                            "alias_ids": position["alias_ids"]})
                seen[partition].update(aliases)
                accepted += 1
            records.append({**position, "disposition": disposition})
        reports.append({"family": key, "root_fen4": family["root_fen4"], "family_rank_sha256": _rank(FAMILY_DOMAIN, key),
                        "partition": partition, "games": family["games"], "eligible_positions": records,
                        "distinct_eligible_positions": len(ranked), "selected_rows": accepted,
                        "disposition_counts": dict(sorted(Counter(record["disposition"] for record in records).items()))})
        if partition == "holdout" and not accepted:
            empty_holdout.append(key)
    issues = []
    for partition, minimum in (("optimization", 384), ("holdout", 96)):
        count = len(selected[partition])
        if count < minimum:
            issues.append({"partition": partition, "selected_rows": count, "minimum_rows": minimum})
    if empty_holdout:
        issues.append({"empty_holdout_families": empty_holdout})
    overlap = seen["holdout"] & eligible_opt_aliases
    if overlap:
        raise ValueError("Holdout selection intersects eligible optimization aliases")
    return {**selected, "status": "infeasible" if issues else "ready", "issues": issues, "families": reports,
            "audit": {"optimization_families": len(optimization_families), "holdout_families": len(ordered) - len(optimization_families),
                      "optimization_rows": len(selected["optimization"]), "holdout_rows": len(selected["holdout"]),
                      "eligible_optimization_aliases": len(eligible_opt_aliases),
                      "eligible_holdout_aliases_before_exclusion": len(eligible_holdout_aliases),
                      "eligible_candidate_alias_intersection_before_selection": sorted(eligible_opt_aliases & eligible_holdout_aliases),
                      "selected_aliases": {part: len(ids) for part, ids in seen.items()},
                      "heldout_intersection_with_all_eligible_optimization_aliases": sorted(overlap)}}


def build_corpus(edited_pgn: Path, parent_pgn: Path) -> dict:
    """Return selected rows, ordered family/occurrence provenance, counts and feasibility."""
    paths = {"edited": edited_pgn, "parent": parent_pgn}
    arms = {arm: _games(_read_pinned(path, arm), arm) for arm, path in paths.items()}
    result = _select(_collect(arms))
    result["audit"].update(ordered_roots_match_across_arms=True, reversed_pairs_per_arm=64, unique_root_families=63,
                           eligible_occurrences=sum(len(game["eligible"]) for games in arms.values() for game in games),
                           skipped_sampled_plies_by_reason=dict(sorted(Counter(
                               row["reason"] for games in arms.values() for game in games for row in game["skipped"]).items())))
    result.update(schema="stockfish-layout-public-games-corpus/1", public_commit=COMMIT, python_chess_version=chess.__version__,
                  sources={arm: {"path": path, "bytes": size, "sha256": sha, "games": len(arms[arm])}
                           for arm, (path, size, sha) in SOURCES.items()},
                  sampling={"relative_plies": list(SAMPLE_PLIES), "max_positions_per_family": CAP, "game_index_base": 0,
                            "encounter_order": ["edited", "parent"], "terminal_claim_draw": False,
                            "family_domain": FAMILY_DOMAIN.decode("ascii"), "position_domain": POSITION_DOMAIN.decode("ascii")})
    return result
