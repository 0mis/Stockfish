"""Pure receipt, heldout-request and actual-measurement checks. No fitting or I/O."""
from collections import Counter
import hashlib
import json
import chess
from lichess_fc2 import core,patch

PARENT=core.PARENT_SHA256
PARENT_BYTES=core.PARENT_BYTES
VALIDATION_SHA="f542d111aa1c850c00da08beb6d45a8fa5b0447a6aa9df93d06350ee4ba5cd24"
VALIDATION_BYTES=162650
VALIDATION_ROWS=68
GLOBAL_SPLIT="c37208fd77435ba0343fdae2c0f74e748b018eae9e4a07d282273345de1e43b9"
need=core.require

def normalize_mapping(source):
    need("heads92" in source and "heads" not in source,"Accepted source map must use exactly heads92")
    return {k:value for k,value in source.items() if k!="heads92"}|{"heads":source["heads92"]}

def verify_frozen(wrapper,mapping,approval,*,file_sha256,allow_synthetic=False):
    """No heldout data or binaries are needed to verify this first gate."""
    need(wrapper["schema"]=="lichess-fc2-fitted-candidate-freeze/1" and wrapper["sha256"]==core.digest({k:v for k,v in wrapper.items() if k!="sha256"}),"Frozen wrapper digest/schema differs")
    core.validate_mapping(mapping,allow_synthetic=allow_synthetic)
    frozen,recipe=wrapper["core_freeze"],wrapper["byte_patch"]
    core.verify_freeze(frozen,mapping); patch.verify_patch(recipe,mapping)
    need(recipe["freeze_sha256"]==frozen["freeze_sha256"] and recipe["edits"]==frozen["edits"] and recipe["identity"] is frozen["identity"],"Joint choice and byte recipe differ")
    candidate=wrapper["candidate"]
    need(candidate["sha256"]==recipe["candidate_sha256"] and candidate["bytes"]==recipe["bytes"]
        and candidate["parent_sha256"]==recipe["parent_sha256"] and candidate["identity"] is recipe["identity"],"Candidate/recipe binding differs")
    need(approval["schema"]=="root-verified-lichess-fc2-freeze/1" and approval["status"]=="VERIFIED_FROZEN_RECIPE"
        and approval["root_verified"] is True and approval["frozen_file_sha256"]==file_sha256
        and approval["wrapper_sha256"]==wrapper["sha256"] and approval["freeze_sha256"]==frozen["freeze_sha256"]
        and approval["patch_sha256"]==recipe["patch_sha256"] and approval["mapping_sha256"]==core.digest(mapping)
        and approval["candidate_sha256"]==candidate["sha256"] and approval["identity"] is candidate["identity"],"Root's exact frozen-recipe acceptance is missing or differs")
    if candidate["identity"]:
        need(candidate["sha256"]==mapping["parent_sha256"] and not recipe["edits"],"Identity receipt differs")
    else: need(candidate["sha256"]!=mapping["parent_sha256"] and 1<=len(recipe["edits"])<=4,"Nonidentity candidate is missing")
    if not allow_synthetic:
        need(approval["candidate_bytes"]==candidate["bytes"]==PARENT_BYTES
            and core.hash_string(approval["completed_fit_result_sha256"])
            and approval["completed_fit_result_schema"]=="lichess-fc2-real-training-fit-receipt/1"
            and approval["independent_disk_readback"] is True and approval["complete_inverse_sha256"]==PARENT,
            "Root's completed-fit readback/inverse admission is missing")
        if not candidate["identity"]:
            need(approval["completed_fit_result_status"]=="candidate_frozen_awaiting_separate_hosted_validation","Completed nonidentity fit status differs")
        need(frozen["component_split_sha256"]==GLOBAL_SPLIT,"Frozen global prospective split differs")
        for component in frozen["training_components"]:
            source_hash=hashlib.sha256(("lichess-multipv-readout-v1/component|"+component).encode("ascii")).hexdigest()
            need(int(source_hash,16)%5!=0,"Frozen training component belongs to validation")
    return {"wrapper":wrapper,"mapping":mapping,"frozen":frozen,"recipe":recipe,"identity":candidate["identity"],
        "target_verified":False,"validation_opened":False,"frozen_file_sha256":file_sha256}

def reconstruct_target(context,parent,*,allow_synthetic=False):
    """Authenticate the full original, forward patch and inverse before heldout."""
    need(not context["identity"] and not context["validation_opened"],"Identity stops before binaries/heldout; validation cannot reopen")
    mapping,recipe=context["mapping"],context["recipe"]
    target=patch.apply_patch_bytes(parent,mapping,recipe,allow_synthetic=allow_synthetic)
    inverse=patch.apply_patch_bytes(target,mapping,recipe,reverse=True,allow_synthetic=allow_synthetic)
    need(inverse==parent and hashlib.sha256(inverse).hexdigest()==mapping["parent_sha256"],"Whole inverse differs from exact parent")
    context["target_verified"]=True
    return target

def normalize_bundle(bundle,expected_global):
    """Apply only Root's approved source-component/global-core schema adapter."""
    global_sha=core.digest({"schema":"lichess-public-prospective-split-binding-v1","prefix_sha256":bundle["prefix_sha256"],
        "prospective_split":bundle["prospective_split"]})
    need(global_sha==expected_global,"Validation prospective wrapper split differs from the frozen training binding")
    normalized=[]
    for original in bundle["rows"]:
        component=original["component_id"]
        source_hash=hashlib.sha256(("lichess-multipv-readout-v1/component|"+component).encode("ascii")).hexdigest()
        need(original["component_split_sha256"]==source_hash and original["split"]=="validation"
            and int(source_hash,16)%5==0,"Original component hash/mod5 validation assignment differs")
        normalized.append({**original,"source_component_split_sha256":source_hash,"component_split_sha256":global_sha})
    return {**bundle,"rows":normalized,"core_global_component_split_sha256":global_sha,
        "split_adapter":"original component hashes/mod5 verified; original hash preserved; only core API split field normalized"}

def open_validation(context,reader,descriptor,*,expected_rows=VALIDATION_ROWS):
    """reader is called only after a verified nonidentity target exists."""
    need(not context["identity"] and context["target_verified"] and not context["validation_opened"],"Heldout input is sealed until target verification")
    raw=reader()
    need(len(raw)==descriptor["bytes"] and hashlib.sha256(raw).hexdigest()==descriptor["sha256"],"Heldout file-byte binding differs")
    bundle=json.loads(raw)
    need(bundle["schema"]=="lichess-public-multipv-teachers-v1" and bundle["partition"]=="validation"
        and bundle["projection"]=="ordered-first-move-pairs-v1" and len(bundle["rows"])==expected_rows,"Heldout partition/projection/count differs")
    frozen=context["frozen"]
    bundle=normalize_bundle(bundle,frozen["component_split_sha256"])
    joined=core.join_teacher_rows(bundle,{})
    core._metadata(joined,"validation",frozen["component_split_sha256"])
    need(not {r["position_id"] for r in joined}&set(frozen["training_root_ids"])
        and not {r["component_id"] for r in joined}&set(frozen["training_components"]),"Heldout split intersects frozen training")
    context["validation_opened"]=True
    return bundle

def requests(bundle):
    """Keep all parent/child dispositions; eligibility is per child, never per pair."""
    wanted,parents=[],[]
    for row in bundle["rows"]:
        board=chess.Board(row["fen"])
        need(board.is_valid() and not board.chess960 and ("white" if board.turn else "black")==row["root_pov"],"Published root context differs")
        need(hashlib.sha256(" ".join(board.fen(en_passant="legal").split()[:4]).encode()).hexdigest()==row["position_id"],"Published root position ID differs")
        children=[]
        for candidate in row["candidates"]:
            need(len(candidate["standard_uci"])==1,"Require the frozen single-first-move projection")
            token=candidate["standard_uci"][0]
            move=board.parse_uci(token); need(move in board.legal_moves,"Illegal published first move")
            child=board.copy(stack=False); child.push(move)
            supplied=candidate["child"]
            fen=child.fen(en_passant="legal")
            aliases=sorted({hashlib.sha256(" ".join(child.fen(shredder=s,en_passant=ep).split()[:4]).encode()).hexdigest() for s in (False,True) for ep in ("legal","fen")})
            key=hashlib.sha256(" ".join(fen.split()[:4]).encode()).hexdigest()
            need(fen==supplied["fen"] and key==supplied["position_id"] and aliases==supplied["alias_ids"]
                and child.is_check() is supplied["in_check"] and child.is_game_over(claim_draw=False) is supplied["terminal"],"Published child replay/disposition differs")
            reasons=(["terminal_child_ordinary_static_unavailable"] if supplied["terminal"] else [])+(["in_check_child_ordinary_static_unavailable"] if supplied["in_check"] else [])
            item={"parent_position_id":row["position_id"],"rank":candidate["rank"],"component_id":row["component_id"],
                "component_split_sha256":row["component_split_sha256"],"split":"validation","child_position_id":key,"child_alias_ids":aliases,
                "source_component_split_sha256":row["source_component_split_sha256"],
                "fen":row["fen"],"move":token,"child_fen":fen,"rule50":child.halfmove_clock,"root_pov":row["root_pov"],
                "eligible_for_ordinary_static":not reasons,"unavailability_reasons":reasons,"row_id":None}
            if not reasons: item["row_id"]=len(wanted); wanted.append(item)
            children.append(item)
        parents.append({"position_id":row["position_id"],"component_id":row["component_id"],"children":children})
    return wanted,parents

def reports(context,bundle,captures,ordinary_parent,ordinary_candidate,*,allow_synthetic=False):
    """Score actual measured CP independently of the frozen activation predictions."""
    need(context["target_verified"] and context["validation_opened"] and not context["identity"],"Missing target/heldout gates")
    mapping,frozen,recipe=context["mapping"],context["frozen"],context["recipe"]
    rows=core.join_teacher_rows(bundle,captures)
    proxy=core.score_validation(rows,mapping,frozen,recipe,allow_synthetic=allow_synthetic)
    edits={e["physical_head"]:e for e in frozen["edits"]}
    all_children,actual_rows,matched=[],[],[]
    for row in rows:
        children=[]; reasons=[]
        for child in row["children"]:
            key=(row["position_id"],child["rank"])
            record={"parent_position_id":row["position_id"],"rank":child["rank"],"child_position_id":child["position_id"]}
            if child["terminal"] or child["in_check"]:
                why=(["terminal_child_ordinary_static_unavailable"] if child["terminal"] else [])+(["in_check_child_ordinary_static_unavailable"] if child["in_check"] else [])
                record.update(status="unavailable",reasons=why,parent_cp=None,candidate_cp=None)
                reasons.extend("child"+str(child["rank"])+":"+s for s in why)
                children.append(None)
            else:
                prepared=core._prepare_child(child,row,mapping)
                need(key in ordinary_parent and key in ordinary_candidate,"Eligible child actual measurement is missing; no imputation or row drop")
                old,new=ordinary_parent[key],ordinary_candidate[key]
                edit=edits.get(prepared["head"])
                predicted_raw=core.raw_from_combined(prepared["combined"]+(edit["delta"]*prepared["input"][edit["lane"]] if edit else 0))
                predicted_cp=core.child_cp(prepared,edit)
                need(old["physical_head"]==new["physical_head"]==prepared["head"] and old["raw_score"]==prepared["raw"]
                    and old["static_cp"]==core.child_cp(prepared) and new["raw_score"]==predicted_raw and new["static_cp"]==predicted_cp,
                    "Actual parent/candidate raw/CP does not match native frozen FC2 prediction")
                if "buckets" in old and "buckets" in new:
                    need(len(old["buckets"])==len(new["buckets"])==92,"Incomplete ordinary bucket table")
                    for h,(a,b) in enumerate(zip(old["buckets"],new["buckets"])):
                        if h not in edits: need(a==b,"An unchanged physical head's ordinary trace changed")
                record.update(status="measured",reasons=[],physical_head=prepared["head"],parent_cp=old["static_cp"],
                    candidate_cp=new["static_cp"],parent_raw=old["raw_score"],candidate_raw=new["raw_score"],
                    predicted_candidate_cp=predicted_cp,predicted_candidate_raw=predicted_raw,native_parity=True)
                children.append(record)
            all_children.append(record)
        entry={"position_id":row["position_id"],"component_id":row["component_id"],"reasons":reasons,"children":children}
        if reasons: entry.update(status="unavailable",parent_margin_cp=None,candidate_margin_cp=None)
        else:
            a,b=children
            old_margin=-a["parent_cp"]+b["parent_cp"]; new_margin=-a["candidate_cp"]+b["candidate_cp"]
            old_loss,old_deficit=core._loss(old_margin,row["teacher_margin_cp"])
            new_loss,new_deficit=core._loss(new_margin,row["teacher_margin_cp"])
            entry.update(status="measured",target_cp=min(60,row["teacher_margin_cp"]),parent_margin_cp=old_margin,candidate_margin_cp=new_margin,
                parent_deficit_cp=old_deficit,candidate_deficit_cp=new_deficit,parent_loss=core.fraction(old_loss),candidate_loss=core.fraction(new_loss),
                touches_changed_head=bool({a["physical_head"],b["physical_head"]}&set(edits)),cross_head_pair=a["physical_head"]!=b["physical_head"])
            matched.append((row["component_id"],old_loss,new_loss,old_margin,new_margin,old_deficit,new_deficit))
        actual_rows.append(entry)
    def mean(index):
        value=core.component_mean((r[0],r[index]) for r in matched)
        return core.fraction(value) if value is not None else None
    metrics={"matched_rows":len(matched),"matched_components":len({r[0] for r in matched}),"baseline_loss":mean(1),"candidate_loss":mean(2),
        "baseline_margin_cp":mean(3),"candidate_margin_cp":mean(4),"baseline_deficit_cp":mean(5),"candidate_deficit_cp":mean(6)}
    need(metrics==proxy["metrics_on_matched_rows"],"Actual CP aggregates differ from frozen native-parity proxy aggregates")
    actual={"schema":"lichess-fc2-actual-heldout-static/1","freeze_sha256":frozen["freeze_sha256"],"patch_sha256":recipe["patch_sha256"],
        "rows":actual_rows,"children":all_children,"metrics_on_matched_rows":metrics,"supplied_rows":len(rows),
        "measured_children":sum(r["status"]=="measured" for r in all_children),"unavailable_children":sum(r["status"]=="unavailable" for r in all_children),
        "unavailable_rows":sum(r["status"]=="unavailable" for r in actual_rows),"unavailability_reasons":dict(Counter(s for r in all_children for s in r["reasons"])),
        "actual_ordinary_candidate_and_parent_measured":True,"all_eligible_children_native_parity":True,"selection_reopened":False,
        "holdout_improvement_gate_applied":False,"verified_gain":False}
    return proxy,actual
