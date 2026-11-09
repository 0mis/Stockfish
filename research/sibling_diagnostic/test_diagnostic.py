"""Synthetic no-engine tests of labels, parsing, actual children and signs."""

import unittest
from unittest import mock

import chess

import diagnostic as diag
import experiment


NET = r"..\inputs\parent.nnue"
ACK = diag.native.ACK_PREFIX + NET + diag.native.ACK_SUFFIX


def info(depth, rank, cp=40, pv=None, *, kind="cp", bound=None, hits=0):
    pv = pv or ("e2e4 e7e5" if rank == 1 else "d2d4 d7d5")
    score = f"{kind} {cp}" + (" " + bound if bound else "")
    return f"info depth {depth} seldepth 18 multipv {rank} score {score} nodes 1234 nps 1000 hashfull 1 tbhits {hits} time 3 pv {pv}"


def search(depth=13, scores=(40, 10), *, lines=None, best="e2e4"):
    lines = lines if lines is not None else [info(depth, 1, scores[0]), info(depth, 2, scores[1])]
    return diag.parse_search([ACK, *lines, "bestmove " + best], chess.Board(), NET, depth)


def root(board=None, family="f"):
    board = board or chess.Board()
    return {"fen": board.fen(en_passant="legal"), "position_id": diag.position_id(board), "family": family}


def eval_block(row, score="+0.30", used=10):
    side = "White" if row["fen"].split()[1] == "w" else "Black"
    block = [ACK, f"NNUE network contributions (Normalized, {side} to move)", diag.native.BORDER,
             "|   Bucket   | Evaluation |", diag.native.BORDER]
    block.extend(f"| {i} | + 0.00 |" + (" <-- this bucket is used" if i == used else "") for i in range(92))
    block.extend([diag.native.BORDER, "NNUE evaluation 100 (side to move, internal units)",
                  "NNUE evaluation +0.28 (white side)",
                  f"Final evaluation {score} (white side) [with scaled NNUE, ...]", "readyok"])
    return block


class NoEngineTests(unittest.TestCase):
    def setUp(self):
        self.launcher = mock.patch("subprocess.Popen", side_effect=AssertionError("Native process forbidden in tests"))
        self.launcher.start()
        self.addCleanup(self.launcher.stop)

    def test_final_exact_rank_pair_and_signed_root_margin(self):
        observed = search(scores=(-40, -75))
        self.assertEqual(observed["reason"], None)
        self.assertEqual(observed["margin_cp"], 35)
        self.assertEqual([r["rank"] for r in observed["ranks"]], [1, 2])
        self.assertEqual([r["cp"] for r in observed["ranks"]], [-40, -75])

    def test_latest_bound_mate_or_wrong_depth_cannot_reuse_earlier_scores(self):
        good = [info(13, 1), info(13, 2, 10)]
        cases = [
            (good + [info(13, 1), info(13, 2, 10, bound="upperbound")], "bounded_final_score"),
            (good + [info(13, 1, 2, kind="mate"), info(13, 2, 10)], "mate_final_score"),
            ([info(12, 1), info(12, 2, 10)], "requested_depth_not_completed_for_both_ranks"),
            (good + [info(13, 1)], "final_rank_order_or_completeness"),
            ([info(13, 1), "info depth 13 multipv 2 score cp 10 pv d2d4"], "missing_or_malformed_final_rank_pair"),
        ]
        for lines, reason in cases:
            with self.subTest(reason=reason):
                observed = search(lines=lines)
                self.assertEqual(observed["reason"], reason)
                self.assertIsNone(observed["margin_cp"])

    def test_missing_duplicate_and_bestmove_mismatch_are_exclusions(self):
        self.assertEqual(search(lines=[])['reason'], "missing_or_malformed_final_rank_pair")
        self.assertEqual(search(lines=[info(13, 1), info(13, 2, pv="e2e4")])["reason"], "duplicate_candidate_first_move")
        self.assertEqual(search(best="b1c3")["reason"], "bestmove_disagrees_with_final_rank1")

    def test_illegal_pv_rank_or_table_hit_is_protocol_failure(self):
        for lines in ([info(13, 1, pv="e2e5"), info(13, 2)],
                      [info(13, 1, pv="e2e4 e7e4"), info(13, 2)],
                      [info(13, 1), info(13, 3)], [info(13, 1, hits=1), info(13, 2)]):
            with self.subTest(lines=lines), self.assertRaises(ValueError):
                search(lines=lines)
        with self.assertRaises(ValueError):
            search(best="e2e5")
        with self.assertRaises(ValueError):
            diag.parse_search([ACK, info(13, 1), info(13, 2), "bestmove e2e4"], chess.Board(), "wrong.nnue", 13)

    def test_clear_pair_threshold_edges_and_order(self):
        shallow, deep = search(9, (40, 20)), search(13, (50, 30))
        self.assertTrue(diag.teacher_pair(shallow, deep)["teacher_clear"])
        self.assertTrue(diag.teacher_pair(search(9, (40, 20)), search(13, (60, 20)))["teacher_clear"])
        self.assertTrue(diag.teacher_pair(search(9, (250, 230)), search(13, (250, 230)))["teacher_clear"])
        self.assertFalse(diag.teacher_pair(search(9, (40, 21)), deep)["teacher_clear"])
        self.assertFalse(diag.teacher_pair(shallow, search(13, (61, 20)))["teacher_clear"])
        self.assertFalse(diag.teacher_pair(shallow, search(13, (251, 231)))["teacher_clear"])
        changed = search(13, lines=[info(13, 1, 50, pv="d2d4"), info(13, 2, 20, pv="e2e4")], best="d2d4")
        pair = diag.teacher_pair(shallow, changed)
        self.assertFalse(pair["ordered_top_two_stable"])
        self.assertIn("ordered_top_two_disagreement", pair["teacher_exclusions"])

    def test_static_white_display_to_child_stm_to_parent_for_both_colors(self):
        for parent_turn, expected_parent in ((chess.WHITE, 30), (chess.BLACK, -30)):
            board = chess.Board()
            board.turn = parent_turn
            move = "e2e4" if parent_turn else "e7e5"
            board.push_uci(move)
            row = {**root(board), "role": "optimization_child", "physical_head": 10, "rank": 1}
            observed = diag.parse_static(["uciok", "readyok"], eval_block(row), NET, row)
            self.assertEqual(observed["static_cp_parent"], expected_parent)
            self.assertEqual(observed["static_cp_stm"], -expected_parent)
            self.assertEqual(observed["used_bucket"], 10)
        with self.assertRaises(ValueError):
            diag.parse_static(["uciok", "readyok"], eval_block(row, used=11), NET, row)

    def test_absent_static_is_explicit_and_never_zero(self):
        board = chess.Board()
        board.push_uci("e2e4")
        row = {**root(board), "role": "optimization_child", "physical_head": 10, "rank": 1}
        block = [line for line in eval_block(row) if not line.startswith("Final evaluation")]
        observed = diag.parse_static(["uciok", "readyok"], block, NET, row)
        self.assertEqual(observed["status"], "static_unavailable")
        self.assertEqual(observed["missing_fields"], ["final_score"])
        self.assertIsNone(observed["static_cp_parent"])
        self.assertEqual(observed["used_bucket"], 10)

    def test_gross_inversion_sign_boundary_and_no_teacher_or_static_substitute(self):
        children = [{"rank": 1, "status": "measured", "static_cp_parent": 20},
                    {"rank": 2, "status": "measured", "static_cp_parent": 32}]
        self.assertTrue(diag.inversion({"teacher_clear": True}, children)["gross_inversion"])
        children[1]["static_cp_parent"] = 31
        self.assertFalse(diag.inversion({"teacher_clear": True}, children)["gross_inversion"])
        children[1]["static_cp_parent"] = 40
        self.assertFalse(diag.inversion({"teacher_clear": False}, children)["gross_inversion"])
        children[1].update(status="static_unavailable", static_cp_parent=None)
        result = diag.inversion({"teacher_clear": True}, children)
        self.assertFalse(result["inversion_assessable"])
        self.assertIsNone(result["static_parent_margin_cp"])

    def test_actual_capture_children_reset_clock_and_change_material(self):
        board = chess.Board("4k3/8/8/3p4/4P3/8/8/4K3 w - - 9 1")
        self.assertTrue(board.is_valid())
        deep = {"ranks": [{"pv": ["e4d5"]}, {"pv": ["e4e5"]}]}
        children = diag.candidate_children(root(board), deep)
        self.assertTrue(children[0]["capture"])
        self.assertFalse(children[1]["capture"])
        self.assertEqual(children[0]["halfmove_clock"], 0)
        self.assertEqual(chess.Board(children[0]["fen"]).occupied.bit_count(), 3)
        self.assertEqual(chess.Board(children[1]["fen"]).occupied.bit_count(), 4)
        self.assertEqual(children[0]["position_id"], diag.position_id(chess.Board(children[0]["fen"])))

    def test_check_and_mate_child_static_exclusions(self):
        board = chess.Board("4k3/8/8/8/8/8/R7/4K3 w - - 0 1")
        children = diag.candidate_children(root(board), {"ranks": [{"pv": ["a2e2"]}, {"pv": ["a2a3"]}]})
        self.assertEqual(children[0]["static_exclusion"], "in_check_static_unavailable")
        self.assertIsNone(children[1]["static_exclusion"])
        board = chess.Board("7k/5Q2/6K1/8/8/8/8/8 w - - 0 1")
        children = diag.candidate_children(root(board), {"ranks": [{"pv": ["f7g7"]}, {"pv": ["f7f6"]}]})
        self.assertEqual(children[0]["static_exclusion"], "terminal_checkmate")

    def test_family_selection_uses_hash_cap_and_keeps_eligibility(self):
        rows = [root()]
        for move in ("e2e4", "d2d4", "c2c4", "b1c3", "g1f3", "g2g3"):
            board = chess.Board()
            board.push_uci(move)
            rows.append(root(board))
        mate = chess.Board("7k/6Q1/6K1/8/8/8/8/8 b - - 0 1")
        rows.append(root(mate))
        selected = diag.select_roots(rows)
        expected = sorted(rows[:-1], key=lambda row: (diag.selection_rank(row["position_id"]), row["position_id"]))[:4]
        self.assertEqual([row["position_id"] for row in selected["selected"]], [row["position_id"] for row in expected])
        self.assertEqual(selected["counts"]["ineligible"], {"terminal": 1})
        self.assertEqual(selected["families"][0]["selected_positions"], 4)

    def test_all_iterations_and_both_ranks_enter_public_pv_exposure(self):
        observed = search(lines=[info(12, 1, pv="g1f3 g8f6"), info(12, 2, pv="c2c4 e7e5"),
                                info(13, 1), info(13, 2, 10)])
        exposure = {}
        diag.add_exposures(exposure, root(), observed)
        for moves in (("g1f3", "g8f6"), ("c2c4", "e7e5"), ("e2e4", "e7e5"), ("d2d4", "d7d5")):
            board = chess.Board()
            for move in moves:
                board.push_uci(move)
                self.assertIn(diag.position_id(board), exposure)

    def test_excluded_score_pair_still_accounts_for_bestmove_ponder(self):
        observed = search(lines=[], best="e2e4 ponder e7e5")
        self.assertIsNotNone(observed["reason"])
        exposure = {}
        diag.add_exposures(exposure, root(), observed)
        board = chess.Board()
        self.assertIn(diag.position_id(board), exposure)
        for move in ("e2e4", "e7e5"):
            board.push_uci(move)
            self.assertIn(diag.position_id(board), exposure)

    def test_fresh_search_barriers_and_explicit_multipv2(self):
        class Transcript(experiment.Session):
            def __init__(self):
                self.sent = []

            def send(self, command):
                self.sent.append(command)

            def receive(self, predicate):
                last = self.sent[-1]
                if last == "uci":
                    reply = ["uciok"]
                elif last == "isready":
                    reply = ["readyok"]
                else:
                    depth = int(last.split()[-1])
                    reply = [ACK, info(depth, 1), info(depth, 2, 10), "bestmove e2e4"]
                self_test.assertTrue(predicate(reply[-1]))
                return reply

        self_test = self
        session = Transcript()
        session.configure(NET)
        self.assertIn("setoption name MultiPV value 2", session.sent)
        self.assertIn("setoption name Threads value 1", session.sent)
        self.assertIn("setoption name Hash value 16", session.sent)
        beginning = len(session.sent)
        for depth in (9, 13):
            session.search_pair(NET, root(), depth)
        expected = []
        for depth in (9, 13):
            expected.extend(["ucinewgame", "isready", "position fen " + chess.STARTING_FEN,
                             f"go depth {depth}", "isready"])
        self.assertEqual(session.sent[beginning:], expected)

    def test_immutable_plan_and_resource_caps(self):
        plan = experiment.plan_document(experiment.HERE / "plan.json", experiment.PLAN_SHA)
        self.assertEqual(plan["resources"]["max_wall_seconds"], 2400)
        self.assertEqual(plan["resources"]["max_search_seconds"], 60)
        with self.assertRaises(ValueError):
            experiment.plan_document(experiment.HERE / "plan.json", "0" * 64)


if __name__ == "__main__":
    unittest.main()
