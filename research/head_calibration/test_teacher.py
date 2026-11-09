import io
import unittest

import chess
import teacher


NET = r"..\inputs\parent.nnue"
ACK = teacher.native.ACK_PREFIX + NET + teacher.native.ACK_SUFFIX
ROW = {"position_id": "p", "family": "f", "role": "fit", "physical_head": 10, "fen": chess.STARTING_FEN}


def score_line(score="cp 15", pv="e2e4 e7e5", nodes=16389):
    return f"info depth 12 seldepth 15 multipv 1 score {score} nodes {nodes} nps 995 hashfull 2 tbhits 0 time 5 pv {pv}"


def search(lines, best="e2e4", budget=16384):
    return teacher._parse_search([ACK, *lines, "bestmove " + best], chess.Board(), NET, budget)


class TeacherTests(unittest.TestCase):
    def test_final_white_pawns_convert_exactly_to_stm_cp(self):
        prefix = ["uciok", "readyok"]
        for turn, cp in (("w", -513), ("b", 513)):
            row = {**ROW, "fen": ROW["fen"].replace(" w ", f" {turn} ")}
            side = "White" if turn == "w" else "Black"
            block = [ACK, f"NNUE network contributions (Normalized, {side} to move)", teacher.native.BORDER,
                     "|   Bucket   | Evaluation |", teacher.native.BORDER]
            block.extend(f"| {i} | - 0.00 |" + (" <-- this bucket is used" if i == 10 else "") for i in range(92))
            block.extend([teacher.native.BORDER, "NNUE evaluation -1702 (side to move, internal units)",
                          "NNUE evaluation -4.95 (white side)", "Final evaluation -5.13 (white side) [with scaled NNUE, ...]", "readyok"])
            result = teacher._parse_static(prefix, block, NET, row)
            self.assertEqual((result["static_cp"], result["raw_score"], result["used_bucket"]), (cp, -1702, 10))
            self.assertEqual(result["buckets"][10], {"index": 10, "printed_value": "-0.00", "used": True})
            with self.assertRaises(ValueError):
                teacher._parse_static(prefix, block, NET, {**row, "physical_head": 11})

    def test_only_final_matching_exact_primary_score_is_eligible(self):
        good = score_line()
        result = search([good], best="e2e4 ponder e7e5")
        self.assertEqual((result["cp"], result["reported_nodes"], result["bestmove"], result["ponder"]), (15, 16389, "e2e4", "e7e5"))
        for lines, reason in (([], "no_final_score_marker"), ([good, score_line("cp 20 upperbound")], "bounded_score"),
                              ([good, score_line("mate -2")], "mate_score"), ([good, score_line(pv="d2d4")], "no_matching_primary_pv")):
            observed = search(lines)
            self.assertEqual((observed["cp"], observed["reason"]), (None, reason))
        for invalid in ("e2e5", "0000"):
            with self.assertRaises(ValueError):
                search([score_line(pv=invalid)])
        with self.assertRaises(ValueError):
            search([good], best="e2e5")
        with self.assertRaises(ValueError):
            teacher._parse_search([ACK, good, "bestmove e2e4"], chess.Board(), "fallback.nnue", 16384)

    def test_thresholds_and_budget_move_consistency(self):
        for shallow_cp, deep_cp, expected in ((230, 250, (True, "stable")), (-250, -230, (True, "stable")),
                                              (231, 251, (False, "score_out_of_range")), (0, -21, (False, "budget_score_difference"))):
            shallow = search([score_line(f"cp {shallow_cp}")])
            deep = search([score_line(f"cp {deep_cp}")], budget=65536)
            self.assertEqual(teacher._stability(shallow, deep), expected)
        changed = search([score_line(pv="d2d4")], best="d2d4", budget=65536)
        self.assertEqual(teacher._stability(shallow, changed), (False, "bestmove_disagreement"))
        self.assertEqual(teacher._stability(shallow, search([])), (False, "deep_no_final_score_marker"))

    def test_independent_search_barriers(self):
        class Transcript(teacher._Session):
            def __init__(self):
                self.sent = []

            def send(self, command):
                self.sent.append(command)

            def receive(self, predicate):
                reply = {"uci": ["uciok"], "isready": ["readyok"]}.get(self.sent[-1], [ACK, score_line(), "bestmove e2e4"])
                if not predicate(reply[-1]):
                    raise AssertionError("Wrong UCI completion marker")
                return reply

        transcript = Transcript()
        transcript.configure(NET)
        setup_len = len(transcript.sent)
        self.assertIn("setoption name Hash value 16", transcript.sent)
        self.assertIn("setoption name Threads value 1", transcript.sent)
        for budget in (teacher.SHALLOW_NODES, teacher.DEEP_NODES):
            transcript.search(NET, ROW, budget)
        expected = []
        for budget in (16384, 65536):
            expected.extend(["ucinewgame", "isready", "position fen " + ROW["fen"], f"go nodes {budget}", "isready"])
        self.assertEqual(transcript.sent[setup_len:], expected)

    def test_reader_retains_log_after_error(self):
        stream = io.BytesIO()
        session = teacher._Session(None, None, {"stderr": stream})
        raw = b"ERROR: damaged stream\r\nremaining diagnostic\r\n"
        session._read("stderr", io.BytesIO(raw))
        self.assertEqual(stream.getvalue(), raw)
        self.assertTrue(session.reader_errors)


if __name__ == "__main__":
    unittest.main()
