"""Portable public experiment validation/statistics. Never reads a private ledger."""
import hashlib,io,json,math,re,os
from pathlib import Path
import chess,chess.pgn

NAMES={'candidate':'test1758-selected','parent':'official-7cdd5e89'}
SCORES={'1-0':1,'1/2-1/2':.5,'0-1':0}
def require(ok,message):
 if not ok:raise ValueError(message)
def sha(raw):return hashlib.sha256(raw).hexdigest()
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def dump(path,value):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 with path.open('x',encoding='utf-8') as f:json.dump(value,f,sort_keys=True,indent=2,allow_nan=False);f.write('\n')
def bound(path,digest):
 raw=Path(path).read_bytes();require(sha(raw)==digest,'Pinned public input bytes changed');return raw
def verify_plan(path,expected):
 raw=bound(path,expected);plan=json.loads(raw);require(plan['pairs']==128 and plan['games']==256 and plan['time_control']=='10+0.1','Fixed public sample changed')
 implementation=json.loads(bound(Path(path).parent/'implementation.json',os.environ.get('FOLLOWUP_IMPLEMENTATION_SHA256','NO_UNPINNED_IMPLEMENTATION')))
 require(implementation['plan_sha256']==expected,'Implementation binds another plan')
 for name,digest in implementation['code_sha256'].items():bound(Path(path).parent/name,digest)
 active=Path(path).parent.parent/'.github/workflows/test1758-selected-vs-official-dev-001.yml'
 template=(Path(path).parent/'gameplay-workflow.disabled').read_bytes()
 rendered=template.replace(b'@IMPLEMENTATION@',os.environ['FOLLOWUP_IMPLEMENTATION_SHA256'].encode()).replace(b'@ADMISSION@',os.environ.get('FOLLOWUP_ADMISSION_SHA256','NO_ADMISSION').encode()).replace(b'@PLAN@',expected.encode())
 require(active.read_bytes()==rendered,'Active workflow differs from hash-pinned source-only template')
 return plan
def pair_games(raw,opening,event,candidate_first):
 text=raw.decode('utf-8-sig');stream=io.StringIO(text);games=[];records=[];points=0
 for index in range(2):
  game=chess.pgn.read_game(stream);require(game is not None and not game.errors,'Missing/illegal PGN')
  require(game.headers.get('Event')==event and game.board().fen(en_passant='legal')==opening['fen'],'PGN event/opening changed')
  first=NAMES['candidate'] if candidate_first else NAMES['parent'];second=NAMES['parent'] if candidate_first else NAMES['candidate']
  white,black=(first,second) if index==0 else (second,first)
  require((game.headers['White'],game.headers['Black'])==(white,black) and game.headers['Result'] in SCORES,'Colors/result differ')
  board=game.board();plies=0
  for move in game.mainline_moves():require(board.is_legal(move),'Illegal reported PGN move');board.push(move);plies+=1
  require(plies>0,'No played moves: preserve as incomplete startup/native failure')
  result=game.headers['Result'];score=SCORES[result] if white==NAMES['candidate'] else 1-SCORES[result];points+=score
  reason=game.headers.get('Termination',game.end().comment or game.comment)
  records.append({'date':game.headers.get('Date'),'time':game.headers.get('Time'),'utc_date':game.headers.get('UTCDate'),'utc_time':game.headers.get('UTCTime'),'time_control':game.headers.get('TimeControl'),'reported_clock_forfeit':bool(re.search(r'(?:time.?forfeit|loses? on time|time.?out|time.?loss)',str(reason),re.I)),'candidate_white':white==NAMES['candidate'],'result':result,'candidate_score':score,'plies':plies,'termination':reason})
  games.append(game)
 require(chess.pgn.read_game(stream) is None and not stream.read().strip(),'Extra PGN games/text')
 return {'pair_index':opening['pair_index'],'opening_id':opening['opening_id'],'candidate_first':candidate_first,'candidate_points':points,'games':records}
def bootstrap(counts):
 require(len(counts)==5 and sum(counts)==128,'Full128-pair empirical distribution required')
 coefficients=[1]
 for _ in range(128):
  out=[0]*(len(coefficients)+4)
  for i,value in enumerate(coefficients):
   for j,count in enumerate(counts):out[i+j]+=value*count
  coefficients=out
 total=128**128;require(sum(coefficients)==total,'Bootstrap convolution normalization differs')
 endpoints=[]
 for q in (1,39):
  cumulative=0
  for index,value in enumerate(coefficients):
   cumulative+=value
   if 40*cumulative>=q*total:break
  score=index/512;elo=None if index in (0,512) else 400*math.log10(score/(1-score))
  endpoints.append({'percentile_numerator':q,'percentile_denominator':40,'twice_pair_point_sum':index,'score_fraction':{'numerator':index,'denominator':512},
                    'logistic_elo':elo,'boundary':'negative_infinity' if index==0 else 'positive_infinity' if index==512 else None})
 return {'method':'exact_integer_empirical_paired_bootstrap_percentile','nominal_central_percent':95,'iid_empirical_pair_draws':128,
         'empirical_pentanomial':counts,'quantile_rule':'smallest k with40*CDF(k)>=q*128^128, q=1,39; no interpolation',
         'coefficient_vector_sha256':sha(canonical([str(x) for x in coefficients])),'endpoints':endpoints,
         'scope':'Descriptive for this fixed retired-opening development match on standard public Actions VM; no formal confirmation or global gain inference.'}
