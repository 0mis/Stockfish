"""Independent full PGN/paired-point/interval reread; no engine execution."""
import argparse,json
from pathlib import Path
from common import require,sha,dump,bound,verify_plan,pair_games,bootstrap

def main():
 cli=argparse.ArgumentParser();cli.add_argument('--plan-sha256',required=True);args=cli.parse_args();here=Path(__file__).resolve().parent;root=here.parent;evidence=root/'evidence'
 plan=verify_plan(here/'plan.json',args.plan_sha256);result=json.loads((evidence/'RESULT.json').read_bytes());openings=json.loads(bound(here/'openings.json',plan['openings_sha256']))
 counts=[0]*5;points=0;native_games=0
 for i,opening in enumerate(openings):
  directory=evidence/'pairs'/('pair-'+str(i).zfill(6));saved=json.loads((directory/'PAIR-AUDIT.json').read_bytes())
  require(sha((directory/'PAIR-AUDIT.json').read_bytes())==result['all_pair_audits_sha256'][i],'Saved pair proof changed')
  for name,ref in saved['raw_artifacts'].items():
   body=(directory/name).read_bytes();require(len(body)==ref['bytes'] and sha(body)==ref['sha256'],'Actual referee artifact changed')
  parsed=pair_games((directory/'games.pgn').read_bytes(),opening,plan['experiment_id']+'-pair-'+str(i).zfill(6),opening['candidate_first'])
  require(all(parsed[k]==saved[k] for k in parsed),'Paired-point replay differs');points+=parsed['candidate_points'];counts[int(2*parsed['candidate_points'])]+=1;native_games+=2
 require(native_games==256 and sum(counts)==128 and points==result['candidate_points'] and 256-points==result['parent_points'] and counts==result['pentanomial'],'Actual full sample accounting differs')
 require(bootstrap(counts)==result['descriptive_paired_bootstrap'],'Exact scoped percentile uncertainty differs')
 require(sha((evidence/'games.pgn').read_bytes())==result['raw_combined_pgn_sha256'],'Combined PGN changed')
 dump(evidence/'INDEPENDENT-AUDIT.json',{'schema':'full-fixed-public-development-independent-audit/1','status':'all128pairs_256games_raw_receipts_reverified',
  'result_sha256':sha((evidence/'RESULT.json').read_bytes()),'plan_sha256':args.plan_sha256,'candidate_points':points,'parent_points':256-points,
  'pentanomial':counts,'descriptive_paired_bootstrap':bootstrap(counts),'private_formal_evidence':False,'confirmed_strength_gain':False})
 print(json.dumps({'status':'independently_verified','pairs':128,'games':256,'candidate_points':points,'parent_points':256-points,'pentanomial':counts}),flush=True)

if __name__=='__main__':main()
