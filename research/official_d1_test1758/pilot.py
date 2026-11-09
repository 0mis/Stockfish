"""Hosted-only initial corpus, training throughput and exact-D1 parity pilot."""
from collections import Counter
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import struct
import subprocess
import sys
import time
import traceback
import urllib.request
import zipfile
import chess
import corpus
import native
import official_release

HERE = Path(__file__).resolve().parent
BRANCH = "research/official-d1-test1758-diagnostic-ci-v1"


def download(url, target, size, digest):
    request = urllib.request.Request(url,headers={"User-Agent":"0mis-official-d1-test1758-diagnostic"})
    count, hashed = 0, hashlib.sha256()
    with urllib.request.urlopen(request,timeout=180) as stream, Path(target).open("xb") as output:
        while block := stream.read(1024*1024):
            count += len(block); corpus.need(count<=size,"Pinned public input exceeds size")
            output.write(block); hashed.update(block)
    corpus.need((count,hashed.hexdigest())==(size,digest),"Pinned public input hash/size differs")


def prepare_models(work,out):
    plan = corpus.policy()
    work.mkdir(parents=True,exist_ok=False)
    student_exe, student_net, student_receipt = official_release.prepare(work/"official")
    teacher = plan["teacher"]
    archive = work/"history-release.zip"
    download(teacher["release_url"],archive,teacher["release_bytes"],teacher["release_sha256"])
    teacher_exe = work/"history.exe"
    with zipfile.ZipFile(archive) as bundle:
        info = bundle.getinfo("stockfish-history-floor-bmi2.exe")
        corpus.need(info.file_size==teacher["native_engine_bytes"],"Exact public history executable size differs")
        with bundle.open(info) as stream:
            official_release.copy_checked(stream,teacher_exe,teacher["native_engine_bytes"],teacher["native_engine_sha256"])
        raw = bundle.read("source-tested-d19047cc.tar.gz")
        corpus.need(corpus.sha(raw)=="2057e5b8f19a079e4f7d3e03e5129361975b738b7a9dcf073647a3d2c4522927","Public corresponding teacher source differs")
        (out/"teacher-corresponding-source.tar.gz").write_bytes(raw)
    teacher_net=work/teacher["network"]
    download(teacher["network_url"],teacher_net,teacher["network_bytes"],teacher["network_sha256"])
    receipt={"schema":"official-d1-test1758-model-inputs/1","plan_sha256":corpus.PLAN_SHA,
             "student":student_receipt,"student_executable":corpus.reference(student_exe),
             "student_network":corpus.reference(student_net),
             "teacher_executable":corpus.reference(teacher_exe),"teacher_network":corpus.reference(teacher_net),
             "teacher_public_release":{"url":teacher["release_url"],"bytes":teacher["release_bytes"],"sha256":teacher["release_sha256"]},
             "teacher_source":teacher["native_source"],"teacher_source_tree":teacher["native_source_tree"],
             "teacher_upstream_network_source":teacher["upstream_source"],"original_network_author":"Shawn Xu (xu-shawn)",
             "teacher_net_unmodified":True,"student_parent_unmodified":True,"network_license":"CC0-1.0"}
    corpus.put(out/"MODEL-INPUTS.json",receipt)
    return student_exe,student_net,teacher_exe,teacher_net


def public_source_gate(source,out):
    plan=corpus.policy()
    head=subprocess.check_output(["git","-C",str(source),"rev-parse","HEAD"],text=True).strip()
    tree=subprocess.check_output(["git","-C",str(source),"rev-parse","HEAD^{tree}"],text=True).strip()
    corpus.need((head,tree)==(plan["student"]["source"],plan["student"]["tree"]),"Exact original official source differs")
    manifest=json.loads((HERE/"official-source-manifest.json").read_bytes())
    for relative,ref in manifest["files"].items():
        raw=(source/relative).read_bytes()
        corpus.need(len(raw)==ref["bytes"] and corpus.sha(raw)==ref["sha256"],"Official source differs: "+relative)
    corpus.put(out/"SOURCE-IDENTITY.json",{"source_commit":head,"source_tree":tree,
               "manifest":corpus.reference(HERE/"official-source-manifest.json"),"files":len(manifest["files"]),
               "driver_commit":os.environ["GITHUB_SHA"],"plan_sha256":corpus.PLAN_SHA})


def cache_children(labels,student_net,out):
    sys.path.insert(0,str(HERE/"d1lib"))
    from stage2 import features_v19 as features,nnue_v19
    from stage4.d1_fc2_study import forward,weights
    raw=student_net.read_bytes()
    corpus.need(corpus.sha(raw)==weights.PARENT_SHA256,"Exact original official D1 required")
    network=nnue_v19.Network(raw,expected_sha256=weights.PARENT_SHA256)
    builder=forward.CacheBuilder(network)
    started=time.monotonic()
    cached=[]
    for parent in labels:
        for child in parent["children"]:
            if not child["ordinary_static_available"]:
                continue
            board=chess.Board(child["fen6"]); context=forward.eval_context(board)
            label={"source_root_index":len(cached),"root_fen6":child["fen6"],
                   "component_id":parent["family_id"],"fold":"train","head":features.stack_bucket(board),
                   "rule50":context.rule50,"simple_eval_stm":context.simple_eval_stm,
                   "static_material":context.static_material,"wdl_material":context.wdl_material}
            row=builder.row(label).record()
            cached.append({**row,"parent_root_id":parent["root_id"],"rank":child["rank"],
                           "child_position_id":child["position_id"],"partition":parent["partition"],
                           "teacher_consensus_pair_eligible":parent["eligible"]})
    elapsed=time.monotonic()-started
    with (out/"PILOT-CHILD-CACHE.jsonl").open("xb") as output:
        for row in cached:
            output.write(corpus.canonical(row)+b"\n")
    return cached,elapsed,forward


def native_parity(executable,network,rows,out,forward,name="original-pilot-parity"):
    if not rows:
        return {"status":"NO_MEASURABLE_PILOT_CHILDREN","rows":0,"elapsed_seconds":0}
    commands=["uci","setoption name NumaPolicy value none","setoption name Threads value 1",
              "setoption name Hash value 16","setoption name UCI_Chess960 value false",
              "setoption name EvalFile value "+str(network),"isready"]
    expected=[]
    for row in rows:
        commands.extend(("position fen "+row["root_fen6"],"eval","isready"))
        cp=row["base_static_cp_stm"]
        expected.append({"source_root_index":row["source_root_index"],"head":row["head"],
                         "raw_nnue_i32":row["base_raw_nnue_i32"],
                         "static_cp_white":cp if row["side_to_move"]=="w" else -cp})
    commands.append("quit"); stdin=("\n".join(commands)+"\n").encode()
    (out/(name+".stdin")).write_bytes(stdin)
    plan=corpus.policy()
    request={"expected":expected,"max_stdout_bytes":max(8*1024**2,len(rows)*65536),
             "eval_file":str(network),"engine_sha256":plan["student"]["engine_sha256"],
             "eval_file_sha256":plan["student"]["network_sha256"],"candidate_tail_sha256":"original-unmodified-D1",
             "stdin_sha256":corpus.sha(stdin)}
    corpus.put(out/(name+".request.json"),request)
    engine_before,net_before=corpus.reference(executable),corpus.reference(network)
    started=time.monotonic()
    with (out/(name+".stdout")).open("xb") as stdout,(out/(name+".stderr")).open("xb") as stderr:
        done=subprocess.run([str(executable)],cwd=executable.parent,input=stdin,stdout=stdout,stderr=stderr,timeout=300)
    elapsed=time.monotonic()-started
    result=forward.verify_eval_transcript(request,(out/(name+".stdout")).read_bytes(),(out/(name+".stderr")).read_bytes(),done.returncode)
    corpus.need(engine_before==corpus.reference(executable) and net_before==corpus.reference(network),"Original native inputs changed")
    receipt={"schema":"official-d1-test1758-original-native-parity/1","status":"ORIGINAL_RAW_AND_ORDINARY_CP_PARITY_PASSED",
             "rows":len(rows),"elapsed_seconds":elapsed,"native_numeric_result":result,"engine":engine_before,"network":net_before,
             "stdin":corpus.reference(out/(name+".stdin")),"stdout":corpus.reference(out/(name+".stdout")),
             "stderr":corpus.reference(out/(name+".stderr")),"returncode":done.returncode,"inputs_rehashed_before_after":True}
    corpus.put(out/(name+".accepted.json"),receipt)
    return receipt


def run(source,out):
    corpus.need(os.environ.get("GITHUB_ACTIONS")=="true" and sys.platform=="win32"
                and os.environ.get("GITHUB_REPOSITORY")=="0mis/Stockfish"
                and os.environ.get("GITHUB_REF")=="refs/heads/"+BRANCH,"Hosted exact-branch Windows-only native scope")
    out.mkdir(parents=True,exist_ok=False)
    try:
        plan=corpus.policy()
        public_source_gate(source,out)
        corpus_report=corpus.build(out/"corpus")
        # This process never opens development/final/screen root files.
        train=[json.loads(line) for line in (out/"corpus/train.jsonl").read_bytes().splitlines()]
        train.sort(key=lambda row:corpus.sha(("calibration|"+row["root_id"]).encode()))
        roots=train[:64]
        corpus.need(len(roots)==64,"At least64 admitted training roots required for calibration")
        work=Path(os.environ["RUNNER_TEMP"])/"official-d1-test1758-pilot-models"
        student_exe,student_net,teacher_exe,teacher_net=prepare_models(work,out)
        teacher=native.UCI(teacher_exe,teacher_net,out,"teacher-training-calibration")
        labels,exposures=[],[]
        loop_started=time.monotonic()
        try:
            for index,root in enumerate(roots):
                if root["source_reasons"]:
                    label={"root_id":root["root_id"],"family_id":root["family_id"],"partition":"train",
                           "eligible":False,"reasons":root["source_reasons"],"children":[],"origins":root["origins"]}
                else:
                    low,exposure=native.parse_search(root,teacher.search(root,65536))
                    exposures.append(exposure)
                    high=None
                    if low["complete_exact_top_two"]:
                        moves=[row["move"] for row in low["complete_exact_top_two"]]
                        high,exposure=native.parse_search(root,teacher.search(root,262144,moves))
                        exposures.append(exposure)
                    label=native.consensus(root,low,high)
                labels.append(label)
                if (index+1)%16==0:
                    print("PILOT ACQUIRED",index+1,"eligible",sum(row["eligible"] for row in labels),flush=True)
        finally:
            teacher.close()
        loop_elapsed=time.monotonic()-loop_started
        with (out/"PILOT-LABELS.jsonl").open("xb") as output:
            for row in labels:
                output.write(corpus.canonical(row)+b"\n")
        exposure_ref=corpus.gzip_rows(out/"PILOT-TEACHER-EXPOSURE.jsonl.gz",exposures)
        cached,cache_elapsed,forward=cache_children(labels,student_net,out)
        parity=native_parity(student_exe,student_net,cached,out,forward)
        total_roots=corpus_report["partitions"]["train"]["rows"]+corpus_report["partitions"]["development"]["rows"]
        requested=sum(row["budget_nodes"] for label in labels for row in (label.get("low"),label.get("high")) if row)
        search_seconds=sum(row["elapsed_seconds"] for label in labels for row in (label.get("low"),label.get("high")) if row)
        need_search=total_roots*327680
        predicted_search=1.5*max(loop_elapsed/64*total_roots,search_seconds/max(1,requested)*need_search)
        predicted_cache=1.5*(cache_elapsed+parity["elapsed_seconds"])/max(1,len(cached))*total_roots*2
        predicted_total=predicted_search+predicted_cache
        feasible=requested>0 and predicted_total<=10800 and predicted_total/4<=3600
        result={"schema":"official-d1-test1758-training-pilot/1",
                "status":"PILOT_SOURCE_TEACHER_AND_NATIVE_PARITY_VERIFIED" if parity["rows"] else "PILOT_HAS_NO_MEASURABLE_CHILDREN",
                "plan_sha256":corpus.PLAN_SHA,"driver_commit":os.environ["GITHUB_SHA"],
                "run_id":os.environ["GITHUB_RUN_ID"],"corpus":corpus.reference(out/"corpus/CORPUS.json"),
                "calibration_roots":64,"eligible_consensus_pairs":sum(row["eligible"] for row in labels),
                "ordinary_measurable_children":len(cached),"unavailable_reasons":dict(Counter(reason for row in labels for reason in row["reasons"])),
                "requested_nodes":requested,"actual_reported_nodes":sum(row["actual_reported_max_nodes"] for label in labels for row in (label.get("low"),label.get("high")) if row),
                "search_seconds":search_seconds,"full_loop_seconds":loop_elapsed,
                "cache_seconds":cache_elapsed,"native_parity":parity,"teacher_exposure":exposure_ref,
                "resource_prediction":{"train_development_roots":total_roots,"requested_nodes_ceiling":need_search,
                                       "conservative_search_worker_seconds":predicted_search,
                                       "conservative_cache_and_parity_worker_seconds":predicted_cache,
                                       "conservative_total_worker_seconds":predicted_total,
                                       "four_shard_mean_worker_seconds":predicted_total/4,
                                       "within_frozen_resource_bounds":feasible},
                "development_or_final_searches_evaluations_or_features_opened":False,
                "fitting_performed":False,"methods_changed_from_pilot_outcomes":False,
                "no_current_or_formal_match_data_read":True,"no_elo_claim":True}
        corpus.put(out/"PILOT-RESULT.json",result)
        corpus.put(out/"ARTIFACT-MANIFEST.json",{"schema":"official-d1-test1758-artifact-manifest/1",
                   "files":[{"path":path.relative_to(out).as_posix(),"bytes":path.stat().st_size,
                             "sha256":hashlib.sha256(path.read_bytes()).hexdigest()}
                            for path in sorted(out.rglob("*")) if path.is_file()]})
        print(json.dumps({key:result[key] for key in ("status","calibration_roots","eligible_consensus_pairs",
                          "ordinary_measurable_children","unavailable_reasons","resource_prediction")}),flush=True)
    except BaseException as error:
        corpus.put(out/"FAILURE.json",{"schema":"official-d1-test1758-pilot-failure/1","type":type(error).__name__,
                   "message":str(error),"traceback":traceback.format_exc(),"plan_sha256":corpus.PLAN_SHA})
        raise


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--source",type=Path,required=True)
    parser.add_argument("--out",type=Path,required=True)
    args=parser.parse_args()
    run(args.source.resolve(),args.out.resolve())
