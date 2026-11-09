"""Synthetic contracts only; no actual labels, models or native processes."""
import copy
import unittest
import chess
import corpus
import native


def fixture(fen=chess.STARTING_FEN, a="e2e4", b="d2d4", budget=65536):
    root={"root_id":"a"*64,"family_id":"b"*64,"partition":"train","fen6":fen,"origins":[]}
    reply={"budget_nodes":budget,"searchmoves":None,"elapsed_seconds":1.0,"eval_file":"net.nnue",
           "input_line":10,"stdout_line_start":20,"stdout_line_stop":25,
           "lines":["info string NNUE evaluation using net.nnue (132MiB)",
                    f"info depth 15 seldepth 20 multipv 1 score cp 42 nodes {budget} pv {a}",
                    f"info depth 15 seldepth 20 multipv 2 score cp 18 nodes {budget} pv {b}",
                    f"bestmove {a}"]}
    return root,reply


class FrozenContracts(unittest.TestCase):
    def test_policy_and_unchanged_sources(self):
        self.assertEqual(corpus.policy()["fit"]["minimum_train_pairs"],2000)

    def test_identity_ignores_clocks_preserves_ep_aliases(self):
        board=chess.Board();board.push_uci("e2e4")
        first,aliases=corpus.identity(board)
        copy_board=board.copy();copy_board.halfmove_clock=18;copy_board.fullmove_number=22
        self.assertEqual(first,corpus.identity(copy_board)[0])
        self.assertGreaterEqual(len(aliases),2)

    def test_consensus_target_and_sign(self):
        root,response=fixture()
        low,_=native.parse_search(root,response)
        high,_=native.parse_search(root,{**response,"budget_nodes":262144})
        row=native.consensus(root,low,high)
        self.assertTrue(row["eligible"])
        self.assertEqual(row["target_margin_cp"],24)
        self.assertEqual(-(-12)+(-3),9)  # child-STM -> root ordering margin

    def test_black_root_uci_cp_needs_no_white_flip(self):
        root,response=fixture(chess.STARTING_FEN.replace(" w "," b "),"e7e5","d7d5")
        low,_=native.parse_search(root,response)
        high,_=native.parse_search(root,{**response,"budget_nodes":262144})
        self.assertEqual(native.consensus(root,low,high)["target_margin_cp"],24)

    def test_order_disagreement_held(self):
        root,response=fixture()
        low,_=native.parse_search(root,response)
        _,reverse=fixture(a="d2d4",b="e2e4",budget=262144)
        high,_=native.parse_search(root,reverse)
        self.assertIn("teacher_budget_order_disagreement",native.consensus(root,low,high)["reasons"])

    def test_no_shallower_bestmove_fallback(self):
        root,response=fixture()
        response["lines"][-1:-1]=[
            "info depth 16 multipv 1 score cp 44 nodes 65536 pv d2d4",
            "info depth 16 multipv 2 score cp 10 nodes 65536 pv e2e4"]
        parsed,_=native.parse_search(root,response)
        self.assertIsNone(parsed["complete_exact_top_two"])

    def test_illegal_pv_and_node_overshoot_refused(self):
        root,response=fixture(a="e2e5")
        with self.assertRaises(ValueError):
            native.parse_search(root,response)
        root,response=fixture()
        response["lines"][1]=response["lines"][1].replace("65536","69633")
        with self.assertRaises(ValueError):
            native.parse_search(root,response)

    def test_ordinary_in_check_child_remains_unavailable(self):
        fen="4k2r/8/8/8/8/8/8/R5K1 w - - 0 1"
        root,response=fixture(fen,"a1e1","a1b1")
        low,_=native.parse_search(root,response)
        high,_=native.parse_search(root,{**response,"budget_nodes":262144})
        row=native.consensus(root,low,high)
        self.assertFalse(row["eligible"])
        self.assertIn("child1_in_check",row["reasons"])
        self.assertTrue(row["children"][1]["ordinary_static_available"])


if __name__=="__main__":
    unittest.main()
