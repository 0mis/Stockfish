"""Fixed16000 TRAIN roots in eight disjoint shards; no holdout or refit."""
from collections import Counter,defaultdict
import argparse,copy,gzip,hashlib,json,os,time,traceback,zipfile
from pathlib import Path
import chess
import common as c
import train_pilot as pilot
import teacher_protocol as t
import qualify_harness as q

PILOT_RUN=42716289108
PILOT_COMMIT="e9b377a7dd33c0a71851634c85382c7dda762f93"
PILOT_ARTIFACT=13718136882
PILOT_BYTES=2213586
PILOT_SHA="0fab7dcdcfa9f66639aeda6d8dd72c7acd8c7d3fb632d3b5bda829e94f29d51e"


def fixed_train(work,plan):
    pin=next(row for row in plan["corpus"]["public_artifacts"] if row["role"]=="positions")
    archive=work/"fixed-sanitized-positions.zip"
    pilot.api_download(pin["id"],pin["bytes"],pin["sha256"],archive)
    train_pin=plan["corpus"]["partitions"]["TRAIN"]["file"]
    with zipfile.ZipFile(archive) as bundle:
        matches=[info for info in bundle.infolist() if not info.is_dir() and Path(info.filename).name==train_pin["file"]]
        c.need(len(matches)==1,"Unique fixed TRAIN member required")
        body=bundle.read(matches[0])
    c.need(len(body)==train_pin["bytes"] and c.sha(body)==train_pin["sha256"],"Exact cleared TRAIN bytes differ")
    source_rows=[json.loads(line) for line in body.splitlines()]
    c.need(len(source_rows)==16000 and len({row["root_id"] for row in source_rows})==16000,"Full fixed TRAIN denominator differs")
    groups=Counter(row["component_id"] for row in source_rows)
    c.need(len(groups)==4711 and max(groups.values())<=8,"Audited component count/cap differs")
    rows=[]
    for row in source_rows:
        board=chess.Board(row["fen"])
        c.need(board.is_valid() and not board.chess960 and not board.is_check()
               and not board.is_game_over(claim_draw=False) and c.position_id(board)==row["root_id"],
               "Fixed admitted TRAIN root differs")
        rows.append({**row,"fen6":row["fen"],"partition":"train","source_family_id":row["family_id"],
                     "origins":[{"source_record":row["source_record"],"game_id":row["game_id"],
                                 "source_family_id":row["family_id"],"ply":row["ply"],
                                 "prefix_uci_sha256":c.sha(c.canonical(row["prefix_uci"]))}]})
    return rows


def accepted_pilot(work,out):
    archive=work/"accepted-fixed-pilot.zip"
    pilot.api_download(PILOT_ARTIFACT,PILOT_BYTES,PILOT_SHA,archive)
    names=["RESULT.json","PILOT-ROSTER.json","TRAIN-PILOT-LABELS.jsonl","TRAIN-PILOT-CAPTURES.jsonl",
           "ORIGINAL-PILOT-PARITY.json","QUALIFICATION-ADMISSION.json"]
    bodies={}
    with zipfile.ZipFile(archive) as bundle:
        pins={row["path"]:row for row in json.loads(pilot.member(bundle,"ARTIFACT-MANIFEST.json"))["files"]}
        for name in names:bodies[name]=pilot.member(bundle,name,pins)
    result=json.loads(bodies["RESULT.json"])
    c.need(result["status"]=="TRAIN_PILOT_CURRENT_NATIVE_PARITY_PASSED"
           and result["plan_sha256"]==c.PLAN_SHA and result["driver_commit"]==PILOT_COMMIT
           and result["resource_prediction"]["frozen_resource_gate_passed"] is True,
           "Actual matching current pilot/resource acceptance required")
    c.need(result["calibration_roots"]==128 and result["ordinary_measured_children"]==219,
           "Actual fixed pilot coverage differs")
    labels=[json.loads(line) for line in bodies["TRAIN-PILOT-LABELS.jsonl"].splitlines()]
    captures=[json.loads(line) for line in bodies["TRAIN-PILOT-CAPTURES.jsonl"].splitlines()]
    c.need(len(labels)==128 and len(captures)==219,"Pilot payload coverage differs")
    c.put(out/"PILOT-ADMISSION.json",{"schema":"sfnnv20-natural-fixed-pilot-admission/1",
          "run_id":PILOT_RUN,"driver":PILOT_COMMIT,"artifact_id":PILOT_ARTIFACT,
          "archive_bytes":PILOT_BYTES,"archive_sha256":PILOT_SHA,
          "selected_members":[pins[name] for name in names],"resource_prediction":result["resource_prediction"],
          "no_pilot_teacher_or_feature_requests_repeated":True})
    return result,{row["root_id"]:row for row in labels},captures


def choose_shard(rows,shard):
    c.need(shard in range(8) and all(row["partition"]=="train" for row in rows),"Exact TRAIN-only shard0..7 required")
    c.need(len({row["root_id"] for row in rows})==len(rows),"Distinct canonical root IDs required")
    return sorted((row for row in rows if int(row["root_id"],16)%8==shard),key=lambda row:row["root_id"])


def verify_children(labels,captured):
    actual={(row["parent_root_id"],row["rank"]) for row in captured}
    expected={(row["root_id"],child["rank"]) for row in labels for child in row["children"]
              if child["ordinary_static_available"]}
    c.need(len(actual)==len(captured) and actual==expected,"Every available composite child, including held-pair counterparts, is required")


def gzip_exposure(path,rows):
    digest=hashlib.sha256();count=0
    with path.open("xb") as raw,gzip.GzipFile(fileobj=raw,mode="wb",mtime=0) as stream:
        for row in rows:
            body=c.canonical(row)+b"\n";stream.write(body);digest.update(body);count+=1
    return {**c.ref(path),"rows":count,"canonical_uncompressed_sha256":digest.hexdigest()}


def run(out,shard):
    plan=c.hosted("TRAIN");out.mkdir(parents=True,exist_ok=False);started=time.monotonic()
    try:
        work=Path(os.environ["RUNNER_TEMP"])/("sfnnv20-fixed-TRAIN-shard-"+str(shard));work.mkdir()
        all_rows=fixed_train(work,plan)
        pilot_result,reused,pilot_captures=accepted_pilot(work,out)
        expected_pilot={row["root_id"] for row in pilot.select_calibration(all_rows)}
        c.need(set(reused)==expected_pilot,"Exact128 calibration membership changed")
        roots=choose_shard(all_rows,shard)
        c.put(out/"SHARD-ROSTER.json",{"schema":"sfnnv20-natural-fixed-train-shard-roster/1",
              "plan_sha256":c.PLAN_SHA,"shard":shard,"shards":8,"partition":"TRAIN",
              "root_ids":[row["root_id"] for row in roots],"component_ids":sorted({row["component_id"] for row in roots})})
        qualified=work/"qualified";qualified.mkdir()
        ordinary,observer,parent,byte_map=pilot.qualified_inputs(qualified,out,plan)
        teacher=q.teacher_file(work,plan)
        transport=t.UCI(ordinary,teacher,out,"current-teacher-TRAIN-shard-"+str(shard))
        labels,new_labels,exposures=[],[],[];inherited_roots=0
        loop_started=time.monotonic()
        try:
            with (out/"TRAIN-LABELS.jsonl").open("xb") as stream:
                for index,root in enumerate(roots):
                    c.need(time.monotonic()-started<4500,"Frozen75-minute shard wall cap")
                    if root["root_id"] in reused:
                        label=copy.deepcopy(reused[root["root_id"]])
                        label["capture_origin"]={"stage":"fixed128-component-pilot","run_id":PILOT_RUN,
                                                 "artifact_id":PILOT_ARTIFACT,"archive_sha256":PILOT_SHA}
                        inherited_roots+=1
                    else:
                        low,exposure=t.parse_search(root,transport.search(root,65536));exposures.append(exposure)
                        high=None
                        if low["complete_exact_top_two"]:
                            high,exposure=t.parse_search(root,transport.search(root,262144,
                                              [row["move"] for row in low["complete_exact_top_two"]]))
                            exposures.append(exposure)
                        label=t.consensus(root,low,high)
                        label["capture_origin"]={"stage":"fixed-TRAIN-acquisition","run_id":os.environ["GITHUB_RUN_ID"],
                                                 "driver_commit":os.environ["GITHUB_SHA"],"shard":shard,
                                                 "process":"current-teacher-TRAIN-shard-"+str(shard)+".process.json"}
                        new_labels.append(label)
                    c.need(label["component_id"]==root["component_id"] and label["partition"]=="train","Source/component join differs")
                    labels.append(label);stream.write(c.canonical(label)+b"\n")
                    if (index+1)%64==0:
                        stream.flush()
                        print("FIXED TRAIN SHARD",shard,index+1,"/",len(roots),"stable",sum(row["eligible"] for row in labels),flush=True)
        finally:
            transport.close()
        loop_elapsed=time.monotonic()-loop_started
        exposure=gzip_exposure(out/"NEW-TEACHER-REPORTED-EXPOSURE.jsonl.gz",exposures)
        fresh,capture_elapsed,parity=pilot.capture_children(observer,parent,ordinary,byte_map,new_labels,out)
        legacy=out/"TRAIN-PILOT-CAPTURES.jsonl"
        if legacy.exists():legacy.rename(out/"NEW-CAPTURES.jsonl")
        inherited_ids={row["root_id"] for row in roots if row["root_id"] in reused}
        inherited=[{**row,"native_origin":{"stage":"fixed128-component-pilot","run_id":PILOT_RUN,
                                          "artifact_id":PILOT_ARTIFACT,"archive_sha256":PILOT_SHA,"capture_row_id":row["row_id"]}}
                   for row in pilot_captures if row["parent_root_id"] in inherited_ids]
        new=[{**row,"native_origin":{"stage":"fixed-TRAIN-acquisition","run_id":os.environ["GITHUB_RUN_ID"],
                                    "shard":shard,"capture_row_id":row["row_id"],
                                    "capture_process":"natural-training-pilot-capture.process.json",
                                    "ordinary_process":"natural-training-pilot-ordinary-parity.process.json"}} for row in fresh]
        captured=sorted(inherited+new,key=lambda row:(row["parent_root_id"],row["rank"]))
        verify_children(labels,captured)
        with (out/"TRAIN-CAPTURES.jsonl").open("xb") as stream:
            for row in captured:stream.write(c.canonical(row)+b"\n")
        accounted_native=loop_elapsed+capture_elapsed
        accounted_native+=pilot_result["loop_seconds"]*inherited_roots/128
        accounted_native+=pilot_result["capture_and_ordinary_parity_seconds"]*len(inherited)/219
        quota=21600*len(roots)/22000
        c.need(accounted_native<=quota,"Frozen aggregate native worker-time allocation exceeded")
        children={(row["parent_root_id"],row["rank"]):row for row in captured}
        eligible=[row for row in labels if row["eligible"]]
        same=sum(children[(row["root_id"],1)]["physical_head"]==children[(row["root_id"],2)]["physical_head"] for row in eligible)
        requested=sum(query["budget_nodes"] for row in new_labels for query in (row["low"],row["high"]) if query)
        result={"schema":"sfnnv20-natural-fixed-train-acquisition-shard/1","status":"COMPLETE_CURRENT_TRAIN_SHARD_PARITY_PASSED",
                "plan_sha256":c.PLAN_SHA,"source_commit":c.SOURCE,"network_sha256":c.PARENT,
                "driver_commit":os.environ["GITHUB_SHA"],"run_id":os.environ["GITHUB_RUN_ID"],"shard":shard,"shards":8,
                "root_denominator":len(roots),"components":len({row["component_id"] for row in labels}),
                "pilot_roots_reused_without_new_requests":inherited_roots,
                "stable_eligible_pairs":len(eligible),"same_physical_head_pairs":same,"cross_physical_head_pairs":len(eligible)-same,
                "ordinary_measured_children":len(captured),"fresh_current_native_parity_rows":len(fresh),
                "inherited_current_native_parity_rows":len(inherited),
                "unavailable_reasons":dict(Counter(reason for row in labels for reason in row["reasons"])),
                "new_requested_teacher_nodes":requested,"new_loop_seconds":loop_elapsed,
                "new_capture_and_ordinary_parity_seconds":capture_elapsed,
                "native_worker_seconds_including_allocated_pilot":accounted_native,"frozen_worker_seconds_quota":quota,
                "elapsed_wall_seconds":time.monotonic()-started,"teacher_exposure":exposure,
                "labels":c.ref(out/"TRAIN-LABELS.jsonl",out),"captures":c.ref(out/"TRAIN-CAPTURES.jsonl",out),
                "native_feature_and_ordinary_raw_cp_parity_all_available_children":True,
                "DEV_FINAL_SCREEN_labels_features_or_predictions_opened":False,"fitting_performed":False,
                "no_cap_threshold_or_method_changes_from_outcomes":True,
                "input_Root_admission_scope":"Exact approved canonical parent/one-ply inputs only; unreported search internals unknown."}
        c.put(out/"RESULT.json",result)
        c.put(out/"ARTIFACT-MANIFEST.json",{"schema":"sfnnv20-natural-train-shard-artifact-manifest/1",
              "files":[c.ref(path,out) for path in sorted(out.rglob("*")) if path.is_file()]})
        print(json.dumps({key:result[key] for key in ("status","shard","root_denominator","stable_eligible_pairs",
              "same_physical_head_pairs","cross_physical_head_pairs","ordinary_measured_children",
              "native_worker_seconds_including_allocated_pilot","frozen_worker_seconds_quota")}),flush=True)
    except BaseException as error:
        c.put(out/"FAILURE.json",{"schema":"sfnnv20-natural-train-shard-failure/1","type":type(error).__name__,
                                "message":str(error),"traceback":traceback.format_exc(),"shard":shard,"plan_sha256":c.PLAN_SHA})
        raise


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--out",type=Path,required=True)
    parser.add_argument("--shard",type=int,required=True);args=parser.parse_args()
    run(args.out.resolve(),args.shard)
