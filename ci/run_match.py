"""One admitted, disjoint143-pair shard. No partial scores are printed."""
import argparse,datetime,json,os,subprocess,time
from pathlib import Path
from common import ADMISSION_SHA,PLAN_SHA,SCHEDULE_SHA,NAMES,accounting,canonical,dump,file_ref,load_binding,pair_games,pgn_index,require,sha,verify_native_pair
from prepare_inputs import process_identity,safe_env

def command(root,directory,opening,plan):
 rel=lambda p:os.path.relpath(p,directory);argv=[rel(root/'inputs/fastchess.exe')]
 arms=['candidate','reference'] if opening['candidate_first'] else ['reference','candidate']
 for arm in arms:argv+=['-engine','cmd='+rel(root/'inputs'/(arm+'.exe')),'name='+NAMES[arm],'option.EvalFile='+rel(root/'inputs'/plan[arm]['network_filename'])]
 argv+=['-each','proto=uci','tc=10+0.1','restart=on','timemargin=0','option.Threads=1','option.Hash=64','option.Ponder=false','option.UCI_LimitStrength=false','option.SyzygyProbeLimit=0','option.Move Overhead=10',
  '-openings','file=opening.epd','format=epd','order=sequential','-rounds','1','-repeat','-concurrency','1','-event',plan['experiment']+'-pair-'+str(opening['pair_index']).zfill(6),
  '-pgnout','file=games.pgn','append=false','min=false','nodes=true','timeleft=true','latency=true','-log','file=referee.log','level=trace','engine=true','append=false','realtime=false',
  '-events','file=events.ndjson','info=true','interval=1000','-startup-ms','10000','-ucinewgame-ms','60000','-ping-ms','60000','-ratinginterval','2','-report','penta=true']
 return argv

def main():
 cli=argparse.ArgumentParser();cli.add_argument('--shard',type=int,choices=(0,1),required=True);args=cli.parse_args();root=Path(__file__).resolve().parent.parent
 plan,all_rows,_=load_binding(root);rows=[x for x in all_rows if x['shard']==args.shard];evidence=root/'evidence';inputs=json.loads((evidence/'INPUTS.json').read_bytes())
 require(inputs['shard']==args.shard and inputs['all_public_asset_hashes_verified'] is True and inputs['all_qualifications_before_games'] is True,'Exact host qualification absent')
 for arm in ('candidate','reference'):
  require(file_ref(root/'inputs'/(arm+'.exe'))['sha256']==plan[arm]['engine_sha256'] and file_ref(root/'inputs'/plan[arm]['network_filename'])['sha256']==plan[arm]['network_sha256'],'Runtime bundle changed after qualification')
 require(file_ref(root/'inputs/fastchess.exe')['sha256']==plan['referee']['exe_sha256'],'Referee changed')
 completed=[];raw_pairs=[];proofs={};started=datetime.datetime.now(datetime.timezone.utc).isoformat();began=time.monotonic()
 try:
  for opening in rows:
   index=opening['pair_index'];directory=evidence/'pairs'/('pair-'+str(index).zfill(6));directory.mkdir(parents=True,exist_ok=False)
   (directory/'opening.epd').write_text(opening['fen']+'\n',encoding='ascii');argv=command(root,directory,opening,plan)
   dump(directory/'COMMAND.json',{'argv':argv,'sha256':sha(canonical(argv)),'global_pair_index':index,'shard':args.shard,'time_control':'10+0.1','outcome_retry':False})
   pair_started=time.monotonic()
   with (directory/'console.log').open('xb') as console:
    process=subprocess.Popen(argv,executable=str((root/'inputs/fastchess.exe').resolve()),cwd=directory,stdin=subprocess.DEVNULL,stdout=console,stderr=subprocess.STDOUT,env=safe_env())
    dump(directory/'PROCESS.json',{'identity':process_identity(process),'pair_index':index,'shard':args.shard,'referee_sha256':plan['referee']['exe_sha256']})
    try:code=process.wait(timeout=300)
    except subprocess.TimeoutExpired:
     subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True,timeout=30);process.wait(timeout=30)
     dump(directory/'EXIT.json',{'returncode':process.returncode,'incomplete_timeout':True,'automatic_retry':False});raise RuntimeError('Pair process exceeded frozen hard limit; no replay')
   dump(directory/'EXIT.json',{'returncode':code,'seconds':time.monotonic()-pair_started,'finished_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'automatic_retry':False})
   require(code==0,'Nonzero referee exit invalidates experiment');raw=(directory/'games.pgn').read_bytes()
   parsed=pair_games(raw,opening,plan['experiment']+'-pair-'+str(index).zfill(6));native=verify_native_pair(directory,opening,plan)
   parsed.update(native=native,raw_artifacts={p.name:file_ref(p) for p in sorted(directory.iterdir()) if p.is_file()});dump(directory/'PAIR-AUDIT.json',parsed)
   proofs[str(index)]=file_ref(directory/'PAIR-AUDIT.json');completed.append(parsed);raw_pairs.append((index,raw))
   heartbeat={'status':'fixed_shard_running','shard':args.shard,'completed_pairs':len(completed),'target_pairs':143,'games':2*len(completed),'outcome_based_stopping':False}
   temporary=evidence/'PROGRESS.tmp';temporary.write_bytes(canonical(heartbeat)+b'\n');os.replace(temporary,evidence/'PROGRESS.json')
   print(json.dumps({'status':'pair_complete','shard':args.shard,'completed_pairs':len(completed),'target_pairs':143}),flush=True)
  require(len(completed)==143 and len({x['position_id'] for x in completed})==143,'Complete disjoint shard required')
  (evidence/'games.pgn').write_bytes(b'\n'.join(body for _,body in raw_pairs));dump(evidence/'PGN-BYTE-INDEX.json',pgn_index(raw_pairs))
  penta=[0]*5
  for row in completed:penta[int(2*row['candidate_points'])]+=1
  points=sum(row['candidate_points'] for row in completed)
  dump(evidence/'RESULT.json',{'schema':'released-composite-nov9-fixed-shard-result/1','status':'all143pairs_286games_shard_complete','shard':args.shard,'pairs':143,'games':286,
   'plan_sha256':PLAN_SHA,'schedule_sha256':SCHEDULE_SHA,'admission_sha256':ADMISSION_SHA,'implementation_sha256':os.environ['COMPOSITE_IMPLEMENTATION_SHA256'],
   'candidate_points':points,'reference_points':286-points,'pentanomial':penta,'game_accounting':accounting(completed),'pair_indices':[x['pair_index'] for x in completed],
   'all_pair_audits':proofs,'runtime':inputs,'started_utc':started,'completed_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'elapsed_seconds':time.monotonic()-began,
   'pgn':file_ref(evidence/'games.pgn'),'pgn_byte_index':file_ref(evidence/'PGN-BYTE-INDEX.json'),'whole_sample_uncertainty_pending_both_shards':True,
   'scope':'One disjoint part of the frozen retired development population; no separate selection, formal confirmation or weights-only attribution.'})
  print(json.dumps({'status':'fixed_shard_complete','shard':args.shard,'pairs':143,'games':286,'scores_not_printed':True}),flush=True)
 except Exception as exc:
  dump(evidence/'FAILURE.json',{'phase':'fixed_match','shard':args.shard,'error':str(exc),'exception':type(exc).__name__,'accepted_complete_pairs':len(completed),
   'accepted_complete_games':2*len(completed),'automatic_retry':False,'full_experiment_valid':False,'partial_game_files_preserved':True})
  raise

if __name__=='__main__':main()
