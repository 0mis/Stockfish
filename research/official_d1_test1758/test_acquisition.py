import unittest
import acquire


class AcquisitionContracts(unittest.TestCase):
    def test_shards_are_disjoint_complete_and_fixed(self):
        rows=[{"root_id":f"{i:064x}","partition":"train"} for i in range(32)]
        pieces=[{row["root_id"] for row in acquire.shard_roots(rows,s)} for s in range(4)]
        self.assertEqual(set.union(*pieces),{row["root_id"] for row in rows})
        self.assertEqual(sum(map(len,pieces)),32)
        for first in range(4):
            for second in range(first):
                self.assertFalse(pieces[first]&pieces[second])

    def test_nontraining_input_refused(self):
        with self.assertRaises(ValueError):
            acquire.shard_roots([{"root_id":"0"*64,"partition":"development"}],0)

    def test_unavailable_counterpart_does_not_drop_measurable_child(self):
        labels=[{"root_id":"a","children":[{"rank":1,"ordinary_static_available":True},
                                        {"rank":2,"ordinary_static_available":False}]}]
        acquire.require_complete_children(labels,[{"parent_root_id":"a","rank":1}])
        with self.assertRaises(ValueError):
            acquire.require_complete_children(labels,[])
        with self.assertRaises(ValueError):
            acquire.require_complete_children(labels,[{"parent_root_id":"a","rank":1}]*2)


if __name__=="__main__":
    unittest.main()
