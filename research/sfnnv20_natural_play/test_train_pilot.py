import unittest
import chess
import common as c
import train_pilot
import teacher_protocol as t


class NaturalPilotContracts(unittest.TestCase):
    def test_distinct_components_fixed_lowest_root_no_replacement(self):
        rows=[{"component_id":f"{i:064x}","root_id":f"{i*2+j:064x}"} for i in range(140) for j in (1,0)]
        first=train_pilot.select_calibration(rows)
        second=train_pilot.select_calibration(list(reversed(rows)))
        self.assertEqual(first,second)
        self.assertEqual(len({row["component_id"] for row in first}),128)
        self.assertTrue(all(int(row["root_id"],16)%2==0 for row in first))

    def test_canonical_identity_separate_usage_alias_domain(self):
        board=chess.Board();board.push_uci("e2e4")
        canonical,aliases=t.identity(board)
        self.assertEqual(canonical,c.position_id(board))
        self.assertIn(canonical,aliases)
        self.assertGreater(len(aliases),1)

    def test_teacher_consensus_root_stm_and_unavailable_counterpart(self):
        board=chess.Board()
        root={"root_id":c.position_id(board),"component_id":"a"*64,"partition":"train","fen6":board.fen(),"origins":[]}
        base={"complete_exact_top_two":[{"move":"e2e4","root_stm_cp":40},{"move":"d2d4","root_stm_cp":16}]}
        low={**base,"budget_nodes":65536};high={**base,"budget_nodes":262144}
        label=t.consensus(root,low,high)
        self.assertTrue(label["eligible"])
        self.assertEqual(label["target_margin_cp"],24)
        self.assertEqual({child["rank"] for child in label["children"]},{1,2})


if __name__=="__main__":unittest.main()
