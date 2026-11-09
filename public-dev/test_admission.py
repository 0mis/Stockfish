"""Synthetic only: no network access, native process, real result or receipt."""
import copy,io,json,os,unittest,zipfile
from pathlib import Path
import chess,chess.pgn
import common
from common import sha,canonical,bootstrap,NAMES
from admit_primary import validate_selection,validate_members,replay_primary,accepted_local,opening_partition
from run_match import command,game_accounting

def fixtures(points=128):
 plan=json.loads((Path(__file__).parent/'plan.json').read_bytes());selection=plan['selection']
 counts=[0,0,128,0,0] if points==128 else [0,0,127,1,0]
 inputs={'all_public_asset_hashes_verified':True,'actual_engine_sha256':plan['inputs']['engine_sha256'],'actual_referee_sha256':plan['inputs']['referee_sha256'],
         'actual_parent_sha256':selection['less_than_or_equal_128_points'],'actual_candidate_sha256':selection['strict_greater_than_128_points']}
 result={'schema':'test1758-transferred-bias-public-development-result/1','status':'all128pairs_256games_complete','pairs':128,'games':256,
         'plan_sha256':selection['primary_plan_sha256'],'outcome_based_retries':0,'confirmed_strength_gain':False,'candidate_points':points,'parent_points':256-points,
         'pentanomial':counts,'descriptive_paired_bootstrap':bootstrap(counts),'runtime':inputs}
 audit={'status':'all128pairs_256games_raw_receipts_reverified','plan_sha256':result['plan_sha256'],'candidate_points':points,'parent_points':256-points,'pentanomial':counts,'descriptive_paired_bootstrap':result['descriptive_paired_bootstrap']}
 admission={'schema':'test1758-primary-verified-followup-admission/1','status':'VERIFIED_COMPLETE_PRIMARY_ADMITTED','primary':{'repository':'0mis/Stockfish','run_id':42675059274,'head_sha':selection['primary_driver_commit'],'run_attempt':1,'conclusion':'success','status':'completed'},
            'selected_network_sha256':selection['strict_greater_than_128_points'] if points>128 else selection['less_than_or_equal_128_points'],'primary_candidate_points':points}
 return plan,admission,result,audit,inputs

class AdmissionTests(unittest.TestCase):
 def test_actual_canonical_partition_rejects_overlap_aliases_and_repeats(self):
  def row(board):
   fen=board.fen(en_passant='legal');return {'fen':fen,'opening_id':sha(' '.join(fen.split()[:4]).encode())}
  board=chess.Board();old=row(board);board.push_uci('e2e4');new=row(board)
  self.assertEqual(opening_partition([old,old],[new])['canonical_root_overlap'],0)
  alias=copy.deepcopy(old);alias['fen']=' '.join(old['fen'].split()[:4])+' 9 34'
  with self.assertRaises(ValueError):opening_partition([old],[alias])
  with self.assertRaises(ValueError):opening_partition([old],[new,new])
  forged=copy.deepcopy(new);forged['opening_id']='0'*64
  with self.assertRaises(ValueError):opening_partition([old],[forged])
 def test_exact_rendered_workflow_and_implementation_are_required(self):
  import tempfile
  from unittest.mock import patch
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);here=root/'public-dev';here.mkdir();active=root/'.github/workflows';active.mkdir(parents=True)
   plan={'pairs':128,'games':256,'time_control':'10+0.1'};body=canonical(plan);(here/'plan.json').write_bytes(body);plan_sha=sha(body)
   template=b'plan=@PLAN@ impl=@IMPLEMENTATION@ admission=@ADMISSION@\n';(here/'gameplay-workflow.disabled').write_bytes(template)
   implementation=canonical({'plan_sha256':plan_sha,'code_sha256':{'gameplay-workflow.disabled':sha(template)}});(here/'implementation.json').write_bytes(implementation);impl_sha=sha(implementation)
   target=active/'test1758-selected-vs-official-dev-001.yml';target.write_bytes(template.replace(b'@PLAN@',plan_sha.encode()).replace(b'@IMPLEMENTATION@',impl_sha.encode()).replace(b'@ADMISSION@',b'fixture-admission'))
   with patch.dict(os.environ,{'FOLLOWUP_IMPLEMENTATION_SHA256':impl_sha,'FOLLOWUP_ADMISSION_SHA256':'fixture-admission'}):
    self.assertEqual(common.verify_plan(here/'plan.json',plan_sha),plan)
    target.write_bytes(target.read_bytes()+b'changed')
    with self.assertRaises(ValueError):common.verify_plan(here/'plan.json',plan_sha)
 def test_strict_threshold_tie_parent_greater_candidate(self):
  for points in (128,128.5):
   plan,admission,result,audit,inputs=fixtures(points)
   self.assertEqual(validate_selection(plan,admission,result,audit,inputs),admission['selected_network_sha256'])
   admission['selected_network_sha256']='0'*64
   with self.assertRaises(ValueError):validate_selection(plan,admission,result,audit,inputs)
 def test_incomplete_invalid_identity_or_retry_never_opens_gate(self):
  cases=[('result','games',254),('result','status','incomplete'),('result','outcome_based_retries',1),('inputs','actual_engine_sha256','0'*64),('primary','run_attempt',2)]
  for target,key,value in cases:
   plan,admission,result,audit,inputs=fixtures();where={'result':result,'inputs':inputs,'primary':admission['primary']}[target];where[key]=value
   with self.assertRaises(ValueError):validate_selection(plan,admission,result,audit,inputs)
 def test_new_command_uses_two_exact_different_executables_full_strength(self):
  import tempfile
  with tempfile.TemporaryDirectory() as d:
   argv=command(Path(d),Path(d)/'evidence/pairs/pair-000000',{'pair_index':0,'candidate_first':True},{'experiment_id':'fixture'})
   self.assertTrue(any(x.endswith('history.exe') for x in argv));self.assertTrue(any(x.endswith('official.exe') for x in argv))
   self.assertIn('name=official-7cdd5e89',argv);self.assertIn('option.UCI_LimitStrength=false',argv)
 def test_prospective_roots_really_disjoint_and_unique(self):
  here=Path(__file__).parent;plan=json.loads((here/'plan.json').read_bytes());rows=json.loads((here/'openings.json').read_bytes());proof=json.loads((here/'opening-provenance.json').read_bytes())
  self.assertEqual(len(rows),128);self.assertEqual(len({r['opening_id'] for r in rows}),128);self.assertEqual(sha((here/'openings.json').read_bytes()),plan['openings_sha256'])
  self.assertEqual(proof['overlap_primary'],0)
  for i,row in enumerate(rows):
   self.assertEqual(row['pair_index'],i);self.assertEqual(row['candidate_first'],i%2==0)
   self.assertEqual(sha(' '.join(chess.Board(row['fen']).fen(en_passant='legal').split()[:4]).encode()),row['opening_id'])
   self.assertTrue(row['published_sources'])
 def test_zip_inventory_and_traversal(self):
  for name in ('../RESULT.json','C:/RESULT.json','x\\RESULT.json'):
   out=io.BytesIO()
   with zipfile.ZipFile(out,'w') as z:z.writestr(name,b'x')
   with zipfile.ZipFile(io.BytesIO(out.getvalue())) as z:
    with self.assertRaises(ValueError):validate_members(z,{})
  out=io.BytesIO()
  with zipfile.ZipFile(out,'w') as z:z.writestr('RESULT.json',b'x')
  with zipfile.ZipFile(io.BytesIO(out.getvalue())) as z:
   self.assertEqual(validate_members(z,{'RESULT.json':{'bytes':1,'sha256':sha(b'x')}})['RESULT.json']['bytes'],1)
   with self.assertRaises(ValueError):validate_members(z,{'RESULT.json':{'bytes':1,'sha256':'0'*64}})
 def test_clock_loss_and_color_accounting_retains_reported_outcome(self):
  row={'games':[{'candidate_white':True,'candidate_score':0,'reported_clock_forfeit':True},{'candidate_white':False,'candidate_score':1,'reported_clock_forfeit':True}]}
  out=game_accounting([row]);self.assertEqual(out['candidate_white']['candidate_reported_clock_losses'],1);self.assertEqual(out['candidate_black']['official_reported_clock_losses'],1)
 def test_clock_comment_is_preserved_even_with_generic_termination_header(self):
  games=[]
  for white in (True,False):
   g=chess.pgn.Game();g.headers.update(Event='clock-fixture',White=NAMES['candidate'] if white else NAMES['parent'],Black=NAMES['parent'] if white else NAMES['candidate'],Result='0-1',Termination='normal')
   node=g.add_variation(chess.Move.from_uci('e2e4'));node.comment='White lost on time';games.append(str(g))
  opening={'pair_index':0,'opening_id':'synthetic','fen':chess.STARTING_FEN}
  parsed=common.pair_games(('\n\n'.join(games)+'\n').encode(),opening,'clock-fixture',True)
  self.assertTrue(all(g['reported_clock_forfeit'] for g in parsed['games']));self.assertEqual(parsed['candidate_points'],1)
 def test_full256_raw_fixture_replays_and_changed_raw_fails(self):
  plan,admission,result,audit,inputs=fixtures();members={};openings=[];combined=[];proofs=[]
  for i in range(128):
   opening={'pair_index':i,'opening_id':sha(' '.join(chess.STARTING_FEN.split()[:4]).encode()),'fen':chess.STARTING_FEN,'candidate_first':i%2==0};openings.append(opening)
   event='test1758-fixed-bias-dev-001-pair-'+str(i).zfill(6);games=[]
   for color in (opening['candidate_first'],not opening['candidate_first']):
    g=chess.pgn.Game();g.headers.update(Event=event,White=NAMES['candidate'] if color else NAMES['parent'],Black=NAMES['parent'] if color else NAMES['candidate'],Result='1/2-1/2');g.add_variation(chess.Move.from_uci('e2e4'));games.append(str(g))
   pgn=('\n\n'.join(games)+'\n').encode();directory='pairs/pair-'+str(i).zfill(6)+'/'
   argv=['fixture-only-never-executed'];command_doc={'argv':argv,'sha256':sha(json.dumps(argv,separators=(',',':')).encode()),'time_control':'10+0.1','outcome_retry':False}
   raw={'COMMAND.json':canonical(command_doc),'PROCESS.json':canonical({'fixture_only':True}),'EXIT.json':canonical({'returncode':0}),'games.pgn':pgn,'referee.log':b'NNUE evaluation using nn-f92ca6117ec5.nnue nn-68752b755c93.nnue','events.ndjson':b'','console.log':b'','opening.epd':(chess.STARTING_FEN+'\n').encode()}
   saved=common.pair_games(pgn,opening,event,opening['candidate_first']);saved.update(raw_artifacts={n:{'bytes':len(b),'sha256':sha(b)} for n,b in raw.items()},pgn_sha256=sha(pgn));saved_bytes=canonical(saved);proofs.append(sha(saved_bytes))
   members.update({directory+n:b for n,b in raw.items()});members[directory+'PAIR-AUDIT.json']=saved_bytes;combined.append(pgn)
  result.update(all_pair_audits_sha256=proofs,raw_combined_pgn_sha256=sha(b'\n'.join(combined)))
  result_raw=canonical(result);audit['result_sha256']=sha(result_raw);members.update({'RESULT.json':result_raw,'INDEPENDENT-AUDIT.json':canonical(audit),'INPUTS.json':canonical(inputs),'games.pgn':b'\n'.join(combined)})
  admission['primary']['members']={n:{'bytes':len(b),'sha256':sha(b)} for n,b in members.items()}
  def zipped():
   out=io.BytesIO()
   with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED) as z:
    for n,b in members.items():z.writestr(n,b)
   return zipfile.ZipFile(io.BytesIO(out.getvalue()))
  with zipped() as z:self.assertTrue(replay_primary(plan,admission,z,common,openings)['full_raw_audit_passed'])
  members['pairs/pair-000000/games.pgn']+=b'changed'
  with zipped() as z:
   with self.assertRaises(ValueError):replay_primary(plan,admission,z,common,openings)

if __name__=='__main__':unittest.main()
