from fractions import Fraction
from types import SimpleNamespace
import unittest
import fit_core


class ExactFitContracts(unittest.TestCase):
    def test_family_equal_weighting(self):
        self.assertEqual(fit_core.family_mean([0,100,0],["a","a","b"]),25)

    def test_piecewise_huber_and_absolute_anchor(self):
        self.assertEqual(fit_core.huber20(20),200)
        self.assertEqual(fit_core.huber20(21),220)
        value=fit_core.exact_objective([0],[10],[2],[0],["a"],{(1,0):2})
        self.assertEqual(value,Fraction(105,2))

    def test_joint_quantization_is_not_sum_of_independent_deltas(self):
        self.assertEqual(fit_core.raw_value(14),0)
        self.assertEqual(fit_core.raw_value(28),1)
        self.assertEqual(fit_core.raw_value(-28),-1)

    def test_identity_wins_exact_tie(self):
        self.assertLess(fit_core.edit_tie({}),fit_core.edit_tie({(31,4):1}))
        self.assertLess(fit_core.edit_tie({(31,4):-1}),fit_core.edit_tie({(31,4):1}))

    def test_signed_bounds_and_global_sparse_budget(self):
        heads=[SimpleNamespace(weights=tuple([0]*128)) for _ in range(32)]
        self.assertTrue(fit_core.allowed({(31,0):4,(31,1):-4},heads))
        self.assertFalse(fit_core.allowed({(31,0):4,(31,1):-4,(31,2):1},heads))
        self.assertFalse(fit_core.allowed({(h,0):4 for h in range(5)},heads))
        heads[31]=SimpleNamespace(weights=tuple([127]+[0]*127))
        self.assertFalse(fit_core.allowed({(31,0):1},heads))

    def test_support_counts_unique_families_and_lowest_head_tie(self):
        pairs=[{"root_id":str(i),"family_id":str(i%2)} for i in range(4)]
        children={(str(i),rank):{"head":i//2} for i in range(4) for rank in (1,2)}
        selected,support=fit_core.select_heads(pairs,children,minimum_roots=2,minimum_families=2)
        self.assertEqual(selected,[0,1])

    def test_ranking_ties_are_half_errors(self):
        self.assertEqual(fit_core.ranking_error(0),Fraction(1,2))
        self.assertEqual(fit_core.ranking_error(-1),1)
        self.assertEqual(fit_core.ranking_error(1),0)


if __name__=="__main__":
    unittest.main()
