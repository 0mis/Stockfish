import unittest
import acquire_train as a


class FixedTrainAcquisitionContracts(unittest.TestCase):
    def test_eight_shards_are_complete_disjoint_and_fixed(self):
        rows=[{"root_id":f"{i:064x}","partition":"train"} for i in range(80)]
        shards=[{row["root_id"] for row in a.choose_shard(rows,index)} for index in range(8)]
        self.assertEqual(set.union(*shards),{row["root_id"] for row in rows})
        self.assertEqual(sum(map(len,shards)),80)
        for index in range(8):
            for previous in range(index):self.assertFalse(shards[index]&shards[previous])

    def test_no_holdout_input_or_duplicate_roots(self):
        with self.assertRaises(ValueError):
            a.choose_shard([{"root_id":"0"*64,"partition":"final"}],0)
        with self.assertRaises(ValueError):
            a.choose_shard([{"root_id":"0"*64,"partition":"train"}]*2,0)

    def test_measurable_counterpart_must_be_preserved(self):
        labels=[{"root_id":"x","children":[{"rank":1,"ordinary_static_available":True},
                                         {"rank":2,"ordinary_static_available":False}]}]
        a.verify_children(labels,[{"parent_root_id":"x","rank":1}])
        with self.assertRaises(ValueError):a.verify_children(labels,[])
        with self.assertRaises(ValueError):a.verify_children(labels,[{"parent_root_id":"x","rank":1}]*2)


if __name__=="__main__":unittest.main()
