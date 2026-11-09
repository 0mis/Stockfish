import json,unittest
from unittest.mock import patch
import capture_protocol as p
import common as c


def synthetic():
    fc0=[i*127-1000 for i in range(32)]
    fc1=[i*91-1000 for i in range(32)]
    serial=p.activations(fc0,7)+p.activations(fc1,6)
    slots=[p.physical_slot(i) for i in range(128)]
    native=[0]*128
    for index,slot in enumerate(slots):native[slot]=serial[index]
    weights=[i%7-3 for i in range(128)]
    bias=17;affine=bias+sum(w*x for w,x in zip(weights,serial))
    skip=fc0[30]-fc0[31];combined=affine+skip
    scaled=p.tz(combined*9600,16384);raw=p.tz(scaled,16)
    return {"schema":"sfnnv20-fc2-activation/1","source_commit":c.SOURCE,"row_id":0,
            "integer_parity":True,"history_scope":"supplied_fen_and_legal_prefix_only",
            "stored_heads":92,"physical_head":0,"fc0":fc0,"fc1":fc1,"input_native":native,
            "input_serialized":serial,"serialized_to_native":slots,"output_weights":weights,"output_bias":bias,
            "fc2_affine":affine,"skip":skip,"combined":combined,"scaled":scaled,
            "raw_nnue":raw,"ordinary_raw_nnue":raw}


class CurrentV20Contracts(unittest.TestCase):
    def test_frozen_current_source_and_independent_corpus(self):
        plan=c.policy()
        self.assertEqual(plan["official_source"]["commit"],c.SOURCE)
        self.assertEqual(plan["resources"]["native_rosters"]["TRAIN"],16000)
        self.assertFalse(plan["separate_from"]["retry_or_extension"])
        self.assertFalse(plan["separate_from"]["old_D1_features_or_32_head_caches_used"])

    def test_explicit_root_clearance_binds_exact_public_inputs(self):
        grant=json.loads((c.HERE/"ROOT-CLEARANCE.json").read_bytes())
        self.assertEqual(grant,c.policy()["technical_admission"])
        self.assertEqual(grant["public_child_stream_sha256"],"dad3370e2be1b627af1691324656236b538c3aeee88d3ba6313c578593058840")

    def test_no_local_native_scope(self):
        with patch.dict("os.environ",{"GITHUB_ACTIONS":"false"}):
            with self.assertRaises(ValueError):c.hosted("TRAIN")

    def test_current_serialized_dot_skip_quantizer(self):
        row=synthetic()
        self.assertTrue(p.validate(row)["exact_integer_parity"])

    def test_wrong_serialized_lane_mapping_refused(self):
        row=synthetic();row["serialized_to_native"][0]=row["serialized_to_native"][1]
        with self.assertRaises(ValueError):p.validate(row)

    def test_joint_signed_quantization_boundary_and_int64(self):
        raw=lambda value:p.tz(p.tz(value*9600,16384),16)
        self.assertEqual(raw(14),0);self.assertEqual(raw(28),1);self.assertEqual(raw(-28),-1)
        self.assertEqual(raw(600000),21972)

    def test_every_lane_is_bijective_uint7(self):
        row=synthetic()
        self.assertEqual(set(row["serialized_to_native"]),set(range(128)))
        self.assertTrue(all(0<=value<=127 for value in row["input_serialized"]))


if __name__=="__main__":unittest.main()
