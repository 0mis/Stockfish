"""Only after Root's exact schedule admission: fetch and qualify the two bundles."""
import argparse,ctypes,datetime,hashlib,json,os,re,subprocess,time,urllib.parse,urllib.request,zipfile
from ctypes import wintypes
from pathlib import Path,PurePosixPath
from common import ADMISSION_SHA,PLAN_SHA,SCHEDULE_SHA,bound,dump,file_ref,load_binding,require,sha
from bench import parse_bench,parse_perft

def safe_env():
 allowed={'SYSTEMROOT','WINDIR','PATH','PATHEXT','TEMP','TMP','COMSPEC','NUMBER_OF_PROCESSORS','PROCESSOR_ARCHITECTURE','USERPROFILE'}
 env={k:v for k,v in os.environ.items() if k.upper() in allowed};env['TZ']='UTC';return env

def process_identity(process):
 k=ctypes.WinDLL('kernel32',use_last_error=True);k.GetProcessTimes.argtypes=[wintypes.HANDLE,*([ctypes.POINTER(wintypes.FILETIME)]*4)]
 values=[wintypes.FILETIME() for _ in range(4)];require(k.GetProcessTimes(wintypes.HANDLE(int(process._handle)),*[ctypes.byref(v) for v in values]),'Actual process start token unavailable')
 return {'pid':process.pid,'creation_filetime':(values[0].dwHighDateTime<<32)|values[0].dwLowDateTime,'observed_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}

def fetch(url,path,expected_size,expected_sha):
 req=urllib.request.Request(url,headers={'User-Agent':'0mis-released-composite-nov9/1','Accept-Encoding':'identity','Cache-Control':'no-cache'})
 h=hashlib.sha256();size=0;started=time.monotonic()
 with urllib.request.urlopen(req,timeout=120) as source,path.open('xb') as out:
  parsed=urllib.parse.urlsplit(source.url);require(parsed.scheme=='https' and parsed.hostname in ('github.com','release-assets.githubusercontent.com','objects.githubusercontent.com','data.stockfishchess.org'),'Unexpected public asset redirect')
  while block:=source.read(1024*1024):
   size+=len(block);require(size<=expected_size,'Public asset exceeds pinned byte count');out.write(block);h.update(block)
   time.sleep(max(0,size/(4*1024*1024)-(time.monotonic()-started)))
 require(size==expected_size and h.hexdigest()==expected_sha,'Actual public asset EOF/size/SHA differs')
 return {'url':url,'resolved_origin':parsed.scheme+'://'+parsed.netloc,'bytes':size,'sha256':h.hexdigest()}

def unique_member(archive,suffix,expected_sha,output):
 matches=[]
 with zipfile.ZipFile(archive) as z:
  infos=z.infolist();require(len(infos)<5000 and len({x.filename.casefold() for x in infos})==len(infos),'Archive member inventory is ambiguous')
  for info in infos:
   parts=PurePosixPath(info.filename).parts;require(not info.filename.startswith('/') and '\\' not in info.filename and ':' not in info.filename and '..' not in parts,'Unsafe public archive member')
   if info.is_dir() or not info.filename.lower().endswith(suffix):continue
   require(info.file_size<250*1024*1024,'Native archive member unexpectedly large');body=z.read(info)
   if sha(body)==expected_sha:matches.append((info.filename,body))
 require(len(matches)==1,'Exact SHA-pinned native/net member absent or ambiguous');name,raw=matches[0];output.write_bytes(raw)
 return {'archive_member':name,**file_ref(output)}

def native(engine,net,commands,prefix,work):
 directory=work/prefix.name;directory.mkdir(parents=True,exist_ok=False)
 stdin=('setoption name EvalFile value '+str(net.resolve())+'\n'+commands).encode('ascii');prefix.with_suffix('.stdin').write_bytes(stdin)
 started=time.monotonic();process=subprocess.Popen([str(engine.resolve())],cwd=directory,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=safe_env())
 identity=process_identity(process)
 try:stdout,stderr=process.communicate(stdin,timeout=180)
 except subprocess.TimeoutExpired:process.kill();stdout,stderr=process.communicate();prefix.with_suffix('.stdout').write_bytes(stdout);prefix.with_suffix('.stderr').write_bytes(stderr);raise RuntimeError('Native qualification timeout; no games or retry')
 prefix.with_suffix('.stdout').write_bytes(stdout);prefix.with_suffix('.stderr').write_bytes(stderr)
 dump(prefix.with_suffix('.process.json'),{'identity':identity,'executable_sha256':file_ref(engine)['sha256'],'network_sha256':file_ref(net)['sha256'],
  'stdin_sha256':sha(stdin),'returncode':process.returncode,'elapsed_seconds':time.monotonic()-started,'automatic_retry':False,'fresh_process':True})
 require(process.returncode==0,'Native qualification failed');return stdout.decode('utf-8'),stderr.decode('utf-8')

def main():
 cli=argparse.ArgumentParser();cli.add_argument('--shard',type=int,choices=(0,1),required=True);args=cli.parse_args();here=Path(__file__).resolve().parent;root=here.parent
 plan,rows,admission=load_binding(root);evidence=root/'evidence';evidence.mkdir(exist_ok=False);inputs=root/'inputs';inputs.mkdir();qual=evidence/'qualifications';qual.mkdir();work=root/'qualification-work';work.mkdir()
 for name in ('PLAN.json','openings.json','SCHEDULE-MANIFEST.json','ROOT-ADMISSION.json'):(evidence/name).write_bytes((root/name).read_bytes())
 timezone=subprocess.check_output(['tzutil','/g'],text=True).strip();require(timezone=='UTC','Hosted PGN date/time timezone must be UTC')
 host={'shard':args.shard,'timezone':timezone,'started_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
  'runner':{key:os.environ.get(key) for key in ('RUNNER_NAME','RUNNER_OS','RUNNER_ARCH','ImageOS','ImageVersion','GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT','GITHUB_SHA')},'processor_count':os.cpu_count()}
 dump(evidence/'HOST.json',host);assets={};members={}
 for arm in ('candidate','reference'):
  spec=plan[arm]['zip'];archive=inputs/(arm+'.zip');assets[arm]=fetch(spec['url'],archive,spec['bytes'],spec['sha256']);members[arm]=unique_member(archive,'.exe',plan[arm]['engine_sha256'],inputs/(arm+'.exe'))
 members['candidate_network']=unique_member(inputs/'candidate.zip','.nnue',plan['candidate']['network_sha256'],inputs/plan['candidate']['network_filename'])
 reference=plan['reference'];assets['reference_network']=fetch(reference['network_url'],inputs/reference['network_filename'],reference['network_bytes'],reference['network_sha256'])
 ref=plan['referee'];assets['referee']=fetch(ref['zip_url'],inputs/'referee.zip',ref['zip_bytes'],ref['zip_sha256']);members['referee']=unique_member(inputs/'referee.zip','.exe',ref['exe_sha256'],inputs/'fastchess.exe')
 qualifications={}
 for arm in ('candidate','reference'):
  engine=inputs/(arm+'.exe');net=inputs/plan[arm]['network_filename'];require(file_ref(net)=={'bytes':plan[arm]['network_bytes'],'sha256':plan[arm]['network_sha256']},'Actual configured network differs')
  stdout,_=native(engine,net,'uci\nisready\ncompiler\nquit\n',qual/(arm+'-identity'),work);require('uciok' in stdout and 'readyok' in stdout,'Native UCI readiness missing')
  records=[]
  for index in (1,2):
   stdout,stderr=native(engine,net,'bench\nquit\n',qual/(arm+'-bench-'+str(index)),work);announcements=re.findall(r'NNUE evaluation using (\S+)',stdout+stderr)
   require(announcements and all(x.replace('\\','/').split('/')[-1]==plan[arm]['network_filename'] for x in announcements),'Qualification used a different NNUE')
   record=parse_bench(stdout,stderr);expected=plan[arm]['expected_configured_default_bench'];require(record['nodes']==expected['nodes'] and record['search_signature_sha256']==expected['search_signature_sha256'],'Actual configured bench differs from the pinned bundle');records.append(record)
  stdout,_=native(engine,net,'position startpos\ngo perft 4\nquit\n',qual/(arm+'-perft4'),work);perft=parse_perft(stdout)
  qualifications[arm]={'executable':file_ref(engine),'network':file_ref(net),'fresh_configured_benches':records,'perft4':perft}
 dump(evidence/'INPUTS.json',{'schema':'whole-composite-nov9-host-input-qualification/1','shard':args.shard,'plan_sha256':PLAN_SHA,'schedule_sha256':SCHEDULE_SHA,
  'admission_sha256':ADMISSION_SHA,'all_public_asset_hashes_verified':True,'assets':assets,'archive_members':members,'qualifications':qualifications,'host':host,
  'native_match_games_started':0,'all_qualifications_before_games':True,'workspace_or_private_pool_access':False})
 print(json.dumps({'status':'both_exact_bundles_qualified_before_games','shard':args.shard,'configured_bench_nodes':{arm:qualifications[arm]['fresh_configured_benches'][0]['nodes'] for arm in qualifications}}),flush=True)

if __name__=='__main__':
 try:main()
 except Exception as exc:
  root=Path(__file__).resolve().parent.parent;evidence=root/'evidence';evidence.mkdir(exist_ok=True)
  if not (evidence/'FAILURE.json').exists():dump(evidence/'FAILURE.json',{'phase':'input_or_native_qualification','error':str(exc),'exception':type(exc).__name__,'automatic_retry':False,'match_games_started':0})
  raise
