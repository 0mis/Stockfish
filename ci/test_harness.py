"""Pure conformance fixtures; no native program or private opening is used."""
import copy,io,json,tempfile,unittest,zipfile
from pathlib import Path
import chess,chess.pgn
from common import ADMISSION_SHA,PLAN_SHA,SCHEDULE_SHA,NAMES,bootstrap,canonical,load_binding,pair_games,require,sha,verify_native_pair,verify_schedule
from prepare_inputs import unique_member
from run_match import command

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent if (HERE.parent/'PLAN.json').is_file() else HERE
PLAN,ROWS,_=load_binding(ROOT,check_implementation=False)

def synthetic_pair(opening,results=('1/2-1/2','1/2-1/2'),termination='normal'):
 out=[]
 for i,result in enumerate(results):
  game=chess.pgn.Game();board=chess.Board(opening['fen']);game.setup(board);cw=opening['candidate_first'] if i==0 else not opening['candidate_first']
  game.headers.update({'Event':PLAN['experiment']+'-pair-'+str(opening['pair_index']).zfill(6),'White':NAMES['candidate'] if cw else NAMES['reference'],
   'Black':NAMES['reference'] if cw else NAMES['candidate'],'Result':result,'Termination':termination,'TimeControl':'10+0.1','UTCDate':'2026.11.09','UTCTime':'00:00:00'})
  game.add_variation(next(iter(board.legal_moves))).comment='synthetic tl=10';out.append(str(game)+'\n\n')
 return ''.join(out).encode()

def native_fixture(directory,opening):
 arms=['candidate','reference'] if opening['candidate_first'] else ['reference','candidate'];engines=[]
 for arm in arms:
  engines.append({'name':NAMES[arm],'cmd':'..\\..\\..\\inputs\\'+arm+'.exe','dir':'','args':'','restart':True,
   'options':[['EvalFile','..\\..\\..\\inputs\\'+PLAN[arm]['network_filename']],['Threads','1'],['Hash','64'],['Ponder','false'],['UCI_LimitStrength','false'],['SyzygyProbeLimit','0'],['Move Overhead','10']],
   'limit':{'tc':{'increment':100,'fixed_time':0,'time':10000,'moves':0,'timemargin':0},'nodes':0,'plies':0}})
 cfg={'games':2,'rounds':1,'concurrency':1,'recover':False,'noswap':False,'reverse':False,'variant':0,'engines':engines,
  'opening':{'file':'opening.epd','format':0,'order':1,'plies':-1,'start':1},'pgn':{'event_name':PLAN['experiment']+'-pair-'+str(opening['pair_index']).zfill(6),'track_timeleft':True,'track_latency':True,'min':False}}
 for key in ('sprt','resign','draw','maxmoves','tb_adjudication'):cfg[key]={'enabled':False}
 (directory/'config.json').write_text(json.dumps(cfg));log=[]
 for arm in arms:log+=['name <--- go wtime 10000 btime 10000 winc 100 binc 100',NAMES[arm]+' ---> info string NNUE evaluation using ..\\inputs\\'+PLAN[arm]['network_filename']+' (131MiB)']
 (directory/'referee.log').write_text('\n'.join(log));return cfg

class Conformance(unittest.TestCase):
 def test_exact_public_freeze_and_root_admission(self):self.assertEqual(len(ROWS),286);self.assertEqual(sha((ROOT/'ROOT-ADMISSION.json').read_bytes()),ADMISSION_SHA)
 def test_disjoint_complete_shards(self):
  a={x['position_id'] for x in ROWS if x['shard']==0};b={x['position_id'] for x in ROWS if x['shard']==1};self.assertEqual((len(a),len(b),len(a&b),len(a|b)),(143,143,0,286))
 def test_order_color_and_duplicate_tampering_refused(self):
  for key,value in [('candidate_first',not ROWS[0]['candidate_first']),('shard',1),('position_id',ROWS[1]['position_id'])]:
   changed=copy.deepcopy(ROWS);changed[0][key]=value
   with self.assertRaises(ValueError):verify_schedule(changed,PLAN)
 def test_reversed_pair_legal_replay(self):
  opening=ROWS[0];parsed=pair_games(synthetic_pair(opening),opening,PLAN['experiment']+'-pair-000000');self.assertEqual(parsed['candidate_points'],1);self.assertEqual([x['candidate_white'] for x in parsed['games']],[opening['candidate_first'],not opening['candidate_first']])
 def test_clock_forfeits_retained(self):
  opening=ROWS[0];parsed=pair_games(synthetic_pair(opening,('1-0','0-1'),'time forfeit'),opening,PLAN['experiment']+'-pair-000000');self.assertTrue(all(x['reported_clock_forfeit'] for x in parsed['games']))
 def test_native_failure_and_extra_game_refused(self):
  opening=ROWS[0];event=PLAN['experiment']+'-pair-000000'
  for raw in (synthetic_pair(opening,termination='abandoned'),synthetic_pair(opening)*2):
   with self.assertRaises(ValueError):pair_games(raw,opening,event)
 def test_missing_game_refused(self):
  opening=ROWS[0];raw=synthetic_pair(opening);first=chess.pgn.read_game(io.StringIO(raw.decode()))
  with self.assertRaises(ValueError):pair_games((str(first)+'\n').encode(),opening,PLAN['experiment']+'-pair-000000')
 def test_bootstrap_exact_manual_two_pair_polynomial(self):
  result=bootstrap([1,0,0,0,1],n=2);self.assertEqual(result['coefficient_vector_sha256'],sha(canonical(['1','0','0','0','2','0','0','0','1'])));self.assertEqual([x['boundary'] for x in result['endpoints']],['negative_infinity','positive_infinity'])
 def test_full286_all_draws_and_incomplete_refusal(self):
  self.assertEqual([x['twice_pair_point_sum'] for x in bootstrap([0,0,286,0,0])['endpoints']],[572,572])
  with self.assertRaises(ValueError):bootstrap([0,0,285,0,0])
 def test_actual_config_and_real_clock_frame(self):
  with tempfile.TemporaryDirectory(prefix='composite-pure-') as name:
   directory=Path(name);native_fixture(directory,ROWS[0]);self.assertEqual(verify_native_pair(directory,ROWS[0],PLAN)['real_clock_go_commands'],2)
   log=(directory/'referee.log').read_text();(directory/'referee.log').write_text(log.replace('winc 100','winc 10'))
   with self.assertRaises(ValueError):verify_native_pair(directory,ROWS[0],PLAN)
 def test_recovery_and_wrong_net_refused(self):
  with tempfile.TemporaryDirectory(prefix='composite-pure-') as name:
   directory=Path(name);cfg=native_fixture(directory,ROWS[0]);cfg['recover']=True;(directory/'config.json').write_text(json.dumps(cfg))
   with self.assertRaises(ValueError):verify_native_pair(directory,ROWS[0],PLAN)
   native_fixture(directory,ROWS[0]);log=(directory/'referee.log').read_text();(directory/'referee.log').write_text(log.replace('nn-d5c257728ba5.nnue','nn-wrong.nnue'))
   with self.assertRaises(ValueError):verify_native_pair(directory,ROWS[0],PLAN)
 def test_exact_asset_sha_member_and_ambiguity(self):
  with tempfile.TemporaryDirectory(prefix='composite-pure-') as name:
   directory=Path(name);archive=directory/'synthetic.zip'
   with zipfile.ZipFile(archive,'w') as z:z.writestr('one.exe',b'synthetic')
   unique_member(archive,'.exe',sha(b'synthetic'),directory/'out.bin')
   with zipfile.ZipFile(archive,'a') as z:z.writestr('two.exe',b'synthetic')
   with self.assertRaises(ValueError):unique_member(archive,'.exe',sha(b'synthetic'),directory/'never.bin')
 def test_commands_are_new_exact_bundles_and_no_recover(self):
  root=Path('C:/fixed-public');directory=root/'evidence/pairs/pair-000000';argv=command(root,directory,ROWS[0],PLAN)
  self.assertIn('tc=10+0.1',argv);self.assertIn('option.Hash=64',argv);self.assertNotIn('-recover',argv);self.assertTrue(any('nn-d5c257728ba5.nnue' in x for x in argv));self.assertTrue(any('nn-18cd32e11a9c.nnue' in x for x in argv));self.assertFalse(any('1758' in x or '7cdd' in x for x in argv))

if __name__=='__main__':unittest.main()
