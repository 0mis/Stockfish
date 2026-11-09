"""Fixed128-component TRAIN-only latency/current-native-parity calibration."""
from collections import Counter,defaultdict
import argparse,hashlib,json,os,shutil,subprocess,time,traceback,zipfile
from pathlib import Path
import chess
import common as c
import capture_protocol as p
import teacher_protocol as t
import qualify_harness as q

QUAL_RUN=42714588710
QUAL_COMMIT="dd26777bc23848956a5fabbe88365e4992f5ac41"
QUAL_ARTIFACT=13717137876
QUAL_BYTES=115409744
QUAL_SHA="7b6af89b93dabc10a310e2105461673daaca4e0b5635fda5cf5d31630f33434e"


def select_calibration(rows,count=128):
    groups=defaultdict(list)
    for row in rows:groups[row["component_id"]].append(row)
    representatives=[min(group,key=lambda row:row["root_id"]) for group in groups.values()]
    representatives.sort(key=lambda row:c.sha(("sfnnv20-natural-201301-v1/calibration|"+row["component_id"]).encode()))
    c.need(len(representatives)>=count,"Insufficient fixed TRAIN components")
    return representatives[:count]


def api_download(artifact,size,digest,target):
    with target.open("xb") as output:
        done=subprocess.run(["gh","api",f"repos/0mis/Stockfish/actions/artifacts/{artifact}/zip"],
                            stdout=output,stderr=subprocess.PIPE,timeout=240)
    c.need(done.returncode==0 and target.stat().st_size==size and c.file_sha(target)==digest,
           "Pinned hosted input ZIP differs")


def member(bundle,name,pins=None):
    matches=[info for info in bundle.infolist() if not info.is_dir() and info.filename==name]
    c.need(len(matches)==1,"Unique exact input member required: "+name)
    raw=bundle.read(matches[0])
    if pins is not None:
        pin=pins[name]
        c.need(len(raw)==pin["bytes"] and c.sha(raw)==pin["sha256"],"Input member SHA/size differs: "+name)
    return raw


def qualified_inputs(work,out,plan):
    archive=work/"qualified-current-observer.zip"
    api_download(QUAL_ARTIFACT,QUAL_BYTES,QUAL_SHA,archive)
    bins=work/"bin";bins.mkdir()
    with zipfile.ZipFile(archive) as bundle:
        pins={row["path"]:row for row in json.loads(member(bundle,"ARTIFACT-MANIFEST.json"))["files"]}
        qualification=json.loads(member(bundle,"QUALIFICATION.json",pins))
        c.need(qualification["status"]=="PUBLIC_FIXTURE_CURRENT_NATIVE_PARITY_PASSED"
               and qualification["source_commit"]==c.SOURCE and qualification["source_tree"]==c.TREE
               and qualification["plan_sha256"]==c.PLAN_SHA and qualification["driver_commit"]==QUAL_COMMIT,
               "Complete matching current-source fixture qualification required")
        c.need(qualification["capture_rows"]==46 and qualification["ordinary_raw_cp_parity_rows"]==45
               and qualification["natural_cohort_downloaded_or_evaluated"] is False,"Actual fixture coverage differs")
        for entry in ("ordinary","observer"):
            pin=qualification[entry]
            raw=member(bundle,pin["path"],pins)
            target=bins/Path(pin["path"]).name
            target.write_bytes(raw);target.chmod(0o755)
            c.need(c.file_sha(target)==pin["sha256"],"Qualified executable differs")
        byte_map_raw=member(bundle,"FC2-BYTE-MAP.json",pins)
        byte_map=json.loads(byte_map_raw)
        ordinary_input=json.loads(member(bundle,"ORDINARY-INPUT.json",pins))
    c.need(c.sha(c.canonical(byte_map))==qualification["byte_map_content_sha256"]
           and byte_map["source_commit"]==c.SOURCE and byte_map["network_sha256"]==c.PARENT,
           "Fresh current-source map binding differs")
    ordinary=bins/"baseline-stockfish";observer=bins/"fc2-observer"
    c.need(c.file_sha(ordinary)==plan["ordinary_oracle"]["executable_sha256"],"Exact original43e oracle differs")
    parent=work/"nn-18cd32e11a9c.nnue"
    with ordinary.open("rb") as source,parent.open("xb") as target:
        source.seek(ordinary_input["embedded_parent_offset"])
        remaining=q.NET_BYTES
        while remaining:
            block=source.read(min(1024**2,remaining));c.need(bool(block),"Truncated qualified embedded parent")
            target.write(block);remaining-=len(block)
    c.need(parent.stat().st_size==q.NET_BYTES and c.file_sha(parent)==c.PARENT,"Exact original18cd full bytes differ")
    c.need(p.network_layout(parent)==byte_map["raw_to_physical"],"Current captured map/native parent layout differs")
    (out/"FC2-BYTE-MAP.json").write_bytes(byte_map_raw)
    c.put(out/"QUALIFICATION-ADMISSION.json",{"schema":"sfnnv20-qualified-harness-admission/1",
          "run_id":QUAL_RUN,"source_commit":c.SOURCE,"driver_commit":QUAL_COMMIT,
          "artifact_id":QUAL_ARTIFACT,"archive_bytes":QUAL_BYTES,"archive_sha256":QUAL_SHA,
          "qualification":qualification,"byte_map_file_sha256":c.sha(byte_map_raw),
          "byte_map_content_sha256":c.sha(c.canonical(byte_map))})
    return ordinary,observer,parent,byte_map


def load_train(work,out,plan):
    c.need(c.file_sha(c.HERE/"corpus-bindings/RESULT.json")==plan["corpus"]["result_sha256"]
           and c.file_sha(c.HERE/"corpus-bindings/AUDIT.json")==plan["corpus"]["independent_audit_sha256"],
           "Exact admitted public corpus/audit commitments differ")
    positions=next(pin for pin in plan["corpus"]["public_artifacts"] if pin["role"]=="positions")
    archive=work/"sanitized-selected-positions.zip"
    api_download(positions["id"],positions["bytes"],positions["sha256"],archive)
    pin=plan["corpus"]["partitions"]["TRAIN"]["file"]
    with zipfile.ZipFile(archive) as bundle:
        matches=[info for info in bundle.infolist() if not info.is_dir() and Path(info.filename).name==pin["file"]]
        c.need(len(matches)==1,"Unique exact TRAIN-only product required")
        raw=bundle.read(matches[0])
    c.need(len(raw)==pin["bytes"] and c.sha(raw)==pin["sha256"],"Exact fixed16000 TRAIN JSONL differs")
    rows=[json.loads(line) for line in raw.splitlines()]
    c.need(len(rows)==16000 and len({row["root_id"] for row in rows})==16000,"Fixed TRAIN root denominator differs")
    groups=defaultdict(list)
    for row in rows:groups[row["component_id"]].append(row)
    c.need(len(groups)==4711 and max(map(len,groups.values()))<=8,"Audited connected components differ")
    selected=select_calibration(rows)
    normalized=[]
    for row in selected:
        board=chess.Board(row["fen"])
        c.need(board.is_valid() and not board.chess960 and not board.is_check()
               and not board.is_game_over(claim_draw=False) and c.position_id(board)==row["root_id"],
               "Admitted pilot root FEN/canonical identity differs")
        c.need(len(row["fen"].split())==6,"Actual source clocks/FEN6 required")
        normalized.append({**row,"fen6":row["fen"],"partition":"train",
                           "source_family_id":row["family_id"],
                           "origins":[{"source_record":row["source_record"],"game_id":row["game_id"],
                                       "source_family_id":row["family_id"],"ply":row["ply"],
                                       "prefix_uci_sha256":c.sha(c.canonical(row["prefix_uci"]))}]})
    c.put(out/"PILOT-ROSTER.json",{"schema":"sfnnv20-natural-fixed-training-pilot-roster/1",
          "plan_sha256":c.PLAN_SHA,"corpus_train":pin,"roots":normalized,"components":128,
          "component_selection":"fixed prospective component hash, one lowest root hash",
          "only_TRAIN_semantically_loaded":True,"DEV_FINAL_SCREEN_members_not_opened":True})
    return normalized


def capture_children(observer,parent,ordinary,byte_map,labels,out):
    children=[]
    for label in labels:
        for child in label["children"]:
            if child["ordinary_static_available"]:
                children.append((label,child))
    if not children:return [],0.0,{"rows":0,"status":"NO_MEASURABLE_PILOT_CHILDREN"}
    commands=[]
    for index,(label,child) in enumerate(children):
        commands.append("capture "+str(index)+" fen "+label["root_fen6"]+" moves "+child["move"])
    commands.append("quit");started=time.monotonic()
    stdout,stderr,process=q.run(out,"natural-training-pilot-capture",
                [str(observer),"--fc2-activations-v1","--network",str(parent)],observer.parent,
                ("\n".join(commands)+"\n").encode())
    c.need(not stderr,"Current native pilot capture stderr")
    captured=[json.loads(line) for line in stdout.splitlines()]
    c.need(len(captured)==len(children),"Every available child requires one native activation/lookup row")
    ordinary_specs=[];wrapped=[]
    map_digest=c.sha(c.canonical(byte_map))
    for index,(row,(label,child)) in enumerate(zip(captured,children)):
        board=chess.Board(child["fen6"])
        c.need(row["row_id"]==index,"Composite capture row alignment differs")
        report=p.validate(row,mapping=byte_map["raw_to_physical"],
                          readout=p.serialized_readout(parent,row["physical_head"]),expected_board=board)
        c.need(c.position_id(board)==child["position_id"],"Actual legal child identity differs")
        wrapped.append({**row,"parent_root_id":label["root_id"],"rank":child["rank"],
                        "child_position_id":child["position_id"],"component_id":label["component_id"],
                        "partition":"train","teacher_consensus_pair_eligible":label["eligible"],
                        "network_sha256":c.PARENT,"source_commit":c.SOURCE,
                        "byte_map_sha256":map_digest})
        ordinary_specs.append({"final_fen":child["fen6"],"position_id":child["position_id"]})
    scores,ordinary_process=q.ordinary_request(ordinary,parent,ordinary_specs,out,"natural-training-pilot-ordinary-parity")
    for row,spec,score in zip(wrapped,ordinary_specs,scores):
        board=chess.Board(spec["final_fen"]);sign=1 if board.turn else -1
        c.need(score["raw_nnue"]==row["raw_nnue"] and score["white_cp"]==sign*row["ordinary_static_cp"],
               "Every natural child needs actual fresh current ordinary raw+CP parity")
    elapsed=time.monotonic()-started
    with (out/"TRAIN-PILOT-CAPTURES.jsonl").open("xb") as stream:
        for row in wrapped:stream.write(c.canonical(row)+b"\n")
    receipt={"schema":"sfnnv20-natural-original-native-parity/1","status":"ALL_PILOT_AVAILABLE_CHILDREN_RAW_CP_PASSED",
             "rows":len(wrapped),"elapsed_seconds":elapsed,"capture_process":c.ref(out/"natural-training-pilot-capture.process.json",out),
             "ordinary_process":c.ref(out/"natural-training-pilot-ordinary-parity.process.json",out),
             "source_commit":c.SOURCE,"network_sha256":c.PARENT,"byte_map_sha256":c.sha(c.canonical(byte_map))}
    c.put(out/"ORIGINAL-PILOT-PARITY.json",receipt)
    return wrapped,elapsed,receipt


def run(out):
    plan=c.hosted("TRAIN");out.mkdir(parents=True,exist_ok=False)
    try:
        work=Path(os.environ["RUNNER_TEMP"])/"sfnnv20-natural-training-pilot";work.mkdir()
        ordinary,observer,parent,byte_map=qualified_inputs(work,out,plan)
        roots=load_train(work,out,plan)
        teacher=q.teacher_file(work,plan)
        transport=t.UCI(ordinary,teacher,out,"current-teacher-fixed-TRAIN-pilot")
        labels=[];exposures=[];started=time.monotonic()
        try:
            for index,root in enumerate(roots):
                low,exposure=t.parse_search(root,transport.search(root,65536));exposures.append(exposure)
                high=None
                if low["complete_exact_top_two"]:
                    high,exposure=t.parse_search(root,transport.search(root,262144,
                                       [row["move"] for row in low["complete_exact_top_two"]]))
                    exposures.append(exposure)
                label=t.consensus(root,low,high)
                labels.append(label)
                if (index+1)%16==0:
                    print("CURRENT TRAIN PILOT",index+1,"eligible",sum(row["eligible"] for row in labels),flush=True)
        finally:
            transport.close()
        loop_elapsed=time.monotonic()-started
        with (out/"TRAIN-PILOT-LABELS.jsonl").open("xb") as stream:
            for row in labels:stream.write(c.canonical(row)+b"\n")
        with (out/"TRAIN-PILOT-REPORTED-EXPOSURE.jsonl").open("xb") as stream:
            for row in exposures:stream.write(c.canonical(row)+b"\n")
        captured,capture_elapsed,parity=capture_children(observer,parent,ordinary,byte_map,labels,out)
        requested=sum(query["budget_nodes"] for row in labels for query in (row["low"],row["high"]) if query)
        search_elapsed=sum(query["elapsed_seconds"] for row in labels for query in (row["low"],row["high"]) if query)
        predicted_search=1.5*max(loop_elapsed/128*22000,search_elapsed/max(1,requested)*7208960000)
        predicted_children=1.5*capture_elapsed/max(1,len(captured))*44000
        predicted_total=predicted_search+predicted_children
        per_train_shard=predicted_total*2000/22000
        feasible=bool(captured) and predicted_total<=21600 and per_train_shard<=3600
        c.need(c.file_sha(ordinary)==plan["ordinary_oracle"]["executable_sha256"] and c.file_sha(parent)==c.PARENT
               and c.file_sha(teacher)==c.TEACHER,"Exact current original native inputs changed")
        result={"schema":"sfnnv20-natural-fixed-training-pilot/1","status":"TRAIN_PILOT_CURRENT_NATIVE_PARITY_PASSED",
                "plan_sha256":c.PLAN_SHA,"driver_commit":os.environ["GITHUB_SHA"],"run_id":os.environ["GITHUB_RUN_ID"],
                "calibration_roots":128,"calibration_components":128,"stable_eligible_pairs":sum(row["eligible"] for row in labels),
                "ordinary_measured_children":len(captured),"native_parity":parity,
                "unavailable_reasons":dict(Counter(reason for row in labels for reason in row["reasons"])),
                "requested_teacher_nodes":requested,
                "reported_teacher_nodes":sum(query["actual_reported_max_nodes"] for row in labels for query in (row["low"],row["high"]) if query),
                "loop_seconds":loop_elapsed,"capture_and_ordinary_parity_seconds":capture_elapsed,
                "resource_prediction":{"teacher_worker_seconds":predicted_search,"child_worker_seconds":predicted_children,
                                       "total_acquisition_worker_seconds":predicted_total,
                                       "one2000root_TRAIN_shard_seconds":per_train_shard,
                                       "frozen_resource_gate_passed":feasible},
                "all_original_available_child_current_native_raw_cp_matched":bool(captured),
                "Root_technical_admission_bound_before_native":True,
                "DEV_FINAL_SCREEN_searches_features_or_predictions_opened":False,"fit_performed":False,
                "thresholds_or_caps_changed_from_pilot_outcomes":False,"large_acquisition_launched_by_this_job":False,
                "source_search_tree_internals_unreported":"unknown"}
        c.put(out/"RESULT.json",result)
        c.put(out/"ARTIFACT-MANIFEST.json",{"schema":"sfnnv20-natural-pilot-artifact-manifest/1",
              "files":[c.ref(path,out) for path in sorted(out.rglob("*")) if path.is_file()]})
        print(json.dumps({key:result[key] for key in ("status","calibration_roots","stable_eligible_pairs",
                   "ordinary_measured_children","unavailable_reasons","resource_prediction")}),flush=True)
    except BaseException as error:
        c.put(out/"FAILURE.json",{"schema":"sfnnv20-natural-training-pilot-failure/1","type":type(error).__name__,
                                "message":str(error),"traceback":traceback.format_exc(),"plan_sha256":c.PLAN_SHA})
        raise


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--out",type=Path,required=True);args=parser.parse_args()
    run(args.out.resolve())
