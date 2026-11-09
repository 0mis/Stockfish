"""Full raw shard reread only; the operator opens scoring after both terminals."""
import argparse,json
from pathlib import Path
from common import accounting,dump,file_ref,load_binding,pair_games,pgn_index,require,verify_native_pair

def main():
 cli=argparse.ArgumentParser();cli.add_argument('--shard',type=int,choices=(0,1),required=True);args=cli.parse_args();root=Path(__file__).resolve().parent.parent
 plan,all_rows,_=load_binding(root);rows=[x for x in all_rows if x['shard']==args.shard];folder=root/'evidence';result=json.loads((folder/'RESULT.json').read_bytes())
 require(result['status']=='all143pairs_286games_shard_complete' and result['shard']==args.shard and result['pair_indices']==[x['pair_index'] for x in rows] and not (folder/'FAILURE.json').exists(),'Wrong or incomplete shard')
 require(set(result['all_pair_audits'])=={str(x['pair_index']) for x in rows},'Missing/extra proof commitments')
 parsed=[];raws=[];counts=[0]*5
 for opening in rows:
  index=opening['pair_index'];pair=folder/'pairs'/('pair-'+str(index).zfill(6));require(file_ref(pair/'PAIR-AUDIT.json')==result['all_pair_audits'][str(index)],'Pair proof changed')
  saved=json.loads((pair/'PAIR-AUDIT.json').read_bytes())
  require(set(saved['raw_artifacts'])=={'opening.epd','COMMAND.json','PROCESS.json','EXIT.json','config.json','console.log','events.ndjson','referee.log','games.pgn'},'Unexpected native artifact inventory')
  for name,expected in saved['raw_artifacts'].items():require(file_ref(pair/name)==expected,'Raw native artifact changed')
  body=(pair/'games.pgn').read_bytes();row=pair_games(body,opening,plan['experiment']+'-pair-'+str(index).zfill(6))
  require(all(saved[key]==value for key,value in row.items()),'Independent legal paired replay differs')
  require(verify_native_pair(pair,opening,plan)==saved['native'],'Native clocks/config/load replay differs');parsed.append(row);raws.append((index,body));counts[int(row['candidate_points']*2)]+=1
 require(sum(counts)==143 and counts==result['pentanomial'] and sum(x['candidate_points'] for x in parsed)==result['candidate_points'],'Shard paired accounting differs')
 require(accounting(parsed)==result['game_accounting'],'Color/clock/WDL accounting differs')
 require((folder/'games.pgn').read_bytes()==b'\n'.join(body for _,body in raws) and file_ref(folder/'games.pgn')==result['pgn'],'Raw combined shard PGN differs')
 require(json.loads((folder/'PGN-BYTE-INDEX.json').read_bytes())==pgn_index(raws) and file_ref(folder/'PGN-BYTE-INDEX.json')==result['pgn_byte_index'],'Raw PGN byte index differs')
 dump(folder/'INDEPENDENT-AUDIT.json',{'schema':'released-composite-nov9-independent-shard-audit/1','status':'all143pairs_286games_raw_reverified','shard':args.shard,
  'result':file_ref(folder/'RESULT.json'),'pairs':143,'games':286,'all_pair_indices_verified':result['pair_indices'],'game_accounting':result['game_accounting'],
  'pentanomial':counts,'candidate_points':result['candidate_points'],'reference_points':result['reference_points'],'whole_sample_inference_pending_both_shards':True})
 dump(folder/'MEMBERS.json',{p.relative_to(folder).as_posix():file_ref(p) for p in sorted(folder.rglob('*')) if p.is_file()})
 print(json.dumps({'status':'independent_shard_raw_replay_passed','shard':args.shard,'pairs':143,'games':286,'scores_not_printed':True}),flush=True)

if __name__=='__main__':main()
