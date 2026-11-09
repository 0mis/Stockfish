"""Fetch exact public assets and reproduce the fixed candidate; no native startup."""
import argparse,json,os,sys,urllib.request,zipfile
from pathlib import Path
from common import require,sha,verify_plan,dump
from transfer_public import reproduce

def fetch(spec,path):
 require(not path.exists(),'New input namespace required')
 request=urllib.request.Request(spec['url'],headers={'User-Agent':'0mis-public-development-match/1'})
 h=__import__('hashlib').sha256();size=0
 with urllib.request.urlopen(request,timeout=90) as source,path.open('xb') as target:
  while block:=source.read(1024*1024):size+=len(block);require(size<=spec['bytes'],'Public asset too large');target.write(block);h.update(block)
 require(size==spec['bytes'] and h.hexdigest()==spec['sha256'],'Public asset EOF/size/SHA differs')

def main():
 cli=argparse.ArgumentParser();cli.add_argument('--plan-sha256',required=True);args=cli.parse_args();here=Path(__file__).resolve().parent
 plan=verify_plan(here/'plan.json',args.plan_sha256);root=here.parent;inputs=root/'inputs';evidence=root/'evidence'
 inputs.mkdir();evidence.mkdir()
 for label in ('engine_zip','parent_network','referee_zip'):fetch(plan['inputs'][label],inputs/plan['inputs'][label]['file'])
 for label,member,output in [('engine_zip',plan['inputs']['engine_member'],'history.exe'),('referee_zip',plan['inputs']['referee_member'],'fastchess.exe')]:
  with zipfile.ZipFile(inputs/plan['inputs'][label]['file']) as z:
   require(member in z.namelist(),'Pinned executable member absent');body=z.read(member)
  expected=plan['inputs']['engine_sha256' if label=='engine_zip' else 'referee_sha256'];require(sha(body)==expected,'Actual executable digest differs')
  (inputs/output).write_bytes(body)
 recipe=json.loads((here/'published-test1757-recipe.json').read_bytes());binding=json.loads((here/'transfer-binding.json').read_bytes())
 result=reproduce(inputs/plan['inputs']['parent_network']['file'],inputs/'nn-f92ca6117ec5.nnue',recipe,binding)
 require(result['candidate_sha256']==plan['candidate_network_sha256'],'Generated candidate differs')
 dump(evidence/'INPUTS.json',{'plan_sha256':args.plan_sha256,'all_public_asset_hashes_verified':True,'actual_engine_sha256':plan['inputs']['engine_sha256'],
  'actual_referee_sha256':plan['inputs']['referee_sha256'],'actual_parent_sha256':result['parent_sha256'],'actual_candidate_sha256':result['candidate_sha256'],
  'transfer':result,'public_inputs':plan['inputs'],'runner_environment':'standard GitHub-hosted windows-2022, public repository',
  'custom_secrets_required':False,'native_processes_started_here':0})
 print(json.dumps({'status':'public_inputs_verified','engine_sha256':plan['inputs']['engine_sha256'],'parent_sha256':result['parent_sha256'],'candidate_sha256':result['candidate_sha256']}),flush=True)

if __name__=='__main__':main()
