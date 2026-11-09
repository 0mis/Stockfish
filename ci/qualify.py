"""Build exact public source trees, verify embedded nets, qualify only bench/perft."""
import argparse,gzip,hashlib,json,os,platform,re,shlex,shutil,subprocess,sys,time,urllib.parse,urllib.request
from pathlib import Path
from qualify_common import canonical,dump,embedded_elf,file_ref,parse_bench,parse_perft,require,sha,source_pair

BASE='43e01e07210948e1130dc687c065d7a3742b2793'
BASE_TREE='97c77114c970b5573e33db9c1e90fbef943a6d42'
CANDIDATE='77811f10b69eb54f5a78150c33144af86731ae46'
CANDIDATE_TREE='5d3cd81e42c128a0e94a3960ea08980aa0a73296'
NETS={'baseline':('nn-18cd32e11a9c.nnue','18cd32e11a9cac7c595bc6ebf1301088da8e1f2c7b31efda88dbc74c71f5d2cf'),
 'candidate':('nn-d5c257728ba5.nnue','d5c257728ba53cb9808543ce2800080c860f1c878896cd1b17f75ed4b20aafee')}

def command(argv,cwd,timeout=120):return subprocess.check_output(argv,cwd=cwd,timeout=timeout).decode('utf-8')
def source_manifest(folder,commit,tree):
 require(command(['git','rev-parse','HEAD'],folder).strip()==commit,'Source checkout commit differs')
 require(command(['git','rev-parse','HEAD^{tree}'],folder).strip()==tree,'Source checkout tree differs')
 require(not command(['git','status','--porcelain','--untracked-files=no'],folder).strip(),'Tracked source worktree is modified')
 rows={}
 for line in command(['git','ls-tree','-r','HEAD'],folder).splitlines():
  meta,name=line.split('\t',1);mode,kind,blob=meta.split();require(kind=='blob','Unexpected source object type')
  p=folder/name;raw=p.read_bytes()
  require(hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==blob,'Actual tracked source bytes differ')
  rows[name]={'mode':mode,'git_blob':blob,**file_ref(p)}
 return rows

def download(name,expected,target):
 url='https://tests.stockfishchess.org/api/nn/'+name;req=urllib.request.Request(url,headers={'User-Agent':'0mis-net-only-qualification/1','Accept-Encoding':'identity'})
 import hashlib
 h=hashlib.sha256();size=0;started=time.monotonic()
 with urllib.request.urlopen(req,timeout=90) as response,target.open('xb') as out:
  final=response.url;require(urllib.parse.urlparse(final).scheme=='https' and urllib.parse.urlparse(final).hostname=='data.stockfishchess.org','Unexpected public net redirect')
  while block:=response.read(1024*1024):
   size+=len(block);require(size<=200*1024*1024,'Public NNUE exceeds bound');out.write(block);h.update(block)
   time.sleep(max(0,size/(4*1024*1024)-(time.monotonic()-started)))
 require(h.hexdigest()==expected,'Actual public NNUE EOF/SHA256 differs')
 return {'filename':name,'url':url,'resolved_url':final,'bytes':size,'sha256':h.hexdigest(),'download_cap_bytes_per_second':4*1024*1024}

def native(binary,stdin,folder,label,evidence):
 folder.mkdir(parents=True,exist_ok=False);require(not any(folder.iterdir()),'Fresh native working directory required')
 raw=stdin.encode('ascii');(evidence/(label+'.stdin')).write_bytes(raw)
 safe={'PATH','HOME','LANG','LC_ALL','TMPDIR','TEMP','TMP','LD_LIBRARY_PATH'};env={k:v for k,v in os.environ.items() if k in safe};env['LC_ALL']='C'
 began=time.monotonic();run=subprocess.run([str(binary.resolve())],input=raw,cwd=folder,capture_output=True,timeout=180,env=env)
 (evidence/(label+'.stdout')).write_bytes(run.stdout);(evidence/(label+'.stderr')).write_bytes(run.stderr)
 dump(evidence/(label+'.process.json'),{'argv':['../bin/'+binary.name],'stdin_sha256':sha(raw),'cwd':'fresh empty '+folder.name,
  'returncode':run.returncode,'elapsed_seconds':time.monotonic()-began,'automatic_retry':False,'evalfile_override':False})
 require(run.returncode==0,'Native qualification process failed')
 stdout,stderr=run.stdout.decode('utf-8'),run.stderr.decode('utf-8')
 require(not re.search(r'(?:ERROR:|CRITICAL ERROR|Unable to open file|Network evaluation parameters compatible with the engine must be available)',stdout+stderr),'Native error output')
 return stdout,stderr

def main():
 cli=argparse.ArgumentParser();cli.add_argument('--root',type=Path,required=True);args=cli.parse_args();root=args.root.resolve();evidence=root/'evidence';evidence.mkdir(exist_ok=False)
 here=Path(__file__).resolve().parent;plan=json.loads((here/'plan.json').read_bytes())
 require(plan['base_commit']==BASE and plan['candidate_commit']==CANDIDATE,'Fixed qualification plan differs')
 for name,digest in plan['code_sha256'].items():require(file_ref(here/name)['sha256']==digest,'Qualification source bytes differ')
 host={'platform':platform.platform(),'machine':platform.machine(),'python':sys.version,'logical_cpus':os.cpu_count(),
  'compiler':command(['g++','--version'],root),'compiler_binary':file_ref(Path(shutil.which('g++')).resolve()),
  'make':command(['make','--version'],root),'linker':command(['ld','--version'],root)}
 cpu=Path('/proc/cpuinfo').read_text();require(platform.machine()=='x86_64' and all(x in cpu.split() for x in ('avx2','bmi2')),'Standard host does not support the pinned BMI2 architecture')
 (evidence/'cpuinfo.txt').write_text(cpu);dump(evidence/'HOST.json',host)
 manifests={}
 for label,commit,tree in [('baseline',BASE,BASE_TREE),('candidate',CANDIDATE,CANDIDATE_TREE)]:manifests[label]=source_manifest(root/label,commit,tree)
 source_pair(manifests['baseline'],manifests['candidate'])
 old=(root/'baseline/src/evaluate.h').read_bytes();new=(root/'candidate/src/evaluate.h').read_bytes()
 require(old.count(b'"nn-18cd32e11a9c.nnue"')==1 and new==old.replace(b'"nn-18cd32e11a9c.nnue"',b'"nn-d5c257728ba5.nnue"',1),'Actual source change is not the sole default-net line')
 dump(evidence/'SOURCE-MANIFESTS.json',manifests);results={};networks={}
 inputs=root/'public-net-downloads';inputs.mkdir();bins=evidence/'bin';bins.mkdir();work=root/'native-fresh-work';work.mkdir()
 for label in ('baseline','candidate'):
  directory=evidence/label;directory.mkdir();source=root/label;name,expected=NETS[label]
  archive=subprocess.check_output(['git','archive','--format=tar','HEAD'],cwd=source)
  with (evidence/('corresponding-source-'+label+'.tar.gz')).open('xb') as target:
   with gzip.GzipFile(filename='',mode='wb',fileobj=target,mtime=0) as z:z.write(archive)
  networks[label]=download(name,expected,inputs/name);shutil.copyfile(inputs/name,source/'src'/name)
  capture=shlex.join([sys.executable,'-B',str(here/'pgo_capture.py'),str(source/'src/stockfish'),str(directory/'pgo')])
  build_argv=['make','-j2','profile-build','ARCH=x86-64-bmi2','COMP=gcc','PGOBENCH='+capture]
  dump(directory/'BUILD-COMMAND.json',{'argv':build_argv,'cwd':label+'/src','same_compiler':host['compiler_binary'],'pgo':True,'embedding_disabled':False})
  with (directory/'build.stdout').open('xb') as out,(directory/'build.stderr').open('xb') as err:
   build=subprocess.run(build_argv,cwd=source/'src',stdout=out,stderr=err,timeout=1800)
  require(build.returncode==0,'Fresh '+label+' build failed')
  require(source_manifest(source,BASE if label=='baseline' else CANDIDATE,BASE_TREE if label=='baseline' else CANDIDATE_TREE)==manifests[label],'Build changed tracked source')
  pgo=parse_bench((directory/'pgo.stdout').read_text(),(directory/'pgo.stderr').read_text())
  binary=bins/(label+'-stockfish');shutil.copyfile(source/'src/stockfish',binary);binary.chmod(0o755)
  nm=command(['nm','-n',str(binary)],root);(directory/'symbols.nm').write_text(nm);embed=embedded_elf(binary,nm,networks[label])
  uci,_=native(binary,'uci\nisready\nquit\n',work/(label+'-uci'),label+'-uci',directory)
  require('option name EvalFile type string default '+name in uci and 'uciok' in uci and 'readyok' in uci,'Actual default UCI network differs')
  benches=[]
  for index in (1,2):
   stdout,stderr=native(binary,'bench\nquit\n',work/(label+'-bench-'+str(index)),'bench-'+str(index),directory)
   require('NNUE evaluation using '+name in stdout+stderr,'Default bench does not attest its actual embedded NNUE')
   benches.append(parse_bench(stdout,stderr))
  require(benches[0]['nodes']==benches[1]['nodes'] and benches[0]['search_signature_sha256']==benches[1]['search_signature_sha256'],'Two fresh default benches disagree')
  stdout,_=native(binary,'position startpos\ngo perft 4\nquit\n',work/(label+'-perft4'),'perft4',directory);perft=parse_perft(stdout)
  results[label]={'commit':BASE if label=='baseline' else CANDIDATE,'tree':BASE_TREE if label=='baseline' else CANDIDATE_TREE,
   'executable':{'member':'bin/'+binary.name,**file_ref(binary)},'embedded_network':embed,'network':networks[label],
   'default_uci_option':name,'bench_nodes':benches[0]['nodes'],'search_signature_sha256':benches[0]['search_signature_sha256'],'fresh_default_benches':benches,'instrumented_pgo_bench':pgo,'perft4':perft,
   'corresponding_source':{'member':'corresponding-source-'+label+'.tar.gz',**file_ref(evidence/('corresponding-source-'+label+'.tar.gz'))}}
  print(json.dumps({'label':label,'fresh_bench_nodes':[b['nodes'] for b in benches],'signature':benches[0]['search_signature_sha256'],'perft4':perft['nodes'],'embedded_network_sha256':embed['sha256']}),flush=True)
 require(results['baseline']['perft4']==results['candidate']['perft4'],'Baseline/candidate perft differs')
 dump(evidence/'RESULT.json',{'schema':'official-master-net-only-build-qualification/1','status':'both_fresh_default_embedded_network_builds_qualified',
  'plan_sha256':file_ref(here/'plan.json')['sha256'],'base_commit':BASE,'base_tree':BASE_TREE,'candidate_commit':CANDIDATE,'candidate_tree':CANDIDATE_TREE,
  'sole_source_change':'src/evaluate.h EvalFileDefaultName nn-18cd32e11a9c.nnue to nn-d5c257728ba5.nnue','all_other_tracked_sources_and_modes_identical':True,
  'architecture':'x86-64-bmi2','compiler':host['compiler'],'compiler_binary':host['compiler_binary'],'build_target':'profile-build','sides':results,
  'formal_inputs_used':False,'games_launched':0,'elo_or_strength_claim':False,'qualification_scope':'Functional source/net/embedded-binary/default-bench/perft qualification only; not the previously released search build.'})
 dump(evidence/'MEMBERS.json',{p.relative_to(evidence).as_posix():file_ref(p) for p in sorted(evidence.rglob('*')) if p.is_file()})

if __name__=='__main__':
 try:main()
 except Exception as exc:
  folder=Path(sys.argv[sys.argv.index('--root')+1]).resolve()/'evidence'
  if folder.exists() and not (folder/'FAILURE.json').exists():dump(folder/'FAILURE.json',{'schema':'preserved-net-only-build-failure/1','error':str(exc),'exception':type(exc).__name__,'automatic_retry':False})
  raise
