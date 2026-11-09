"""Run exactly128 fixed, color-reversed pairs on a public standard Windows VM.

No statistical/outcome stopping, seed choice, game recovery or pair retry.
"""
import argparse,datetime,json,os,subprocess,sys,time
from pathlib import Path
from common import require,sha,dump,bound,verify_plan,pair_games,bootstrap,NAMES
from admit_primary import accepted_local

def state(path,value):
 raw=(json.dumps(value,sort_keys=True,indent=2,allow_nan=False)+'\n').encode();temp=path.with_suffix('.tmp');temp.write_bytes(raw);os.replace(temp,path)
def relative(path,cwd):return os.path.relpath(path,cwd)
def spawn_referee(argv,root,directory,console,env):
 # On Windows, cwd does not make a relative CreateProcess executable portable.
 # Keep public logged argv relative, but pin lpApplicationName to the local
 # already-SHA-verified runner input. No user/workspace path is in public source.
 return subprocess.Popen(argv,executable=str((root/'inputs/fastchess.exe').resolve()),cwd=directory,
                         stdin=subprocess.DEVNULL,stdout=console,stderr=subprocess.STDOUT,env=env)
def command(root,directory,opening,plan):
 args=[relative(root/'inputs/fastchess.exe',directory)]
 arms=[('candidate','history.exe','nn-selected.nnue'),('parent','official.exe','nn-d1ee674a62ca.nnue')]
 if not opening['candidate_first']:arms.reverse()
 for arm,engine,net in arms:
  args+=['-engine','cmd='+relative(root/'inputs'/engine,directory),'name='+NAMES[arm],
         'option.EvalFile='+relative(root/'inputs'/net,directory)]
 args+=['-each','proto=uci','tc=10+0.1','restart=on','timemargin=0','option.Threads=1','option.Hash=64','option.Ponder=false',
        'option.UCI_LimitStrength=false','option.SyzygyProbeLimit=0','option.Move Overhead=10',
        '-openings','file=opening.epd','format=epd','order=sequential','-rounds','1','-repeat','-concurrency','1',
        '-event',plan['experiment_id']+'-pair-'+str(opening['pair_index']).zfill(6),
        '-pgnout','file=games.pgn','append=false','min=false','nodes=true','timeleft=true','latency=true',
        '-log','file=referee.log','level=trace','engine=true','append=false','realtime=false',
        '-events','file=events.ndjson','info=true','interval=1000',
        '-startup-ms','10000','-ucinewgame-ms','60000','-ping-ms','60000','-ratinginterval','2','-report','penta=true']
 return args

def main():
 cli=argparse.ArgumentParser();cli.add_argument('--plan-sha256',required=True);args=cli.parse_args();here=Path(__file__).resolve().parent;root=here.parent
 plan=verify_plan(here/'plan.json',args.plan_sha256);evidence=root/'evidence';admission=accepted_local(here,plan);require((evidence/'INPUTS.json').is_file(),'Inputs not accepted')
 openings=json.loads(bound(here/'openings.json',plan['openings_sha256']));require([r['pair_index'] for r in openings]==list(range(128)),'Occurrence schedule changed')
 for name,expected in [('history.exe',plan['inputs']['engine_sha256']),('fastchess.exe',plan['inputs']['referee_sha256']),
                       ('official.exe',plan['inputs']['official_engine_sha256']),('nn-d1ee674a62ca.nnue',plan['inputs']['official_network']['sha256']),('nn-selected.nnue',admission['selected_network_sha256'])]:
  require(sha((root/'inputs'/name).read_bytes())==expected,'Actual runtime payload changed')
 completed=[];all_pgn=[];started=datetime.datetime.now(datetime.timezone.utc).isoformat();began=time.monotonic()
 try:
  for opening in openings:
   directory=evidence/'pairs'/('pair-'+str(opening['pair_index']).zfill(6));directory.mkdir(parents=True,exist_ok=False)
   (directory/'opening.epd').write_text(opening['fen']+'\n',encoding='ascii')
   argv=command(root,directory,opening,plan);dump(directory/'COMMAND.json',{'argv':argv,'sha256':sha(json.dumps(argv,separators=(',',':')).encode()),'time_control':'10+0.1','outcome_retry':False})
   start=time.monotonic()
   with (directory/'console.log').open('xb') as console:
    # Native children receive only ordinary runtime variables, never Actions tokens.
    safe={'SYSTEMROOT','WINDIR','PATH','PATHEXT','TEMP','TMP','COMSPEC','NUMBER_OF_PROCESSORS','PROCESSOR_ARCHITECTURE','USERPROFILE'}
    child_env={k:v for k,v in os.environ.items() if k.upper() in safe};child_env['TZ']='UTC'
    process=spawn_referee(argv,root,directory,console,child_env)
    dump(directory/'PROCESS.json',{'pid':process.pid,'started_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'pair_index':opening['pair_index']})
    try:exit_code=process.wait(timeout=plan['runtime_policy']['per_pair_hard_timeout_seconds'])
    except subprocess.TimeoutExpired:
     process.kill();process.wait();dump(directory/'EXIT.json',{'returncode':process.returncode,'incomplete_timeout':True,'automatic_retry':False});raise RuntimeError('Generous hard process timeout; preserve partial pair and do not retry')
   dump(directory/'EXIT.json',{'returncode':exit_code,'finished_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'seconds':time.monotonic()-start,'automatic_retry':False})
   require(exit_code==0,'Referee returned failure; preserve partial games, no retry')
   raw=(directory/'games.pgn').read_bytes();audit=pair_games(raw,opening,plan['experiment_id']+'-pair-'+str(opening['pair_index']).zfill(6),opening['candidate_first'])
   log=(directory/'referee.log').read_text(encoding='utf-8',errors='replace')
   require('NNUE evaluation using' in log and 'nn-selected.nnue' in log and 'nn-d1ee674a62ca.nnue' in log,'Both actual NNUE selections not attested in referee log')
   artifacts={p.name:{'bytes':p.stat().st_size,'sha256':sha(p.read_bytes())} for p in sorted(directory.iterdir()) if p.is_file()}
   audit.update(raw_artifacts=artifacts,pgn_sha256=sha(raw));dump(directory/'PAIR-AUDIT.json',audit);completed.append(audit);all_pgn.append(raw)
   state(evidence/'PROGRESS.json',{'status':'fixed_sample_running','completed_pairs':len(completed),'target_pairs':128,'games':2*len(completed),'outcome_based_stopping':False})
   print(json.dumps({'completed_pairs':len(completed),'target_pairs':128,'games':2*len(completed),'status':'pair_complete'}),flush=True)
  counts=[0]*5
  for row in completed:counts[int(2*row['candidate_points'])]+=1
  points=sum(row['candidate_points'] for row in completed)
  require(sum(counts)==128,'Fixed sample incomplete');(evidence/'games.pgn').write_bytes(b'\n'.join(all_pgn));dump(evidence/'PGN-BYTE-INDEX.json',pgn_index(all_pgn))
  dump(evidence/'RESULT.json',{'schema':'test1758-selected-vs-official-public-development-result/1','status':'all128pairs_256games_complete',
   'plan_sha256':args.plan_sha256,'implementation_sha256':os.environ['FOLLOWUP_IMPLEMENTATION_SHA256'],'primary_admission_sha256':sha((here/'ADMISSION.json').read_bytes()),'selected_network_sha256':admission['selected_network_sha256'],'pairs':128,'games':256,'candidate_points':points,'official_points':256-points,'pentanomial':counts,
   'descriptive_paired_bootstrap':bootstrap(counts),'started_utc':started,'elapsed_seconds':time.monotonic()-began,
   'completed_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'pgn_byte_index_sha256':sha((evidence/'PGN-BYTE-INDEX.json').read_bytes()),'game_accounting':game_accounting(completed),'runtime':json.loads((evidence/'INPUTS.json').read_bytes()),'raw_combined_pgn_sha256':sha((evidence/'games.pgn').read_bytes()),
   'all_pair_audits_sha256':[sha((evidence/'pairs'/('pair-'+str(i).zfill(6))/'PAIR-AUDIT.json').read_bytes()) for i in range(128)],
   'scope':'Fixed development comparison on public standard GitHub Actions Windows VM; selected test1758 network with published history executable versus the exact official7cdd executable/network. Separate conditional development estimate; not pooled with primary.',
   'formal_trial4_or_alpha_evidence':False,'confirmed_strength_gain':False,'outcome_based_retries':0})
  print(json.dumps({'status':'all128pairs_256games_complete','candidate_points':points,'official_points':256-points,'pentanomial':counts}),flush=True)
 except Exception as exc:
  dump(evidence/'FAILURE.json',{'schema':'preserved-public-development-failure/1','completed_pairs':len(completed),'completed_games':2*len(completed),
   'reason':str(exc),'exception':type(exc).__name__,'automatic_retry':False,'zero_game_startup_repair_eligible':len(completed)==0 and not any((d/'games.pgn').exists() for d in (evidence/'pairs').glob('pair-*'))})
  raise

def game_accounting(completed):
 rows=[g for p in completed for g in p['games']];out={}
 for color in (True,False):
  games=[g for g in rows if g['candidate_white']==color]
  out['candidate_white' if color else 'candidate_black']={'games':len(games),'wins':sum(g['candidate_score']==1 for g in games),'draws':sum(g['candidate_score']==.5 for g in games),'losses':sum(g['candidate_score']==0 for g in games),'candidate_reported_clock_losses':sum(g['reported_clock_forfeit'] and g['candidate_score']==0 for g in games),'official_reported_clock_losses':sum(g['reported_clock_forfeit'] and g['candidate_score']==1 for g in games)}
 return out

def pgn_index(raw_pairs):
 offset=0;rows=[]
 for i,body in enumerate(raw_pairs):
  rows.append({'pair_index':i,'byte_start':offset,'byte_stop':offset+len(body),'bytes':len(body),'sha256':sha(body)})
  offset+=len(body)+(1 if i+1<len(raw_pairs) else 0)
 return {'schema':'ordered-public-pgn-pair-byte-index/1','pairs':len(rows),'games':2*len(rows),'bytes':offset,'rows':rows}

if __name__=='__main__':main()
