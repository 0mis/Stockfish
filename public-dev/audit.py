"""Independent full PGN/paired-point/interval reread; no engine execution."""
import argparse,json,os
from pathlib import Path
from common import require,sha,dump,bound,verify_plan,pair_games,bootstrap
from admit_primary import accepted_local
from run_match import game_accounting,pgn_index

def main():
 cli=argparse.ArgumentParser();cli.add_argument('--plan-sha256',required=True);args=cli.parse_args();here=Path(__file__).resolve().parent;root=here.parent;evidence=root/'evidence'
 plan=verify_plan(here/'plan.json',args.plan_sha256);admission=accepted_local(here,plan);result=json.loads((evidence/'RESULT.json').read_bytes());openings=json.loads(bound(here/'openings.json',plan['openings_sha256']))
 require(result['status']=='all128pairs_256games_complete' and result['pairs']==128 and result['games']==256 and result['plan_sha256']==args.plan_sha256 and result['selected_network_sha256']==admission['selected_network_sha256'],'Wrong or incomplete final')
 require(not (evidence/'FAILURE.json').exists(),'Failed match cannot pass')
 counts=[0]*5;points=0;native_games=0;replayed=[];combined=[]
 for i,opening in enumerate(openings):
  directory=evidence/'pairs'/('pair-'+str(i).zfill(6));saved=json.loads((directory/'PAIR-AUDIT.json').read_bytes())
  require(sha((directory/'PAIR-AUDIT.json').read_bytes())==result['all_pair_audits_sha256'][i],'Saved pair proof changed')
  for name,ref in saved['raw_artifacts'].items():
   body=(directory/name).read_bytes();require(len(body)==ref['bytes'] and sha(body)==ref['sha256'],'Actual referee artifact changed')
  parsed=pair_games((directory/'games.pgn').read_bytes(),opening,plan['experiment_id']+'-pair-'+str(i).zfill(6),opening['candidate_first'])
  require(all(parsed[k]==saved[k] for k in parsed),'Paired-point replay differs');points+=parsed['candidate_points'];counts[int(2*parsed['candidate_points'])]+=1;native_games+=2;replayed.append(parsed);combined.append((directory/'games.pgn').read_bytes())
 require(native_games==256 and sum(counts)==128 and points==result['candidate_points'] and 256-points==result['official_points'] and counts==result['pentanomial'],'Actual full sample accounting differs')
 require(bootstrap(counts)==result['descriptive_paired_bootstrap'],'Exact scoped percentile uncertainty differs')
 require(result['game_accounting']==game_accounting(replayed),'Color/clock accounting differs')
 require(sha((evidence/'PGN-BYTE-INDEX.json').read_bytes())==result['pgn_byte_index_sha256'] and json.loads((evidence/'PGN-BYTE-INDEX.json').read_bytes())==pgn_index(combined),'PGN byte index differs')
 require((evidence/'games.pgn').read_bytes()==b'\n'.join(combined),'Combined PGN is not exact ordered pair concatenation')
 require(sha((evidence/'games.pgn').read_bytes())==result['raw_combined_pgn_sha256'],'Combined PGN changed')
 dump(evidence/'INDEPENDENT-AUDIT.json',{'schema':'full-fixed-public-development-independent-audit/1','status':'all128pairs_256games_raw_receipts_reverified',
  'result_sha256':sha((evidence/'RESULT.json').read_bytes()),'plan_sha256':args.plan_sha256,'candidate_points':points,'official_points':256-points,
  'pairs':128,'games':256,'game_accounting':game_accounting(replayed),'selected_network_sha256':admission['selected_network_sha256'],'primary_admission_sha256':sha((here/'ADMISSION.json').read_bytes()),'pentanomial':counts,'descriptive_paired_bootstrap':bootstrap(counts),'private_formal_evidence':False,'confirmed_strength_gain':False})
 print(json.dumps({'status':'independently_verified','pairs':128,'games':256,'candidate_points':points,'official_points':256-points,'pentanomial':counts}),flush=True)

if __name__=='__main__':main()
