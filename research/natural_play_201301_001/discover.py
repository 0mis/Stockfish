"""Prospectively fixed, result-blind natural-play corpus feasibility.

Real source replay is hosted only. No engine, fitting, private inputs, ratings,
results, annotations or player identities are used. Standard-game data: Lichess
CC0. New code: GPL-3.0-or-later. Canonical IDs use legal-EP ordinary FEN4+SHA256.
"""
from __future__ import annotations
import argparse
from collections import Counter,defaultdict
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import urllib.request
import chess
import chess.pgn

HERE=Path(__file__).resolve().parent


def need(value,message):
    if not value:raise ValueError(message)


def digest(raw):return hashlib.sha256(raw).hexdigest()
def rank(seed,domain,value):return hashlib.sha256((seed+'|'+domain+'|'+value).encode('ascii')).digest()
def canonical(board):return ' '.join(board.fen(en_passant='legal').split()[:4])
def position_id(board):return digest(canonical(board).encode('ascii'))
def compact(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()+b'\n'
def emit(path,value):
    raw=json.dumps(value,sort_keys=True,indent=2,allow_nan=False).encode()+b'\n'
    with path.open('xb') as f:f.write(raw)
    return file_ref(path)
def file_ref(path):
    h=hashlib.sha256();size=0
    with path.open('rb') as f:
        while block:=f.read(1024*1024):h.update(block);size+=len(block)
    return {'file':path.name,'bytes':size,'sha256':h.hexdigest()}


class MainlineOnly(chess.pgn.BaseVisitor):
    """The PGN lexer may encounter labels, but none is retained or consulted."""
    def begin_game(self):self.meta={};self.moves=[];self.errors=[];self.skipped=False
    def visit_header(self,tag,value):
        if tag in {'Variant','SetUp','FEN'}:self.meta[tag]=value
    def end_headers(self):
        if self.meta.get('Variant','Standard').casefold() not in {'standard','chess'} or self.meta.get('SetUp')=='1' or 'FEN' in self.meta:
            self.skipped=True;return chess.pgn.SKIP
    def begin_variation(self):return chess.pgn.SKIP
    def visit_comment(self,comment):pass
    def visit_result(self,result):pass
    def visit_nag(self,nag):pass
    def parse_san(self,board,san):
        if len(self.moves)>=512:raise ValueError('prospective ply bound')
        return board.parse_san(san)
    def visit_move(self,board,move):
        if not move:self.errors.append('null_move')
        self.moves.append(move.uci())
    def handle_error(self,error):self.errors.append(type(error).__name__)
    def result(self):return {'moves':self.moves,'errors':self.errors,'metadata_rejected':self.skipped}


class Union:
    def __init__(self,values):self.parent={x:x for x in values}
    def find(self,x):
        current=x
        while self.parent[current]!=current:current=self.parent[current]
        while self.parent[x]!=x:
            previous=self.parent[x];self.parent[x]=current;x=previous
        return current
    def join(self,a,b):
        a,b=self.find(a),self.find(b)
        if a!=b:self.parent[max(a,b)]=min(a,b)


def acceptable_root(board,q):
    if not board.is_valid() or board.is_check() or board.is_game_over(claim_draw=False) or board.is_repetition(2):return False
    count=board.occupied.bit_count();pawns=board.pawns.bit_count();queens=board.queens.bit_count()
    if count<q['minimum_pieces'] or count-pawns-2<q['minimum_nonpawn_nonking'] or pawns<q['minimum_pawns'] or queens<q['minimum_queens']:return False
    return board.legal_moves.count()>=q['minimum_legal_moves']


def read_census(pgn,plan,started):
    counters=Counter();families=defaultdict(dict);root_owner={};games=set();origins=[];candidate_plies=set(plan['quality']['candidate_plies'])
    with pgn.open(encoding='utf8',errors='strict') as source:
        for ordinal in range(plan['bounds']['source_records']):
            game=chess.pgn.read_game(source,Visitor=MainlineOnly)
            if game is None:break
            counters['source_records']+=1
            if game['metadata_rejected']:counters['nonordinary_or_variant_records']+=1;continue
            if game['errors']:counters['malformed_illegal_or_null_records']+=1;continue
            counters['fully_legal_standard_records']+=1
            moves=game['moves']
            if not plan['quality']['minimum_plies']<=len(moves)<=plan['quality']['maximum_plies']:
                counters['length_rejected']+=1;continue
            game_id=digest(('std201301-mainline-v1|'+' '.join(moves)).encode('ascii'))
            if game_id in games:counters['duplicate_mainline_records']+=1;continue
            games.add(game_id);counters['unique_quality_controlled_games']+=1
            board=chess.Board();family=None;eligible=[]
            for ply,token in enumerate(moves,1):
                board.push_uci(token)
                if ply==plan['family']['anchor_ply']:family=position_id(board)
                if ply in candidate_plies and acceptable_root(board,plan['quality']):
                    root=position_id(board);record={'root_id':root,'fen':board.fen(en_passant='legal'),'game_id':game_id,'source_record':ordinal+1,'ply':ply,'prefix_uci':moves[:ply],'family_id':family};eligible.append(root)
                    prior=families[family].get(root)
                    if prior is None or (game_id,ply)<(prior['game_id'],prior['ply']):families[family][root]=record
                    root_owner.setdefault(root,family);counters['eligible_root_occurrences']+=1
                if ply>=max(candidate_plies):break
            origins.append({'source_record':ordinal+1,'game_id':game_id,'anchor16_id':family,'eligible_root_ids':sorted(set(eligible))})
            if counters['source_records']%500==0:
                print(json.dumps({'phase':'legal_census','records':counters['source_records'],'quality_games':counters['unique_quality_controlled_games'],'anchor_families':len(families)}),flush=True)
            need(time.monotonic()-started<plan['bounds']['wall_seconds'],'Prospective wall bound exceeded')
    counters['distinct_anchor_families_with_roots']=len(families);counters['unique_eligible_roots']=len(root_owner)
    return counters,families,root_owner,origins


def guarded_components(families,root_owner,plan,started):
    seed=plan['seed'];union=Union(families)
    # Shared eligible roots are checked across the complete bounded census.
    for family,roots in families.items():
        for root in roots:union.join(family,root_owner[root])
    anchors=sorted(families,key=lambda x:rank(seed,'anchor-cap',x))[:plan['family']['maximum_candidate_anchor_families']]
    candidates=[]
    for family in anchors:
        candidates.extend(families[family][root] for root in sorted(families[family],key=lambda x:rank(seed,'family-root',x))[:plan['family']['preliminary_roots_per_anchor']])
    need(len(candidates)<=plan['bounds']['preliminary_roots'],'Prospective preliminary root cap exceeded')
    token_owner={};generated=0
    for number,record in enumerate(candidates,1):
        board=chess.Board(record['fen']);need(position_id(board)==record['root_id'],'Canonical root reconstruction differs')
        children=set()
        for move in list(board.legal_moves):
            child=board.copy(stack=False);child.push(move);children.add(position_id(child));generated+=1
            need(generated<=plan['bounds']['generated_child_boards'],'Prospective legal-child bound exceeded')
        record['children']=sorted(children)
        for token in children|{record['root_id']}:
            if token in root_owner:union.join(record['family_id'],root_owner[token])
            if token in families:union.join(record['family_id'],token)
            if token in token_owner:union.join(record['family_id'],token_owner[token])
            else:token_owner[token]=record['family_id']
        if number%2000==0:
            print(json.dumps({'phase':'legal_child_guards','roots':number,'of':len(candidates),'generated_child_boards':generated,'distinct_guard_ids':len(token_owner)}),flush=True)
            need(time.monotonic()-started<plan['bounds']['wall_seconds'],'Prospective wall bound exceeded')
    members=defaultdict(list)
    for family in families:members[union.find(family)].append(family)
    component_ids={key:digest(('std201301-component-v1|'+'\n'.join(sorted(values))).encode('ascii')) for key,values in members.items()}
    components=defaultdict(dict)
    for record in candidates:
        cid=component_ids[union.find(record['family_id'])];record['component_id']=cid
        previous=components[cid].get(record['root_id'])
        if previous is None or (record['game_id'],record['ply'])<(previous['game_id'],previous['ply']):components[cid][record['root_id']]=record
    family_components={family:component_ids[union.find(family)] for family in families}
    return candidates,components,{'preliminary_anchor_families':len(anchors),'preliminary_parent_records':len(candidates),'generated_child_boards':generated,'distinct_preliminary_guard_ids':len(token_owner),'components_with_roots':len(components)},family_components


def split_for(cid,seed):
    value=int.from_bytes(rank(seed,'split',cid)[:8],'big')%10000
    return 'TRAIN' if value<6500 else 'DEV' if value<8000 else 'FINAL' if value<9000 else 'SCREEN'


def select(components,plan):
    ordered=defaultdict(list);seed=plan['seed']
    for cid,roots in components.items():
        chosen=[roots[root] for root in sorted(roots,key=lambda x:rank(seed,'component-root',x))[:plan['family']['component_root_cap']]]
        ordered[split_for(cid,seed)].append((cid,chosen))
    selected={key:[] for key in plan['partitions']['quotas']}
    for split,quota in plan['partitions']['quotas'].items():
        groups=sorted(ordered[split],key=lambda row:rank(seed,'component-order',row[0]))
        for turn in range(plan['family']['component_root_cap']):
            for cid,roots in groups:
                if turn<len(roots) and len(selected[split])<quota:selected[split].append(roots[turn])
    return selected,{key:len(ordered[key]) for key in selected}


def ids_file(path,values):
    h=hashlib.sha256();count=0;previous=''
    with path.open('xb') as out:
        for key in sorted(values):
            need(len(key)==64 and all(c in '0123456789abcdef' for c in key) and key>previous,'Canonical ID stream malformed')
            raw=(key+'\n').encode('ascii');out.write(raw);h.update(raw);count+=1;previous=key
        out.flush();os.fsync(out.fileno())
    need(path.stat().st_size==65*count,'Canonical stream byte count differs')
    verified=file_ref(path);need(verified['bytes']==65*count and verified['sha256']==h.hexdigest(),'Canonical stream written bytes differ')
    return {'file':path.name,'count':count,'bytes':count*65,'sha256':h.hexdigest()}


def download_decompress(work,plan):
    import zstandard as zstd
    pin=plan['source'];archive=work/'source.pgn.zst';h=hashlib.sha256();size=0
    with urllib.request.urlopen(urllib.request.Request(pin['url'],headers={'User-Agent':'0mis-natural-play-data-study'}),timeout=60) as response,archive.open('xb') as out:
        need(response.status==200,'Primary January2013 source unavailable');headers={key:response.headers.get(key) for key in ('ETag','Last-Modified','Content-Length')}
        while block:=response.read(1024*1024):
            size+=len(block);need(size<=pin['bytes'],'Source exceeds exact predeclared bytes');h.update(block);out.write(block)
    need(size==pin['bytes'] and h.hexdigest()==pin['sha256'],'Source full bytes/SHA does not match primary checksum')
    target=work/'source.pgn';decoded=0;plain_hash=hashlib.sha256()
    with archive.open('rb') as raw,zstd.ZstdDecompressor().stream_reader(raw) as stream,target.open('xb') as out:
        while block:=stream.read(1024*1024):
            decoded+=len(block);need(decoded<=plan['bounds']['decompressed_bytes'],'Prospective decompression bound exceeded');plain_hash.update(block);out.write(block)
    return target,{'url':pin['url'],'archive_bytes':size,'archive_sha256':h.hexdigest(),'decompressed_bytes':decoded,'decompressed_sha256':plain_hash.hexdigest(),'http_metadata':headers,'license':pin['license'],'primary_license_url':pin['license_source'],'raw_PGN_published_or_returned':False}


def export(work,plan,census,families,root_owner,origins,candidates,components,family_components,counts,selected,source,started):
    import resource
    import zstandard as zstd
    index=work/'collision-index';large=work/'sanitized-products';index.mkdir();large.mkdir()
    component_splits={cid:split_for(cid,plan['seed']) for cid in components};guard_sets=defaultdict(set)
    for record in candidates:guard_sets[component_splits[record['component_id']]].update(record['children']+[record['root_id']])
    split_names=list(selected)
    for i,a in enumerate(split_names):
        for b in split_names[i+1:]:need(not guard_sets[a]&guard_sets[b],'Cross-partition parent/legal-child leakage')
    parents={r['root_id'] for r in candidates};children={key for r in candidates for key in r['children']}
    need(len(children)<=plan['bounds']['canonical_child_ids'],'Prospective distinct child bound exceeded')
    picked={(r['component_id'],r['root_id']) for rows in selected.values() for r in rows}
    streams={name:ids_file(index/name,values) for name,values in (
        ('parents.ids',parents),('children.ids',children),('selected-parents.ids',{r['root_id'] for rows in selected.values() for r in rows}),
        ('census-roots.ids',set(root_owner)),('anchor16.ids',set(families)))}
    need(sum(row['bytes'] for row in streams.values())<=plan['bounds']['returned_canonical_stream_total_bytes'],'Prospective canonical stream total bound exceeded')
    manifest=index/'root-child-exposure.jsonl.zst';uncompressed=hashlib.sha256();raw_bytes=0
    with manifest.open('xb') as raw,zstd.ZstdCompressor(level=3).stream_writer(raw,closefd=False) as out:
        for r in sorted(candidates,key=lambda r:(r['component_id'],r['root_id'],r['family_id'])):
            line=compact({'root_id':r['root_id'],'family_id':r['family_id'],'component_id':r['component_id'],'split':component_splits[r['component_id']],
                'selected':(r['component_id'],r['root_id']) in picked,'children':r['children']})
            out.write(line);uncompressed.update(line);raw_bytes+=len(line)
            need(raw_bytes<=plan['bounds']['hash_reverse_manifest_uncompressed_bytes'],'Prospective reversible manifest raw bound exceeded')
    need(manifest.stat().st_size<=plan['bounds']['hash_reverse_manifest_compressed_bytes'],'Prospective reversible manifest bound exceeded')
    check=hashlib.sha256();decoded=0
    with manifest.open('rb') as raw,zstd.ZstdDecompressor().stream_reader(raw) as stream:
        while block:=stream.read(1024*1024):decoded+=len(block);check.update(block)
    need(decoded==raw_bytes and check.hexdigest()==uncompressed.hexdigest(),'Reversible manifest written/decompressed bytes differ')
    origin_path=index/'source-game-origins.jsonl'
    with origin_path.open('xb') as out:
        for origin in origins:
            cid=family_components.get(origin['anchor16_id'])
            out.write(compact({**origin,'component_id':cid,'split':split_for(cid,plan['seed']) if cid else None}))
    partitions={};feasible=True
    for split,rows in selected.items():
        path=large/(split.lower()+'-positions.jsonl')
        with path.open('xb') as out:
            for r in rows:out.write(compact({key:r[key] for key in ('root_id','family_id','component_id','game_id','source_record','ply','fen','prefix_uci')}))
        families_used={r['component_id'] for r in rows};minimum=plan['partitions']['minimum_feasibility'][split]
        sufficient=len(rows)>=minimum['positions'] and len(families_used)>=minimum['components'];feasible&=sufficient
        partitions[split]={'positions':len(rows),'components_used':len(families_used),'components_available':counts[split],
            'quota':plan['partitions']['quotas'][split],'minimum_feasibility':minimum,'minimum_met':sufficient,
            'guard_ids_all_preliminary_roots':len(guard_sets[split]),'sanitized_CI_only_positions':file_ref(path)}
    usage=resource.getrusage(resource.RUSAGE_SELF)
    return {'schema':'natural-play-data-feasibility-result-v1','status':'DATA_ONLY_DISCOVERY_COMPLETE',
        'decision':'STRUCTURAL_CAPACITY_SUFFICIENT_PRIVATE_GATE_REQUIRED' if feasible else 'STRUCTURAL_CAPACITY_INSUFFICIENT_UNDER_FROZEN_RULE',
        'source':source,'census':dict(census),'guard_component_counts':counts,'partitions':partitions,'cross_partition_parent_child_intersections':0,
        'canonical_id_streams':streams,'hash_only_reversible_manifest':{**file_ref(manifest),'records':len(candidates),'decompressed_bytes':raw_bytes,'decompressed_sha256':uncompressed.hexdigest()},
        'source_game_origins':{**file_ref(origin_path),'records':len(origins),'source_record_ordinals':'1-based within fixed first20000 records; only unique legal length-qualified games; labels absent'},
        'canonical_alias_scope':'ordinary Standard castling with legal EP only; no alternate Shredder or FEN-EP hash domains mixed into canonical streams',
        'private_collision_check_completed':False,'training_admitted':False,'public_sampling_conditioned_on_private_ids':False,
        'teacher_labels_collected':0,'native_processes':0,'weights_fitted':False,'outcomes_comments_names_ratings_retained':False,
        'source_record_limit_fixed':plan['bounds']['source_records'],'no_qc_replacement':True,
        'resources':{'wall_seconds':time.monotonic()-started,'user_cpu_seconds':usage.ru_utime,'system_cpu_seconds':usage.ru_stime,'peak_rss_KiB':usage.ru_maxrss},
        'future_replication':plan['future_replication'],'existing_retired_engine_study_touched':False,'learning_reproducibility_claim':False,'source_correction':plan['source_correction']}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--plan',type=Path,default=HERE/'plan.json');parser.add_argument('--expected-plan-sha',required=True);parser.add_argument('--work',type=Path,required=True);args=parser.parse_args()
    raw=args.plan.read_bytes();need(digest(raw)==args.expected_plan_sha,'Precommitted data plan differs');plan=json.loads(raw)
    work=args.work.resolve();need(not work.exists(),'Fresh publicCI work directory required');work.mkdir(parents=True);started=time.monotonic()
    receipt=work/'receipt';receipt.mkdir()
    try:
        pgn,source=download_decompress(work,plan);emit(receipt/'SOURCE.json',source)
        census,families,owners,origins=read_census(pgn,plan,started)
        candidates,components,guards,family_components=guarded_components(families,owners,plan,started)
        selected,counts=select(components,plan)
        result=export(work,plan,census,families,owners,origins,candidates,components,family_components,counts,selected,source,started)
        result['guard_component_counts'].update(guards);result['plan_sha256']=args.expected_plan_sha;result['source_commit']=os.environ.get('GITHUB_SHA')
        emit(receipt/'RESULT.json',result);emit(work/'collision-index/RECEIPT.json',result)
        print(json.dumps({'phase':'complete','decision':result['decision'],'quality_games':census['unique_quality_controlled_games'],
            'anchor_families':len(families),'components':len(components),'partitions':{k:{n:v[n] for n in ('positions','components_used','minimum_met')} for k,v in result['partitions'].items()},'wall_seconds':result['resources']['wall_seconds']}),flush=True)
    except BaseException as error:
        emit(receipt/'FAILURE.json',{'status':'INCOMPLETE','error':type(error).__name__,'message':str(error),'training_admitted':False,'native_processes':0,'weights_fitted':False})
        raise


if __name__=='__main__':main()
