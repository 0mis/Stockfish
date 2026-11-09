"""Finite ordinary-eval/bench/perft parsers and runner-only subprocess captures."""
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
import chess

def need(ok,message):
    if not ok: raise ValueError(message)
def sha(raw): return hashlib.sha256(raw).hexdigest()
def canonical(value): return json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
def ref(path):
    with path.open("rb") as f: h=hashlib.file_digest(f,"sha256").hexdigest()
    return {"path":path.name,"bytes":path.stat().st_size,"sha256":h}
def emit(path,value):
    with path.open("x",encoding="utf-8",newline="\n") as f: json.dump(value,f,sort_keys=True,indent=2,allow_nan=False); f.write("\n")
ERROR=re.compile(r"ERROR:|CRITICAL ERROR|No such option|Unknown command|Unable to open file|network evaluation parameters compatible",re.I)
ACK="info string NNUE evaluation using "
MOVE=re.compile(r"[a-h][1-8][a-h][1-8][qrbn]?\Z")

def commands(net,tail):
    value=str(net)
    need(value.isascii() and value.isprintable(),"Unsafe external EvalFile")
    return "\n".join(["uci","setoption name NumaPolicy value none","setoption name Threads value 1","setoption name Hash value 64",
        "setoption name MultiPV value 1","setoption name Skill Level value 20","setoption name UCI_Chess960 value false",
        "setoption name UCI_LimitStrength value false","setoption name Ponder value false","setoption name SyzygyPath value <empty>",
        "setoption name SyzygyProbeLimit value 0","setoption name EvalFile value "+value,"isready",*tail,"quit",""])

def capture(executable,argv_tail,work,out,stem,control,timeout=300):
    """Caller authenticates exact binaries/networks first; never invokes a shell."""
    executable=executable.resolve(strict=True)
    need(executable.is_file() and executable.suffix.lower()==".exe","Only an authenticated executable may run")
    paths={k:out/(stem+suffix) for k,suffix in (("stdout",".stdout.txt"),("stderr",".stderr.txt"),("commands",".uci"))}
    for p in paths.values(): need(not p.exists(),"Preserve existing native capture")
    paths["commands"].write_bytes(control.encode("ascii"))
    record={"argv":[str(executable),*map(str,argv_tail)],"cwd":str(work),"returncode":None,"timed_out":False,
        "commands":ref(paths["commands"]),"executable":ref(executable)}
    start=time.monotonic()
    try:
        with paths["stdout"].open("xb") as stdout,paths["stderr"].open("xb") as stderr:
            result=subprocess.run(record["argv"],cwd=work,input=control.encode("ascii"),stdout=stdout,stderr=stderr,
                timeout=timeout,check=False)
        record["returncode"]=result.returncode
        need(result.returncode==0,"Native process failed: "+stem)
    except BaseException as exc:
        record["timed_out"]=isinstance(exc,subprocess.TimeoutExpired)
        record["error_type"]=type(exc).__name__
        raise
    finally:
        record["elapsed_seconds"]=time.monotonic()-start
        record["logs"]={k:ref(paths[k]) for k in ("stdout","stderr") if paths[k].exists()}
        emit(out/(stem+".record.json"),record)
    return paths["stdout"].read_text(encoding="utf-8-sig"),paths["stderr"].read_text(encoding="utf-8-sig")

def ready_blocks(stdout,stderr,n):
    need(not ERROR.search(stdout+"\n"+stderr) and not any(x.startswith(ACK) for x in stderr.splitlines()),"Native error or unbound network acknowledgement")
    lines=stdout.splitlines(); cuts=[i for i,line in enumerate(lines) if line=="readyok"]
    need(len(cuts)==n+1 and lines.count("uciok")==1 and lines.index("uciok")<cuts[0]
        and not any(x.strip() for x in lines[cuts[-1]+1:]),"Incomplete native UCI/ready barriers")
    return [lines[a+1:b] for a,b in zip(cuts,cuts[1:])]

def acknowledgements(lines,network,n):
    starts=[i for i,line in enumerate(lines) if line.startswith(ACK)]
    pattern=re.compile(re.escape(ACK+str(network))+r" \(131MiB, \(131952, 1024, 32, 32, 1\)\)\Z")
    need(len(starts)==n and all(pattern.fullmatch(lines[i]) for i in starts),"External network acknowledgement differs")
    return [lines[a+1:b] for a,b in zip(starts,starts[1:]+[len(lines)])]

def ordinary(stdout,stderr,network,requests):
    need(not stderr.strip(),"Unexpected ordinary-eval stderr")
    result={}
    for block,request in zip(ready_blocks(stdout,stderr,len(requests)),requests,strict=True):
        lines,=acknowledgements(block,network,1)
        board=chess.Board(request["child_fen"])
        title="NNUE network contributions (Normalized, "+("White" if board.turn else "Black")+" to move)"
        need(lines.count(title)==1,"Ordinary trace root side differs")
        buckets=[]
        pattern=re.compile(r"\|\s*(\d+)\s*\|\s*([+-]?)\s*(\d+\.\d{2})\s*\|\s*(<-- this bucket is used)?")
        for line in lines:
            if match:=pattern.fullmatch(line): buckets.append({"index":int(match[1]),"printed_value":match[2]+match[3],"used":bool(match[4])})
        need([b["index"] for b in buckets]==list(range(92)),"Ordinary trace omitted/duplicated a physical head")
        active=[b["index"] for b in buckets if b["used"]]
        raw=re.findall(r"^NNUE evaluation\s+([+-]?\d+)\s+\(side to move, internal units\)\s*$","\n".join(lines),re.M)
        cp=re.findall(r"^Final evaluation\s+([+-]?\d+\.\d{2})\s+\(white side\) \[with scaled NNUE, \.\.\.\]\s*$","\n".join(lines),re.M)
        need(len(active)==len(raw)==len(cp)==1,"Ordinary selected/raw/static fields differ")
        white_cp=Decimal(cp[0])*100
        need(white_cp==white_cp.to_integral_value(),"Printed ordinary CP is not an exact integer")
        key=(request["parent_position_id"],request["rank"])
        need(key not in result,"Repeated ordinary request identity")
        result[key]={"physical_head":active[0],"raw_score":int(raw[0]),"static_cp":int(white_cp)*(1 if board.turn else -1),"buckets":buckets,
            "rule50":board.halfmove_clock,"child_fen":request["child_fen"]}
    return result

def benchmark_specs(raw):
    text=raw.decode(); marker="const std::vector<std::string> Defaults = {"
    need(text.count(marker)==1,"Ambiguous pinned benchmark source")
    block=text.split(marker,1)[1].split("\n};",1)[0]; result=[]; mode=False
    for line in block.splitlines():
        line=line.strip()
        if not line or line.startswith("//"): continue
        match=re.fullmatch(r'("(?:[^"\\]|\\.)*")\s*,?\s*(?://[^\r\n]*)?',line)
        need(match,"Unsupported pinned benchmark statement")
        item=json.loads(match[1])
        if item.startswith("setoption"):
            need(item in ("setoption name UCI_Chess960 value true","setoption name UCI_Chess960 value false"),"Unknown benchmark option")
            mode=item.endswith("true")
        else:
            fen,_,moves=item.partition(" moves ")
            board=chess.Board(fen+(" 0 1" if len(fen.split())==4 else ""),chess960=mode)
            need(board.is_valid(),"Invalid benchmark root")
            for token in moves.split(): board.push_uci(token)
            result.append(board)
    need(len(result)==51,"Canonical benchmark must have51 roots")
    return result

def bench(stdout,stderr,network,specs):
    block,=ready_blocks(stdout,stderr,1); searches=acknowledgements(block,network,51)
    markers=re.findall(r"^Position: (\d+)/51 \(([^()]+)\)\s*$",stderr,re.M)
    need(markers==[(str(i+1),b.fen(shredder=b.chess960,en_passant="legal")) for i,b in enumerate(specs)],"Bench exact root roster/history differs")
    normalized=[]
    for lines,board in zip(searches,specs,strict=True):
        bests=[x for x in lines if x.startswith("bestmove ")]
        need(len(bests)==1,"Bench search omitted or duplicated bestmove")
        last=None
        for line in lines:
            if line.startswith("info depth ") and " score " in line: last=line
            words=line.split()
            if "pv" in words:
                replay=board.copy(stack=False)
                for move in words[words.index("pv")+1:]: need(MOVE.fullmatch(move),"Bad bench PV move"); replay.push_uci(move)
        need(last is not None,"Bench final score is missing")
        words=bests[0].split(); need(len(words) in (2,4),"Bad bench terminal")
        if words[1] in ("(none)","0000"):
            need(not any(board.legal_moves) and len(words)==2 and re.fullmatch(r"info depth 0 score (?:cp|mate) -?\d+",last),"Unjustified bench no-move")
        else:
            replay=board.copy(stack=False); replay.push_uci(words[1])
            if len(words)==4: need(words[2]=="ponder","Bad bench ponder"); replay.push_uci(words[3])
            final=re.fullmatch(r"info depth 13 seldepth \d+ multipv 1 score (?:cp|mate) -?\d+(?: (?:lowerbound|upperbound))? nodes \d+ nps \d+ hashfull \d+ tbhits 0 time \d+ pv (.+)",last)
            need(final and final[1].split()[0]==words[1],"Canonical depth13 primary PV is incomplete")
        nodes=re.search(r"\bnodes (\d+)\b",last)
        need(nodes is not None or last.startswith("info depth 0 score "),"Missing final nodes")
        normalized.append({"final_info":re.sub(r"\s+(?:nps|time)\s+\d+","",last.strip()),"bestmove":bests[0],"nodes":int(nodes[1]) if nodes else 0})
    totals=re.findall(r"^Nodes searched\s*:\s*(\d+)\s*$",stderr,re.M)
    need(totals==[str(sum(r["nodes"] for r in normalized))] and int(totals[0])>0,"Bench total differs")
    return {"positions":51,"nodes":int(totals[0]),"search_signature_sha256":sha(canonical(normalized)),"per_position_nodes":[r["nodes"] for r in normalized]}

def perft(stdout,stderr,network):
    need(not stderr.strip(),"Unexpected perft stderr")
    block,=ready_blocks(stdout,stderr,1); lines,=acknowledgements(block,network,1)
    divides={}; totals=[]
    for line in lines:
        if not line.strip() or line.startswith("info string Network replica "): continue
        if match:=re.fullmatch(r"([a-h][1-8][a-h][1-8][qrbn]?): (\d+)",line):
            need(match[1] not in divides and not totals,"Duplicate/out-of-order perft divide"); divides[match[1]]=int(match[2])
        else:
            match=re.fullmatch(r"Nodes searched: (\d+)",line); need(match,"Unknown perft output"); totals.append(int(match[1]))
    need(set(divides)=={m.uci() for m in chess.Board().legal_moves} and totals==[197281] and sum(divides.values())==197281,"Perft4 differs")
    return {"nodes":197281,"first_ply_divides":divides,"unreported_interior_reconstructed":False}
