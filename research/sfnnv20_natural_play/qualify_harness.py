"""Build only the observer; qualify on existing public canonical fixtures."""
import argparse,hashlib,io,json,mmap,os,re,shutil,struct,subprocess,tarfile,time,traceback,urllib.request,zipfile
from pathlib import Path
import chess
import common as c
import capture_protocol as p
import patch_source

HEADER=struct.pack("<III",0x6A448AFA,0x9A4FCB66,84)
PREFIX_SHA="9f9a9166181aaaf78b61d6a5d2fac88b18d4169e85b6f94c50c9573d1fecd933"
NET_BYTES=138360528


def run(out,name,argv,cwd,stdin=b"",timeout=600):
    record={"schema":"sfnnv20-native-process/1","argv":list(map(str,argv)),"cwd":str(cwd),
            "started_utc_epoch":time.time(),"returncode":None}
    if stdin:
        (out/(name+".stdin")).write_bytes(stdin);record["stdin"]=c.ref(out/(name+".stdin"),out)
    try:
        with (out/(name+".stdout")).open("xb") as stdout,(out/(name+".stderr")).open("xb") as stderr:
            process=subprocess.run(list(map(str,argv)),cwd=cwd,input=stdin,stdout=stdout,stderr=stderr,timeout=timeout)
        record["returncode"]=process.returncode
        c.need(process.returncode==0,"Native/build process failed: "+name)
    finally:
        record["finished_utc_epoch"]=time.time()
        for suffix in ("stdout","stderr"):
            path=out/(name+"."+suffix)
            if path.exists():record[suffix]=c.ref(path,out)
        c.put(out/(name+".process.json"),record)
    return (out/(name+".stdout")).read_bytes(),(out/(name+".stderr")).read_bytes(),record


def fetch_oracle(work,out,plan):
    pin=plan["ordinary_oracle"]
    archive=work/"accepted-ordinary-input.zip"
    with archive.open("xb") as target:
        done=subprocess.run(["gh","api",f"repos/0mis/Stockfish/actions/artifacts/{pin['archive_artifact_id']}/zip"],
                            stdout=target,stderr=subprocess.PIPE,timeout=180)
    c.need(done.returncode==0 and archive.stat().st_size==pin["archive_bytes"]
           and c.file_sha(archive)==pin["archive_sha256"],"Accepted ordinary source artifact differs")
    oracle=out/"bin/baseline-stockfish";oracle.parent.mkdir()
    with zipfile.ZipFile(archive) as bundle:
        info=bundle.getinfo(pin["member"])
        c.need(info.file_size==pin["executable_bytes"],"Exact ordinary ELF member size differs")
        with bundle.open(info) as stream,oracle.open("xb") as target:
            shutil.copyfileobj(stream,target,1024**2)
    c.need(c.file_sha(oracle)==pin["executable_sha256"],"Exact ordinary43e ELF differs")
    oracle.chmod(0o755)
    parent=work/"nn-18cd32e11a9c.nnue"
    with oracle.open("rb") as original,mmap.mmap(original.fileno(),0,access=mmap.ACCESS_READ) as image:
        candidates=[];start=image.find(HEADER)
        while start>=0:
            if c.sha(image[start:start+512])==PREFIX_SHA:candidates.append(start)
            start=image.find(HEADER,start+1)
        c.need(len(candidates)==1,"Exactly one authenticated embedded18cd prefix required")
        offset=candidates[0]
        c.need(offset+NET_BYTES<=len(image),"Embedded network truncated")
        with parent.open("xb") as target:
            for begin in range(offset,offset+NET_BYTES,1024**2):
                target.write(image[begin:min(offset+NET_BYTES,begin+1024**2)])
    c.need(parent.stat().st_size==NET_BYTES and c.file_sha(parent)==c.PARENT,"Full extracted original NNUE differs")
    c.put(out/"ORDINARY-INPUT.json",{"schema":"sfnnv20-accepted-ordinary-input/1","source":c.SOURCE,"tree":c.TREE,
          "ordinary":c.ref(oracle,out),"accepted_artifact":pin,"embedded_parent_offset":offset,
          "parent":c.ref(parent),"only_original_baseline_member_opened":True,
          "new_ordinary_build_or_d5_bench_run":False})
    return oracle,parent


def teacher_file(work,plan):
    pin=plan["teacher"];target=work/pin["network"]
    count=0;digest=hashlib.sha256()
    with urllib.request.urlopen(urllib.request.Request(pin["url"],headers={"User-Agent":"0mis-sfnnv20-source-qualification"}),timeout=180) as response,target.open("xb") as output:
        while block:=response.read(1024**2):
            count+=len(block);c.need(count<=pin["expected_serialized_bytes"],"Teacher exceeds pinned size")
            output.write(block);digest.update(block)
    c.need(count==pin["expected_serialized_bytes"] and digest.hexdigest()==c.TEACHER,"Unmodified68752 teacher differs")
    return target


def fixtures(raw):
    c.need(c.sha(raw)=="0a8d3f29278ad53abbceee284721a3ac4ddd7c768052de928672a2869bc2da80","Exact public benchmark source changed")
    begin="const std::vector<std::string> Defaults = {"
    text=raw.decode().split(begin,1)[1].split("\n};",1)[0]
    output=[];mode=False
    for line in text.splitlines():
        line=line.strip()
        if not line or line.startswith("//"):continue
        match=re.fullmatch(r'("(?:[^"\\]|\\.)*")\s*,?\s*(?://[^\r\n]*)?',line)
        c.need(match is not None,"Unknown public fixture grammar")
        value=json.loads(match[1])
        if value.startswith("setoption"):
            mode=value.endswith("true");continue
        if mode:continue
        fen,separator,moves=value.partition(" moves ")
        if len(fen.split())==4:fen+=" 0 1"
        board=chess.Board(fen)
        c.need(board.is_valid(),"Invalid public fixture")
        history=moves.split() if separator else []
        for token in history:board.push(board.parse_uci(token))
        output.append({"source":value,"fen":fen,"moves":history,"final_fen":board.fen(en_passant="legal"),
                       "position_id":c.position_id(board),"in_check":board.is_check(),
                       "terminal":board.is_game_over(claim_draw=False)})
    c.need(len(output)==49,"Exact49 public standard fixtures required")
    return output


def source_overlay(source,diag,out):
    c.need(subprocess.check_output(["git","-C",str(source),"rev-parse","HEAD"],text=True).strip()==c.SOURCE
           and subprocess.check_output(["git","-C",str(source),"rev-parse","HEAD^{tree}"],text=True).strip()==c.TREE,
           "Exact ordinary source checkout differs")
    manifest=json.loads((c.HERE/"SOURCE-OVERLAY.json").read_bytes())
    shutil.copytree(source,diag,ignore=shutil.ignore_patterns(".git"))
    for name,pin in manifest["source_files"].items():
        raw=(source/name).read_bytes()
        c.need(c.sha(raw)==pin["before_sha256"],"Current-source preimage differs")
        patched=patch_source.apply(name,raw)
        c.need(c.sha(patched)==pin["after_sha256"],"Reviewed current-source overlay differs")
        (diag/name).write_bytes(patched)
    for name,pin in manifest["diagnostic_headers"].items():
        raw=(c.HERE/"overlay"/name).read_bytes()
        c.need(c.sha(raw)==pin["sha256"],"Diagnostic header changed")
        (diag/name).write_bytes(raw)
    c.put(out/"SOURCE-IDENTITY.json",{"source_commit":c.SOURCE,"source_tree":c.TREE,
          "driver_commit":os.environ["GITHUB_SHA"],"overlay":manifest,"plan_sha256":c.PLAN_SHA})
    return manifest


def ordinary_request(executable,network,rows,out,name):
    commands=["uci","setoption name NumaPolicy value none","setoption name Threads value 1",
              "setoption name Hash value 16","setoption name UCI_Chess960 value false",
              "setoption name EvalFile value "+str(network),"isready"]
    for row in rows:
        commands.extend(("position fen "+row["final_fen"],"eval","isready"))
    commands.append("quit");body=("\n".join(commands)+"\n").encode()
    stdout,stderr,record=run(out,name,[str(executable)],executable.parent,body)
    c.need(not stderr,"Ordinary source evaluation stderr differs")
    text=stdout.decode().replace("\r\n","\n")
    segments,pending=[],[]
    for line in text.splitlines():
        if line=="readyok":segments.append(pending);pending=[]
        else:pending.append(line)
    c.need(text.splitlines().count("uciok")==1 and len(segments)==len(rows)+1
           and not any(line.strip() for line in pending),"Ordinary source eval framing differs")
    uses=[line for line in text.splitlines() if line.startswith("info string NNUE evaluation using ")]
    c.need(uses and all(line.startswith("info string NNUE evaluation using "+str(network)+" (") for line in uses),
           "Actual ordinary EvalFile path differs")
    scores=[]
    from decimal import Decimal
    for row,lines in zip(rows,segments[1:]):
        raw=[int(m[1]) for line in lines if(m:=re.fullmatch(r"NNUE evaluation\s+([+-]?\d+) \(side to move, internal units\)",line))]
        cp=[int(Decimal(m[1])*100) for line in lines if(m:=re.fullmatch(r"Final evaluation\s+([+-]?\d+\.\d{2}) \(white side\) \[with scaled NNUE, \.\.\.\]",line))]
        c.need(len(raw)==len(cp)==1,"Exactly one actual ordinary raw/CP score required")
        scores.append({"position_id":row["position_id"],"raw_nnue":raw[0],"white_cp":cp[0]})
    return scores,record


def qualify(source,out):
    plan=c.hosted("qualification");out.mkdir(parents=True,exist_ok=False)
    try:
        work=Path(os.environ["RUNNER_TEMP"])/"sfnnv20-current-harness-qualification";work.mkdir()
        ordinary,parent=fetch_oracle(work,out,plan)
        shutil.copyfile(c.HERE/"plan.json",out/"PLAN.json")
        diag=work/"diagnostic-source";source_overlay(source,diag,out)
        tracked=subprocess.check_output(["git","-C",str(source),"ls-files","-z"]).decode().split("\0")
        with tarfile.open(out/"diagnostic-corresponding-source.tar.gz","w:gz") as archive:
            for name in sorted(set(filter(None,tracked))|set(patch_source.HEADERS)):
                archive.add(diag/name,arcname="Stockfish-source/"+name,recursive=False)
        shutil.copyfile(parent,diag/"src/nn-18cd32e11a9c.nnue")
        flags="-DNNUE_EMBEDDING_OFF -DSF_SFNNV20_FC2_DIAGNOSTIC"
        run(out,"build-observer",["make","-j2","build","ARCH=x86-64-bmi2","COMP=gcc","EXTRACXXFLAGS="+flags],diag/"src",timeout=1200)
        observer=out/"bin/fc2-observer";shutil.copyfile(diag/"src/stockfish",observer);observer.chmod(0o755)
        mapping=p.network_layout(parent)
        heads=[]
        for index in range(92):
            bias,weights=p.serialized_readout(parent,index)
            offset=NET_BYTES-92*35208+index*35208+35076
            heads.append({"physical_head":index,"bias_offset":offset,"bias_before":bias,
                          "weight_offsets":[offset+4+i for i in range(128)],"weights":weights,
                          "serialized_to_native":[p.physical_slot(i) for i in range(128)]})
        byte_map={"schema":"sfnnv20-fc2-byte-map/1","source_commit":c.SOURCE,"network_sha256":c.PARENT,
                  "network_bytes":NET_BYTES,"stored_heads":92,"raw_routes":192,"raw_to_physical":mapping,"heads":heads,
                  "quantizer":{"combined":"checked_int32","multiply":"int64","numerator":9600,"first_divisor":16384,
                               "second_divisor":16,"rounding":"toward_zero"}}
        c.put(out/"FC2-BYTE-MAP.json",byte_map)
        specs=fixtures((source/"src/benchmark.cpp").read_bytes())
        c.put(out/"PUBLIC-FIXTURES.json",specs)
        supported=[row for row in specs if not row["terminal"]]
        commands=[]
        for index,row in enumerate(supported):
            command="capture "+str(index)+" fen "+row["fen"]
            if row["moves"]:command+=" moves "+" ".join(row["moves"])
            commands.append(command)
        commands.append("quit")
        stdout,stderr,process=run(out,"public-fixture-capture",[str(observer),"--fc2-activations-v1","--network",str(parent)],
                                 observer.parent,("\n".join(commands)+"\n").encode())
        c.need(not stderr,"Diagnostic fixture stderr differs")
        captured=[json.loads(line) for line in stdout.splitlines()]
        c.need(len(captured)==len(supported),"Every supported public fixture requires one activation row")
        results=[]
        ordinary_specs=[];ordinary_captures=[]
        for index,(row,spec) in enumerate(zip(captured,supported)):
            board=chess.Board(spec["final_fen"])
            c.need(row["row_id"]==index,"Native capture row identity differs")
            report=p.validate(row,mapping=mapping,readout=p.serialized_readout(parent,row["physical_head"]),expected_board=board)
            c.need(row["raw_offset_min"]==-80 and row["raw_offset_max"]==80,"Exact161-offset native lookup required")
            results.append({**report,"position_id":spec["position_id"],"ordinary_available":not spec["in_check"]})
            if not spec["in_check"]:ordinary_specs.append(spec);ordinary_captures.append(row)
        native_scores,native_process=ordinary_request(ordinary,parent,ordinary_specs,out,"ordinary-public-fixture-parity")
        for spec,row,score in zip(ordinary_specs,ordinary_captures,native_scores):
            sign=1 if chess.Board(spec["final_fen"]).turn else -1
            c.need(score["raw_nnue"]==row["raw_nnue"] and score["white_cp"]==sign*row["ordinary_static_cp"],
                   "Fresh current ordinary raw/CP vs capture parity differs")
        teacher=teacher_file(work,plan)
        teacher_scores,teacher_process=ordinary_request(ordinary,teacher,ordinary_specs[:1],out,"unmodified-teacher-public-load")
        c.need(c.file_sha(parent)==c.PARENT and c.file_sha(teacher)==c.TEACHER
               and c.file_sha(ordinary)==plan["ordinary_oracle"]["executable_sha256"],"Qualified inputs changed")
        result={"schema":"sfnnv20-current-native-harness-qualification/1","status":"PUBLIC_FIXTURE_CURRENT_NATIVE_PARITY_PASSED",
                "plan_sha256":c.PLAN_SHA,"source_commit":c.SOURCE,"source_tree":c.TREE,"driver_commit":os.environ["GITHUB_SHA"],
                "ordinary":c.ref(ordinary,out),"observer":c.ref(observer,out),"parent":c.ref(parent),"teacher":c.ref(teacher),
                "byte_map":c.ref(out/"FC2-BYTE-MAP.json",out),"byte_map_content_sha256":c.sha(c.canonical(byte_map)),
                "capture_rows":len(captured),"ordinary_raw_cp_parity_rows":len(native_scores),
                "public_terminal_fixture_holds":sum(row["terminal"] for row in specs),
                "in_check_counterfactual_rows_not_promoted":sum(row["in_check"] for row in supported),
                "source_and_integer_serialized_routing_checks":results,
                "teacher_version_matched_public_load_passed":len(teacher_scores)==1,
                "ordinary_default_bench_bound_to_accepted_prior_receipt":[3464885,3464885],
                "ordinary_bench_or_d5_work_not_duplicated":True,"natural_cohort_downloaded_or_evaluated":False,
                "feature_or_weight_fit_performed":False,"local_native_execution":False}
        c.put(out/"QUALIFICATION.json",result)
        c.put(out/"ARTIFACT-MANIFEST.json",{"schema":"sfnnv20-harness-artifact-manifest/1",
              "files":[c.ref(path,out) for path in sorted(out.rglob("*")) if path.is_file()]})
        print(json.dumps({key:result[key] for key in ("status","capture_rows","ordinary_raw_cp_parity_rows",
                   "public_terminal_fixture_holds","in_check_counterfactual_rows_not_promoted",
                   "natural_cohort_downloaded_or_evaluated")}),flush=True)
    except BaseException as error:
        c.put(out/"FAILURE.json",{"schema":"sfnnv20-harness-failure/1","type":type(error).__name__,
                                "message":str(error),"traceback":traceback.format_exc(),"plan_sha256":c.PLAN_SHA})
        raise


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--source",type=Path,required=True)
    parser.add_argument("--out",type=Path,required=True);args=parser.parse_args()
    qualify(args.source.resolve(),args.out.resolve())
