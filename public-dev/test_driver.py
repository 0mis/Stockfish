"""No native execution: frozen command/transfer/statistics/PGN validation fixtures."""
import copy,json,tempfile,unittest
from pathlib import Path
import chess,chess.pgn
from common import pair_games,bootstrap,NAMES
from transfer_public import apply_edits
from run_match import command,spawn_referee

class DriverTests(unittest.TestCase):
 def test_windows_referee_resolution_is_absolute_without_a_native_launch(self):
  from unittest.mock import patch
  with tempfile.TemporaryDirectory() as d:
   root=Path(d).resolve();directory=root/'evidence/pairs/pair-000000'
   with patch('run_match.subprocess.Popen') as popen:
    spawn_referee(['../../../inputs/fastchess.exe'],root,directory,None,{})
   self.assertEqual(popen.call_args.kwargs['executable'],str(root/'inputs/fastchess.exe'))
   self.assertTrue(Path(popen.call_args.kwargs['executable']).is_absolute())
 def opening(self):
  fen=chess.STARTING_FEN
  return {'pair_index':0,'fen':fen,'opening_id':__import__('hashlib').sha256(' '.join(fen.split()[:4]).encode()).hexdigest(),'candidate_first':True}
 def pgn(self):
  out=[]
  for i in range(2):
   g=chess.pgn.Game();g.headers['Event']='fixture-pair';g.headers['White']=NAMES['candidate'] if i==0 else NAMES['parent'];g.headers['Black']=NAMES['parent'] if i==0 else NAMES['candidate'];g.headers['Result']='1/2-1/2'
   g.add_variation(chess.Move.from_uci('e2e4'));out.append(str(g))
  return ('\n\n'.join(out)+'\n').encode()
 def test_exact_symmetric_clock_options_no_recover_or_statistical_stop(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);argv=command(root,root/'evidence/pairs/pair-000000',self.opening(),{'experiment_id':'fixed'})
   for x in ['tc=10+0.1','option.Threads=1','option.Hash=64','timemargin=0','restart=on','option.Ponder=false']:self.assertIn(x,argv)
   for x in ['-recover','-sprt','-draw','-resign']:self.assertNotIn(x,argv)
   self.assertFalse(any(__import__('re').fullmatch(r'(?:nodes|st)=\d+(?:\.\d+)?',y) for y in argv))
   self.assertIn('option.EvalFile=../../../inputs/nn-selected.nnue'.replace('/',__import__('os').sep),argv)
 def test_paired_colors_results_and_legal_prefix(self):
  result=pair_games(self.pgn(),self.opening(),'fixture-pair',True);self.assertEqual(result['candidate_points'],1);self.assertEqual(len(result['games']),2)
 def test_foreign_opening_or_colors_refused(self):
  changed=self.pgn().replace(NAMES['parent'].encode(),b'foreign')
  with self.assertRaises(ValueError):pair_games(changed,self.opening(),'fixture-pair',True)
  wrong=self.opening();wrong['fen']=chess.Board('8/8/8/8/8/8/K7/7k w - - 0 1').fen()
  with self.assertRaises(ValueError):pair_games(self.pgn(),wrong,'fixture-pair',True)
 def test_transfer_before_inverse_and_unchanged_bytes(self):
  data=bytes(64);edited,changed=apply_edits(data,[(4,0,96),(20,0,-192)]);self.assertTrue(changed);self.assertEqual(edited[24:],data[24:])
  with self.assertRaises(ValueError):apply_edits(data,[(4,1,2)])
  with self.assertRaises(ValueError):apply_edits(data,[(4,0,2),(4,0,3)])
 def test_exact_bootstrap_all_draws_and_boundaries(self):
  result=bootstrap([0,0,128,0,0]);self.assertEqual([x['score_fraction']['numerator'] for x in result['endpoints']],[256,256])
  extreme=bootstrap([0,0,0,0,128]);self.assertTrue(all(x['logistic_elo'] is None and x['boundary']=='positive_infinity' for x in extreme['endpoints']))
 def test_observed_distribution_reproduces_existing_exact_quantiles(self):
  result=bootstrap([2,28,63,32,3]);self.assertEqual([x['twice_pair_point_sum'] for x in result['endpoints']],[245,279])

if __name__=='__main__':unittest.main()
