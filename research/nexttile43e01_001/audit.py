"""Passive full raw-output and timing readback; invokes no engine."""
import argparse
import json
from pathlib import Path
import tarfile
from qualify_common import dump,embedded_elf,file_ref,parse_bench,parse_perft,require
from policy import legal_bench,reverse_port,roster


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--evidence',type=Path,required=True);args=parser.parse_args();folder=args.evidence
    result=json.loads((folder/'RESULT.json').read_bytes());members=json.loads((folder/'MEMBERS.json').read_bytes());plan=json.loads((Path(__file__).parent/'plan.json').read_bytes())
    require(result['status']=='COMPLETE','Diagnostic is incomplete')
    for name,pin in members.items():require(file_ref(folder/name)==pin,'Saved bytes changed: '+name)
    source={}
    for side in 'AB':
        with tarfile.open(folder/side/'corresponding-source.tar.gz','r:gz') as archive:
            source[side]=archive.extractfile('src/nnue/nnue_accumulator.cpp').read()
            if side=='A':roots=roster(archive.extractfile('src/benchmark.cpp').read().decode())
    require(reverse_port(source['B'])==source['A'],'Archived source has non-prefetch changes')
    functional=json.loads((folder/'FUNCTIONAL.json').read_bytes());signatures=set();legal_rows=0;benches=0
    def reread(directory,stem):
        nonlocal legal_rows,benches
        out=(directory/(stem+'.stdout')).read_text();err=(directory/(stem+'.stderr')).read_text();parsed=parse_bench(out,err)
        checked=legal_bench(out,err,parsed,roots);legal_rows+=checked['all_reported_PV_rows_checked'];benches+=1
        require(parsed['nodes']==3464885,'Raw default node regression');signatures.add(parsed['search_signature_sha256']);return parsed
    for side in 'AB':
        data=functional['sides'][side]
        require(reread(folder/side,'qualify-bench')==data['qualification_bench'],'Raw qualification record differs')
        require(reread(folder/side,'pgo')==data['PGO_default_bench'],'Raw PGO record differs')
        reread(folder/side,'warmup')
        require(parse_perft((folder/side/'qualify-perft.stdout').read_text())==data['perft4'],'Raw perft differs')
        actual=embedded_elf(folder/'bin'/(side+'-stockfish'),(folder/side/'symbols.nm').read_text(),plan['network'])
        require(actual==data['embedded_network'],'Actual archived embedded NNUE differs')
    observed=[]
    for index,row in enumerate(result['timed_runs']):
        parsed=reread(folder,row['stem']);process=json.loads((folder/(row['stem']+'.process.json')).read_bytes())
        duration=process['monotonic_end_ns']-process['monotonic_start_ns']
        require(process['returncode']==0 and duration==row['monotonic_wall_ns']==process['monotonic_wall_ns'] and parsed['elapsed_ms']==row['engine_elapsed_ms'],'Raw clock record differs')
        require(row['side']==plan['timing']['orders'][index//4][index%4],'Raw balanced order differs');observed.append((row['side'],parsed['elapsed_ms'],duration))
    require(len(signatures)==1 and signatures=={result['functional_signature']} and len(observed)==32,'Function or fixed timing sample differs')
    engine=[];wall=[];favorable=0
    for offset in range(0,32,4):
        rows=observed[offset:offset+4];a=[r for r in rows if r[0]=='A'];b=[r for r in rows if r[0]=='B'];require(len(a)==len(b)==2,'Unbalanced raw timing block')
        e=sum(r[1] for r in a)/sum(r[1] for r in b)-1;w=sum(r[2] for r in a)/sum(r[2] for r in b)-1;engine.append(e);wall.append(w);favorable+=e>0 and w>0
    median=lambda x:(sorted(x)[3]+sorted(x)[4])/2
    passed=median(engine)>=.01 and median(wall)>=.01 and favorable>=7
    require(abs(median(engine)-result['timing']['median_engine_speed_fraction'])<1e-12 and abs(median(wall)-result['timing']['median_wall_speed_fraction'])<1e-12 and favorable==result['timing']['favorable_both_blocks'] and passed==result['timing']['pass'],'Independent raw timing arithmetic differs')
    receipt={'schema':'official43e01-prefetch-passive-audit-v1','status':'PASS','result':file_ref(folder/'RESULT.json'),'members':file_ref(folder/'MEMBERS.json'),'raw_benches_verified':benches,'all_reported_PV_rows_legally_verified':legal_rows,'final_positions_verified':benches*51,'canonical_nodes':3464885,'perft4':197281,'source_nonprefetch_bytes_preserved':True,'embedded_network_sha256':plan['network']['sha256'],'timing_gate_passed':passed,'median_engine_speed_fraction':median(engine),'median_wall_speed_fraction':median(wall),'favorable_both_blocks':favorable,'games_dispatched':0,'elo_claim':False}
    dump(folder/'INDEPENDENT-AUDIT.json',receipt);print(json.dumps(receipt),flush=True)


if __name__=='__main__':main()
