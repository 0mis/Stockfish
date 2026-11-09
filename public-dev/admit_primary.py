"""No native execution. Admit only the exact verified complete primary artifact.

The initial prospective branch contains no gameplay workflow. Later activation
pins an actual public-safe reviewer receipt and this input document by SHA256.
The runtime independently downloads and replays that same actual primary ZIP.
"""
import argparse,datetime,hashlib,importlib.util,io,json,os,re,sys,urllib.request,zipfile
from pathlib import Path,PurePosixPath
from common import require,sha,canonical,dump,bound,verify_plan

def read_url(url,token=None,limit=16*1024*1024):
 headers={'User-Agent':'0mis-prospective-public-followup/1','Accept':'application/vnd.github+json'}
 if token:headers['Authorization']='Bearer '+token
 with urllib.request.urlopen(urllib.request.Request(url,headers=headers),timeout=120) as source:
  body=source.read(limit+1)
 require(len(body)<=limit,'Remote response exceeds declared bound')
 return body

def validate_selection(plan,admission,result,audit,inputs):
 selection=plan['selection'];primary=admission['primary']
 require(admission['schema']=='test1758-primary-verified-followup-admission/1' and admission['status']=='VERIFIED_COMPLETE_PRIMARY_ADMITTED','Not a verified full primary admission')
 require(primary['repository']==selection['primary_repository'] and primary['run_id']==selection['primary_run_id'] and primary['head_sha']==selection['primary_driver_commit'] and primary['run_attempt']==1,'Wrong primary execution')
 require(primary['conclusion']=='success' and primary['status']=='completed','Primary not successful and terminal')
 require(result['schema']=='test1758-transferred-bias-public-development-result/1' and result['status']=='all128pairs_256games_complete' and result['pairs']==128 and result['games']==256,'Primary sample incomplete')
 require(result['plan_sha256']==selection['primary_plan_sha256'] and audit['plan_sha256']==result['plan_sha256'],'Primary plan differs')
 require(audit['status']=='all128pairs_256games_raw_receipts_reverified','Primary independent audit incomplete')
 require(result['outcome_based_retries']==0 and not result['confirmed_strength_gain'],'Invalid primary outcome policy')
 points=result['candidate_points']
 require(type(points) in (int,float) and 0<=points<=256 and float(points*2).is_integer(),'Invalid primary point total')
 require(result['parent_points']==256-points and audit['candidate_points']==points and audit['parent_points']==256-points,'Primary scoring receipts disagree')
 require(len(result['pentanomial'])==5 and all(type(x) is int and x>=0 for x in result['pentanomial']) and sum(result['pentanomial'])==128 and sum(i*n for i,n in enumerate(result['pentanomial']))==2*points,'Primary pair distribution disagrees')
 require(result['pentanomial']==audit['pentanomial'] and result['descriptive_paired_bootstrap']==audit['descriptive_paired_bootstrap'],'Primary exact interval receipts disagree')
 expected=selection['strict_greater_than_128_points'] if points>128 else selection['less_than_or_equal_128_points']
 require(admission['selected_network_sha256']==expected and admission['primary_candidate_points']==points,'Prospective selection rule violated')
 require(inputs['all_public_asset_hashes_verified'] is True and inputs['actual_engine_sha256']==plan['inputs']['engine_sha256'] and inputs['actual_referee_sha256']==plan['inputs']['referee_sha256'],'Primary executable/referee identity differs')
 require(inputs['actual_parent_sha256']==selection['less_than_or_equal_128_points'] and inputs['actual_candidate_sha256']==selection['strict_greater_than_128_points'],'Primary network identity differs')
 require(result['runtime']==inputs,'Primary embedded input receipt differs')
 return expected

def validate_members(archive,expected):
 infos=archive.infolist();names=[i.filename for i in infos]
 require(len(names)==len(set(names)),'Duplicate ZIP members')
 actual={}
 for info in infos:
  name=info.filename;path=PurePosixPath(name)
  require(not path.is_absolute() and '..' not in path.parts and '\\' not in name and not re.match(r'^[A-Za-z]:',name),'Unsafe primary ZIP member')
  require(not info.flag_bits&1,'Encrypted primary ZIP member')
  if info.is_dir():continue
  require(info.file_size<=256*1024*1024,'Oversized primary ZIP member')
  body=archive.read(info);actual[name]={'bytes':len(body),'sha256':sha(body)}
 require(actual==expected,'Actual primary ZIP member inventory differs')
 return actual

def replay_primary(plan,admission,archive,primary_common,primary_openings):
 inventory=validate_members(archive,admission['primary']['members'])
 results=[name for name in inventory if PurePosixPath(name).name=='RESULT.json']
 require(len(results)==1,'Ambiguous primary result root');prefix=results[0][:-len('RESULT.json')]
 def raw(name):return archive.read(prefix+name)
 result=json.loads(raw('RESULT.json'));audit=json.loads(raw('INDEPENDENT-AUDIT.json'));inputs=json.loads(raw('INPUTS.json'))
 require(audit['result_sha256']==sha(raw('RESULT.json')),'Primary audit result binding changed')
 require(not any(PurePosixPath(name).name=='FAILURE.json' for name in inventory),'Failed primary evidence cannot open gate')
 selected=validate_selection(plan,admission,result,audit,inputs)
 require(len(primary_openings)==128 and len(result['all_pair_audits_sha256'])==128,'Primary occurrence coverage differs')
 counts=[0]*5;points=0;combined=[]
 for index,opening in enumerate(primary_openings):
  require(opening['pair_index']==index,'Primary occurrence order differs')
  directory='pairs/pair-'+str(index).zfill(6)+'/'
  saved_bytes=raw(directory+'PAIR-AUDIT.json');saved=json.loads(saved_bytes)
  require(sha(saved_bytes)==result['all_pair_audits_sha256'][index],'Primary pair proof hash differs')
  for name,ref in saved['raw_artifacts'].items():
   require(PurePosixPath(name).name==name and '\\' not in name,'Invalid primary raw name')
   body=raw(directory+name);require(len(body)==ref['bytes'] and sha(body)==ref['sha256'],'Primary raw bytes differ')
  require({'COMMAND.json','PROCESS.json','EXIT.json','games.pgn','referee.log','events.ndjson','console.log','opening.epd'}.issubset(saved['raw_artifacts']),'Missing primary runtime evidence')
  require(json.loads(raw(directory+'EXIT.json'))['returncode']==0,'Primary referee failed')
  command=json.loads(raw(directory+'COMMAND.json'));require(command['time_control']=='10+0.1' and command['outcome_retry'] is False,'Primary command policy differs')
  require(sha(json.dumps(command['argv'],separators=(',',':')).encode())==command['sha256'],'Primary command digest differs')
  log=raw(directory+'referee.log').decode('utf-8',errors='replace')
  require('NNUE evaluation using' in log and 'nn-f92ca6117ec5.nnue' in log and 'nn-68752b755c93.nnue' in log,'Primary actual network acknowledgements missing')
  pgn=raw(directory+'games.pgn');parsed=primary_common.pair_games(pgn,opening,'test1758-fixed-bias-dev-001-pair-'+str(index).zfill(6),opening['candidate_first'])
  require(all(parsed[k]==saved[k] for k in parsed),'Primary legal PGN/point replay differs')
  counts[int(2*parsed['candidate_points'])]+=1;points+=parsed['candidate_points'];combined.append(pgn)
 require(points==result['candidate_points'] and counts==result['pentanomial'],'Full primary raw score differs')
 require(primary_common.bootstrap(counts)==result['descriptive_paired_bootstrap'],'Exact primary128-draw polynomial differs')
 require(raw('games.pgn')==b'\n'.join(combined) and sha(raw('games.pgn'))==result['raw_combined_pgn_sha256'],'Primary combined PGN differs')
 return {'selected_network_sha256':selected,'primary_candidate_points':points,'primary_pairs':128,'primary_games':256,'full_raw_audit_passed':True,
         'primary_result_sha256':sha(raw('RESULT.json')),'primary_independent_audit_sha256':sha(raw('INDEPENDENT-AUDIT.json')),'primary_inputs_sha256':sha(raw('INPUTS.json')),
         'primary_actual_members':inventory,'primary_descriptive_paired_bootstrap':result['descriptive_paired_bootstrap']}

def accepted_local(here,plan):
 admission=json.loads(bound(here/'ADMISSION.json',os.environ.get('FOLLOWUP_ADMISSION_SHA256','NO_UNPINNED_ADMISSION')))
 require(admission['followup_plan_sha256']==sha((here/'plan.json').read_bytes()),'Admission binds another follow-up plan')
 proof=json.loads((here.parent/'evidence/PRIMARY-ADMISSION.json').read_bytes())
 require(proof['admission_sha256']==sha((here/'ADMISSION.json').read_bytes()) and proof['full_raw_audit_passed'] is True and proof['actual_primary_archive_sha256']==admission['primary']['artifact']['sha256'],'No actual complete primary raw admission')
 require(proof['selected_network_sha256']==admission['selected_network_sha256'] and proof['primary_candidate_points']==admission['primary_candidate_points'],'Admitted selection changed')
 expected=plan['selection']['strict_greater_than_128_points'] if proof['primary_candidate_points']>128 else plan['selection']['less_than_or_equal_128_points']
 require(proof['selected_network_sha256']==expected,'Local selection violates frozen rule')
 return proof

def main():
 cli=argparse.ArgumentParser();cli.add_argument('--plan-sha256',required=True);args=cli.parse_args();here=Path(__file__).resolve().parent;root=here.parent
 plan=verify_plan(here/'plan.json',args.plan_sha256)
 admission_raw=bound(here/'ADMISSION.json',os.environ.get('FOLLOWUP_ADMISSION_SHA256','NO_ADMISSION'));admission=json.loads(admission_raw)
 require(admission['followup_plan_sha256']==args.plan_sha256,'Wrong follow-up admission')
 # The actual independent reviewer document is immutable and hash-bound here.
 review=admission['review'];review_raw=bound(here/review['file'],review['sha256']);require(len(review_raw)==review['bytes'],'Reviewer receipt size differs')
 require(os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('GITHUB_REPOSITORY')=='0mis/Stockfish' and os.environ.get('RUNNER_OS')=='Windows' and os.environ.get('GITHUB_RUN_ATTEMPT')=='1','Only one first-attempt standard public Windows Actions execution is allowed')
 token=os.environ.get('GITHUB_TOKEN');require(bool(token),'Read-only artifact API token unavailable')
 api='https://api.github.com/repos/0mis/Stockfish'
 run=json.loads(read_url(api+'/actions/runs/'+str(plan['selection']['primary_run_id']),token))
 require(run['status']=='completed' and run['conclusion']=='success' and run['head_sha']==plan['selection']['primary_driver_commit'] and run['run_attempt']==1,'Actual primary run not admitted terminal')
 require(run['repository']['private'] is False,'Private/paid-run scope refused')
 # A second workflow execution may never repeat the follow-up sample.
 own_run=json.loads(read_url(api+'/actions/runs/'+os.environ['GITHUB_RUN_ID'],token))
 require(own_run['repository']['private'] is False and own_run['head_branch']==plan['branch'],'Wrong follow-up repository/branch')
 prior=json.loads(read_url(api+'/actions/workflows/'+str(own_run['workflow_id'])+'/runs?branch='+plan['branch']+'&per_page=100',token))
 require(prior['total_count']==1 and len(prior['workflow_runs'])==1 and str(prior['workflow_runs'][0]['id'])==os.environ['GITHUB_RUN_ID'],'Previous follow-up execution exists: no outcome retries')
 artifact=admission['primary']['artifact'];metadata=json.loads(read_url(api+'/actions/artifacts/'+str(artifact['id']),token))
 require(metadata['expired'] is False and metadata['name']==artifact['name'] and metadata['workflow_run']['id']==plan['selection']['primary_run_id'] and metadata['workflow_run']['head_sha']==plan['selection']['primary_driver_commit'],'Actual primary artifact ownership differs')
 require(0<artifact['bytes']<=512*1024*1024,'Unbounded primary artifact')
 payload=read_url(api+'/actions/artifacts/'+str(artifact['id'])+'/zip',token,artifact['bytes'])
 require(len(payload)==artifact['bytes'] and sha(payload)==artifact['sha256'],'Actual primary ZIP size/SHA differs')
 # Import only the published, pinned original pure parser/statistics implementation.
 source_root=root/'primary-audit-source';source_root.mkdir(exist_ok=False)
 source_files={name:plan['implementation_ancestry']['public_harness_code_sha256'][name] for name in ('common.py','openings.json')}
 for name,digest in source_files.items():
  body=read_url('https://raw.githubusercontent.com/0mis/Stockfish/'+plan['selection']['primary_driver_commit']+'/public-dev/'+name)
  require(sha(body)==digest,'Published primary audit source changed');(source_root/name).write_bytes(body)
 spec=importlib.util.spec_from_file_location('pinned_primary_common',source_root/'common.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 with zipfile.ZipFile(io.BytesIO(payload)) as archive:proof=replay_primary(plan,admission,archive,module,json.loads((source_root/'openings.json').read_bytes()))
 proof.update(schema='test1758-actual-primary-runtime-admission/1',admission_sha256=sha(admission_raw),actual_primary_archive_sha256=sha(payload),verified_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
              primary_run_id=run['id'],primary_head_sha=run['head_sha'],primary_run_attempt=run['run_attempt'],review_receipt_sha256=sha(review_raw),native_processes_started_here=0)
 evidence=root/'evidence';evidence.mkdir(exist_ok=False);dump(evidence/'PRIMARY-ADMISSION.json',proof)
 print(json.dumps({'status':'actual_complete_primary_raw_evidence_admitted','selected_network_sha256':proof['selected_network_sha256'],'native_processes_started_here':0}),flush=True)

if __name__=='__main__':main()
