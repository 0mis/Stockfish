"""Synthetic tests for the pinned public PGN corpus builder."""

import hashlib
import io
import unittest
from pathlib import Path
from unittest.mock import patch

import chess
import chess.pgn

import corpus


def _pgn(root, white, black, moves="*"):
    return f'[White "{white}"]\n[Black "{black}"]\n[SetUp "1"]\n[FEN "{root}"]\n\n{moves}\n\n'


def _roots():
    roots = []
    board = chess.Board()
    for white in sorted(board.legal_moves, key=str):
        board.push(white)
        for black in sorted(board.legal_moves, key=str):
            board.push(black)
            roots.append(board.fen())
            board.pop()
            if len(roots) == 63:
                return roots
        board.pop()


def _paired_games(roots):
    return "".join(_pgn(root, "Side A", "Side B") + _pgn(root, "Side B", "Side A") for root in roots)


def _position(label):
    key = corpus._sha(label)
    return {"position_id": key, "alias_ids": [key], "position_rank_sha256": corpus._rank(corpus.POSITION_DOMAIN, key),
            "occurrences": [{"source_arm": "edited", "game_index": 0, "relative_ply": 8, "fen": chess.STARTING_FEN}]}


class CorpusTests(unittest.TestCase):
    def test_legal_ep_castling_spellings_and_original_counters(self):
        start = chess.Board()
        start.fullmove_number = 73
        row = corpus._identity(start)
        self.assertEqual(row["fen"], start.fen())
        self.assertEqual(row["position_id"], hashlib.sha256(" ".join(start.fen().split()[:4]).encode("ascii")).hexdigest())
        self.assertEqual(row["alias_ids"], sorted([row["position_id"], corpus._sha(corpus._fen4(start.shredder_fen()))]))
        pinned = chess.Board("k3r3/8/8/3pP3/8/8/8/4K3 w - d6 17 60")
        self.assertEqual(corpus._identity(pinned)["fen"].split()[3:], ["-", "17", "60"])
        capture = chess.Board("4k3/8/8/3pP3/8/8/8/4K3 w - d6 17 60")
        self.assertEqual(corpus._identity(capture)["fen"].split()[3], "d6")

    def test_relative_mainline_plies_ignore_annotations_variations_and_result_tags(self):
        board = chess.Board()
        board.fullmove_number = 37
        root = board.fen()
        sans = "e4 e5 Nf3 Nc6 Bb5 a6 Ba4 Nf6 O-O Be7 Re1 b5 Bb3 d6 c3 O-O".split()
        expected = []
        for ply, san in enumerate(sans, 1):
            board.push_san(san)
            if ply % 8 == 0:
                expected.append(corpus._identity(board))
        plain = _pgn(root, "A", "B", " ".join(sans) + " *")
        marked = plain.replace("e4 e5", "e4 {[%clk 0:00:01] [%eval +99]} (d4 d5) e5").replace(" *", " 1-0")
        for text in (plain, marked):
            game = chess.pgn.read_game(io.StringIO(text), Visitor=corpus._Mainline)
            self.assertEqual(game["plies"], 16)
            self.assertEqual(game["eligible"], [{"relative_ply": ply, **row} for ply, row in zip((8, 16), expected)])
            self.assertEqual(game["skipped"], [])

    def test_illegal_after_sampling_window_and_eligibility(self):
        late_null = " ".join(["Nf3 Nf6 Ng1 Ng8"] * 24) + " -- *"
        with self.assertRaises(ValueError):
            chess.pgn.read_game(io.StringIO(_pgn(chess.STARTING_FEN, "A", "B", late_null)), Visitor=corpus._Mainline)
        check = chess.Board("k3r3/8/8/8/8/8/8/4K3 w - - 0 1")
        self.assertEqual(corpus._ineligible(check), "in_check")
        self.assertEqual(corpus._ineligible(chess.Board("k7/8/8/8/8/8/8/7K w - - 0 1")), "terminal")
        self.assertEqual(corpus._ineligible(chess.Board.empty()), "invalid_or_nonstandard")
        self.assertEqual(corpus._ineligible(chess.Board(chess960=True)), "invalid_or_nonstandard")

    def test_in_memory_normal_entry_pins_arbitrary_paths_and_reports_infeasible(self):
        roots = _roots()
        raw = _paired_games([*roots, roots[0]]).encode()
        pins = {arm: (f"toy/{arm}.pgn", len(raw), hashlib.sha256(raw).hexdigest()) for arm in ("edited", "parent")}
        with patch.object(corpus, "SOURCES", pins), patch.object(Path, "open", side_effect=lambda *a, **k: io.BytesIO(raw)):
            report = corpus.build_corpus(Path("arbitrary-A"), Path("arbitrary-B"))
        self.assertEqual(report["status"], "infeasible")
        self.assertEqual([len(report[part]) for part in ("optimization", "holdout")], [0, 0])
        self.assertEqual([report["audit"][key] for key in ("optimization_families", "holdout_families")], [48, 15])
        self.assertEqual(sum(len(family["games"]) for family in report["families"]), 256)
        self.assertEqual(len(report["issues"][-1]["empty_holdout_families"]), 15)
        self.assertNotIn("Side A", repr(report))
        for damaged in (raw[:-1], b"!" + raw[1:]):
            with patch.object(corpus, "SOURCES", pins), patch.object(Path, "open", return_value=io.BytesIO(damaged)):
                with self.assertRaisesRegex(ValueError, "SHA256"):
                    corpus.build_corpus(Path("arbitrary-A"), Path("arbitrary-B"))

    def test_bad_reversals_order_counts_and_63_family_requirement(self):
        roots = _roots()
        text = _paired_games([*roots, roots[0]])
        with self.assertRaisesRegex(ValueError, "128"):
            corpus._games(_paired_games([roots[0]]), "edited")
        with self.assertRaisesRegex(ValueError, "reverse colors"):
            corpus._games(text.replace('[White "Side B"]', '[White "Side C"]', 1), "edited")
        games = corpus._games(text, "edited")
        with self.assertRaisesRegex(ValueError, "ordered starting roots"):
            corpus._collect({"edited": games, "parent": games[2:] + games[:2]})
        with self.assertRaisesRegex(ValueError, "63 unique"):
            corpus._collect({"edited": games[:2], "parent": games[:2]})

    def test_first_occurrence_is_edited_game_then_ply_in_original_order(self):
        roots = _roots()
        games = corpus._games(_paired_games([*roots, roots[0]]), "toy")
        board = chess.Board()
        first = {"relative_ply": 8, **corpus._identity(board)}
        board.fullmove_number = 2
        later = {"relative_ply": 16, **corpus._identity(board)}
        games[0]["eligible"] = [first, later]
        game_family = corpus._sha(corpus._fen4(games[0]["root"]))
        families = corpus._collect({"edited": games, "parent": games})
        occurrences = families[game_family]["positions"][first["position_id"]]["occurrences"]
        self.assertEqual([(item["source_arm"], item["game_index"], item["relative_ply"]) for item in occurrences],
                         [("edited", 0, 8), ("edited", 0, 16), ("parent", 0, 8), ("parent", 0, 16)])
        rows = corpus._select(families)
        selected = rows["optimization"] + rows["holdout"]
        self.assertEqual((selected[0]["fen"], selected[0]["source_arm"], selected[0]["relative_ply"]), (first["fen"], "edited", 8))

    def test_family_ranking_caps_dedup_and_all_optimization_alias_exclusion(self):
        ids = [corpus._sha(f"root-{i}") for i in range(63)]
        order = sorted(ids, key=lambda key: (hashlib.sha256(b"stockfish-layout-public-games-v1/family\0" + key.encode()).hexdigest(), key))
        families = {}
        for index, key in enumerate(reversed(order)):
            count = 40 if key == order[0] else 10
            positions = [_position(f"p-{index}-{n}") for n in range(count)]
            families[key] = {"family": key, "root_fen4": "toy", "games": [], "positions": {p["position_id"]: p for p in positions}}
        rank = lambda p: (hashlib.sha256(b"stockfish-layout-public-games-v1/position\0" + p["position_id"].encode()).hexdigest(), p["position_id"])
        first_family = sorted(families[order[0]]["positions"].values(), key=rank)
        capped_alias = first_family[-1]["position_id"]
        shared = corpus._sha("distinct-shared-castling-spelling")
        first_family[0]["alias_ids"].append(shared)
        later_opt = next(iter(families[order[1]]["positions"].values()))
        later_opt["alias_ids"].append(shared)
        contender = next(iter(families[order[48]]["positions"].values()))
        contender["alias_ids"].append(capped_alias)
        report = corpus._select(families)
        self.assertEqual(report["status"], "ready")
        self.assertEqual([item["family"] for item in report["families"]], order)
        self.assertEqual([item["position_id"] for item in report["optimization"][:32]], [p["position_id"] for p in first_family[:32]])
        all_positions = {p["position_id"]: p["disposition"] for family in report["families"] for p in family["eligible_positions"]}
        self.assertEqual(all_positions[capped_alias], "family_cap")
        self.assertEqual(all_positions[later_opt["position_id"]], "already_selected_alias")
        self.assertEqual(all_positions[contender["position_id"]], "eligible_optimization_alias")
        self.assertFalse(report["audit"]["heldout_intersection_with_all_eligible_optimization_aliases"])
        self.assertGreaterEqual(report["audit"]["optimization_rows"], 384)
        self.assertGreaterEqual(report["audit"]["holdout_rows"], 96)
        self.assertTrue(all(f["selected_rows"] for f in report["families"][48:]))


if __name__ == "__main__":
    unittest.main()
