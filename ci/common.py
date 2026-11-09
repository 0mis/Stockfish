"""Bound public protocol, legal paired PGN replay and exact descriptive statistics."""
import hashlib,io,json,math,os,re
from pathlib import Path
import chess,chess.pgn

NAMES={'candidate':'released-composite-ae1d-d5','reference':'official-nov9-43e01-18cd'}
PLAN_SHA='09fc4d8286b92b352a40f40d3863b56fc2190e8fb8cbf2f9d50fcdf554c7ed43'
SCHEDULE_SHA='e8a84dc8fc6cca10ec52c31a65aa074131a1887c766db36af7869b6660e07ef5'
ADMISSION_SHA='42674c4684e8b9d2f1c2535e1e34dd4e3fa827aad7d16717277c40216d2efb2b'
def require(ok,message):
 if not ok:raise ValueError(message)
def sha(raw):return hashlib.sha256(raw).hexdigest()
def canonical(obj):return json.dumps(obj,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def dump(path,obj):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 with path.open('x',encoding='utf-8',newline='\n') as f:json.dump(obj,f,sort_keys=True,indent=2,allow_nan=False);f.write('\n')
def file_ref(path):
 h=hashlib.sha256();size=0
 with Path(path).open('rb') as f:
  while block:=f.read(1024*1024):h.update(block);size+=len(block)
 return {'bytes':size,'sha256':h.hexdigest()}
def bound(path,digest):
 raw=Path(path).read_bytes();require(sha(raw)==digest,'Pinned bytes differ: '+Path(path).name);return raw
def fen4(board):return ' '.join(board.fen(en_passant='legal').split()[:4])
def root_id(board):return sha(fen4(board).encode('ascii'))

def verify_schedule(rows,plan):
 require(len(rows)==286 and [x['pair_index'] for x in rows]==list(range(286)),'Complete global schedule required')
 require(len({x['position_id'] for x in rows})==286,'Duplicate retired family')
 for i,row in enumerate(rows):
  board=chess.Board(row['fen']);require(board.is_valid() and fen4(board)==row['canonical_fen4'] and root_id(board)==row['position_id'],'Public root identity differs')
  order=sha((plan['opening_order']['order_domain']+row['position_id']).encode());color=int(sha((plan['opening_order']['color_domain']+row['position_id']).encode()),16)%2==0
  require(row['order_key']==order and row['candidate_first'] is color and row['shard']==i%2,'Frozen order/color/shard differs')
 require([x['order_key'] for x in rows]==sorted(x['order_key'] for x in rows),'Hash order differs')
 require(all(sum(x['shard']==s for x in rows)==143 for s in (0,1)),'Disjoint equal shards required')

def load_binding(root,check_implementation=True):
 root=Path(root);plan=json.loads(bound(root/'PLAN.json',PLAN_SHA));rows=json.loads(bound(root/'openings.json',SCHEDULE_SHA));verify_schedule(rows,plan)
 bound(root/'SCHEDULE-MANIFEST.json',plan['schedule_manifest_sha256'])
 if os.environ.get('GITHUB_ACTIONS')=='true':require(os.environ.get('GITHUB_RUN_ATTEMPT')=='1' and os.environ.get('GITHUB_EVENT_NAME')=='push' and os.environ.get('GITHUB_REPOSITORY')=='0mis/Stockfish' and os.environ.get('GITHUB_REF')=='refs/heads/'+plan['branch'],'Only the first authorized push run may start native activity')
 admission=json.loads(bound(root/'ROOT-ADMISSION.json',ADMISSION_SHA))
 require(admission['schema']=='released-composite-nov9-root-admission/1' and admission['freeze_commit']=='ea629f55bfdeb682f5e533faf877f81bf87c0406','Root admission identity differs')
 require(admission['native_play_admitted'] is True and admission['all_reserved_opening_pools_checked'] is True and admission['aggregate_parent_overlaps']==admission['aggregate_child_overlaps']==0,'Root aggregate admission absent')
 require(admission['plan_sha256']==PLAN_SHA and admission['schedule_sha256']==SCHEDULE_SHA and admission['root_count']==286 and admission['child_count']==10724,'Admission does not bind full sample')
 for field,key in [('root_id_sha256','root_id_stream'),('child_id_sha256','legal_one_ply_id_stream')]:require(admission[field]==plan[key]['sha256'] and file_ref(root/plan[key]['file'])['sha256']==admission[field],'Public ID stream/admission differs')
 if check_implementation:
  digest=os.environ.get('COMPOSITE_IMPLEMENTATION_SHA256','UNPINNED');impl=json.loads(bound(root/'ci/IMPLEMENTATION.json',digest))
  require(impl['plan_sha256']==PLAN_SHA and impl['schedule_sha256']==SCHEDULE_SHA and impl['admission_sha256']==ADMISSION_SHA,'Implementation binds different protocol')
  for name,value in impl['code_sha256'].items():bound(root/'ci'/name,value)
  template=(root/'ci/gameplay-workflow.disabled').read_bytes();active=(root/'.github/workflows/released-composite-nov9-dev-001.yml').read_bytes()
  require(active==template.replace(b'@IMPLEMENTATION@',digest.encode()).replace(b'@ADMISSION@',ADMISSION_SHA.encode()).replace(b'@PLAN@',PLAN_SHA.encode()),'Active workflow differs from pinned template')
 return plan,rows,admission

def pair_games(raw,opening,event):
 stream=io.StringIO(raw.decode('utf-8-sig'));records=[];points=0
 for j in (0,1):
  game=chess.pgn.read_game(stream);require(game is not None and not game.errors,'Missing or illegal actual PGN')
  require(game.headers.get('Event')==event and game.board().fen(en_passant='legal')==opening['fen'],'PGN occurrence/opening differs')
  cw=opening['candidate_first'] if j==0 else not opening['candidate_first'];white=NAMES['candidate'] if cw else NAMES['reference'];black=NAMES['reference'] if cw else NAMES['candidate']
  require((game.headers.get('White'),game.headers.get('Black'))==(white,black),'Actual reversed colors differ')
  require(game.headers.get('Result') in ('1-0','0-1','1/2-1/2') and game.headers.get('Termination') in ('normal','time forfeit'),'Incomplete/native failure or unfrozen adjudication')
  board=game.board();plies=0;comments=0
  for node in game.mainline():require(board.is_legal(node.move),'Illegal actual move');board.push(node.move);plies+=1;comments+=bool(node.comment)
  require(plies>0,'Zero-move native result invalidates experiment');score={'1-0':1,'0-1':0,'1/2-1/2':.5}[game.headers['Result']];score=score if cw else 1-score;points+=score
  records.append({'candidate_white':cw,'candidate_score':score,'result':game.headers['Result'],'plies':plies,'moves_with_comments':comments,
   'termination':game.headers['Termination'],'reported_clock_forfeit':game.headers['Termination']=='time forfeit','end_comment':game.end().comment,
   'date':game.headers.get('Date'),'time':game.headers.get('Time'),'utc_date':game.headers.get('UTCDate'),'utc_time':game.headers.get('UTCTime'),'time_control':game.headers.get('TimeControl')})
 require(chess.pgn.read_game(stream) is None and not stream.read().strip(),'Extra actual PGN game or text')
 return {'pair_index':opening['pair_index'],'position_id':opening['position_id'],'shard':opening['shard'],'candidate_first':opening['candidate_first'],'candidate_points':points,'games':records}

def accounting(pairs):
 games=[g for p in pairs for g in p['games']];out={}
 for label,rows in [('all',games),('candidate_white',[g for g in games if g['candidate_white']]),('candidate_black',[g for g in games if not g['candidate_white']])]:
  out[label]={'games':len(rows),'wins':sum(g['candidate_score']==1 for g in rows),'draws':sum(g['candidate_score']==.5 for g in rows),'losses':sum(g['candidate_score']==0 for g in rows),
   'candidate_points':sum(g['candidate_score'] for g in rows),'candidate_clock_losses':sum(g['reported_clock_forfeit'] and g['candidate_score']==0 for g in rows),
   'reference_clock_losses':sum(g['reported_clock_forfeit'] and g['candidate_score']==1 for g in rows),'normal':sum(g['termination']=='normal' for g in rows),'time_forfeit':sum(g['termination']=='time forfeit' for g in rows)}
 return out

def bootstrap(counts,n=286):
 require(len(counts)==5 and sum(counts)==n,'Complete empirical pair distribution required');coeff=[1]
 for _ in range(n):
  out=[0]*(len(coeff)+4)
  for i,value in enumerate(coeff):
   for j,count in enumerate(counts):out[i+j]+=value*count
  coeff=out
 total=n**n;require(sum(coeff)==total,'Integer polynomial normalization differs');endpoints=[];den=4*n
 for q in (1,39):
  cumulative=0
  for k,value in enumerate(coeff):
   cumulative+=value
   if cumulative*40>=q*total:break
  score=k/den;endpoints.append({'percentile_numerator':q,'percentile_denominator':40,'twice_pair_point_sum':k,'score_fraction':{'numerator':k,'denominator':den},
   'logistic_elo':None if k in (0,den) else 400*math.log10(score/(1-score)),'boundary':'negative_infinity' if k==0 else 'positive_infinity' if k==den else None})
 return {'method':'exact_integer_empirical_paired_bootstrap_percentile','iid_empirical_pair_draws':n,'nominal_central_percent':95,'empirical_pentanomial':counts,
  'coefficient_vector_sha256':sha(canonical([str(x) for x in coeff])),'endpoints':endpoints,'scope':'Descriptive for this fixed retired development population and two hosted VMs; no fresh confirmation or strength attribution to a single component.'}

def verify_native_pair(directory,opening,plan):
 directory=Path(directory);cfg=json.loads((directory/'config.json').read_bytes())
 require(cfg['games']==2 and cfg['rounds']==1 and cfg['concurrency']==1 and cfg['recover'] is False and cfg['noswap'] is False and cfg['reverse'] is False and cfg['variant']==0,'Referee schedule/recovery differs')
 require(all(cfg[key]['enabled'] is False for key in ('sprt','resign','draw','maxmoves','tb_adjudication')),'Unfrozen stopping/adjudication')
 require(cfg['opening']=={'file':'opening.epd','format':0,'order':1,'plies':-1,'start':1},'Native opening policy differs')
 require(cfg['pgn']['event_name']==plan['experiment']+'-pair-'+str(opening['pair_index']).zfill(6) and cfg['pgn']['track_timeleft'] is True and cfg['pgn']['track_latency'] is True and cfg['pgn']['min'] is False,'Native PGN/clock logging differs')
 arms=['candidate','reference'] if opening['candidate_first'] else ['reference','candidate'];require(len(cfg['engines'])==2,'Wrong engine count')
 for engine,arm in zip(cfg['engines'],arms):
  net=plan[arm]['network_filename'];options=[['EvalFile','..\\..\\..\\inputs\\'+net],['Threads','1'],['Hash','64'],['Ponder','false'],['UCI_LimitStrength','false'],['SyzygyProbeLimit','0'],['Move Overhead','10']]
  require(engine['name']==NAMES[arm] and engine['cmd']=='..\\..\\..\\inputs\\'+arm+'.exe' and engine['options']==options and engine['restart'] is True and engine['args']==engine['dir']=='','Actual engine/config/net differs')
  require(engine['limit']=={'tc':{'increment':100,'fixed_time':0,'time':10000,'moves':0,'timemargin':0},'nodes':0,'plies':0},'Actual clocks/search limits differ')
 log=(directory/'referee.log').read_text(encoding='utf-8');go=0;announcements={arm:0 for arm in arms}
 for line in log.splitlines():
  if re.search(r'\bgo\s+wtime\b',line):
   require(all(re.search(r'\b'+k+r'\s+'+v+r'\b',line) for k,v in [('wtime',r'\d+'),('btime',r'\d+'),('winc','100'),('binc','100')]) and not re.search(r'\b(depth|nodes|movetime|infinite)\b',line),'Actual real-clock command differs');go+=1
  if 'NNUE evaluation using' in line:
   matches=[arm for arm in arms if NAMES[arm]+' ---> info string NNUE evaluation using ' in line];require(len(matches)==1,'Unrecognized native NNUE announcement')
   arm=matches[0];name=re.search(r'NNUE evaluation using (\S+)',line)[1].replace('\\','/').split('/')[-1];require(name==plan[arm]['network_filename'],'Actual loaded NNUE differs');announcements[arm]+=1
 require(go>0 and sum(announcements.values())==go and all(announcements.values()),'Native clocks/network announcement coverage differs')
 return {'real_clock_go_commands':go,'actual_network_announcements':announcements}

def pgn_index(rows):
 offset=0;out=[]
 for i,(index,body) in enumerate(rows):out.append({'pair_index':index,'byte_start':offset,'byte_stop':offset+len(body),**{'bytes':len(body),'sha256':sha(body)}});offset+=len(body)+(i+1<len(rows))
 return {'schema':'ordered-public-pgn-pair-byte-index/1','pairs':len(rows),'games':2*len(rows),'bytes':offset,'rows':out}
