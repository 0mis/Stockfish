"""Hosted independent complete TRAIN evidence replay; no new native/model work."""
from collections import Counter
from decimal import Decimal
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import traceback
import zipfile
import chess
import corpus
import fit_core
import native
import pilot

ACQUISITION_RUN=42696757852
ACQUISITION_COMMIT="90f6a1032ba36934ad9ad7fb1961246a81adb2d2"
FIT_CORE_SHA="3ef267d5cb69bd3b69b1618905851167596ff0d4bb27902dd11ff0389df46e42"


def api(path):
    return json.loads(subprocess.check_output(["gh","api",path]))


def fetch(pin,target):
    corpus.need(not target.exists(),"Preserve every retained input archive")
    with target.open("xb") as output:
        done=subprocess.run(["gh","api",f"repos/0mis/Stockfish/actions/artifacts/{pin['id']}/zip"],
                            stdout=output,stderr=subprocess.PIPE,timeout=240)
    with target.open("rb") as stream:
        digest=hashlib.file_digest(stream,"sha256").hexdigest()
    corpus.need(done.returncode==0 and target.stat().st_size==pin["bytes"] and digest==pin["sha256"],
                "Pinned immutable input archive differs")


class Bundle:
    def __init__(self,path,full=True):
        self.bundle=zipfile.ZipFile(path)
        names=[row.filename for row in self.bundle.infolist() if not row.is_dir()]
        corpus.need(len(names)==len(set(name.casefold() for name in names)),"Duplicate artifact member")
        self.manifest=json.loads(self.bundle.read("ARTIFACT-MANIFEST.json"))
        self.refs={row["path"]:row for row in self.manifest["files"]}
        corpus.need(set(names)==set(self.refs)|{"ARTIFACT-MANIFEST.json"},"Artifact manifest is not exhaustive")
        self.checked={}
        self.body_cache={}
        self.transports={}
        if full:
            for name in names:
                self.read(name)

    def read(self,name):
        if name in self.body_cache:
            return self.body_cache[name]
        body=self.bundle.read(name)
        if name!="ARTIFACT-MANIFEST.json":
            expected=self.refs[name]
            corpus.need(len(body)==expected["bytes"] and corpus.sha(body)==expected["sha256"],"Member SHA/size differs: "+name)
        self.checked[name]={"bytes":len(body),"sha256":corpus.sha(body)}
        self.body_cache[name]=body
        return body

    def json(self,name):
        return json.loads(self.read(name))


def parity_replay(bundle,stem,expected_cache):
    request=bundle.json(stem+".request.json")
    accepted=bundle.json(stem+".accepted.json")
    stdout,stderr=bundle.read(stem+".stdout"),bundle.read(stem+".stderr")
    corpus.need(not stderr and accepted["returncode"]==0 and accepted["inputs_rehashed_before_after"],"Native parity process failed")
    text=stdout.decode("utf-8").replace("\r\n","\n")
    corpus.need("\r" not in text and text.splitlines().count("uciok")==1,"Actual native handshake/framing differs")
    segments,pending=[],[]
    for line in text.splitlines():
        if line=="readyok":segments.append(pending);pending=[]
        else:pending.append(line)
    expected=request["expected"]
    corpus.need(len(segments)==len(expected)+1 and not any(line.strip() for line in pending),"Native ready-boundary/EOF differs")
    actual_stdin=bundle.read(stem+".stdin")
    corpus.need(corpus.sha(actual_stdin)==request["stdin_sha256"],"Exact native request bytes differ")
    commands=actual_stdin.decode().splitlines()
    network_prefix="info string NNUE evaluation using "
    network_lines=[line for line in text.splitlines() if line.startswith(network_prefix)]
    corpus.need(network_lines and all(line.startswith(network_prefix+request["eval_file"]+" (") for line in network_lines),
                "Actual original native loaded EvalFile path differs")
    fens=[line[len("position fen "):] for line in commands if line.startswith("position fen ")]
    corpus.need(len(fens)==len(expected),"Every native expected child needs one exact source FEN")
    seen=[]
    for item,lines,fen in zip(expected,segments[1:],fens):
        corpus.need(item["source_root_index"] in expected_cache,"Native source index is not an admitted measured child")
        row=expected_cache[item["source_root_index"]]
        corpus.need(fen==row["root_fen6"] and item["head"]==row["head"],"Native request/cache composite child join differs")
        cp=row["base_static_cp_stm"] if row["side_to_move"]=="w" else -row["base_static_cp_stm"]
        corpus.need(item["raw_nnue_i32"]==row["base_raw_nnue_i32"] and item["static_cp_white"]==cp,
                    "Native request differs from original captured raw/CP")
        raws=[int(match[1]) for line in lines if (match:=re.fullmatch(r"NNUE evaluation\s+([+-]?\d+) \(side to move, internal units\)",line))]
        cps=[Decimal(match[1])*100 for line in lines if (match:=re.fullmatch(r"Final evaluation\s+([+-]?\d+\.\d{2}) \(white side\) \[with scaled NNUE, \.\.\.\]",line))]
        corpus.need(raws==[item["raw_nnue_i32"]] and cps==[Decimal(cp)],"Actual original native raw/ordinary-CP mismatch")
        seen.append(item["source_root_index"])
    corpus.need(accepted["rows"]==len(seen) and request["engine_sha256"]==corpus.policy()["student"]["engine_sha256"],
                "Actual original official engine/coverage differs")
    corpus.need(request["eval_file_sha256"]==corpus.policy()["student"]["network_sha256"],"Wrong original official network")
    corpus.need(accepted["engine"]["sha256"]==request["engine_sha256"]
                and accepted["network"]["sha256"]==request["eval_file_sha256"],"Native authenticated inputs differ")
    return seen


def teacher_replay(bundle,stem,label,root,exposure_hash):
    if stem not in bundle.transports:
        commands=bundle.read(stem+".stdin").decode().splitlines()
        stdout=bundle.read(stem+".stdout").decode().replace("\r\n","\n").splitlines()
        paths=[line[len("setoption name EvalFile value "):] for line in commands if line.startswith("setoption name EvalFile value ")]
        corpus.need(len(paths)==1 and paths[0].endswith("nn-68752b755c93.nnue"),"Actual unmodified teacher EvalFile differs")
        record=bundle.json(stem+".process.json")
        corpus.need(bundle.read(stem+".stderr")==b"","Actual teacher stderr is not empty")
        corpus.need(record["returncode"]==0 and record["engine"]["sha256"]==corpus.policy()["teacher"]["native_engine_sha256"]
                    and record["network"]["sha256"]==corpus.policy()["teacher"]["network_sha256"],"Actual teacher identities differ")
        corpus.need(record["engine"]==record["engine_rehashed"] and record["network"]==record["network_rehashed"],"Teacher inputs changed")
        bundle.transports[stem]={"commands":commands,"stdout":stdout,"eval_file":paths[0],"used_go_indices":set()}
    transport=bundle.transports[stem]
    commands,stdout=transport["commands"],transport["stdout"]
    count=0
    for key in ("low","high"):
        old=label.get(key)
        if not old:continue
        start,stop=old["process_stdout_lines"]
        response={**old,"stdout_line_start":start,"stdout_line_stop":stop,"input_line":old["process_input_line"],
                  "lines":stdout[start-1:stop],"eval_file":transport["eval_file"]}
        parsed,exposure=native.parse_search(root,response)
        corpus.need(parsed==old,"Actual raw teacher snapshot differs")
        index=old["process_input_line"]-1
        corpus.need(index not in transport["used_go_indices"],"Actual teacher search reused twice")
        transport["used_go_indices"].add(index)
        expected="go nodes "+str(old["budget_nodes"])
        if key=="high":expected+=" searchmoves "+" ".join(row["move"] for row in label["low"]["complete_exact_top_two"])
        corpus.need(commands[index]==expected and commands[index-1]=="position fen "+root["fen6"],
                    "Actual fixed-budget/same-pair/root request differs")
        corpus.need(commands[index-3:index-1]==["ucinewgame","isready"],"Each budget requires a fresh cleared root search")
        exposure_hash.update(corpus.canonical(exposure)+b"\n");count+=1
    reproduced=native.consensus(root,label["low"],label.get("high"))
    corpus.need(reproduced=={key:value for key,value in label.items() if key!="capture_origin"},"Actual teacher consensus/holds differ")
    return count


def run(pins_path,out):
    corpus.need(os.environ.get("GITHUB_ACTIONS")=="true"
                and os.environ.get("GITHUB_REF")=="refs/heads/"+pilot.BRANCH,"Hosted exact-branch audit only")
    out.mkdir(parents=True,exist_ok=False)
    try:
        plan=corpus.policy()
        corpus.need(corpus.sha((corpus.HERE/"fit_core.py").read_bytes())==FIT_CORE_SHA,"Pre-outcome estimator source changed")
        pins=json.loads(Path(pins_path).read_bytes())
        corpus.need(pins["schema"]=="official-d1-test1758-complete-train-inputs/1"
                    and pins["run_id"]==ACQUISITION_RUN and pins["driver_commit"]==ACQUISITION_COMMIT
                    and {row["shard"] for row in pins["artifacts"]}==set(range(4)),"Exact four completed shard input pins required")
        run=api(f"repos/0mis/Stockfish/actions/runs/{ACQUISITION_RUN}")
        jobs=api(f"repos/0mis/Stockfish/actions/runs/{ACQUISITION_RUN}/jobs?per_page=100")["jobs"]
        corpus.need(run["status"]=="completed" and run["conclusion"]=="success" and run["head_sha"]==ACQUISITION_COMMIT,
                    "Complete successful immutable acquisition required")
        corpus.need(len(jobs)==4 and all(job["conclusion"]=="success" and job["labels"]==["windows-2022"] for job in jobs),
                    "All four actual standard public shard jobs must pass")
        corpus.put(out/"ACTUAL-RUN.json",run);corpus.put(out/"ACTUAL-JOBS.json",jobs)
        retained=out/"retained-source-artifacts";retained.mkdir()
        pilot_pin={"id":13708966730,"bytes":41010310,"sha256":"85a6fd44d0adc6c06d4f46bdea242daac3f731450d131305fc8078eca485caac"}
        pilot_zip=retained/"fixed-training-pilot.zip";fetch(pilot_pin,pilot_zip)
        original=Bundle(pilot_zip,full=False) # Final/screen/development FEN row members remain sealed.
        corpus_report=original.json("corpus/CORPUS.json")
        train=[json.loads(line) for line in original.read("corpus/train.jsonl").splitlines()]
        roots={row["root_id"]:row for row in train}
        corpus.need(len(roots)==6658 and all(row["partition"]=="train" for row in train),"Exact training denominator differs")
        pilot_labels=[json.loads(line) for line in original.read("PILOT-LABELS.jsonl").splitlines()]
        pilot_children=[json.loads(line) for line in original.read("PILOT-CHILD-CACHE.jsonl").splitlines()]
        pilot_map={row["root_id"]:row for row in pilot_labels}
        pilot_cache={(row["parent_root_id"],row["rank"]):row for row in pilot_children}
        original_parity=parity_replay(original,"original-pilot-parity",{row["source_root_index"]:row for row in pilot_children})
        corpus.need(len(original_parity)==94,"All pilot native children required")
        pilot_exposure=hashlib.sha256();teacher_queries=0
        for label in pilot_labels:
            teacher_queries+=teacher_replay(original,"teacher-training-calibration",label,roots[label["root_id"]],pilot_exposure)
        original_transport=original.transports["teacher-training-calibration"]
        corpus.need(len(original_transport["used_go_indices"])==sum(line.startswith("go nodes ") for line in original_transport["commands"]),
                    "Every actual pilot search must be indexed")
        corpus.need(pilot_exposure.hexdigest()==original.json("PILOT-RESULT.json")["teacher_exposure"]["canonical_uncompressed_sha256"],
                    "Pilot full legal exposure stream differs")
        labels,cache,seen_roots=[],[],set()
        native_count=94
        shard_refs=[]
        for pin in sorted(pins["artifacts"],key=lambda row:row["shard"]):
            meta=api(f"repos/0mis/Stockfish/actions/artifacts/{pin['id']}")
            corpus.need(meta["digest"]=="sha256:"+pin["sha256"] and meta["size_in_bytes"]==pin["bytes"]
                        and meta["workflow_run"]["id"]==ACQUISITION_RUN,"Actual immutable artifact metadata differs")
            archive=retained/("train-shard-"+str(pin["shard"])+".zip");fetch(pin,archive)
            bundle=Bundle(archive)
            result=bundle.json("RESULT.json");roster=bundle.json("SHARD-ROSTER.json")
            corpus.need(result["status"]=="COMPLETE_TRAIN_SHARD_NATIVE_PARITY_PASSED"
                        and result["plan_sha256"]==corpus.PLAN_SHA and result["driver_commit"]==ACQUISITION_COMMIT,
                        "Source/native shard receipt differs")
            shard_labels=[json.loads(line) for line in bundle.read("TRAIN-LABELS.jsonl").splitlines()]
            shard_label_by_root={row["root_id"]:row for row in shard_labels}
            shard_cache=[json.loads(line) for line in bundle.read("TRAIN-CHILD-CACHE.jsonl").splitlines()]
            expected={root for root in roots if int(root,16)%4==pin["shard"]}
            corpus.need(set(roster["root_ids"])==expected=={row["root_id"] for row in shard_labels}
                        and len(expected)==len(shard_labels)==result["root_denominator"] and not seen_roots&expected,
                        "Complete disjoint exact shard coverage differs")
            seen_roots.update(expected)
            fresh=[json.loads(line) for line in bundle.read("NEW-CHILD-CACHE.jsonl").splitlines()]
            fresh_by_index={row["source_root_index"]:row for row in fresh}
            corpus.need(len(fresh_by_index)==len(fresh),"Fresh native source indices must be unique")
            parity_seen=[]
            for index in range(len(result["native_parity"])):
                parity_seen.extend(parity_replay(bundle,"original-train-parity-"+str(index).zfill(3),fresh_by_index))
            corpus.need(set(parity_seen)==set(fresh_by_index) and len(parity_seen)==len(fresh),"Every fresh original child requires native raw/CP replay")
            native_count+=len(fresh)
            exposure_digest=hashlib.sha256();queries=0
            for label in shard_labels:
                root=roots[label["root_id"]]
                corpus.need(label["family_id"]==root["family_id"] and label["partition"]=="train","Family/source binding differs")
                origin=label["capture_origin"]
                if origin["stage"]=="fixed-training-pilot":
                    corpus.need({key:value for key,value in label.items() if key!="capture_origin"}==pilot_map[label["root_id"]],
                                "Reused pilot row changed")
                elif origin["stage"]=="source-unavailable":
                    corpus.need(label["reasons"]==root["source_reasons"] and not label["eligible"] and not label["children"],
                                "Source-unavailable denominator altered")
                else:
                    corpus.need(origin["stage"]=="training-acquisition" and origin["driver_commit"]==ACQUISITION_COMMIT,
                                "Unknown teacher capture role")
                    queries+=teacher_replay(bundle,"teacher-train-shard-"+str(pin["shard"]),label,root,exposure_digest)
            corpus.need(exposure_digest.hexdigest()==result["teacher_exposure"]["canonical_uncompressed_sha256"],
                        "Complete actual teacher legal-path index differs")
            transport=bundle.transports.get("teacher-train-shard-"+str(pin["shard"]))
            corpus.need(transport is not None and len(transport["used_go_indices"])==sum(line.startswith("go nodes ") for line in transport["commands"]),
                        "Every actual shard teacher search must be indexed exactly once")
            teacher_queries+=queries
            all_children={(row["parent_root_id"],row["rank"]):row for row in shard_cache}
            expected_children={(row["root_id"],child["rank"]) for row in shard_labels for child in row["children"]
                               if child["ordinary_static_available"]}
            corpus.need(set(all_children)==expected_children and len(all_children)==len(shard_cache),
                        "Unavailable pairs lost measurable children")
            for key,row in all_children.items():
                origin=row["native_origin"];base={k:v for k,v in row.items() if k!="native_origin"}
                expected_row=pilot_cache[key] if origin["stage"]=="fixed-training-pilot" else fresh_by_index[origin["source_root_index"]]
                corpus.need(base==expected_row,"Native child cache was changed during lossless merge")
                label=shard_label_by_root[key[0]]
                child=next(child for child in label["children"] if child["rank"]==key[1])
                source_board=chess.Board(roots[key[0]]["fen6"])
                source_board.push(source_board.parse_uci(child["move"]))
                child_id,aliases=corpus.identity(source_board)
                corpus.need(row["root_fen6"]==child["fen6"]==source_board.fen(en_passant="fen")
                            and row["child_position_id"]==child["position_id"]==child_id
                            and child["alias_ids"]==aliases and row["component_id"]==label["family_id"],
                            "Actual native cache child must be the selected legal sibling")
                board=chess.Board(row["root_fen6"])
                corpus.need(board.is_valid() and not board.is_check() and not board.is_game_over(claim_draw=False),
                            "Ordinary static unavailable child used")
                head=((board.occupied.bit_count()-1)//4)*4+2*bool(board.pieces_mask(chess.QUEEN,board.turn))+bool(board.pieces_mask(chess.QUEEN,not board.turn))
                corpus.need(row["head"]==head and row["rule50"]==board.halfmove_clock
                            and row["side_to_move"]==("w" if board.turn else "b")
                            and len(row["input128"])==128 and all(type(v) is int and 0<=v<=127 for v in row["input128"]),
                            "Actual D1 route/clock/STM/canonical activation schema differs")
            labels.extend(shard_labels);cache.extend(shard_cache)
            shard_refs.append({"artifact":pin,"producer_result":bundle.refs["RESULT.json"],"verified_members":len(bundle.checked)})
            print("AUDITED COMPLETE SHARD",pin["shard"],"roots",len(shard_labels),"eligible",sum(row["eligible"] for row in shard_labels),flush=True)
        corpus.need(seen_roots==set(roots),"Fixed entire training denominator not complete")
        corpus.need(native_count==len(cache),"All unique measured training children require original native parity")
        labels.sort(key=lambda row:row["root_id"]);cache.sort(key=lambda row:(row["parent_root_id"],row["rank"]))
        children={(row["parent_root_id"],row["rank"]):row for row in cache}
        eligible=[row for row in labels if row["eligible"]]
        families={row["family_id"] for row in eligible}
        baseline_map={key:row["base_static_cp_stm"] for key,row in children.items()}
        baseline=fit_core.metrics(eligible,children,baseline_map) if eligible else None
        selected_heads,support=fit_core.select_heads(eligible,children)
        selected_error,strata=fit_core.select_error_stratum(eligible,children)
        requested=sum(query["budget_nodes"] for row in labels for query in (row.get("low"),row.get("high")) if query)
        actual=sum(query["actual_reported_max_nodes"] for row in labels for query in (row.get("low"),row.get("high")) if query)
        enough=len(eligible)>=2000 and len(families)>=64
        result={"schema":"official-d1-test1758-complete-train-audit/1",
                "status":"TRAIN_NATIVE_DATA_READY_FOR_FROZEN_FIT" if enough else "INSUFFICIENT_FROZEN_TRAIN_SUPPORT_IDENTITY",
                "plan_sha256":corpus.PLAN_SHA,"fit_program_sha256":FIT_CORE_SHA,
                "run_id":ACQUISITION_RUN,"source_driver_commit":ACQUISITION_COMMIT,
                "fixed_training_roots":len(labels),"training_families":len({row["family_id"] for row in labels}),
                "stable_eligible_pairs":len(eligible),"eligible_families":len(families),
                "same_head_pairs":sum(children[(row["root_id"],1)]["head"]==children[(row["root_id"],2)]["head"] for row in eligible),
                "ordinary_measured_children":len(cache),"all_original_native_raw_cp_rows_replayed":native_count,
                "actual_teacher_queries_legally_replayed":teacher_queries,"requested_nodes":requested,"actual_reported_nodes":actual,
                "unavailable_reasons":dict(Counter(reason for row in labels for reason in row["reasons"])),
                "baseline_static_ordering_vs_teacher_consensus":fit_core.serialize_metrics(baseline) if baseline else None,
                "selected_training_error_stratum":selected_error,"selected_supported_heads":selected_heads,
                "minimum_required_pairs":2000,"minimum_required_families":64,"support_gate_passed":enough,
                "all_raw_archives_preserved_in_this_artifact":True,"shard_inputs":shard_refs,
                "pilot_input":pilot_pin,"development_final_screen_labels_features_predictions_opened":False,
                "current_or_formal_games_or_results_read":False,"native_engines_executed_by_this_audit":0,
                "fresh_training_strength_replication_performed":False,"proxy_is_Elo":False,"farm_submitted":False,
                "scope":"Static ordering disagreement relative to a fallible searched teacher; not proof of a live playing error or strength gain."}
        with (out/"TRAIN-LABELS.jsonl").open("xb") as stream:
            for row in labels:stream.write(corpus.canonical(row)+b"\n")
        with (out/"TRAIN-CHILD-CACHE.jsonl").open("xb") as stream:
            for row in cache:stream.write(corpus.canonical(row)+b"\n")
        corpus.put(out/"SUPPORT.json",support);corpus.put(out/"TRAINING-ERROR-STRATA.json",strata)
        if not enough:
            frozen=fit_core.fit(labels,cache,None,out/"frozen-identity")
            corpus.need(frozen["identity"] and frozen["reason"]=="INSUFFICIENT_FIXED_TRAIN_SUPPORT","Frozen identity stop differs")
            result["frozen_identity"]=corpus.reference(out/"frozen-identity/FROZEN-CORE.json")
            result["candidate_network_written"]=False;result["fit_performed"]=False
            result["retained_parent_network_sha256"]=plan["student"]["network_sha256"]
            result["identity_network_sha256"]=plan["student"]["network_sha256"]
        else:
            result["candidate_network_written"]=False;result["fit_performed"]=False
        corpus.put(out/"RESULT.json",result)
        corpus.put(out/"ARTIFACT-MANIFEST.json",{"schema":"official-d1-test1758-artifact-manifest/1",
                   "files":[{"path":path.relative_to(out).as_posix(),"bytes":path.stat().st_size,
                             "sha256":hashlib.sha256(path.read_bytes()).hexdigest()}
                            for path in sorted(out.rglob("*")) if path.is_file()]})
        print(json.dumps(result),flush=True)
    except BaseException as error:
        corpus.put(out/"FAILURE.json",{"schema":"official-d1-test1758-train-audit-failure/1","type":type(error).__name__,
                   "message":str(error),"traceback":traceback.format_exc(),"plan_sha256":corpus.PLAN_SHA})
        raise


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--pins",type=Path,required=True)
    parser.add_argument("--out",type=Path,required=True)
    args=parser.parse_args();run(args.pins,args.out.resolve())
