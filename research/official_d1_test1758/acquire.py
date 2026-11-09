"""Disjoint TRAIN-only acquisition, reusing the fixed64 pilot without reruns."""
from collections import Counter
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
import zipfile
import corpus
import native
import pilot

PILOT_RUN=42695021971
PILOT_ARTIFACT=13708966730
PILOT_BYTES=41010310
PILOT_SHA="85a6fd44d0adc6c06d4f46bdea242daac3f731450d131305fc8078eca485caac"
PILOT_COMMIT="816bac3a6591e2b8295d1d2054a1c559491b02f3"


def shard_roots(roots,shard):
    corpus.need(shard in range(4) and all(row["partition"]=="train" for row in roots),"TRAIN-only valid shard required")
    corpus.need(len({row["root_id"] for row in roots})==len(roots),"Duplicate admitted TRAIN root")
    return sorted((row for row in roots if int(row["root_id"],16)%4==shard),key=lambda row:row["root_id"])


def require_complete_children(labels,complete):
    actual={(row["parent_root_id"],row["rank"]) for row in complete}
    expected={(row["root_id"],child["rank"]) for row in labels for child in row["children"]
              if child["ordinary_static_available"]}
    corpus.need(len(actual)==len(complete) and actual==expected,
                "Every measurable composite child must be retained, including unavailable pairs")


def load_pilot(work,out):
    work.mkdir(parents=True,exist_ok=False)
    archive=work/"immutable-pilot.zip"
    with archive.open("xb") as target,(out/"pilot-download.stderr").open("xb") as errors:
        done=subprocess.run(["gh","api",f"repos/0mis/Stockfish/actions/artifacts/{PILOT_ARTIFACT}/zip"],
                            stdout=target,stderr=errors,timeout=180)
    corpus.need(done.returncode==0 and archive.stat().st_size==PILOT_BYTES
                and hashlib.sha256(archive.read_bytes()).hexdigest()==PILOT_SHA,"Immutable pilot artifact differs")
    selected=["PILOT-RESULT.json","MODEL-INPUTS.json","SOURCE-IDENTITY.json","corpus/CORPUS.json","corpus/train.jsonl",
              "PILOT-LABELS.jsonl","PILOT-CHILD-CACHE.jsonl","original-pilot-parity.accepted.json"]
    bodies={}
    with zipfile.ZipFile(archive) as bundle:
        manifest=json.loads(bundle.read("ARTIFACT-MANIFEST.json"))
        pins={row["path"]:row for row in manifest["files"]}
        for name in selected:
            body=bundle.read(name)
            corpus.need(len(body)==pins[name]["bytes"] and corpus.sha(body)==pins[name]["sha256"],"Pilot payload differs: "+name)
            bodies[name]=body
    result=json.loads(bodies["PILOT-RESULT.json"])
    corpus.need(result["plan_sha256"]==corpus.PLAN_SHA and result["driver_commit"]==PILOT_COMMIT
                and result["status"]=="PILOT_SOURCE_TEACHER_AND_NATIVE_PARITY_VERIFIED","Verified fixed pilot required")
    corpus.need(result["resource_prediction"]["within_frozen_resource_bounds"] is True,"Frozen throughput refusal")
    admission=json.loads(bodies["corpus/CORPUS.json"])
    roots=[json.loads(line) for line in bodies["corpus/train.jsonl"].splitlines()]
    corpus.need(len(roots)==6658 and len({row["root_id"] for row in roots})==len(roots)
                and all(row["partition"]=="train" for row in roots),"Exact TRAIN-only roster differs")
    train_pin=admission["partitions"]["train"]
    corpus.need(corpus.sha(bodies["corpus/train.jsonl"])==train_pin["sha256"]
                and train_pin["sha256"]=="ec6dc5c250226f08e0b6e7edcf67419884ba704dec939fb9321fb9ed04609d26",
                "TRAIN corpus pin differs")
    labels=[json.loads(line) for line in bodies["PILOT-LABELS.jsonl"].splitlines()]
    cached=[json.loads(line) for line in bodies["PILOT-CHILD-CACHE.jsonl"].splitlines()]
    parity=json.loads(bodies["original-pilot-parity.accepted.json"])
    corpus.need(len(labels)==64 and parity["rows"]==len(cached)==94
                and parity["status"]=="ORIGINAL_RAW_AND_ORDINARY_CP_PARITY_PASSED","Pilot original parity differs")
    root_ids={row["root_id"] for row in sorted(roots,key=lambda r:corpus.sha(("calibration|"+r["root_id"]).encode()))[:64]}
    corpus.need(root_ids=={row["root_id"] for row in labels},"Calibration membership differs")
    reference={"schema":"official-d1-test1758-acquisition-pilot-admission/1",
               "run_id":PILOT_RUN,"artifact_id":PILOT_ARTIFACT,"archive_bytes":PILOT_BYTES,
               "archive_sha256":PILOT_SHA,"driver_commit":PILOT_COMMIT,
               "plan_sha256":corpus.PLAN_SHA,"selected_payload_refs":[pins[name] for name in selected],
               "resource_prediction":result["resource_prediction"],
               "final_development_screen_row_contents_opened":False}
    corpus.put(out/"PILOT-ADMISSION.json",reference)
    return roots,{row["root_id"]:row for row in labels},cached


def run(source,out,shard):
    corpus.need(os.environ.get("GITHUB_ACTIONS")=="true" and sys.platform=="win32"
                and os.environ.get("GITHUB_REF")=="refs/heads/"+pilot.BRANCH
                and os.environ.get("GITHUB_REPOSITORY")=="0mis/Stockfish","Hosted exact-branch Windows-only acquisition")
    corpus.need(shard in range(4),"Exact disjoint shard0..3 required")
    out.mkdir(parents=True,exist_ok=False)
    started=time.monotonic()
    try:
        corpus.policy();pilot.public_source_gate(source,out)
        work=Path(os.environ["RUNNER_TEMP"])/("official-d1-test1758-shard-"+str(shard))
        roots,reused,pilot_cache=load_pilot(work/"pilot",out)
        roots=shard_roots(roots,shard)
        corpus.put(out/"SHARD-ROSTER.json",{"schema":"official-d1-test1758-train-shard-roster/1",
                   "partition":"train","shard":shard,"shards":4,"root_ids":[row["root_id"] for row in roots],
                   "family_ids":sorted({row["family_id"] for row in roots}),"plan_sha256":corpus.PLAN_SHA})
        student_exe,student_net,teacher_exe,teacher_net=pilot.prepare_models(work/"models",out)
        teacher=native.UCI(teacher_exe,teacher_net,out,"teacher-train-shard-"+str(shard))
        labels,new_labels,exposures=[],[],[]
        reused_count=0
        search_seconds=0.0
        search_quota_seconds=10800*len(roots)/11440
        try:
            with (out/"TRAIN-LABELS.jsonl").open("xb") as output:
                for index,root in enumerate(roots):
                    corpus.need(time.monotonic()-started<75*60,"Frozen75-minute shard timeout")
                    if root["root_id"] in reused:
                        label=copy.deepcopy(reused[root["root_id"]])
                        label["capture_origin"]={"stage":"fixed-training-pilot","run_id":PILOT_RUN,
                                                 "artifact_id":PILOT_ARTIFACT,"archive_sha256":PILOT_SHA}
                        reused_count+=1
                    elif root["source_reasons"]:
                        label={"root_id":root["root_id"],"family_id":root["family_id"],"partition":"train",
                               "eligible":False,"reasons":root["source_reasons"],"children":[],"origins":root["origins"],
                               "capture_origin":{"stage":"source-unavailable","shard":shard}}
                        new_labels.append(label)
                    else:
                        low,exposure=native.parse_search(root,teacher.search(root,65536));exposures.append(exposure)
                        high=None
                        if low["complete_exact_top_two"]:
                            high,exposure=native.parse_search(root,teacher.search(root,262144,
                                                       [row["move"] for row in low["complete_exact_top_two"]]))
                            exposures.append(exposure)
                        label=native.consensus(root,low,high)
                        label["capture_origin"]={"stage":"training-acquisition","run_id":os.environ["GITHUB_RUN_ID"],
                                                 "driver_commit":os.environ["GITHUB_SHA"],"shard":shard,
                                                 "native_process":"teacher-train-shard-"+str(shard)+".process.json"}
                        new_labels.append(label)
                    corpus.need(label["family_id"]==root["family_id"] and label["partition"]=="train","Capture/roster join differs")
                    search_seconds+=sum(search["elapsed_seconds"] for search in (label.get("low"),label.get("high")) if search)
                    corpus.need(search_seconds<=search_quota_seconds,"Frozen aggregate search-time quota exceeded")
                    labels.append(label);output.write(corpus.canonical(label)+b"\n")
                    if (index+1)%64==0:
                        output.flush()
                        print("TRAIN SHARD",shard,index+1,"/",len(roots),"eligible",sum(row["eligible"] for row in labels),flush=True)
        finally:
            teacher.close()
        exposures_ref=corpus.gzip_rows(out/"TRAIN-TEACHER-EXPOSURE.jsonl.gz",exposures)
        cached,cache_elapsed,forward=pilot.cache_children(new_labels,student_net,out)
        (out/"PILOT-CHILD-CACHE.jsonl").rename(out/"NEW-CHILD-CACHE.jsonl")
        native_receipts=[]
        for offset in range(0,len(cached),256):
            corpus.need(time.monotonic()-started<75*60,"Frozen75-minute shard timeout during original parity")
            native_receipts.append(pilot.native_parity(student_exe,student_net,cached[offset:offset+256],out,forward,
                                                     name="original-train-parity-"+str(offset//256).zfill(3)))
        reused_ids={row["root_id"] for row in labels if row["root_id"] in reused}
        inherited=[{**row,"native_origin":{"stage":"fixed-training-pilot","run_id":PILOT_RUN,
                                         "artifact_id":PILOT_ARTIFACT,"archive_sha256":PILOT_SHA,
                                         "source_root_index":row["source_root_index"]}}
                   for row in pilot_cache if row["parent_root_id"] in reused_ids]
        fresh=[{**row,"native_origin":{"stage":"training-acquisition","run_id":os.environ["GITHUB_RUN_ID"],
                                      "shard":shard,"source_root_index":row["source_root_index"],
                                      "parity_batch":row["source_root_index"]//256}} for row in cached]
        complete=sorted(inherited+fresh,key=lambda row:(row["parent_root_id"],row["rank"]))
        corpus.need(len({(row["parent_root_id"],row["rank"]) for row in complete})==len(complete),"Composite child key duplicate")
        with (out/"TRAIN-CHILD-CACHE.jsonl").open("xb") as output:
            for row in complete:
                output.write(corpus.canonical(row)+b"\n")
        children={(row["parent_root_id"],row["rank"]):row for row in complete}
        require_complete_children(labels,complete)
        eligible=[row for row in labels if row["eligible"]]
        for row in eligible:
            corpus.need(all((row["root_id"],rank) in children for rank in (1,2)),"Eligible parent lacks measured child parity")
        same_head=sum(children[(row["root_id"],1)]["head"]==children[(row["root_id"],2)]["head"] for row in eligible)
        requested=sum(search["budget_nodes"] for row in new_labels for search in (row.get("low"),row.get("high")) if search)
        result={"schema":"official-d1-test1758-train-acquisition-shard/1","status":"COMPLETE_TRAIN_SHARD_NATIVE_PARITY_PASSED",
                "plan_sha256":corpus.PLAN_SHA,"driver_commit":os.environ["GITHUB_SHA"],"run_id":os.environ["GITHUB_RUN_ID"],
                "shard":shard,"shards":4,"partition":"train","root_denominator":len(roots),
                "families":len({row["family_id"] for row in labels}),"pilot_roots_reused_without_new_searches":reused_count,
                "eligible_consensus_pairs":len(eligible),"same_head_pairs":same_head,"cross_head_pairs":len(eligible)-same_head,
                "ordinary_measured_children":len(complete),"new_original_native_parity_rows":len(cached),
                "inherited_pilot_parity_rows":len(inherited),
                "unavailable_reasons":dict(Counter(reason for row in labels for reason in row["reasons"])),
                "requested_new_nodes":requested,"cache_seconds":cache_elapsed,"elapsed_seconds":time.monotonic()-started,
                "all_label_search_seconds_including_reused_pilot":search_seconds,
                "frozen_aggregate_time_quota_seconds":search_quota_seconds,
                "labels":corpus.reference(out/"TRAIN-LABELS.jsonl"),"cache":corpus.reference(out/"TRAIN-CHILD-CACHE.jsonl"),
                "native_parity":[corpus.reference(out/("original-train-parity-"+str(i).zfill(3)+".accepted.json"))
                                 for i in range(len(native_receipts))],"teacher_exposure":exposures_ref,
                "all_measured_original_children_native_raw_cp_matched":True,
                "development_final_or_screen_labels_features_or_predictions_opened":False,"fit_performed":False,
                "minimum_support_applies_to_complete_union_not_one_shard":2000,"methods_or_caps_changed_from_outcomes":False}
        corpus.put(out/"RESULT.json",result)
        corpus.put(out/"ARTIFACT-MANIFEST.json",{"schema":"official-d1-test1758-artifact-manifest/1",
                   "files":[{"path":path.relative_to(out).as_posix(),"bytes":path.stat().st_size,
                             "sha256":hashlib.sha256(path.read_bytes()).hexdigest()}
                            for path in sorted(out.rglob("*")) if path.is_file()]})
        print(json.dumps({key:result[key] for key in ("status","shard","root_denominator","eligible_consensus_pairs",
                         "same_head_pairs","cross_head_pairs","ordinary_measured_children","elapsed_seconds")}),flush=True)
    except BaseException as error:
        corpus.put(out/"FAILURE.json",{"schema":"official-d1-test1758-acquisition-failure/1",
                   "type":type(error).__name__,"message":str(error),"traceback":traceback.format_exc(),
                   "shard":shard,"plan_sha256":corpus.PLAN_SHA,"measured_elapsed_seconds":time.monotonic()-started})
        raise


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--source",type=Path,required=True);parser.add_argument("--out",type=Path,required=True)
    parser.add_argument("--shard",type=int,required=True)
    args=parser.parse_args();run(args.source.resolve(),args.out.resolve(),args.shard)
