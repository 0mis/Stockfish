"""Freeze every retired public start family; never use an old game result."""
import hashlib,io,json,urllib.request
from pathlib import Path
import chess,chess.pgn

ROOT=Path(__file__).resolve().parent
COMMIT='c6a21a063f5a219a72f8f814bfb6287d0925171b'
EXPERIMENT='released-composite-vs-official-nov9-dev-001'
SOURCES=[('test1757-edited-screen',918097,'90475fc71ae415c4b0a141e8f43d425547a41913'),
 ('test1757-history-floor-screen',981421,'29b1763b34807648e1db46f100d5f335dddc6199'),
 ('test1757-parent-screen',935475,'b81d5b87b498536536329dce50ff5ea5000dd4e7'),
 ('test1757-readout-ridge-screen',953620,'fb9ae2f77e48cbdef39eede7f7dbdd0ed073161d'),
 ('test1757-selected-vs-official',1879333,'af0cbe990d156efad11705aacb929b4460394482')]

def require(ok,message):
 if not ok:raise ValueError(message)
def sha(raw):return hashlib.sha256(raw).hexdigest()
def dump(path,value):
 with path.open('x',encoding='utf-8',newline='\n') as f:json.dump(value,f,sort_keys=True,indent=2,allow_nan=False);f.write('\n')
def key(board):return ' '.join(board.fen(en_passant='legal').split()[:4])
def position_id(board):return sha(key(board).encode('ascii'))
def main():
 stored=ROOT/'public-inputs';stored.mkdir();families={};proofs=[];total=0
 for label,size,git_blob in SOURCES:
  path='results/'+label+'/games-utc.pgn';url='https://raw.githubusercontent.com/0mis/stockfish-weight-space-lab/'+COMMIT+'/'+path
  with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'0mis-public-retired-schedule/1'}),timeout=45) as source:raw=source.read(size+1)
  require(len(raw)==size and hashlib.sha1(b'blob '+str(size).encode()+b'\0'+raw).hexdigest()==git_blob,'Exact public PGN Git blob/EOF differs')
  (stored/(label+'.pgn')).write_bytes(raw);stream=io.StringIO(raw.decode('utf-8-sig'));count=0
  while True:
   headers=chess.pgn.read_headers(stream)
   if headers is None:break
   require(headers.get('Variant','Standard')=='Standard' and 'FEN' in headers,'Only ordinary public starting FEN headers are permitted')
   board=chess.Board(headers['FEN']);require(board.is_valid(),'Invalid public start header')
   ident=position_id(board);occurrence={'source_path':path,'game_index':count}
   if ident not in families:families[ident]={'position_id':ident,'canonical_fen4':key(board),'fen':board.fen(en_passant='legal'),'first_public_occurrence':occurrence,'public_occurrences':[]}
   families[ident]['public_occurrences'].append(occurrence);count+=1;total+=1
  proofs.append({'path':path,'url':url,'bytes':len(raw),'sha256':sha(raw),'git_blob':git_blob,'source_games':count})
 require(total==768 and len(families)==286,'Expected complete retired source population differs')
 parent_stream=('\n'.join(sorted(families))+'\n').encode('ascii');children=set()
 for item in families.values():
  board=chess.Board(item['fen'])
  for move in board.legal_moves:
   child=board.copy(stack=False);child.push(move);children.add(position_id(child))
 child_stream=('\n'.join(sorted(children))+'\n').encode('ascii')
 require(sha(parent_stream)=='a33f09108012dc4b497b3baf657de528e4f7770a1215059582eed760b4b72eb9','Public root population commitment differs')
 require(len(children)==10724 and sha(child_stream)=='12a022058499c7d97f24cbaa250928df4b88ce9a1a6b1171e452727e5e1f40a0','Public legal one-ply population commitment differs')
 (ROOT/'public-root-ids.txt').write_bytes(parent_stream);(ROOT/'public-one-ply-ids.txt').write_bytes(child_stream)
 ordered=sorted(families.values(),key=lambda row:sha((EXPERIMENT+'/order|'+row['position_id']).encode('ascii')))
 schedule=[]
 for index,item in enumerate(ordered):
  row=dict(item);row.update(pair_index=index,shard=index%2,order_key=sha((EXPERIMENT+'/order|'+item['position_id']).encode('ascii')),
   candidate_first=int(sha((EXPERIMENT+'/color|'+item['position_id']).encode('ascii')),16)%2==0)
  schedule.append(row)
 dump(ROOT/'openings.json',schedule);digest=sha((ROOT/'openings.json').read_bytes())
 manifest={'schema':'retired-public-nov9-composite-opening-freeze/1','experiment':EXPERIMENT,'public_repository':'0mis/stockfish-weight-space-lab','public_commit':COMMIT,
  'source_games':total,'distinct_legal_ep_fen4_families':286,'pairs':286,'games':572,'schedule_file':'openings.json','schedule_sha256':digest,
  'root_id_stream':{'file':'public-root-ids.txt','count':286,'bytes':len(parent_stream),'sha256':sha(parent_stream)},
  'legal_one_ply_id_stream':{'file':'public-one-ply-ids.txt','count':10724,'bytes':len(child_stream),'sha256':sha(child_stream)},
  'sources':proofs,'representative_rule':'First occurrence in the fixed listed source order; retain canonical legal-EP six-field FEN including original counters.',
  'order_rule':'Ascending SHA256(experiment+/order|+position_id); position_id=SHA256(ASCII legal-EP canonical FEN4, no newline).',
  'color_rule':'Candidate first iff integer SHA256(experiment+/color|+position_id) modulo2 is0; every pair reverses colors.',
  'shard_rule':'global pair_index modulo2;143 disjoint pairs per host; maximum2 concurrent standard windows-2022 hosts.',
  'old_outcomes_used_for_selection':False,'new_outcomes_accessed':False,'native_processes_started':0,'native_play_admitted':False,
  'scope':'Known retired public development population only; no fresh-confirmation or private-pool claim.'}
 dump(ROOT/'SCHEDULE-MANIFEST.json',manifest)
 print(json.dumps({'schedule_sha256':digest,'schedule_bytes':(ROOT/'openings.json').stat().st_size,'families':286,'source_games':total,
  'root_id_sha256':sha(parent_stream),'legal_one_ply_count':len(children),'legal_one_ply_sha256':sha(child_stream),'source_game_counts':[x['source_games'] for x in proofs]}),flush=True)

if __name__=='__main__':main()
