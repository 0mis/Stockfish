"""No native execution. Admit only the exact verified complete primary artifact.

The initial prospective branch contains no gameplay workflow. Later activation
pins an actual public-safe reviewer receipt and this input document by SHA256.
The runtime independently downloads and replays that same actual primary ZIP.
"""
import argparse,datetime,hashlib,importlib.util,io,json,os,re,sys,urllib.request,urllib.parse,zipfile
from pathlib import Path,PurePosixPath
import chess
from common import require,sha,canonical,dump,bound,verify_plan

def opening_partition(primary,followup):
 def ids(rows):
  out=[]
  for row in rows:
   board=chess.Board(row['fen']);require(board.is_valid(),'Invalid declared starting board')
   identity=sha(' '.join(board.fen(en_passant='legal').split()[:4]).encode('ascii'))
   require(identity==row['opening_id'],'Noncanonical opening identity')
   out.append(identity)
  return out
 old=ids(primary);new=ids(followup)
 require(len(new)==len(set(new)),'Follow-up roots must all be distinct')
 require(not set(old).intersection(new),'Primary/follow-up starting root overlap')
 return {'primary_unique_roots':len(set(old)),'followup_unique_roots':len(set(new)),'canonical_root_overlap':0,
         'primary_canonical_root_set_sha256':sha(canonical(sorted(set(old)))),'followup_ordered_root_ids_sha256':sha(canonical(new))}

def api_origin(url):
 parts=urllib.parse.urlsplit(url)
 return parts.scheme=='https' and parts.hostname=='api.github.com' and parts.port in (None,443) and parts.username is None and parts.password is None

class ApiOnlyAuthorizationRedirect(urllib.request.HTTPRedirectHandler):
 def __init__(self):self.audit=[]
 def redirect_request(self,req,fp,code,msg,headers,newurl):
  target=urllib.parse.urlsplit(newurl)
  require(target.scheme=='https' and bool(target.hostname) and target.username is None and target.password is None,'Non-HTTPS or credential-bearing redirect refused')
  redirected=super().redirect_request(req,fp,code,msg,headers,newurl)
  require(redirected is not None and urllib.parse.urlsplit(redirected.full_url).query==target.query,'Signed redirect query was altered')
  before=redirected.get_header('Authorization') is not None
  if not api_origin(newurl):redirected.remove_header('Authorization')
  self.audit.append({'https_only':True,'target_is_github_api_origin':api_origin(newurl),'authorization_removed':before and redirected.get_header('Authorization') is None,'signed_query_preserved':True})
  return redirected

def read_url(url,token=None,limit=16*1024*1024):
 require(urllib.parse.urlsplit(url).scheme=='https','Only HTTPS public inputs are allowed')
 headers={'User-Agent':'0mis-prospective-public-followup/1','Accept':'application/vnd.github+json'}
 if token:
  require(api_origin(url),'Authorization is restricted to the GitHub API origin');headers['Authorization']='Bearer '+token
 opener=urllib.request.build_opener(ApiOnlyAuthorizationRedirect())
 with opener.open(urllib.request.Request(url,headers=headers),timeout=120) as source:
  body=source.read(limit+1)
 require(len(body)<=limit,'Remote response exceeds declared bound')
 return body

def execution_history(own_run,history,admission,get_json):
 """Permit one actual gameplay execution; preserve proven pre-native refusals."""
 prior=admission.get('preserved_pre_native_failures',[])
 require(len(prior)<=8 and len({r['run_id'] for r in prior})==len(prior),'Ambiguous prior zero-native scope')
 expected={r['run_id'] for r in prior}|{own_run['id']}
 require(history['total_count']==len(expected) and len(history['workflow_runs'])==len(expected) and {r['id'] for r in history['workflow_runs']}==expected,'An unadmitted follow-up execution exists')
 proofs=[]
 for ref in prior:
  old=get_json('/actions/runs/'+str(ref['run_id']))
  require(old['status']=='completed' and old['conclusion']=='failure' and old['head_sha']==ref['head_sha'] and old['run_attempt']==ref['run_attempt']==1 and old['workflow_id']==own_run['workflow_id'] and old['head_branch']==own_run['head_branch'],'Prior pre-native run identity differs')
  jobs=get_json('/actions/runs/'+str(ref['run_id'])+'/jobs')
  require(jobs['total_count']==1 and len(jobs['jobs'])==1 and jobs['jobs'][0]['id']==ref['job_id'],'Prior job identity/coverage differs')
  job=jobs['jobs'][0];steps={s['name']:s for s in job['steps']}
  require(job['status']=='completed' and job['conclusion']=='failure' and steps['Verify complete primary artifact before native input preparation']['conclusion']=='failure','Prior failure was not at admission')
  skipped=['No-native fixed protocol and admission fixtures','Fetch exact public binaries and selected network','Run fixed128 reversed pairs at10plus0point1','Independently replay all256 PGNs clocks hashes and interval']
  require(all(steps[name]['status']=='completed' and steps[name]['conclusion']=='skipped' for name in skipped),'A prior native preparation or game step was entered: no retry')
  proofs.append({'run_id':old['id'],'head_sha':old['head_sha'],'run_attempt':old['run_attempt'],'job_id':job['id'],'native_input_and_game_steps_skipped':True,'actual_job_metadata_sha256':sha(canonical(job))})
 return proofs

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
 zero_native_history=execution_history(own_run,prior,admission,lambda suffix:json.loads(read_url(api+suffix,token)))
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
 primary_openings=json.loads((source_root/'openings.json').read_bytes());followup_openings=json.loads(bound(here/'openings.json',plan['openings_sha256']))
 partition=opening_partition(primary_openings,followup_openings)
 require(partition['primary_unique_roots']==plan['opening_policy']['primary_unique_roots'] and partition['followup_unique_roots']==128 and len(followup_openings)==128,'Frozen root coverage differs')
 with zipfile.ZipFile(io.BytesIO(payload)) as archive:proof=replay_primary(plan,admission,archive,module,primary_openings)
 proof['opening_partition']=partition
 proof['preserved_pre_native_failures']=zero_native_history
 proof.update(schema='test1758-actual-primary-runtime-admission/1',admission_sha256=sha(admission_raw),actual_primary_archive_sha256=sha(payload),verified_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
              primary_run_id=run['id'],primary_head_sha=run['head_sha'],primary_run_attempt=run['run_attempt'],review_receipt_sha256=sha(review_raw),native_processes_started_here=0)
 evidence=root/'evidence';evidence.mkdir(exist_ok=False);dump(evidence/'PRIMARY-ADMISSION.json',proof)
 print(json.dumps({'status':'actual_complete_primary_raw_evidence_admitted','selected_network_sha256':proof['selected_network_sha256'],'native_processes_started_here':0}),flush=True)

if __name__=='__main__':main()
