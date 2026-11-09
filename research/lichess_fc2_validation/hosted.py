"""Hosted-only, no-build verification of one Root-approved sparse FC2 network."""
import argparse
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import re
import shutil
import stat
import sys
import urllib.parse
import urllib.request
import zipfile
import chess
from lichess_fc2 import core
from fc2_activations import protocol
from . import native,validation as v

REFERENCE_SHA="12a8e7bfdd9b1189b4350bd9ebef3501be1e89bd5fa41dcb56921f85ba23869a"
TRAIN_ACCEPT_SHA="55e997c3c5835dc8e76574333330111157af41b9d4b214535a49d33eb2079eba"
DIAGNOSTIC_SHA="f6d79b2df6be65183500ad406869247d9d7c31c11e9e0de202cd2c50f43b8f15"
MAP_SHA="1182e738a34ab7143808e987174c30dda94f5f081dfa29feba815af43d73527b"
MAP_CONTENT_SHA="c26951c4be00aefcb913579e3e4ad55dac5b5fcc3836765491c8dd4b67d4493f"
PROJECTED_MAP_SHA="786502efa2fe9b3e16acdb58ac3f329923bcc7d102e48913b71aa51799ccfd93"
need=native.need

def json_bytes(raw):
    def pairs(items):
        result={}
        for k,value in items:
            need(k not in result,"Duplicate JSON key"); result[k]=value
        return result
    return json.loads(raw,object_pairs_hook=pairs)

def inside(root,name):
    p=PurePosixPath(name)
    need(not p.is_absolute() and ".." not in p.parts and "\\" not in name and ":" not in name,"Unsafe manifest path")
    target=root.joinpath(*p.parts)
    need(target.resolve(strict=True).is_relative_to(root.resolve(strict=True)),"Manifest path escaped source tree")
    s=target.lstat()
    need(stat.S_ISREG(s.st_mode) and not getattr(s,"st_file_attributes",0)&0x400,"Linked/nonregular manifest source")
    return target

def local(root,ref):
    path=inside(root,ref["file"])
    need(path.stat().st_size==ref["bytes"]<=16*1024**2,"Metadata size differs")
    raw=path.read_bytes(); need(native.sha(raw)==ref["sha256"],"Metadata SHA differs: "+path.name)
    return raw

class Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        need(urllib.parse.urlsplit(newurl).scheme=="https","Non-HTTPS artifact redirect")
        follow=super().redirect_request(req,fp,code,msg,headers,newurl)
        if follow and urllib.parse.urlsplit(req.full_url).netloc!=urllib.parse.urlsplit(newurl).netloc: follow.remove_header("Authorization")
        return follow

def api(path):
    token=os.environ.get("GH_TOKEN","")
    need(bool(token),"Read-only Actions credential unavailable")
    req=urllib.request.Request("https://api.github.com/"+path,headers={"Accept":"application/vnd.github+json","User-Agent":"frozen-fc2-heldout-verifier/1","Authorization":"Bearer "+token})
    return urllib.request.build_opener(Redirect()).open(req,timeout=60)

def artifact(asset,target,work):
    """Bind API descriptor, ZIP size/SHA, safe roster and the one executable member."""
    need(asset["repository"]=="0mis/Stockfish" and type(asset["run_id"]) is int and type(asset["artifact_id"]) is int,"Foreign artifact source")
    prefix="repos/"+asset["repository"]+"/actions/artifacts/"+str(asset["artifact_id"])
    with api(prefix) as response: meta=json_bytes(response.read(1024*1024))
    need(meta["id"]==asset["artifact_id"] and meta["name"]==asset["artifact_name"] and meta["size_in_bytes"]==asset["zip_bytes"]
        and meta["digest"]=="sha256:"+asset["zip_sha256"] and meta["expired"] is False
        and meta["workflow_run"]["id"]==asset["run_id"] and meta["workflow_run"]["head_sha"]==asset["head_sha"],"Artifact immutable descriptor differs")
    archive=work/(target.stem+".zip")
    size=0; digest=hashlib.sha256()
    with api(prefix+"/zip") as response,archive.open("xb") as destination:
        while block:=response.read(65536):
            size+=len(block); need(size<=asset["zip_bytes"],"Oversized artifact")
            destination.write(block); digest.update(block)
    need(size==asset["zip_bytes"] and digest.hexdigest()==asset["zip_sha256"],"Artifact ZIP bytes/SHA differ")
    with zipfile.ZipFile(archive) as z:
        infos=z.infolist()
        need(len(infos)<=1000 and sum(i.file_size for i in infos)<=512*1024**2
            and len({i.filename.casefold() for i in infos})==len(infos),"Unsafe/excessive artifact roster")
        for i in infos:
            p=PurePosixPath(i.filename)
            need(not p.is_absolute() and ".." not in p.parts and "\\" not in i.filename and ":" not in i.filename
                and not i.flag_bits&1 and not stat.S_ISLNK(i.external_attr>>16),"Unsafe artifact member")
        i=z.getinfo(asset["member"])
        need(not i.is_dir() and i.file_size==asset["bytes"],"Executable member differs")
        digest=hashlib.sha256(); size=0
        with z.open(i) as source,target.open("xb") as destination:
            while block:=source.read(65536): destination.write(block); digest.update(block); size+=len(block)
        need(size==asset["bytes"] and digest.hexdigest()==asset["sha256"],"Extracted executable member SHA differs")
    return {"repository":asset["repository"],"run_id":asset["run_id"],"artifact_id":asset["artifact_id"],
        "zip_sha256":asset["zip_sha256"],"member":asset["member"],**native.ref(target)}

def parent_network(reference,target):
    need(native.ref(reference)["sha256"]==REFERENCE_SHA and reference.stat().st_size==142322823,"Accepted ordinary executable differs")
    offset=1251840; expected="9f9a9166181aaaf78b61d6a5d2fac88b18d4169e85b6f94c50c9573d1fecd933"
    digest=hashlib.sha256()
    with reference.open("rb") as source,target.open("xb") as destination:
        source.seek(offset); need(native.sha(source.read(512))==expected,"Original embedded network prefix differs"); source.seek(offset)
        remaining=v.PARENT_BYTES
        while remaining:
            block=source.read(min(65536,remaining)); need(block,"Truncated original network")
            destination.write(block); digest.update(block); remaining-=len(block)
    need(target.stat().st_size==v.PARENT_BYTES and digest.hexdigest()==v.PARENT,"Exact original18cd extraction differs")
    return {**native.ref(target),"embedded_offset":offset,"executable_sha256":REFERENCE_SHA}

def teacher_bytes(ref):
    """Called exclusively inside validation.open_validation after target verification."""
    need(ref["transport"]=="pinned_raw_github","Unknown validation delivery transport")
    parts=urllib.parse.urlsplit(ref["url"])
    path=[x for x in parts.path.split("/") if x]
    need(parts.scheme=="https" and parts.netloc=="raw.githubusercontent.com" and not parts.query and not parts.fragment
        and len(path)>=4 and path[0]=="0mis" and path[1] in ("Stockfish","stockfish-weight-space-lab") and re.fullmatch("[0-9a-f]{40}",path[2]),"Validation URL must bind one immutable approved GitHub commit")
    req=urllib.request.Request(ref["url"],headers={"User-Agent":"frozen-fc2-heldout-verifier/1","Accept-Encoding":"identity"})
    with urllib.request.urlopen(req,timeout=60) as response:
        need(response.geturl()==ref["url"],"Unexpected validation redirect")
        raw=response.read(ref["bytes"]+1)
    need(len(raw)==ref["bytes"] and native.sha(raw)==ref["sha256"],"Validation download bytes/SHA differ")
    return raw

def run(source,manifest_path,manifest_sha,work,out):
    need(os.environ.get("GITHUB_ACTIONS")=="true" and sys.platform=="win32" and sys.version_info[:3]==(3,11,9)
        and chess.__version__=="1.11.2","Real execution requires the pinned hosted Windows recipe")
    raw=manifest_path.read_bytes(); need(native.sha(raw)==manifest_sha,"Caller-pinned input manifest differs")
    manifest=json_bytes(raw)
    need(manifest["schema"]=="lichess-fc2-heldout-inputs/1" and manifest["status"]=="ROOT_APPROVED_FROZEN_INPUTS_READY","Real inputs remain sealed/unapproved")
    need(not work.exists() and not out.exists(),"Preserve existing hosted attempt")
    work.mkdir(); out.mkdir()
    summary={"schema":"lichess-fc2-heldout-verification/1","status":"running","input_manifest_sha256":manifest_sha,
        "driver_commit":os.environ["GITHUB_SHA"],"validation_opened":False,"candidate_ready_for_development_games":False,
        "training_or_selection_performed":False,"verified_gain":False}
    try:
        expected_programs={"research/lichess_fc2/"+name for name in ("__init__.py","core.py","patch.py","synthetic_fixtures.py")}
        expected_programs|={"research/fc2_activations/protocol.py"}
        expected_programs|={"research/lichess_fc2_validation/"+name for name in ("__init__.py","validation.py","native.py","hosted.py","test_validation.py","benchmark.cpp","requirements.txt")}
        need(set(manifest["program_files"])==expected_programs,"Program pin roster differs; no data file may enter the pre-freeze code-read phase")
        for name,ref in manifest["program_files"].items(): local(source,{"file":name,**ref})
        for role,filename in (("frozen","FROZEN.json"),("mapping","FC2-BYTE-MAP.json"),("root_acceptance","ROOT-ACCEPTANCE.json"),("training_acceptance","TRAIN-CI-ACCEPTANCE.json")):
            need(manifest[role]["file"]=="research/lichess_fc2_validation/frozen/"+filename,"Unexpected frozen-input role path")
        wrapper_raw=local(source,manifest["frozen"]); wrapper=json_bytes(wrapper_raw)
        need(wrapper["candidate"]["sha256"]==manifest["expected_candidate_sha256"],"Caller-pinned target SHA differs")
        mapping_raw=local(source,manifest["mapping"]); source_mapping=json_bytes(mapping_raw)
        approval_raw=local(source,manifest["root_acceptance"]); approval=json_bytes(approval_raw)
        accepted_raw=local(source,manifest["training_acceptance"]); accepted=json_bytes(accepted_raw)
        need(native.sha(accepted_raw)==TRAIN_ACCEPT_SHA and accepted["schema"]=="test1757-fc2-train-ci-acceptance/1"
            and accepted["status"]=="TRAIN_CI_ACCEPTED" and accepted["validation_input_read"] is False,"Accepted train diagnostic receipt differs")
        need(native.sha(mapping_raw)==MAP_SHA==accepted["byte_map"]["sha256"] and core.digest(source_mapping)==MAP_CONTENT_SHA==accepted["byte_map_canonical_sha256"],"Frozen accepted map differs")
        mapping=v.normalize_mapping(source_mapping)
        need(core.digest(mapping)==PROJECTED_MAP_SHA,"Lossless map projection differs from the accepted fit adapter")
        need(manifest["artifacts"]["diagnostic"]["sha256"]==DIAGNOSTIC_SHA==accepted["diagnostic_executable"]["sha256"]
            and manifest["artifacts"]["reference"]["sha256"]==REFERENCE_SHA,"Executable pins differ from accepted inputs")
        context=v.verify_frozen(wrapper,mapping,approval,file_sha256=native.sha(wrapper_raw))
        with (out/"SOURCE-BYTE-MAP.json").open("xb") as f: f.write(mapping_raw)
        for name,data in (("FROZEN.json",wrapper),("ROOT-ACCEPTANCE.json",approval),("NORMALIZED-BYTE-MAP.json",mapping)):
            native.emit(out/name,data)
        native.emit(out/"FREEZE-VERIFIED.json",{"frozen_file_sha256":native.sha(wrapper_raw),"wrapper_sha256":wrapper["sha256"],
            "root_acceptance_file_sha256":native.sha(approval_raw),"identity":context["identity"],"validation_opened":False})
        if context["identity"]:
            summary.update(status="identity_receipt_verified_no_validation_opened",identity=True)
            native.emit(out/"RESULT.json",summary); return summary
        reference=work/"reference.exe"; diagnostic=work/"diagnostic.exe"
        reference_ref=artifact(manifest["artifacts"]["reference"],reference,work)
        parent=work/"parent.nnue"; parent_ref=parent_network(reference,parent)
        candidate=work/"candidate.nnue"
        target=v.reconstruct_target(context,parent.read_bytes())
        with candidate.open("xb") as stream: stream.write(target); stream.flush(); os.fsync(stream.fileno())
        del target
        need(native.ref(candidate)["sha256"]==wrapper["candidate"]["sha256"],"Saved frozen target differs")
        native.emit(out/"TARGET-VERIFIED.json",{"parent":parent_ref,"candidate":native.ref(candidate),"inverse_sha256":v.PARENT,
            "only_frozen_coefficient_bytes_changed":True,"biases_unchanged":True,"validation_opened":False})
        diagnostic_ref=artifact(manifest["artifacts"]["diagnostic"],diagnostic,work)
        need((manifest["validation"]["sha256"],manifest["validation"]["bytes"],manifest["validation"]["rows"])
            ==(v.VALIDATION_SHA,v.VALIDATION_BYTES,68),"Prospective heldout metadata changed")
        saved_raw=[]
        def heldout_reader():
            raw=teacher_bytes(manifest["validation"]); saved_raw.append(raw); return raw
        bundle=v.open_validation(context,heldout_reader,manifest["validation"])
        summary["validation_opened"]=True
        with (out/"VALIDATION-ORIGINAL.json").open("xb") as f: f.write(saved_raw[0])
        native.emit(out/"VALIDATION-NORMALIZED.json",bundle)
        requests,account=v.requests(bundle)
        native.emit(out/"REQUESTS.json",{"requests":requests,"all_parent_and_child_dispositions":account,"supplied_rows":68})
        captures={}
        if requests:
            commands="\n".join("capture "+str(r["row_id"])+" fen "+r["fen"]+" moves "+r["move"] for r in requests)+"\nquit\n"
            stdout,stderr=native.capture(diagnostic,["--fc2-activations-v1","--network",str(parent)],work,out,"original-diagnostic",commands)
            need(not stderr.strip(),"Diagnostic capture stderr differs")
            rows=[json_bytes(line) for line in stdout.splitlines()]
            need([r["row_id"] for r in rows]==list(range(len(requests))),"Diagnostic capture missing/duplicate requests")
            for request,row in zip(requests,rows,strict=True):
                protocol.validate(row,mapping=mapping["raw_to_physical"],readout=protocol.serialized_readout(parent,row["physical_head"]),
                    expected_board=chess.Board(request["child_fen"]))
                need(row["static_loss_available"] is True,"Eligible child lost ordinary static availability")
                cap={**row,**{k:request[k] for k in ("parent_position_id","rank","component_id","component_split_sha256","source_component_split_sha256","split","child_position_id")},
                    "position_id":request["child_position_id"],"network_sha256":v.PARENT,"parent_sha256":v.PARENT,
                    "byte_map_sha256":core.digest(mapping),"source_byte_map_sha256":MAP_CONTENT_SHA,"byte_map_file_sha256":native.sha(mapping_raw),
                    "diagnostic_executable_sha256":DIAGNOSTIC_SHA,"validation_input_sha256":v.VALIDATION_SHA}
                captures[(request["parent_position_id"],request["rank"])]=cap
        native.emit(out/"ORIGINAL-ACTIVATIONS.json",list(captures.values()))
        ordinary={}
        for role,network in (("parent",parent),("candidate",candidate)):
            if requests:
                tail=[token for r in requests for token in ("position fen "+r["child_fen"],"eval","isready")]
                stdout,stderr=native.capture(reference,[],work,out,role+"-ordinary",native.commands(network,tail))
                ordinary[role]=native.ordinary(stdout,stderr,network,requests)
            else: ordinary[role]={}
            native.emit(out/(role.upper()+"-ORDINARY.json"),[{"parent_position_id":k[0],"rank":k[1],**r} for k,r in ordinary[role].items()])
        proxy,actual=v.reports(context,bundle,captures,ordinary["parent"],ordinary["candidate"])
        native.emit(out/"PREDICTED-VALIDATION.json",proxy); native.emit(out/"ACTUAL-VALIDATION.json",actual)
        specs=native.benchmark_specs((Path(__file__).with_name("benchmark.cpp")).read_bytes())
        qualification={}
        for role,network in (("parent",parent),("candidate",candidate)):
            benches=[]
            for number in (1,2):
                stdout,stderr=native.capture(reference,[],work,out,role+"-bench-"+str(number),native.commands(network,["bench 16 1 13 default depth","isready"]))
                benches.append(native.bench(stdout,stderr,network,specs))
            need(benches[0]==benches[1],"Canonical bench did not repeat exactly")
            if role=="parent": need(benches[0]["nodes"]==3464885,"Accepted ordinary parent bench differs")
            stdout,stderr=native.capture(reference,[],work,out,role+"-perft4",native.commands(network,["position startpos","go perft 4","isready"]))
            qualification[role]={"benches":benches,"perft4":native.perft(stdout,stderr,network)}
        native.emit(out/"NATIVE-QUALIFICATION.json",qualification)
        need(native.ref(reference)["sha256"]==REFERENCE_SHA and native.ref(diagnostic)["sha256"]==DIAGNOSTIC_SHA
            and native.ref(parent)["sha256"]==v.PARENT and native.ref(candidate)["sha256"]==wrapper["candidate"]["sha256"],"A frozen binary/network changed during verification")
        qualified=out/"qualified-candidate"; qualified.mkdir()
        candidate.rename(qualified/("nn-"+wrapper["candidate"]["sha256"][:12]+".nnue"))
        summary.update(status="frozen_candidate_structurally_and_functionally_verified",identity=False,candidate_ready_for_development_games=True,
            candidate_sha256=wrapper["candidate"]["sha256"],candidate_bytes=wrapper["candidate"]["bytes"],qualified_network="qualified-candidate/nn-"+wrapper["candidate"]["sha256"][:12]+".nnue",
            root_denominator=68,eligible_children=len(requests),unavailable_children=136-len(requests),all_child_native_parity=True,
            holdout_proxy_metrics=proxy["metrics_on_matched_rows"],holdout_actual_metrics=actual["metrics_on_matched_rows"],
            reference_artifact=reference_ref,diagnostic_artifact=diagnostic_ref,qualification=qualification,
            holdout_improvement_gate_applied=False,formal_alpha_reserved=False)
        native.emit(out/"RESULT.json",summary); return summary
    except BaseException as exc:
        native.emit(out/"FAILED.json",{**summary,"status":"verification_failed","error_type":type(exc).__name__,
            "reason":str(exc) if isinstance(exc,(ValueError,AssertionError)) else "External/native failure; inspect retained capture records.",
            "candidate_ready_for_development_games":False,"selection_reopened":False})
        raise

def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--manifest",type=Path,required=True); cli.add_argument("--manifest-sha256",required=True)
    cli.add_argument("--work",type=Path,required=True); cli.add_argument("--output",type=Path,required=True)
    args=cli.parse_args(); need(re.fullmatch("[0-9a-f]{64}",args.manifest_sha256),"Explicit input-manifest SHA is required")
    source=Path(__file__).resolve().parents[2]
    print(json.dumps(run(source,args.manifest.resolve(),args.manifest_sha256,args.work.resolve(),args.output.resolve()),sort_keys=True))

if __name__=="__main__": main()
