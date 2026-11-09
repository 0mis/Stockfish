"""Independently reread every default bench, perft and output hash; no execution."""
import argparse,json
from pathlib import Path
from qualify_common import dump,file_ref,parse_bench,parse_perft,require

def main():
 cli=argparse.ArgumentParser();cli.add_argument('--evidence',type=Path,required=True);args=cli.parse_args();evidence=args.evidence
 result=json.loads((evidence/'RESULT.json').read_bytes());members=json.loads((evidence/'MEMBERS.json').read_bytes())
 require(result['status']=='both_fresh_default_embedded_network_builds_qualified','Actual qualification incomplete')
 for name,expected in members.items():require(file_ref(evidence/name)==expected,'Actual artifact changed: '+name)
 sides={}
 for label in ('baseline','candidate'):
  side=result['sides'][label];records=[]
  for index in (1,2):
   parsed=parse_bench((evidence/label/('bench-'+str(index)+'.stdout')).read_text(),(evidence/label/('bench-'+str(index)+'.stderr')).read_text())
   require(parsed==side['fresh_default_benches'][index-1],'Actual default bench replay differs');records.append(parsed)
  require(records[0]['nodes']==records[1]['nodes'] and records[0]['search_signature_sha256']==records[1]['search_signature_sha256'],'Repeated default search differs')
  require(parse_perft((evidence/label/'perft4.stdout').read_text())==side['perft4'],'Actual perft replay differs')
  sides[label]={'bench_nodes':side['bench_nodes'],'search_signature_sha256':side['search_signature_sha256'],'perft4_nodes':side['perft4']['nodes'],'executable':side['executable'],'embedded_network':side['embedded_network']}
 dump(evidence/'INDEPENDENT-AUDIT.json',{'schema':'official-master-net-only-independent-build-audit/1','status':'all_raw_build_outputs_and_fresh_default_signatures_reverified',
  'result':file_ref(evidence/'RESULT.json'),'manifest':file_ref(evidence/'MEMBERS.json'),'sides':sides,'games_launched':0,'strength_claim':False})
 print(json.dumps({'status':'independently_reverified','sides':{label:{'bench_nodes':side['bench_nodes'],'signature':side['search_signature_sha256']} for label,side in sides.items()}}),flush=True)

if __name__=='__main__':main()
