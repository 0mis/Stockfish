"""Small synthetic fixtures only; no real checkpoint or local engine calls."""
import hashlib
import json
from pathlib import Path
import struct
import sys
import unittest
import numpy as np
import alignment as a


PLAN=json.loads(Path(__file__).with_name('plan.json').read_bytes())


class AlignmentTests(unittest.TestCase):
    def toy(self):
        rng=np.random.default_rng(763);x=rng.integers(-12,13,(256,8)).astype(float);x[x==0]=1;return x
    def report(self,x,y):
        corr,_=a.conditional_corr(x,y,16,0.5)
        direct=(corr[:4,:4]+corr[4:,4:])/2;swap=(corr[:4,4:]+corr[4:,:4])/2
        return a.paired_report(direct,swap,PLAN['ft_gate'],1)
    def test_identity_is_preferred_for_informative_identical_coordinates(self):
        x=self.toy();r=self.report(x,x);self.assertTrue(r['pass']);self.assertEqual(r['assignment_identity_fraction'],1)
    def test_nonidentity_paired_permutation_refuses_direct_gate(self):
        x=self.toy();y=x[:,[1,0,2,3,5,4,6,7]];r=self.report(x,y)
        self.assertFalse(r['pass']);self.assertLess(r['assignment_identity_fraction'],0.9)
    def test_physically_permissible_factor_swap_is_detected(self):
        x=self.toy();y=x[:,[4,1,2,3,0,5,6,7]];r=self.report(x,y)
        self.assertFalse(r['pass']);self.assertTrue(any(r['best_assignment_factor_swaps']))
    def test_zero_or_constant_lanes_never_fake_identity_support(self):
        x=self.toy();x[:,0]=3;r=self.report(x,x);self.assertFalse(r['pass']);self.assertEqual(r['supported_pairs'],3)
        corr,n=a.conditional_corr(np.zeros((256,4)),np.zeros((256,4)),16,0.5)
        self.assertTrue(np.all(np.isnan(corr)));self.assertTrue(np.all(n==0))
    def test_shared_zeros_are_excluded_and_centering_is_real(self):
        x=np.r_[np.zeros(200),np.arange(1,9)].reshape(-1,1)
        y=np.r_[np.zeros(200),np.arange(8,0,-1)].reshape(-1,1)
        corr,n=a.conditional_corr(x,y,4,0.5);self.assertEqual(n[0,0],8);self.assertAlmostEqual(corr[0,0],-1)
        corr,_=a.conditional_corr(np.arange(1,129).reshape(-1,1),17+2*np.arange(1,129).reshape(-1,1),16,0.5)
        self.assertAlmostEqual(corr[0,0],1)
    def test_sampling_is_fixed_hash_ranked_unique_and_plane_specific(self):
        one=a.sample_rows('plane0',200,64);two=a.sample_rows('plane0',200,64)
        self.assertEqual(one.tolist(),two.tolist());self.assertEqual(len(set(one.tolist())),64)
        self.assertNotEqual(one.tolist(),a.sample_rows('plane1',200,64).tolist())
    def test_structurally_impossible_rows_never_supply_alignment_evidence(self):
        self.assertEqual(len(a.eligible_feature_rows('PP_3Wide',4560)),1488)
        zero=a.eligible_feature_rows('K32H3_block0',22528);one=a.eligible_feature_rows('K32H3_block1',22528)
        self.assertTrue(all((i%704)//64 not in (6,7,8,9) for i in zero))
        self.assertTrue(all((i%704)//64!=9 for i in one))
    def test_uninformative_all_plane_gate_is_refused(self):
        r=a.paired_report(np.full((4,4),np.nan),np.full((4,4),np.nan),PLAN['ft_gate'],1)
        self.assertFalse(r['pass']);self.assertEqual(r['supported_pairs'],0);self.assertIsNone(r['median_identity_correlation'])


class ReaderTests(unittest.TestCase):
    def miniature(self):
        reader=a.parser(PLAN);fallback=struct.pack('<4H',0,0,2,2)
        geometry=reader.Geometry(4,(32,24,64),4,3,4,2,4,hashlib.sha256(fallback).hexdigest())
        raw=bytearray(struct.pack('<3I',reader.VERSION,reader.NETWORK_TAG,4)+b'test')
        raw.extend(struct.pack('<3I',reader.ROUTE_TAG,4,2)+fallback+struct.pack('<I',reader.FT_TAG))
        raw.extend(np.asarray([-32768,32767,-2,-1,0,1,2,3],dtype='<i2').tobytes())
        raw.extend(np.arange(sum(geometry.feature_rows)*geometry.lanes,dtype=np.int64).astype('i1').tobytes())
        for index in range(2):
            raw.extend(struct.pack('<I',reader.STACK_TAG))
            for outputs,inputs in ((4,8),(3,8),(1,14)):
                raw.extend(np.arange(outputs,dtype='<i4').tobytes())
                weights=np.full((outputs,reader._padded(inputs)),101,dtype='i1')
                weights[:,:inputs]=np.arange(outputs*inputs,dtype=np.int64).reshape(outputs,inputs).astype('i1')
                raw.extend(weights.tobytes())
        self.assertEqual(len(raw),geometry.size);return reader,geometry,raw
    def test_every_stack_and_routing_and_signed_views_are_checked(self):
        reader,geometry,raw=self.miniature();layout=reader.parse_layout(raw,geometry)
        self.assertEqual(layout.raw_to_physical,(0,0,1,1));self.assertEqual(reader.ft_views(raw,layout).ft_biases[0],-32768)
        for i in range(2):
            arrays,meta=a.stack_views(raw,layout,i,reader)
            self.assertEqual(arrays['fc0']['weights'].shape,(4,8));self.assertEqual(arrays['fc1']['weights'].shape,(3,8));self.assertEqual(arrays['fc2']['weights'].shape,(1,14))
            self.assertGreater(meta['padding_bytes'],0);self.assertFalse(np.any(arrays['fc2']['weights']==101))
    def test_last_stack_corruption_and_trailing_bytes_refuse_geometry(self):
        reader,geometry,raw=self.miniature();layout=reader.parse_layout(raw,geometry)
        corrupt=bytearray(raw);struct.pack_into('<I',corrupt,layout.stacks_start+geometry.stack_bytes,0)
        with self.assertRaises(ValueError):reader.parse_layout(corrupt,geometry)
        with self.assertRaises(ValueError):reader.parse_layout(raw+b'\0',geometry)


if __name__=='__main__':unittest.main()
