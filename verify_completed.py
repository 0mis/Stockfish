"""Operator independent artifact verification. Never launches a chess binary."""
import argparse,datetime,hashlib,io,json,math,os,re,sys
from pathlib import Path
import chess,chess.pgn

ENGINE='ae1d4772a71529085b26d7460e76fcf65dbe09782e55ea67ecef2e4f033c5ba5'
PARENT='68752b755c93e419e714829c4aac1bbd66e9509a96cf175be28a00164b6d7f21'
CANDIDATE='f92ca6117ec5ae7b0ca8c9242165aee06bd2417183daa4393b11f2df409e005d'
REFEREE='a7aa6762058e1f699809e9f3d1a35b609e90dbb2c3dde32d99099d641780eb9b'
PLAN='e67d024da0dc028cec1e772271ef5b074c43c67655efcbe171589eb6fac5ad95'
def require(ok,msg):
 if not ok:raise ValueError(msg)
def sha(raw):return hashlib.sha256(raw).hexdigest()
def conv(a,b):
 out=[0]*(len(a)+len(b)-1)
 for i,x in enumerate(a):
  if x:
   for j,y in enumerate(b):
    if y:out[i+j]+=x*y
 return out
def percentile(counts):
 # Independent exponentiation by squaring, rather than128linear updates.
 coeff=[1];base=counts[:];power=128
 while power:
  if power&1:coeff=conv(coeff,base)
  power//=2
  if power:base=conv(base,base)
 require(sum(coeff)==128**128,'Independent bootstrap normalization')
 endpoints=[]
 for q in (1,39):
  c=0
  for i,n in enumerate(coeff):
   c+=n
   if c*40>=q*128**128:break
  endpoints.append(i)
 digest=sha(json.dumps([str(x) for x in coeff],separators=(',',':')).encode())
 return endpoints,digest

def main():
 if os.name=='nt':
  import ctypes
  from ctypes import wintypes
  kernel=ctypes.WinDLL('kernel32',use_last_error=True);kernel.GetCurrentProcess.restype=wintypes.HANDLE
  kernel.SetPriorityClass.argtypes=[wintypes.HANDLE,wintypes.DWORD];kernel.SetProcessAffinityMask.argtypes=[wintypes.HANDLE,ctypes.c_size_t]
  handle=kernel.GetCurrentProcess();require(kernel.SetPriorityClass(handle,0x4000),'BelowNormal priority unavailable');require(kernel.SetProcessAffinityMask(handle,16),'CPU4 affinity unavailable')
 cli=argparse.ArgumentParser();cli.add_argument('--evidence',type=Path,required=True);cli.add_argument('--out',type=Path,required=True);args=cli.parse_args()
 root=Path(__file__).resolve().parent;folder=args.evidence.resolve();result=json.loads((folder/'RESULT.json').read_bytes());remote=json.loads((folder/'INDEPENDENT-AUDIT.json').read_bytes())
 plan=json.loads((root/'public-dev/plan.json').read_bytes());openings=json.loads((root/'public-dev/openings.json').read_bytes())
 require(result['plan_sha256']==sha((root/'public-dev/plan.json').read_bytes())==remote['plan_sha256']==PLAN,'Actual frozen plan binding differs')
 require(result['status']=='all128pairs_256games_complete' and result['pairs']==128 and result['games']==256 and result['outcome_based_retries']==0,'Full frozen sample is absent')
 require(remote['status']=='all128pairs_256games_raw_receipts_reverified','Hosted independent final audit is absent')
 require(not (folder/'FAILURE.json').exists(),'A native failure is retained; no valid complete result admission')
 require(len(openings)==128 and len(result['all_pair_audits_sha256'])==128,'Frozen pair count differs')
 for name,digest in plan['code_sha256'].items():require(sha((root/'public-dev'/name).read_bytes())==digest,'Frozen source bytes differ')
 sys.path.insert(0,str(root/'public-dev'))
 from run_match import command
 inputs_raw=(folder/'INPUTS.json').read_bytes();inputs=json.loads(inputs_raw)
 require(result['runtime']==inputs and inputs['all_public_asset_hashes_verified'] is True and inputs['plan_sha256']==PLAN,'Native input receipt differs')
 require(result['runtime']['actual_engine_sha256']==ENGINE and result['runtime']['actual_parent_sha256']==PARENT
         and result['runtime']['actual_candidate_sha256']==CANDIDATE and inputs['actual_referee_sha256']==REFEREE,'Actual runtime hashes differ')
 require(sorted(x.name for x in (folder/'pairs').iterdir())==['pair-'+str(i).zfill(6) for i in range(128)],'Extra or missing native pair directory')
 required_raw={'opening.epd','COMMAND.json','PROCESS.json','EXIT.json','config.json','console.log','referee.log','events.ndjson','games.pgn'}
 counts=[0]*5;points=0;wdl=[0,0,0];games_total=0;pair_hashes=[];terminations={};combined=[];clock_commands=0;move_comments=0;elapsed=0
 for i,opening in enumerate(openings):
  pair=folder/'pairs'/('pair-'+str(i).zfill(6));proof_raw=(pair/'PAIR-AUDIT.json').read_bytes();proof=json.loads(proof_raw)
  require(sha(proof_raw)==result['all_pair_audits_sha256'][i],'Per-pair metadata commitment differs')
  require(set(proof['raw_artifacts'])==required_raw and set(x.name for x in pair.iterdir())==required_raw|{'PAIR-AUDIT.json'},'Unexpected raw pair file set')
  for name,ref in proof['raw_artifacts'].items():
   data=(pair/name).read_bytes();require(len(data)==ref['bytes'] and sha(data)==ref['sha256'],'Raw native artifact differs')
  require((pair/'opening.epd').read_bytes()==(opening['fen']+'\r\n').encode('ascii'),'Exact Windows native opening occurrence differs')
  cmd=json.loads((pair/'COMMAND.json').read_bytes());expected=command(folder.parent,pair,opening,plan)
  require(cmd['argv']==expected and cmd['time_control']=='10+0.1' and cmd['outcome_retry'] is False,'Actual native argv differs from frozen driver')
  require(cmd['sha256']==sha(json.dumps(expected,separators=(',',':')).encode()),'Actual argv digest differs')
  config=json.loads((pair/'config.json').read_bytes())
  require(config['games']==2 and config['rounds']==1 and config['concurrency']==1 and config['variant']==0 and config['recover'] is False and config['noswap'] is False and config['reverse'] is False,'Retained native schedule/recovery configuration differs')
  require(all(config[key]['enabled'] is False for key in ('sprt','resign','draw','maxmoves','tb_adjudication')),'Retained native stopping/adjudication configuration differs')
  require(config['opening']=={'file':'opening.epd','format':0,'order':1,'plies':-1,'start':1},'Retained native opening configuration differs')
  require(config['pgn']['event_name']==plan['experiment_id']+'-pair-'+str(i).zfill(6) and config['pgn']['track_timeleft'] is True and config['pgn']['track_latency'] is True and config['pgn']['min'] is False,'Retained native PGN/clock configuration differs')
  arms=[('test1758-transfer-f92','nn-f92ca6117ec5.nnue'),('test1758-parent-68752','nn-68752b755c93.nnue')]
  if not opening['candidate_first']:arms.reverse()
  require(len(config['engines'])==2,'Retained native engine count differs')
  for engine,(name,net) in zip(config['engines'],arms):
   options=[['EvalFile','..\\..\\..\\inputs\\'+net],['Threads','1'],['Hash','64'],['Ponder','false'],['UCI_LimitStrength','false'],['SyzygyProbeLimit','0'],['Move Overhead','10']]
   require(engine['name']==name and engine['cmd']=='..\\..\\..\\inputs\\history.exe' and engine['dir']==engine['args']=='' and engine['restart'] is True and engine['variant']==0 and engine['options']==options,'Retained native engine/network/options differ')
   require(engine['limit']=={'tc':{'increment':100,'fixed_time':0,'time':10000,'moves':0,'timemargin':0},'nodes':0,'plies':0},'Retained native clock or limit differs')
  process=json.loads((pair/'PROCESS.json').read_bytes());end=json.loads((pair/'EXIT.json').read_bytes())
  require(process['pair_index']==i and isinstance(process['pid'],int) and process['pid']>0 and datetime.datetime.fromisoformat(process['started_utc']).utcoffset()==datetime.timedelta(0),'Actual native process identity is absent')
  require(end['returncode']==0 and end['automatic_retry'] is False and 0<end['seconds']<=plan['per_pair_hard_timeout_seconds']+5 and not end.get('incomplete_timeout',False),'Native process did not complete cleanly')
  elapsed+=end['seconds'];log=(pair/'referee.log').read_text(encoding='utf-8',errors='strict')
  require('NNUE evaluation using' in log and 'nn-f92ca6117ec5.nnue' in log and 'nn-68752b755c93.nnue' in log,'Both native network selections are not retained')
  gos=[line for line in log.splitlines() if re.search(r'\bgo\s+wtime\b',line)]
  require(gos,'Actual real-clock go commands are absent')
  for line in gos:
   require(all(re.search(r'\b'+key+r'\s+'+value+r'\b',line) for key,value in [('wtime',r'\d+'),('btime',r'\d+'),('winc','100'),('binc','100')]),'Native real clocks/increments differ')
   require(not re.search(r'\b(depth|nodes|movetime|infinite)\b',line),'Native clock was replaced with a search limit')
  clock_commands+=len(gos)
  raw=(pair/'games.pgn').read_bytes();stream=io.StringIO(raw.decode('utf-8-sig'));pair_points=0
  for j in (0,1):
   game=chess.pgn.read_game(stream);require(game is not None and not game.errors,'Illegal/missing actual PGN')
   require(game.headers.get('Termination') in ('normal','time forfeit'),'Native failure or unfrozen adjudication under the original failure policy')
   require(game.headers['Event']==plan['experiment_id']+'-pair-'+str(i).zfill(6),'PGN occurrence identity differs')
   require(game.board().fen(en_passant='legal')==opening['fen'],'Actual public retired opening differs')
   cw=(i%2==0) if j==0 else (i%2!=0);white='test1758-transfer-f92' if cw else 'test1758-parent-68752';black='test1758-parent-68752' if cw else 'test1758-transfer-f92'
   require((game.headers['White'],game.headers['Black'])==(white,black),'Actual color reversal differs')
   board=game.board();n=0
   for node in game.mainline():
    require(board.is_legal(node.move),'Illegal PGN continuation');board.push(node.move);n+=1;move_comments+=bool(node.comment)
   require(n>0 and game.headers['Result'] in ('1-0','0-1','1/2-1/2'),'Incomplete actual game')
   score={'1-0':1,'1/2-1/2':.5,'0-1':0}[game.headers['Result']];score=score if cw else 1-score
   pair_points+=score;wdl[0 if score==1 else 1 if score==.5 else 2]+=1;games_total+=1
   reason=game.headers.get('Termination',game.end().comment or game.comment);terminations[reason]=terminations.get(reason,0)+1
  require(chess.pgn.read_game(stream) is None,'Extra actual game');points+=pair_points;counts[int(2*pair_points)]+=1;pair_hashes.append(sha(raw));combined.append(raw)
 require(games_total==256 and len(pair_hashes)==128 and counts==result['pentanomial'] and points==result['candidate_points']
         and points==wdl[0]+wdl[1]/2 and 256-points==result['parent_points'],'Independent paired points/WDL differ')
 require((folder/'games.pgn').read_bytes()==b'\n'.join(combined),'Raw combined PGN differs')
 endpoints,digest=percentile(counts);b=result['descriptive_paired_bootstrap']
 require(endpoints==[x['twice_pair_point_sum'] for x in b['endpoints']] and digest==b['coefficient_vector_sha256'],'Independent polynomial percentile result differs')
 require(b['nominal_central_percent']==95 and b['iid_empirical_pair_draws']==128 and b['empirical_pentanomial']==counts,'Descriptive bootstrap scope differs')
 for q,k,item in zip((1,39),endpoints,b['endpoints']):
  require(item['percentile_numerator']==q and item['percentile_denominator']==40 and item['score_fraction']=={'numerator':k,'denominator':512},'Reported percentile fraction differs')
  if k in (0,512):require(item['logistic_elo'] is None and item['boundary']==('negative_infinity' if k==0 else 'positive_infinity'),'Boundary interval must be explicitly labeled')
  else:require(item['boundary'] is None and math.isclose(item['logistic_elo'],400*math.log10(k/(512-k)),rel_tol=1e-12,abs_tol=1e-12),'Descriptive logistic Elo endpoint differs')
 require(remote['result_sha256']==sha((folder/'RESULT.json').read_bytes()),'Hosted final audit is not bound to actual result')
 verified={'schema':'operator-independent-public-test1758-result-audit/1','status':'all128pairs_256games_independently_verified',
  'candidate_points':points,'parent_points':256-points,'wins_draws_losses':wdl,'pentanomial':counts,'bootstrap_quantile_sums':endpoints,
  'bootstrap_coefficient_sha256':digest,'raw_PGN_sha256':sha((folder/'games.pgn').read_bytes()),'result_sha256':sha((folder/'RESULT.json').read_bytes()),
  'plan_sha256':result['plan_sha256'],'actual_engine_sha256':ENGINE,'parent_network_sha256':PARENT,'candidate_network_sha256':CANDIDATE,
  'actual_referee_sha256':REFEREE,'actual_referee_clock_go_commands':clock_commands,'moves_with_raw_comments':move_comments,'summed_native_referee_seconds':elapsed,
  'game_termination_labels':terminations,'extra_games_or_outcome_retries':False,'private_formal_evidence':False,'confirmed_strength_gain':False,
  'scope':'Fixed retired-opening public standard Actions VM development comparison. Integer paired-bootstrap uncertainty is descriptive, separate from trial4.'}
 require(not args.out.exists(),'New audit receipt required');args.out.write_text(json.dumps(verified,sort_keys=True,indent=2)+'\n');print(json.dumps(verified),flush=True)

if __name__=='__main__':main()
