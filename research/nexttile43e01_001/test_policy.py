import copy
import hashlib
import json
from pathlib import Path
import unittest
import chess
from policy import legal_line,reverse_port,roster,timing_gate

HERE=Path(__file__).resolve().parent


class PolicyTests(unittest.TestCase):
    def test_actual_port_removes_to_exact_current_source(self):
        plan=json.loads((HERE/'plan.json').read_bytes());raw=(HERE.parents[1]/'src/nnue/nnue_accumulator.cpp').read_bytes()
        self.assertEqual(hashlib.sha256(reverse_port(raw)).hexdigest(),plan['source']['baseline_accumulator_sha256'])
        self.assertEqual(raw.count(b'UNROLLED_FOR'),reverse_port(raw).count(b'UNROLLED_FOR'))
        self.assertIn(b'std::array<const i8*, RemovedCount> removedWeights;',raw)
        with self.assertRaises(ValueError):reverse_port(raw.replace(b'prefetch_next_tile(j, add);',b'prefetch_next_tile(j, sub);'))

    def test_all_prefetch_addresses_remain_inside_the_changed_row(self):
        dimensions=1024
        for paired,pext,tile in ((True,True,256),(True,True,512),(True,False,256),(False,True,256)):
            addresses=[j+tile+k for j in range(0,dimensions,tile) for k in range(0,tile,64) if paired and pext and dimensions//tile>2 and j+tile<dimensions]
            self.assertTrue(all(0<=x<dimensions for x in addresses))
            self.assertEqual(len(addresses),12 if paired and pext and tile==256 else 0)

    def test_public_roster_chess960_and_legal_prefixes(self):
        roots=roster((HERE.parents[1]/'src/benchmark.cpp').read_text())
        self.assertEqual(len(roots),51);self.assertEqual(sum(b.chess960 for b in roots),2)
        self.assertEqual(roots[0].fen(),chess.STARTING_FEN)

    def test_legal_pv_bestmove_ponder_and_terminal(self):
        board=chess.Board();self.assertEqual(legal_line(board,['e2e4','e7e5'],'e2e4','e7e5'),2)
        with self.assertRaises(ValueError):legal_line(board,['e2e5'],'e2e5')
        with self.assertRaises(ValueError):legal_line(board,['e2e4'],'d2d4')
        with self.assertRaises(ValueError):legal_line(board,[],'0000')
        terminal=chess.Board('7k/6Q1/6K1/8/8/8/8/8 b - - 0 1');legal_line(terminal,[],'(none)')

    def test_fixed_timing_gate_no_outlier_or_order_escape(self):
        plan={'timing':{'orders':['ABBA','BAAB']*4}}
        rows=[{'side':side,'engine_elapsed_ms':1000 if side=='A' else 980,'monotonic_wall_ns':1000000 if side=='A' else 980000} for order in plan['timing']['orders'] for side in order]
        self.assertTrue(timing_gate(rows,plan)['pass'])
        altered=copy.deepcopy(rows)
        for i in range(8):
            if altered[i]['side']=='B':altered[i]['engine_elapsed_ms']=1100
        self.assertFalse(timing_gate(altered,plan)['pass'])
        with self.assertRaises(ValueError):timing_gate(rows[:-1],plan)
        altered=copy.deepcopy(rows);altered[0]['side']='B'
        with self.assertRaises(ValueError):timing_gate(altered,plan)


if __name__=='__main__':unittest.main()
