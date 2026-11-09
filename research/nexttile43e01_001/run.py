"""One bounded hosted-only PR7294 port diagnostic. GPL-3.0-or-later."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.request
from qualify_common import dump,embedded_elf,file_ref,parse_bench,parse_perft,require,sha
from policy import legal_bench,reverse_port,roster,timing_gate

HERE=Path(__file__).resolve().parent


def command(argv,cwd):return subprocess.check_output(argv,cwd=cwd,timeout=120).decode()


def manifest(folder,pin):
    require(command(['git','rev-parse','HEAD'],folder).strip()==pin['commit'],'Source commit differs')
    require(command(['git','rev-parse','HEAD^{tree}'],folder).strip()==pin['tree'],'Source tree differs')
    require(not command(['git','status','--porcelain','--untracked-files=no'],folder).strip(),'Tracked source modified')
    rows={}
    for line in command(['git','ls-tree','-r','HEAD'],folder).splitlines():
        meta,name=line.split('\t',1);mode,kind,blob=meta.split();require(kind=='blob','Unexpected source object')
        raw=(folder/name).read_bytes();require(hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==blob,'Actual tracked source bytes differ')
        rows[name]={'mode':mode,'git_blob':blob,'bytes':len(raw),'sha256':sha(raw)}
    return rows


def native(binary,commands,work,evidence,name,cpu,timeout=90):
    folder=work/name;folder.mkdir();raw=commands.encode('ascii');(evidence/(name+'.stdin')).write_bytes(raw)
    argv=['taskset','-c',str(cpu),str(binary.resolve())]
    env={k:v for k,v in os.environ.items() if k in {'PATH','HOME','LANG','LC_ALL','TMPDIR','TEMP','TMP','LD_LIBRARY_PATH'}};env['LC_ALL']='C'
    before=time.monotonic_ns()
    with (evidence/(name+'.stdout')).open('xb') as out,(evidence/(name+'.stderr')).open('xb') as err:
        run=subprocess.run(argv,input=raw,cwd=folder,stdout=out,stderr=err,timeout=timeout,env=env)
    after=time.monotonic_ns();record={'argv':argv,'stdin_sha256':sha(raw),'returncode':run.returncode,'monotonic_start_ns':before,'monotonic_end_ns':after,'monotonic_wall_ns':after-before,'cpu_affinity':[cpu],'automatic_retry':False,'cwd':'fresh empty '+name}
    dump(evidence/(name+'.process.json'),record);require(run.returncode==0,'Native process failed: '+name)
    stdout=(evidence/(name+'.stdout')).read_text();stderr=(evidence/(name+'.stderr')).read_text()
    require(not re.search(r'ERROR:|CRITICAL ERROR|Unable to open file|Unknown command|No such option',stdout+stderr,re.I),'Native error: '+name)
    return stdout,stderr,record


def main():
    cli=argparse.ArgumentParser();cli.add_argument('--root',type=Path,required=True);cli.add_argument('--expected-plan-sha',required=True);args=cli.parse_args()
    require(os.environ.get('GITHUB_ACTIONS')=='true','Native execution is hosted-only')
    root=args.root.resolve();evidence=root/'evidence';evidence.mkdir();started=time.monotonic()
    raw=(HERE/'plan.json').read_bytes();require(sha(raw)==args.expected_plan_sha,'Frozen plan differs');plan=json.loads(raw)
    for name,digest in plan['code_sha256'].items():require(file_ref(HERE/name)['sha256']==digest,'Reviewed driver bytes differ: '+name)
    allowed=sorted(os.sched_getaffinity(0));cpu=allowed[0]
    cpuinfo=Path('/proc/cpuinfo').read_text();require(platform.machine()=='x86_64' and all(flag in cpuinfo.split() for flag in ('avx2','bmi2')),'Hosted target lacks AVX2/BMI2')
    (evidence/'cpuinfo.txt').write_text(cpuinfo)
    host={'platform':platform.platform(),'kernel':platform.release(),'logical_cpus':os.cpu_count(),'allowed_cpus':allowed,'native_cpu':cpu,'python':sys.version,'compiler':command(['g++','--version'],root),'compiler_binary':file_ref(Path(shutil.which('g++')).resolve()),'make':command(['make','--version'],root),'linker':command(['ld','--version'],root),'loadavg_before':list(os.getloadavg()),'runner_image':{key:os.environ.get(key) for key in ('ImageOS','ImageVersion','RUNNER_ARCH','RUNNER_OS')}}
    dump(evidence/'HOST.json',host);(evidence/'lscpu.json').write_text(command(['lscpu','--json'],root))
    sources={'A':root/'baseline','B':root/'candidate'};manifests={side:manifest(folder,plan['source'][side]) for side,folder in sources.items()}
    require(set(manifests['A'])==set(manifests['B']),'Source tracked paths differ')
    changed=[name for name in manifests['A'] if manifests['A'][name]!=manifests['B'][name]]
    require(changed==['AUTHORS','src/nnue/nnue_accumulator.cpp'] and all(manifests['A'][n]['mode']==manifests['B'][n]['mode'] for n in manifests['A']),'Changes exceed attributed prefetch port')
    before=(sources['A']/'src/nnue/nnue_accumulator.cpp').read_bytes();after=(sources['B']/'src/nnue/nnue_accumulator.cpp').read_bytes()
    require(reverse_port(after)==before,'Non-prefetch accumulator dataflow changed')
    require((sources['B']/'AUTHORS').read_bytes()==(sources['A']/'AUTHORS').read_bytes().replace(b'Maciej \xc5\xbbenczykowski (zenczykowski)\n',b'Maciej \xc5\xbbenczykowski (zenczykowski)\nMaksym Nikbakht (MxTriX77)\n',1),'AUTHORS credit differs')
    dump(evidence/'SOURCE-MANIFESTS.json',manifests);roots=roster((sources['A']/'src/benchmark.cpp').read_text())
    download=root/'public-stock-network.nnue';net=plan['network'];h=hashlib.sha256();size=0;began=time.monotonic()
    with urllib.request.urlopen(urllib.request.Request(net['url'],headers={'User-Agent':'0mis-attributed-prefetch-diagnostic'}),timeout=90) as response,download.open('xb') as out:
        resolved=response.url
        while block:=response.read(1024*1024):
            size+=len(block);require(size<=net['bytes'],'Network exceeds pinned bytes');h.update(block);out.write(block);time.sleep(max(0,size/(4*1024*1024)-(time.monotonic()-began)))
    require(size==net['bytes'] and h.hexdigest()==net['sha256'],'Actual stock network EOF/SHA differs');dump(evidence/'NETWORK.json',{**net,'resolved_url':resolved,'actual_bytes':size,'actual_sha256':h.hexdigest()})
    bins=evidence/'bin';bins.mkdir();work=root/'fresh-native-work';work.mkdir();sides={};reference=None
    setup='uci\nsetoption name NumaPolicy value none\nsetoption name Threads value 1\nsetoption name Hash value 16\nsetoption name MultiPV value 1\nsetoption name Ponder value false\nsetoption name SyzygyPath value <empty>\nisready\n'
    bench_commands=setup+'bench 16 1 13 default depth\nquit\n'
    for side,source in sources.items():
        folder=evidence/side;folder.mkdir();shutil.copyfile(download,source/'src'/net['filename'])
        archive=subprocess.check_output(['git','archive','--format=tar','HEAD'],cwd=source)
        with (folder/'corresponding-source.tar.gz').open('xb') as out:
            with gzip.GzipFile(filename='',fileobj=out,mode='wb',mtime=0) as z:z.write(archive)
        wrapper=shlex.join(['taskset','-c',str(cpu),sys.executable,'-B',str(HERE/'pgo_capture.py'),str(source/'src/stockfish'),str(folder/'pgo')])
        argv=['make','-j2','profile-build','ARCH=x86-64-bmi2','COMP=gcc','COMPCXX=g++','PGOBENCH='+wrapper]
        dump(folder/'BUILD-COMMAND.json',{'argv':argv,'compiler_binary':host['compiler_binary'],'standard_default_profile_inputs':True,'source':plan['source'][side]})
        build_env={key:value for key,value in os.environ.items() if key in {'PATH','HOME','LANG','LC_ALL','TMPDIR','TEMP','TMP','LD_LIBRARY_PATH'}};build_env['LC_ALL']='C'
        with (folder/'build.stdout').open('xb') as out,(folder/'build.stderr').open('xb') as err:
            build=subprocess.run(argv,cwd=source/'src',stdout=out,stderr=err,timeout=900,env=build_env)
        require(build.returncode==0,'Fresh PGO build failed: '+side)
        text=(folder/'build.stdout').read_text();require('-DUSE_PEXT' in text and '-DUSE_AVX2' in text and '-DNO_PREFETCH' not in text,'Actual BMI2 prefetch gate flags differ')
        pgo=parse_bench((folder/'pgo.stdout').read_text(),(folder/'pgo.stderr').read_text());require(pgo['nodes']==plan['functional']['bench_nodes'],'PGO default bench node regression')
        require(manifest(source,plan['source'][side])==manifests[side],'Build modified tracked sources')
        binary=bins/(side+'-stockfish');shutil.copyfile(source/'src/stockfish',binary);binary.chmod(0o755)
        nm=command(['nm','-n',str(binary)],root);(folder/'symbols.nm').write_text(nm);embed=embedded_elf(binary,nm,net)
        stdout,stderr,record=native(binary,setup+'compiler\nposition startpos\ngo perft 4\nquit\n',work,folder,'qualify-perft',cpu)
        require('option name EvalFile type string default '+net['filename'] in stdout and 'uciok' in stdout and 'readyok' in stdout,'Stock embedded default UCI option differs')
        perft=parse_perft(stdout)
        stdout,stderr,record=native(binary,bench_commands,work,folder,'qualify-bench',cpu);parsed=parse_bench(stdout,stderr);legal=legal_bench(stdout,stderr,parsed,roots)
        require('NNUE evaluation using '+net['filename'] in stdout+stderr,'Canonical bench lacks stock network attestation')
        require(parsed['nodes']==plan['functional']['bench_nodes'],'Canonical bench node regression')
        require(pgo['search_signature_sha256']==parsed['search_signature_sha256'],'PGO/final default search signatures differ')
        legal_bench((folder/'pgo.stdout').read_text(),(folder/'pgo.stderr').read_text(),pgo,roots)
        if reference is None:reference=parsed['search_signature_sha256']
        require(parsed['search_signature_sha256']==reference,'Baseline/port final51 PV/bestmove/search signatures differ')
        sides[side]={'source':plan['source'][side],'binary':file_ref(binary),'embedded_network':embed,'PGO_default_bench':pgo,'qualification_bench':parsed,'legal':legal,'perft4':perft}
        print(json.dumps({'phase':'qualified','side':side,'nodes':parsed['nodes'],'signature':reference,'perft4':perft['nodes']}),flush=True)
    dump(evidence/'FUNCTIONAL.json',{'status':'PASS','sides':sides,'source_nonprefetch_bytes_preserved':True,'one_native_process_at_a_time':True})
    for side in 'AB':
        stdout,stderr,process=native(bins/(side+'-stockfish'),bench_commands,work,evidence/side,'warmup',cpu)
        parsed=parse_bench(stdout,stderr);legal_bench(stdout,stderr,parsed,roots);require(parsed['nodes']==plan['functional']['bench_nodes'] and parsed['search_signature_sha256']==reference,'Unmeasured warmup functional mismatch')
    rows=[]
    for block,order in enumerate(plan['timing']['orders'],1):
        for slot,side in enumerate(order,1):
            require(time.monotonic()-started<plan['limits']['wall_seconds'],'Total hosted wall bound exceeded')
            name=f'block-{block:02d}-slot-{slot}-{side}'
            stdout,stderr,process=native(bins/(side+'-stockfish'),bench_commands,work,evidence,name,cpu)
            parsed=parse_bench(stdout,stderr);legal=legal_bench(stdout,stderr,parsed,roots)
            require(parsed['nodes']==plan['functional']['bench_nodes'] and parsed['search_signature_sha256']==reference,'Timed functional signature mismatch')
            rows.append({'block':block,'slot':slot,'side':side,'stem':name,'engine_elapsed_ms':parsed['elapsed_ms'],'monotonic_wall_ns':process['monotonic_wall_ns'],'nodes':parsed['nodes'],'search_signature_sha256':reference,'legal':legal})
        print(json.dumps({'phase':'timing','block':block,'order':order,'complete_runs':len(rows)}),flush=True)
    gate=timing_gate(rows,plan)
    for side in 'AB':require(file_ref(bins/(side+'-stockfish'))==sides[side]['binary'],'Measured binary changed')
    require(file_ref(download)['sha256']==net['sha256'],'Input stock network changed')
    result={'schema':'official43e01-pr7294-port-diagnostic-v1','status':'COMPLETE','decision':'TIMING_GATE_PASSED_SEND_TO_ROOT_ONLY' if gate['pass'] else 'TIMING_GATE_FAILED_REJECT_PORT_ROUTE','plan_sha256':args.expected_plan_sha,'driver_commit':os.environ['GITHUB_SHA'],'source':plan['source'],'attribution':plan['attribution'],'network':net,'functional_signature':reference,'bench_nodes':plan['functional']['bench_nodes'],'perft4':197281,'timing':gate,'timed_runs':rows,'warmups_per_side':1,'native_execution_serial':True,'source_nonprefetch_bytes_preserved':True,'host':host,'loadavg_after':list(os.getloadavg()),'total_wall_seconds':time.monotonic()-started,'games_dispatched':0,'elo_or_strength_claim':False,'automatic_retries':0,'outliers_removed':0,'formal_or_private_inputs_used':False,'target_architecture_scope':'This hosted x86-64-bmi2 host only; no laptop speed or Elo inference.'}
    dump(evidence/'RESULT.json',result);dump(evidence/'MEMBERS.json',{p.relative_to(evidence).as_posix():file_ref(p) for p in sorted(evidence.rglob('*')) if p.is_file()})
    print(json.dumps({'decision':result['decision'],'median_engine_speed_fraction':gate['median_engine_speed_fraction'],'median_wall_speed_fraction':gate['median_wall_speed_fraction'],'favorable_both_blocks':gate['favorable_both_blocks'],'wall_seconds':result['total_wall_seconds']}),flush=True)


if __name__=='__main__':
    try:main()
    except BaseException as error:
        if '--root' in sys.argv:
            target=Path(sys.argv[sys.argv.index('--root')+1]).resolve()/'evidence'
            if target.exists() and not (target/'FAILURE.json').exists():dump(target/'FAILURE.json',{'status':'INCOMPLETE','exception':type(error).__name__,'error':str(error),'automatic_retry':False,'games_dispatched':0})
        raise
