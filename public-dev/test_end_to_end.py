"""Entire fixed sample with a fake referee only; no native/network execution."""
import contextlib,io,json,os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import chess,chess.pgn
import run_match,audit
from common import NAMES,sha

class EndToEndTests(unittest.TestCase):
 def test_full_fixed_run_and_independent_audit_with_fake_referee(self):
  with tempfile.TemporaryDirectory(prefix='test1758-synthetic-') as temp:
   root=Path(temp);here=root/'public-dev';here.mkdir();evidence=root/'evidence';evidence.mkdir();inputs=root/'inputs';inputs.mkdir()
   digest=sha(b'fixture-only-no-executable');names=['history.exe','official.exe','fastchess.exe','nn-selected.nnue','nn-d1ee674a62ca.nnue']
   for name in names:(inputs/name).write_bytes(b'fixture-only-no-executable')
   openings=[{'pair_index':i,'candidate_first':i%2==0,'opening_id':sha(('synthetic-fixture-'+str(i)).encode()),'fen':chess.STARTING_FEN} for i in range(128)]
   (here/'openings.json').write_text(json.dumps(openings));(here/'ADMISSION.json').write_bytes(b'synthetic-only-never-accepted-by-real-gate')
   (evidence/'INPUTS.json').write_text(json.dumps({'synthetic_only':True}))
   plan={'experiment_id':'synthetic-fixed','openings_sha256':sha((here/'openings.json').read_bytes()),'inputs':{'engine_sha256':digest,'official_engine_sha256':digest,'referee_sha256':digest,'official_network':{'sha256':digest}},'runtime_policy':{'per_pair_hard_timeout_seconds':300}}
   admission={'selected_network_sha256':digest}
   def fake_spawn(argv,root,directory,console,env):
    # The command is constructed for both arms, but never passed to an OS.
    index=int(directory.name.split('-')[-1]);opening=openings[index];games=[]
    for candidate_white in (opening['candidate_first'],not opening['candidate_first']):
     g=chess.pgn.Game();g.headers.update(Event='synthetic-fixed-pair-'+str(index).zfill(6),White=NAMES['candidate'] if candidate_white else NAMES['parent'],Black=NAMES['parent'] if candidate_white else NAMES['candidate'],Result='1/2-1/2',UTCDate='2026.11.09',UTCTime='00:00:00',Termination='normal');g.add_variation(chess.Move.from_uci('e2e4'));games.append(str(g))
    (directory/'games.pgn').write_text('\n\n'.join(games)+'\n',encoding='utf-8',newline='\n');(directory/'referee.log').write_text('NNUE evaluation using nn-selected.nnue nn-d1ee674a62ca.nnue');(directory/'events.ndjson').write_text('')
    class Fake:
     pid=1
     def wait(self,timeout):
      self.assert_timeout=timeout
      if timeout!=300:raise AssertionError('Frozen timeout changed')
      return 0
    return Fake()
   with contextlib.ExitStack() as stack:
    stack.enter_context(patch.object(run_match,'__file__',str(here/'run_match.py')));stack.enter_context(patch.object(audit,'__file__',str(here/'audit.py')))
    stack.enter_context(patch.object(run_match,'verify_plan',return_value=plan));stack.enter_context(patch.object(audit,'verify_plan',return_value=plan))
    stack.enter_context(patch.object(run_match,'accepted_local',return_value=admission));stack.enter_context(patch.object(audit,'accepted_local',return_value=admission))
    spawn=stack.enter_context(patch.object(run_match,'spawn_referee',side_effect=fake_spawn));stack.enter_context(patch.dict(os.environ,{'FOLLOWUP_IMPLEMENTATION_SHA256':'synthetic-only'}))
    stack.enter_context(patch.object(sys,'argv',['synthetic','--plan-sha256','synthetic-only']));stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
    run_match.main();audit.main()
   result=json.loads((evidence/'RESULT.json').read_bytes());independent=json.loads((evidence/'INDEPENDENT-AUDIT.json').read_bytes())
   self.assertEqual(spawn.call_count,128);self.assertEqual(result['pairs'],128);self.assertEqual(result['candidate_points'],128);self.assertEqual(result['official_points'],128);self.assertEqual(independent['games'],256)
   self.assertEqual(result['game_accounting']['candidate_white']['draws'],128);self.assertFalse((evidence/'FAILURE.json').exists())

if __name__=='__main__':unittest.main()
