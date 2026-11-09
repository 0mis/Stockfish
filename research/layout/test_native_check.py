import unittest

import native_check as native


NET = r"..\inputs\parent.nnue"
FEN = "8/8/8/8/8/4k3/8/K6R w - - 0 1"


def response(lines):
    return "\r\n".join(["uciok", "readyok", native.ACK_PREFIX + NET + native.ACK_SUFFIX, *lines, "readyok", ""])


class NativeParserTests(unittest.TestCase):
    def test_each_printed_bucket_and_one_raw_score(self):
        table = [f"| {i} | -  0.00 |" + (" <-- this bucket is used" if i == 31 else "") for i in range(92)]
        data = response(["NNUE network contributions (Normalized, White to move)", native.BORDER,
                         "|   Bucket   | Evaluation |", native.BORDER, *table, native.BORDER,
                         "NNUE evaluation +14 (side to move, internal units)", "NNUE evaluation +0.04 (white side)",
                         "Final evaluation +0.06 (white side) [with scaled NNUE, ...]"])
        rows = [{"fen": FEN}]
        parsed = native._parse_eval(data, "", NET, rows)["rows"][0]
        self.assertEqual((len(parsed["buckets"]), parsed["used_bucket"], parsed["raw_score"]), (92, 31, 14))
        self.assertEqual(parsed["buckets"][31], {"index": 31, "used": True, "printed_value": "-0.00"})
        updated = native._parse_eval(data.replace("| 91 | -", "| 91 | +"), "", NET, rows)["rows"][0]
        self.assertNotEqual(parsed, updated)
        for old, new in ((table[42], table[41]), (table[31], table[31].split(" <--")[0]),
                         ("side to move, internal units", "white side"), (NET, "fallback.nnue"),
                         ("Normalized, White", "Normalized, Black")):
            with self.subTest(old=old), self.assertRaises(ValueError):
                native._parse_eval(data.replace(old, new), "", NET, rows)

    def test_final_search_fields_and_terminal_position(self):
        trace = [f"info depth {d} seldepth 16 multipv 1 score cp -12 nodes {10*d} nps 100 hashfull 0 "
                 "tbhits 0 time 4 pv h1e1 e3d4" for d in range(1, 14)] + ["bestmove h1e1 ponder e3d4"]
        row = native._bench_search(trace, 1, FEN)
        self.assertEqual((row["depth"], row["score"], row["nodes"], row["pv"], row["ponder"]),
                         (13, -12, 130, ["h1e1", "e3d4"], "e3d4"))
        self.assertEqual(native._bench_search([s.replace("nps 100", "nps 172") for s in trace], 1, FEN), row)
        self.assertNotEqual(native._bench_search(trace[:-1] + ["bestmove h1e1 ponder e3d3"], 1, FEN), row)
        with self.assertRaises(ValueError):
            native._bench_search(trace[1:], 1, FEN)
        terminal = native._bench_search(["info depth 0 score mate 0", "bestmove (none)"], 49, FEN)
        self.assertEqual((terminal["nodes"], terminal["pv"], terminal["bestmove"]), (0, [], "(none)"))

    def test_perft_rejects_duplicate_or_missing_move(self):
        moves = sorted(native.ROOT_MOVES)
        counts = [7281] + [10000] * 19
        lines = [f"{move}: {count}" for move, count in zip(moves, counts)] + ["Nodes searched: 197281"]
        self.assertEqual(native._parse_perft(response(lines), "", NET)["divides"], dict(zip(moves, counts)))
        for bad in (lines[1:], [lines[0], *lines], lines[:-1] + ["Nodes searched: 197280"]):
            with self.assertRaises(ValueError):
                native._parse_perft(response(bad), "", NET)


if __name__ == "__main__":
    unittest.main()
