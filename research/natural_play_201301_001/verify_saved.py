"""Independent hosted readback of fixed data-only artifacts; no resampling."""
import argparse
from collections import defaultdict
import hashlib
import io
import json
from pathlib import Path
import subprocess
import time
import zipfile
import chess


def require(value,message):
    if not value:raise ValueError(message)


def sha(raw):return hashlib.sha256(raw).hexdigest()
def pid(board):return sha(' '.join(board.fen(en_passant='legal').split()[:4]).encode('ascii'))
def key(seed,domain,value):return hashlib.sha256((seed+'|'+domain+'|'+value).encode('ascii')).digest()
def split_of(seed,cid):
    number=int.from_bytes(key(seed,'split',cid)[:8],'big')%10000
    return 'TRAIN' if number<6500 else 'DEV' if number<8000 else 'FINAL' if number<9000 else 'SCREEN'


def check_ref(path,ref):
    h=hashlib.sha256();size=0
    with path.open('rb') as source:
        while block:=source.read(1024*1024):h.update(block);size+=len(block)
    require(size==ref['bytes'] and h.hexdigest()==ref['sha256'],'Saved file bytes/SHA differs: '+path.name)


def read_ids(path,ref):
    check_ref(path,ref);values=set();previous=b''
    with path.open('rb') as source:
        for line in source:
            require(len(line)==65 and line[-1:]==b'\n' and all(c in b'0123456789abcdef' for c in line[:-1]) and line>previous,'Canonical ID order/framing differs')
            previous=line;values.add(line[:-1].decode('ascii'))
    require(len(values)==ref['count'] and ref['bytes']==65*len(values),'Canonical ID count differs')
    return values


def fetch(work,binding):
    for item in binding['artifacts']:
        archive=work/(item['role']+'.zip')
        with archive.open('xb') as out:
            subprocess.run(['gh','api',f"repos/{binding['repo']}/actions/artifacts/{item['id']}/zip"],stdout=out,check=True)
        check_ref(archive,item);directory=work/item['role'];directory.mkdir()
        with zipfile.ZipFile(archive) as z:
            require(sum(i.file_size for i in z.infolist())<=512*1024*1024,'Saved ZIP uncompressed cap exceeded')
            for member in z.infolist():
                relative=Path(member.filename)
                require(not relative.is_absolute() and '..' not in relative.parts and not member.is_dir(),'Unsafe artifact member')
                target=directory/relative;target.parent.mkdir(exist_ok=True,parents=True)
                with z.open(member) as source,target.open('xb') as out:
                    while block:=source.read(1024*1024):out.write(block)


def audit(work,plan,result,binding):
    import zstandard
    started=time.monotonic();seed=plan['seed'];index=work/'collision';positions=work/'positions'
    require(result['source_commit']==binding['source_commit'] and result['plan_sha256']==binding['plan_sha256'],'Source/plan binding differs')
    saved=json.loads((work/'receipt/natural-play-201301-001/receipt/RESULT.json').read_bytes())
    require(saved==result and json.loads((index/'RECEIPT.json').read_bytes())==result,'Published receipts differ')
    ids={name:read_ids(index/name,ref) for name,ref in result['canonical_id_streams'].items()}
    origin_ref=result['source_game_origins'];check_ref(index/origin_ref['file'],origin_ref)
    origins={};family_roots=defaultdict(set);members=defaultdict(set);family_component={}
    with (index/origin_ref['file']).open() as source:
        for line in source:
            row=json.loads(line);ordinal=row['source_record'];require(1<=ordinal<=plan['bounds']['source_records'] and ordinal not in origins,'Source origin ordinal differs');origins[ordinal]=row
            family=row['anchor16_id'];cid=row['component_id']
            if cid is not None:
                require(row['split']==split_of(seed,cid),'Origin split differs');members[cid].add(family)
                require(family not in family_component or family_component[family]==cid,'Family assigned twice');family_component[family]=cid
                family_roots[family].update(row['eligible_root_ids'])
            else:require(not row['eligible_root_ids'],'Eligible roots lack a component')
    require(len(origins)==origin_ref['records'],'Origin count differs')
    require(set(family_roots)==ids['anchor16.ids'],'Anchor stream differs')
    require(set().union(*family_roots.values())==ids['census-roots.ids'],'Census-root stream differs')
    for cid,anchors in members.items():require(cid==sha(('std201301-component-v1|'+'\n'.join(sorted(anchors))).encode('ascii')),'Component membership hash differs')
    manifest_ref=result['hash_only_reversible_manifest'];check_ref(index/manifest_ref['file'],manifest_ref)
    records={};children=set();guard_owner={};raw_hash=hashlib.sha256();raw_bytes=0;rows_count=0
    with (index/manifest_ref['file']).open('rb') as raw,zstandard.ZstdDecompressor().stream_reader(raw) as decoded,io.TextIOWrapper(decoded,encoding='utf8') as source:
        for line in source:
            encoded=line.encode();raw_hash.update(encoded);raw_bytes+=len(encoded);row=json.loads(line);rows_count+=1
            root=row['root_id'];cid=row['component_id'];split=row['split'];family=row['family_id']
            require(family_component[family]==cid and split_of(seed,cid)==split,'Manifest membership/split differs')
            require((cid,root) not in records,'Duplicate preliminary component/root')
            require(row['children']==sorted(set(row['children'])) and len(row['children'])>=2,'Legal child list malformed')
            records[(cid,root)]=row;children.update(row['children'])
            for token in row['children']+[root]:
                require(token not in guard_owner or guard_owner[token]==split,'Cross-partition parent/child intersection')
                guard_owner[token]=split
    require(raw_bytes==manifest_ref['decompressed_bytes'] and raw_hash.hexdigest()==manifest_ref['decompressed_sha256'] and rows_count==manifest_ref['records'],'Manifest decoded receipt differs')
    require({root for cid,root in records}==ids['parents.ids'] and children==ids['children.ids'],'Manifest/parent/child stream equality differs')
    expected_preliminary={(family,root) for family in sorted(family_roots,key=lambda x:key(seed,'anchor-cap',x))[:plan['family']['maximum_candidate_anchor_families']] for root in sorted(family_roots[family],key=lambda x:key(seed,'family-root',x))[:plan['family']['preliminary_roots_per_anchor']]}
    require(expected_preliminary=={(r['family_id'],r['root_id']) for r in records.values()},'Preliminary source-blind ranking differs')
    grouped=defaultdict(list)
    for cid,root in records:grouped[cid].append(root)
    expected={name:[] for name in plan['partitions']['quotas']}
    for split,quota in plan['partitions']['quotas'].items():
        groups=sorted((cid for cid in grouped if split_of(seed,cid)==split),key=lambda x:key(seed,'component-order',x))
        ranked={cid:sorted(grouped[cid],key=lambda x:key(seed,'component-root',x))[:plan['family']['component_root_cap']] for cid in groups}
        for turn in range(plan['family']['component_root_cap']):
            for cid in groups:
                if turn<len(ranked[cid]) and len(expected[split])<quota:expected[split].append((cid,ranked[cid][turn]))
    selected=set();complete_children_checked=0;partition_counts={}
    for split,ref in result['partitions'].items():
        file_ref=ref['sanitized_CI_only_positions'];check_ref(positions/file_ref['file'],file_ref);actual=[]
        with (positions/file_ref['file']).open() as source:
            for line in source:
                row=json.loads(line);cid=row['component_id'];root=row['root_id'];pair=(cid,root);actual.append(pair)
                require(pair not in selected and row['ply'] in plan['quality']['candidate_plies'] and row['ply']==len(row['prefix_uci']),'Selected duplicate/ply differs');selected.add(pair)
                origin=origins[row['source_record']]
                require(row['game_id']==origin['game_id'] and row['family_id']==origin['anchor16_id'] and root in origin['eligible_root_ids'],'Selected source origin differs')
                board=chess.Board();anchor=None
                for ply,uci in enumerate(row['prefix_uci'],1):
                    move=chess.Move.from_uci(uci);require(move in board.legal_moves,'Selected prefix illegal');board.push(move)
                    if ply==plan['family']['anchor_ply']:anchor=pid(board)
                require(anchor==row['family_id'] and pid(board)==root and board.fen(en_passant='legal')==row['fen'],'Selected prefix/FEN/root reconstruction differs')
                require(not board.is_check() and not board.is_game_over(claim_draw=False) and not board.is_repetition(2),'Selected root terminal/check/repetition')
                q=plan['quality'];pieces=board.occupied.bit_count();pawns=board.pawns.bit_count()
                require(pieces>=q['minimum_pieces'] and pieces-pawns-2>=q['minimum_nonpawn_nonking'] and pawns>=q['minimum_pawns'] and board.queens.bit_count()>=q['minimum_queens'],'Selected material-quality rule differs')
                actual_children=set()
                for move in list(board.legal_moves):
                    child=board.copy(stack=False);child.push(move);actual_children.add(pid(child))
                require(actual_children==set(records[pair]['children']),'Selected complete legal-child reconstruction differs')
                complete_children_checked+=len(actual_children)
        require(actual==expected[split] and len(actual)==ref['positions'],'Selected fixed round-robin ranking/count differs')
        partition_counts[split]={'roots':len(actual),'components':len({cid for cid,root in actual})}
    require({root for cid,root in selected}==ids['selected-parents.ids'],'Selected canonical stream differs')
    require(selected=={pair for pair,r in records.items() if r['selected']},'Manifest selected flags differ')
    return {'schema':'natural-play-independent-saved-audit-v1','status':'PASS','source_run':binding['run_id'],'source_commit':binding['source_commit'],'plan_sha256':binding['plan_sha256'],'partitions':partition_counts,'independently_reconstructed_selected_roots':len(selected),'independently_reconstructed_legal_children':complete_children_checked,'canonical_streams_verified':list(ids),'origin_records_verified':len(origins),'preliminary_manifest_records_verified':rows_count,'cross_partition_parent_child_intersections':0,'source_resampled':False,'native_processes':0,'teacher_labels_collected':0,'private_collision_check_completed':False,'training_admitted':False,'wall_seconds':time.monotonic()-started}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--work',type=Path,required=True);args=parser.parse_args();here=Path(__file__).resolve().parent
    plan=json.loads((here/'plan.json').read_bytes());result=json.loads((here/'readout-001/RESULT.json').read_bytes());binding=json.loads((here/'readout-001/ARTIFACTS.json').read_bytes())
    require(sha((here/'plan.json').read_bytes())==binding['plan_sha256'],'Frozen plan hash differs');args.work.mkdir()
    output=args.work/'audit-receipt';output.mkdir()
    try:
        fetch(args.work,binding);summary=audit(args.work,plan,result,binding)
        (output/'AUDIT.json').write_text(json.dumps(summary,sort_keys=True,indent=2)+'\n');print(json.dumps(summary),flush=True)
    except BaseException as error:
        (output/'FAILURE.json').write_text(json.dumps({'status':'INCOMPLETE','error':type(error).__name__,'message':str(error),'training_admitted':False},sort_keys=True,indent=2)+'\n');raise


if __name__=='__main__':main()
