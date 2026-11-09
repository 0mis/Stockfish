"""Frozen exact sparse joint FC2 estimator. Real fitting is hosted TRAIN-only."""
from collections import Counter,defaultdict
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import sys
import corpus

DELTAS=(-4,-2,-1,0,1,2,4)


def fraction(value):
    return {"numerator":value.numerator,"denominator":value.denominator}


def family_mean(values,families):
    grouped=defaultdict(list)
    for value,family in zip(values,families):
        grouped[family].append(Fraction(value))
    corpus.need(grouped,"At least one represented family required")
    return sum((sum(rows,Fraction())/len(rows) for rows in grouped.values()),Fraction())/len(grouped)


def huber20(error):
    corpus.need(error>=0,"Nonnegative deficit required")
    return Fraction(error*error,2) if error<=20 else 20*(error-10)


def exact_objective(margins,targets,drift_a,drift_b,families,edits):
    rows=[huber20(max(0,target-margin))+Fraction(a*a+b*b,2)
          for margin,target,a,b in zip(margins,targets,drift_a,drift_b)]
    return family_mean(rows,families)+Fraction(sum(delta*delta for delta in edits.values()),8)


def edit_tie(edits):
    changes=sorted((head,lane,delta) for (head,lane),delta in edits.items() if delta)
    return (len(changes),sum(delta*delta for _,_,delta in changes),
            sum(abs(delta) for _,_,delta in changes),
            tuple((head,lane,DELTAS.index(delta)) for head,lane,delta in changes))


def allowed(edits,parent_heads):
    active={key:delta for key,delta in edits.items() if delta}
    counts=Counter(head for head,lane in active)
    return (len(active)<=8 and sum(abs(delta) for delta in active.values())<=16
            and all(count<=2 for count in counts.values())
            and all(delta in DELTAS and -128<=parent_heads[head].weights[lane]+delta<=127
                    for (head,lane),delta in active.items()))


def raw_value(combined):
    def trunc(n,d):
        return n//d if n>=0 else -((-n)//d)
    return trunc(trunc(combined*9600,16384),16)


def select_heads(pairs,children,minimum_roots=256,minimum_families=24):
    roots=Counter();families=defaultdict(set)
    for row in pairs:
        first,second=children[(row["root_id"],1)],children[(row["root_id"],2)]
        if first["head"]==second["head"]:
            head=first["head"];roots[head]+=1;families[head].add(row["family_id"])
    supported=[head for head,count in roots.items() if count>=minimum_roots and len(families[head])>=minimum_families]
    selected=sorted(supported,key=lambda h:(-roots[h],h))[:4]
    return selected,{"heads":[{"head":head,"same_head_pairs":roots[head],"families":len(families[head]),
                              "selected":head in selected} for head in sorted(roots)]}


def stratum(row,children):
    a,b=children[(row["root_id"],1)],children[(row["root_id"],2)]
    if a["head"]!=b["head"]:
        return None
    moves={child["rank"]:child for child in row["children"]}
    return "head=%02d;A_capture=%d;B_capture=%d"%(a["head"],moves[1]["capture_or_promotion"],moves[2]["capture_or_promotion"])


def ranking_error(margin):
    return Fraction(1) if margin<0 else Fraction(1,2) if margin==0 else Fraction()


def select_error_stratum(pairs,children):
    grouped=defaultdict(list)
    for row in pairs:
        key=stratum(row,children)
        if key is not None:
            grouped[key].append(row)
    candidates=[]
    summaries=[]
    for key,rows in sorted(grouped.items()):
        families=[row["family_id"] for row in rows]
        errors=[ranking_error(-children[(row["root_id"],1)]["base_static_cp_stm"]
                              +children[(row["root_id"],2)]["base_static_cp_stm"]) for row in rows]
        error=family_mean(errors,families)
        supported=len(rows)>=128 and len(set(families))>=24 and error>=Fraction(1,4)
        summaries.append({"stratum":key,"pairs":len(rows),"families":len(set(families)),
                          "baseline_family_equal_error":fraction(error),"supported":supported})
        if supported:
            candidates.append((error,key))
    chosen=sorted(candidates,key=lambda item:(-item[0],item[1]))[0][1] if candidates else None
    return chosen,summaries


def metrics(pairs,children,cp_by_key):
    corpus.need(all(row["partition"]=="train" for row in pairs),"TRAIN-only metrics in fitting phase")
    families=[row["family_id"] for row in pairs]
    margins=[-cp_by_key[(row["root_id"],1)]+cp_by_key[(row["root_id"],2)] for row in pairs]
    errors=[ranking_error(margin) for margin in margins]
    deficits=[huber20(max(0,row["target_margin_cp"]-margin)) for row,margin in zip(pairs,margins)]
    return {"pairs":len(pairs),"families":len(set(families)),
            "error":family_mean(errors,families),"huber":family_mean(deficits,families)}


def serialize_metrics(value):
    return {key:fraction(item) if isinstance(item,Fraction) else item for key,item in value.items()}


def fit(labels,cache,parent_path,out):
    corpus.need(os.environ.get("GITHUB_ACTIONS")=="true","Real estimator may only run in hosted CI")
    plan=corpus.policy();out=Path(out);out.mkdir(parents=True,exist_ok=False)
    corpus.need(all(row["partition"]=="train" for row in labels)
                and all(row["partition"]=="train" for row in cache),"Never fit development/final data")
    children={(row["parent_root_id"],row["rank"]):row for row in cache}
    corpus.need(len(children)==len(cache),"Unique composite child key required")
    pairs=[row for row in labels if row["eligible"]]
    if len(pairs)<2000 or len({row["family_id"] for row in pairs})<64:
        core={"schema":"official-d1-test1758-joint-freeze-core/1","identity":True,
              "reason":"INSUFFICIENT_FIXED_TRAIN_SUPPORT","training_pairs":len(pairs),
              "training_families":len({row["family_id"] for row in pairs}),"edits":[],
              "plan_sha256":corpus.PLAN_SHA,"development_or_final_labels_opened":False}
        corpus.put(out/"FROZEN-CORE.json",core)
        return core
    selected,support=select_heads(pairs,children)
    chosen_stratum,strata=select_error_stratum(pairs,children)
    corpus.put(out/"SUPPORT.json",support);corpus.put(out/"ERROR-STRATA.json",strata)
    if not selected or chosen_stratum is None:
        core={"schema":"official-d1-test1758-joint-freeze-core/1","identity":True,
              "reason":"NO_SUPPORTED_HEAD_OR_REPRODUCIBLE_ERROR_STRATUM","training_pairs":len(pairs),
              "training_families":len({row["family_id"] for row in pairs}),"selected_heads":selected,
              "error_stratum":chosen_stratum,"edits":[],"plan_sha256":corpus.PLAN_SHA,
              "development_or_final_labels_opened":False}
        corpus.put(out/"FROZEN-CORE.json",core);return core
    sys.path.insert(0,str(corpus.HERE/"d1lib"))
    import numpy as np
    from stage4.d1_fc2_study import forward,weights
    parent_heads=weights.read_parent_tail(parent_path)
    ordered=sorted(cache,key=lambda row:(row["parent_root_id"],row["rank"]))
    lookup_index={(row["parent_root_id"],row["rank"]):index for index,row in enumerate(ordered)}
    head=np.array([row["head"] for row in ordered],dtype=np.int64)
    inputs=np.array([row["input128"] for row in ordered],dtype=np.int64)
    combined=np.array([row["base_fc2_i32"]+row["skip_i32"] for row in ordered],dtype=np.int64)
    base_raw=np.array([row["base_raw_nnue_i32"] for row in ordered],dtype=np.int64)
    base_cp=np.array([row["base_static_cp_stm"] for row in ordered],dtype=np.int64)
    corpus.need(inputs.shape==(len(ordered),128) and np.all((inputs>=0)&(inputs<=127)),"Exact canonical FC2 uint7 inputs required")
    cp_lut=np.empty((len(ordered),161),dtype=np.int64)
    for index,row in enumerate(ordered):
        original=parent_heads[row["head"]]
        corpus.need(original.bias+sum(w*x for w,x in zip(original.weights,row["input128"]))==row["base_fc2_i32"],
                    "Authenticated parent FC2 dot/cache mismatch")
        corpus.need(raw_value(int(combined[index]))==row["base_raw_nnue_i32"],"Exact original raw quantizer mismatch")
        context=forward.EvalContext(row["side_to_move"],row["rule50"],row["simple_eval_stm"],row["static_material"],row["wdl_material"])
        for delta in range(-80,81):
            cp_lut[index,delta+80]=forward.to_cp(forward.static_wrapper(int(base_raw[index])+delta,context,optimism=0),context.wdl_material)
    corpus.need(np.array_equal(cp_lut[:,80],base_cp),"Original exact CP LUT/cache mismatch")
    a=np.array([lookup_index[(row["root_id"],1)] for row in pairs],dtype=np.int64)
    b=np.array([lookup_index[(row["root_id"],2)] for row in pairs],dtype=np.int64)
    targets=np.array([row["target_margin_cp"] for row in pairs],dtype=np.int64)
    families=[row["family_id"] for row in pairs]
    group_indices=[np.array([i for i,value in enumerate(families) if value==family],dtype=np.int64)
                   for family in sorted(set(families))]
    row_indices=np.arange(len(ordered))
    def score(affine):
        values=combined+affine
        corpus.need(np.all((values>=-(1<<31))&(values<(1<<31))),"FC2 combined int32 bound exceeded")
        scaled=np.where(values>=0,(values*9600)//16384,-((-values*9600)//16384))
        raw=np.where(scaled>=0,scaled//16,-((-scaled)//16))
        delta=raw-base_raw
        corpus.need(np.all((delta>=-80)&(delta<=80)),"Frozen L1-bound exact raw-delta LUT range exceeded")
        return cp_lut[row_indices,delta+80]
    def cost(cp,edits):
        margin=-cp[a]+cp[b]
        error=np.maximum(0,targets-margin)
        twice_huber=np.where(error<=20,error*error,40*(error-10))
        drift_a=cp[a]-base_cp[a];drift_b=cp[b]-base_cp[b]
        twice_cost=twice_huber+drift_a*drift_a+drift_b*drift_b
        result=sum((Fraction(int(twice_cost[indices].sum()),2*len(indices)) for indices in group_indices),Fraction())/len(group_indices)
        return result+Fraction(sum(delta*delta for delta in edits.values()),8)
    edits={};affine=np.zeros(len(ordered),dtype=np.int64);cp=score(affine);current=cost(cp,edits)
    baseline_cost=current;sweeps=0
    with (out/"FIT-TRACE.jsonl").open("xb") as trace:
        for sweep in range(4):
            changed=False;sweeps+=1
            for physical_head in sorted(selected):
                mask=(head==physical_head)
                for lane in range(128):
                    old=edits.get((physical_head,lane),0)
                    best=(current,edit_tie(edits));best_edits=dict(edits);best_cp=cp;best_affine=affine
                    attempts=[]
                    for delta in DELTAS:
                        trial=dict(edits)
                        if delta:trial[(physical_head,lane)]=delta
                        else:trial.pop((physical_head,lane),None)
                        if not allowed(trial,parent_heads):
                            attempts.append({"delta":delta,"allowed":False});continue
                        trial_affine=affine+(delta-old)*inputs[:,lane]*mask
                        trial_cp=score(trial_affine);trial_cost=cost(trial_cp,trial)
                        attempts.append({"delta":delta,"allowed":True,"cost":fraction(trial_cost)})
                        key=(trial_cost,edit_tie(trial))
                        if key<best:
                            best=key;best_edits=trial;best_cp=trial_cp;best_affine=trial_affine
                    trace.write(corpus.canonical({"sweep":sweep,"head":physical_head,"lane":lane,"attempts":attempts,
                                                 "chosen_delta":best_edits.get((physical_head,lane),0)})+b"\n")
                    if best_edits!=edits:
                        changed=True
                    edits,cp,affine,current=best_edits,best_cp,best_affine,best[0]
            if not changed:break
    base_map={key:row["base_static_cp_stm"] for key,row in children.items()}
    changed_map={key:int(cp[index]) for key,index in lookup_index.items()}
    baseline_metrics=metrics(pairs,children,base_map);candidate_metrics=metrics(pairs,children,changed_map)
    drift=[int(value) for value in cp-base_cp]
    anchor={"children":len(drift),"sum_signed_cp":sum(drift),"sum_squared_cp":sum(value*value for value in drift),
            "maximum_abs_cp":max(map(abs,drift),default=0)}
    anchors_pass=(anchor["sum_squared_cp"]<=16*len(drift) and anchor["maximum_abs_cp"]<=12
                  and abs(anchor["sum_signed_cp"])<=len(drift))
    gate=bool(edits) and baseline_metrics["error"]-candidate_metrics["error"]>=Fraction(1,50)
    gate=gate and candidate_metrics["huber"]<=baseline_metrics["huber"]*Fraction(95,100) and anchors_pass
    rows=[{"head":head,"lane":lane,"delta":delta,"before":parent_heads[head].weights[lane],
           "after":parent_heads[head].weights[lane]+delta,
           "offset":weights.STACKS_OFFSET+head*weights.STACK_BYTES+weights.FC2_IN_STACK+4+lane}
          for (head,lane),delta in sorted(edits.items())]
    core={"schema":"official-d1-test1758-joint-freeze-core/1","identity":not bool(edits),
          "reason":"TRAIN_FIT_FROZEN" if edits else "EXACT_OBJECTIVE_SELECTED_IDENTITY",
          "plan_sha256":corpus.PLAN_SHA,"parent_sha256":weights.PARENT_SHA256,
          "training_pairs":len(pairs),"training_families":len(set(families)),
          "selected_heads":selected,"error_stratum":chosen_stratum,"edits":rows,"sweeps":sweeps,
          "baseline_objective":fraction(baseline_cost),"candidate_objective":fraction(current),
          "baseline_proxy_metrics":serialize_metrics(baseline_metrics),
          "candidate_proxy_metrics":serialize_metrics(candidate_metrics),
          "absolute_original_evaluation_anchor":anchor,"training_behavioral_gate_passed_provisionally":gate,
          "native_candidate_parity_required_before_gate_acceptance":True,
          "development_or_final_labels_opened":False,"iterative_reselection_after_holdout":False}
    corpus.put(out/"FROZEN-CORE.json",core)
    return core
