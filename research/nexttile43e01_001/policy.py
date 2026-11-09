"""Pure source, legal-PV and fixed timing checks. GPL-3.0-or-later."""
import re
import statistics
import chess
from qualify_common import require

HELPER=b'''    #if defined(USE_PAIRED_WIDENING_ADD)
// PR7294 by MxTriX77/SanityEclipse000: prefetch the next tile of each changed
// row while its current tile is being added. Keep the original fast-PEXT and
// more-than-two-tiles gate; current specialized row arrays and unrolls stay intact.
sf_always_inline void prefetch_next_tile([[maybe_unused]] IndexType j,
                                         [[maybe_unused]] const i8* data) {
        #if defined(USE_PEXT)
    if constexpr (Dimensions / Tiling::TileHeight > 2)
        if (j + Tiling::TileHeight < Dimensions)
            for (IndexType k = 0; k < Tiling::TileHeight; k += CacheLineSize)
                prefetch(data + Tiling::TileHeight + k);
        #endif
}
    #endif

'''


def reverse_port(raw):
    require(raw.count(HELPER)==1,'Exact attributed policy helper differs')
    raw=raw.replace(HELPER,b'',1)
    for name in (b'data',b'data0',b'data1',b'add',b'sub'):
        line=b'    prefetch_next_tile(j, '+name+b');\n';require(raw.count(line)==1,'Prefetch callsite differs');raw=raw.replace(line,b'',1)
    for old,new,count in (
        (b'apply_feature_pair(IndexType j, Tile acc,',b'apply_feature_pair(Tile acc,',1),
        (b'apply_feature_diff(IndexType j, Tile acc,',b'apply_feature_diff(Tile acc,',1),
        (b'apply_feature_pair<sign>(j, acc,',b'apply_feature_pair<sign>(acc,',3),
        (b'apply_feature_diff(j, acc,',b'apply_feature_diff(acc,',1)):
        require(raw.count(old)==count,'Tile-index plumbing differs');raw=raw.replace(old,new)
    require(b'prefetch_next_tile' not in raw,'Unreviewed extra prefetch policy')
    return raw


def roster(source):
    block=source.split('const std::vector<std::string> Defaults = {',1)[1].split('\n};',1)[0]
    rows=[];chess960=False
    for value in re.findall(r'^\s*"([^"]+)"',block,re.M):
        if value.startswith('setoption '):chess960=value.endswith('true');continue
        fen,sep,moves=value.partition(' moves ');board=chess.Board(fen,chess960=chess960)
        for token in moves.split() if sep else ():board.push_uci(token)
        rows.append(board)
    require(len(rows)==51,'Actual canonical default roster differs')
    return rows


def legal_line(board,pv,bestmove=None,ponder=None):
    copy=board.copy(stack=False);parsed=[]
    for token in pv:
        move=copy.parse_uci(token);require(move and move in copy.legal_moves,'Reported PV contains illegal/null move');parsed.append(move);copy.push(move)
    if bestmove in ('(none)','0000'):
        require(not list(board.legal_moves) and not parsed and ponder is None,'Null bestmove on a nonterminal root')
    elif bestmove is not None:
        move=board.parse_uci(bestmove);require(move and move in board.legal_moves,'Illegal bestmove')
        require(parsed and parsed[0]==move,'Final PV/bestmove differ')
        if ponder is not None:
            child=board.copy(stack=False);child.push(move);reply=child.parse_uci(ponder)
            require(reply and reply in child.legal_moves and len(parsed)>=2 and parsed[1]==reply,'Illegal or inconsistent ponder')
    return len(parsed)


def legal_bench(stdout,stderr,parsed,roots):
    markers=re.findall(r'^Position:\s*(\d+)/51\s*\((.+)\)\s*$',stderr,re.M)
    require(len(markers)==51,'Native root marker roster incomplete');reported_pvs=0;index=0
    for expected,((number,fen),board) in enumerate(zip(markers,roots),1):
        actual=chess.Board(fen,chess960=board.chess960)
        require(int(number)==expected,'Native position order differs')
        require(actual.fen(en_passant='legal')==board.fen(en_passant='legal'),'Native root/FEN differs from authenticated default roster')
    for line in stdout.splitlines():
        if line.startswith('info depth ') and ' pv ' in line:
            require(index<51,'PV after final position');legal_line(roots[index],line.split(' pv ',1)[1].split());reported_pvs+=1
        elif line.startswith('bestmove '):index+=1
    require(index==51,'Native bestmove count differs')
    for board,row in zip(roots,parsed['positions']):
        fields=row['bestmove'].split();require(len(fields) in (2,4) and (len(fields)==2 or fields[2]=='ponder'),'Bestmove syntax differs')
        pv=row['final_info'].split(' pv ',1)[1].split() if ' pv ' in row['final_info'] else []
        legal_line(board,pv,fields[1],fields[3] if len(fields)==4 else None)
    return {'final_positions_checked':51,'all_reported_PV_rows_checked':reported_pvs,'chess960_roots':sum(board.chess960 for board in roots)}


def timing_gate(rows,plan):
    require(len(rows)==32,'Fixed timing sample incomplete');blocks=[]
    for block,order in enumerate(plan['timing']['orders'],1):
        part=rows[(block-1)*4:block*4];require([r['side'] for r in part]==list(order),'Fixed balanced block order differs')
        values={side:[r for r in part if r['side']==side] for side in 'AB'}
        engine={s:statistics.mean(r['engine_elapsed_ms'] for r in values[s]) for s in 'AB'}
        wall={s:statistics.mean(r['monotonic_wall_ns'] for r in values[s]) for s in 'AB'}
        require(all(x>0 for x in list(engine.values())+list(wall.values())),'Nonpositive timing')
        e=engine['A']/engine['B']-1;w=wall['A']/wall['B']-1
        blocks.append({'block':block,'order':order,'engine_mean_ms':engine,'wall_mean_ns':wall,'engine_speed_fraction':e,'wall_speed_fraction':w,'favorable_both':e>0 and w>0})
    median_engine=statistics.median(b['engine_speed_fraction'] for b in blocks);median_wall=statistics.median(b['wall_speed_fraction'] for b in blocks);favorable=sum(b['favorable_both'] for b in blocks)
    passed=median_engine>=0.01 and median_wall>=0.01 and favorable>=7
    return {'gate':'median engine and wall speed>=1%; at least7of8 blocks faster on both clocks','pass':passed,'median_engine_speed_fraction':median_engine,'median_wall_speed_fraction':median_wall,'favorable_both_blocks':favorable,'blocks':blocks,'outliers_removed':0,'automatic_retries':0}
