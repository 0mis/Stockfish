"""Only after actual primary raw admission: fetch exact public native inputs.

No native process is started here. Both arms retain explicit external EvalFile.
"""
import argparse,json,urllib.request,zipfile
from pathlib import Path
from common import require,sha,verify_plan,dump
from transfer_public import reproduce
from admit_primary import accepted_local

def fetch(spec,path):
 require(not path.exists(),'New input namespace required')
 request=urllib.request.Request(spec['url'],headers={'User-Agent':'0mis-public-development-match/1'})
 h=__import__('hashlib').sha256();size=0
 with urllib.request.urlopen(request,timeout=120) as source,path.open('xb') as target:
  while block:=source.read(1024*1024):
   size+=len(block);require(size<=spec['bytes'],'Public asset too large');target.write(block);h.update(block)
 require(size==spec['bytes'] and h.hexdigest()==spec['sha256'],'Public asset EOF/size/SHA differs')

def main():
 cli=argparse.ArgumentParser();cli.add_argument('--plan-sha256',required=True);args=cli.parse_args();here=Path(__file__).resolve().parent
 plan=verify_plan(here/'plan.json',args.plan_sha256);root=here.parent;admission=accepted_local(here,plan);inputs=root/'inputs';evidence=root/'evidence'
 inputs.mkdir(exist_ok=False)
 for label in ('engine_zip','parent_network','referee_zip','official_zip','official_network'):fetch(plan['inputs'][label],inputs/plan['inputs'][label]['file'])
 for label,member,output,expected in [('engine_zip',plan['inputs']['engine_member'],'history.exe',plan['inputs']['engine_sha256']),('official_zip',plan['inputs']['official_member'],'official.exe',plan['inputs']['official_engine_sha256']),('referee_zip',plan['inputs']['referee_member'],'fastchess.exe',plan['inputs']['referee_sha256'])]:
  with zipfile.ZipFile(inputs/plan['inputs'][label]['file']) as z:
   require(z.namelist().count(member)==1,'Pinned executable member absent/ambiguous');body=z.read(member)
  require(sha(body)==expected,'Actual executable digest differs');(inputs/output).write_bytes(body)
 if admission['selected_network_sha256']==plan['selection']['strict_greater_than_128_points']:
  recipe=json.loads((here/'published-test1757-recipe.json').read_bytes());binding=json.loads((here/'transfer-binding.json').read_bytes())
  transfer=reproduce(inputs/plan['inputs']['parent_network']['file'],inputs/'nn-selected.nnue',recipe,binding)
  require(transfer['candidate_sha256']==admission['selected_network_sha256'],'Generated selected candidate differs')
 else:
  require(admission['selected_network_sha256']==plan['selection']['less_than_or_equal_128_points'],'Unknown selected network')
  (inputs/'nn-selected.nnue').write_bytes((inputs/plan['inputs']['parent_network']['file']).read_bytes());transfer={'unedited_parent_selected':True}
 require(sha((inputs/'nn-selected.nnue').read_bytes())==admission['selected_network_sha256'],'Actual selected network SHA differs')
 dump(evidence/'INPUTS.json',{'plan_sha256':args.plan_sha256,'all_public_asset_hashes_verified':True,'actual_engine_sha256':plan['inputs']['engine_sha256'],
  'actual_official_engine_sha256':plan['inputs']['official_engine_sha256'],'actual_official_network_sha256':plan['inputs']['official_network']['sha256'],
  'actual_referee_sha256':plan['inputs']['referee_sha256'],'actual_selected_network_sha256':admission['selected_network_sha256'],
  'primary_admission_sha256':sha((evidence/'PRIMARY-ADMISSION.json').read_bytes()),'transfer':transfer,'public_inputs':plan['inputs'],
  'runner_environment':'standard public GitHub-hosted windows-2022','custom_secrets_required':False,'native_processes_started_here':0})
 print(json.dumps({'status':'admitted_public_inputs_verified','selected_network_sha256':admission['selected_network_sha256']}),flush=True)

if __name__=='__main__':main()
