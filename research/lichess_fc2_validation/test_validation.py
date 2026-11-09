"""Invented bytes/vectors/labels and standard-board fixtures only. No native calls."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch as mock_patch
import chess
from lichess_fc2 import core,patch,synthetic_fixtures as fixture
from lichess_fc2_validation import validation as v,native,hosted

META={"prefix_sha256":"b"*64,"prospective_split":{"rule":"synthetic component mod5"}}
SPLIT=core.digest({"schema":"lichess-public-prospective-split-binding-v1",**META})

def context(identity=False):
    parent,mapping=fixture.mapping_fixture()
    rows=fixture.training_rows(mapping,active_a={} if identity else {0:64})
    for row in rows: row["component_split_sha256"]=SPLIT
    frozen=core.fit_training(rows,mapping,SPLIT,allow_synthetic=True)
    target,recipe=patch.bind_patch(parent,mapping,frozen,allow_synthetic=True)
    wrapper={"schema":"lichess-fc2-fitted-candidate-freeze/1","core_freeze":frozen,"byte_patch":recipe,
        "candidate":{"sha256":recipe["candidate_sha256"],"bytes":len(parent),"parent_sha256":mapping["parent_sha256"],"identity":recipe["identity"],"filename":"synthetic.nnue"},
        "inputs":{"synthetic_only":True}}
    wrapper["sha256"]=core.digest(wrapper)
    file_sha=hashlib.sha256(core.canonical(wrapper)+b"\n").hexdigest()
    approval={"schema":"root-verified-lichess-fc2-freeze/1","status":"VERIFIED_FROZEN_RECIPE","root_verified":True,
        "frozen_file_sha256":file_sha,"wrapper_sha256":wrapper["sha256"],"freeze_sha256":frozen["freeze_sha256"],
        "patch_sha256":recipe["patch_sha256"],"mapping_sha256":core.digest(mapping),"candidate_sha256":recipe["candidate_sha256"],"identity":recipe["identity"]}
    ctx=v.verify_frozen(wrapper,mapping,approval,file_sha256=file_sha,allow_synthetic=True)
    return parent,target,ctx,approval

def component(index):
    while True:
        name="synthetic-validation-"+str(index)
        h=hashlib.sha256(("lichess-multipv-readout-v1/component|"+name).encode()).hexdigest()
        if int(h,16)%5==0: return name,h
        index+=1

def teacher(board,tokens,index=0):
    name,split=component(index*100)
    children=[]
    for rank,token in enumerate(tokens,1):
        child=board.copy(stack=False); child.push_uci(token)
        fen=child.fen(en_passant="legal")
        aliases=sorted({hashlib.sha256(" ".join(child.fen(shredder=s,en_passant=ep).split()[:4]).encode()).hexdigest() for s in (False,True) for ep in ("legal","fen")})
        children.append({"rank":rank,"standard_uci":[token],"source_white_cp":(20 if rank==1 else 0)*(1 if board.turn else -1),
            "root_cp":20 if rank==1 else 0,"source_mate":None,"child":{"fen":fen,"position_id":hashlib.sha256(" ".join(fen.split()[:4]).encode()).hexdigest(),
            "alias_ids":aliases,"in_check":child.is_check(),"terminal":child.is_game_over(claim_draw=False)}})
    return {"position_id":hashlib.sha256(" ".join(board.fen(en_passant="legal").split()[:4]).encode()).hexdigest(),"fen":board.fen(en_passant="legal"),
        "component_id":name,"component_split_sha256":split,"split":"validation","root_pov":"white" if board.turn else "black","teacher_margin_cp":20,"candidates":children}

def bundle(rows): return {"schema":"lichess-public-multipv-teachers-v1","partition":"validation","projection":"ordered-first-move-pairs-v1",**META,"rows":rows}
def open_bundle(ctx,b):
    raw=core.canonical(b)
    return v.open_validation(ctx,lambda:raw,{"bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest()},expected_rows=len(b["rows"]))

class ValidationTests(unittest.TestCase):
    def test_receipt_root_approval_and_target_gates_precede_heldout(self):
        parent,_,ctx,approval=context()
        called=[]
        with self.assertRaises(core.ContractError): v.open_validation(ctx,lambda:called.append(1),{"bytes":0,"sha256":"0"*64})
        self.assertEqual(called,[])
        bad=deepcopy(approval); bad["root_verified"]=False
        with self.assertRaises(core.ContractError): v.verify_frozen(ctx["wrapper"],ctx["mapping"],bad,file_sha256=ctx["frozen_file_sha256"],allow_synthetic=True)
        with self.assertRaises(core.ContractError): v.reconstruct_target(ctx,parent[:-1]+b"x",allow_synthetic=True)
        self.assertFalse(ctx["target_verified"])
        target=v.reconstruct_target(ctx,parent,allow_synthetic=True)
        self.assertEqual(hashlib.sha256(target).hexdigest(),ctx["recipe"]["candidate_sha256"])

    def test_identity_stops_before_binary_or_validation_reader(self):
        parent,_,ctx,_=context(True)
        self.assertTrue(ctx["identity"])
        with self.assertRaises(core.ContractError): v.reconstruct_target(ctx,parent,allow_synthetic=True)
        called=[]
        with self.assertRaises(core.ContractError): v.open_validation(ctx,lambda:called.append(1),{"bytes":0,"sha256":"0"*64})
        self.assertEqual(called,[])

    def test_lossless_map_and_component_adapters(self):
        _,mapping=fixture.mapping_fixture()
        source={k:x for k,x in mapping.items() if k!="heads"}|{"heads92":mapping["heads"]}
        self.assertEqual(v.normalize_mapping(source),mapping)
        with self.assertRaises(core.ContractError): v.normalize_mapping(mapping)
        b=bundle([teacher(chess.Board(),["g1h3","g1f3"])])
        actual=v.normalize_bundle(b,SPLIT)
        self.assertEqual(actual["rows"][0]["source_component_split_sha256"],b["rows"][0]["component_split_sha256"])
        self.assertEqual(actual["rows"][0]["component_split_sha256"],SPLIT)
        self.assertNotEqual(actual["rows"][0]["component_split_sha256"],actual["rows"][0]["source_component_split_sha256"])
        bad=deepcopy(b); bad["rows"][0]["component_split_sha256"]="0"*64
        with self.assertRaises(core.ContractError): v.normalize_bundle(bad,SPLIT)

    def test_individual_child_eligibility_preserves_unavailable_counterpart(self):
        parent,_,ctx,_=context(); v.reconstruct_target(ctx,parent,allow_synthetic=True)
        board=chess.Board(); board.push_uci("e2e4"); board.push_uci("f7f6")
        normalized=open_bundle(ctx,bundle([teacher(board,["d1h5","g1h3"])]))
        wanted,account=v.requests(normalized)
        self.assertEqual(len(wanted),1)
        self.assertEqual(wanted[0]["rank"],2)
        self.assertTrue(account[0]["children"][0]["unavailability_reasons"])
        self.assertEqual(len(account[0]["children"]),2)
        with self.assertRaises(core.ContractError): open_bundle(ctx,bundle([]))

    def test_actual_native_values_are_required_and_worse_holdout_is_not_a_selection_gate(self):
        parent,_,ctx,_=context(); v.reconstruct_target(ctx,parent,allow_synthetic=True)
        b=open_bundle(ctx,bundle([teacher(chess.Board(),["g1h3","g1f3"])]))
        requests,_=v.requests(b)
        captures={}; old={}; new={}
        for request in requests:
            key=(request["parent_position_id"],request["rank"])
            # Put activation on B, so the training-frozen negative edit worsens this invented heldout margin.
            cap=fixture.capture(ctx["mapping"],active_lanes={0:64} if request["rank"]==2 else {},root_pov=request["root_pov"])
            cap.update({k:request[k] for k in ("parent_position_id","rank","component_id","component_split_sha256","source_component_split_sha256","split","child_position_id")})
            cap.update(position_id=request["child_position_id"],rule50=request["rule50"])
            captures[key]=cap
        joined=core.join_teacher_rows(b,captures)[0]
        edit=ctx["frozen"]["edits"][0]
        for child in joined["children"]:
            key=(joined["position_id"],child["rank"]); prepared=core._prepare_child(child,joined,ctx["mapping"])
            raw=core.raw_from_combined(prepared["combined"]+edit["delta"]*prepared["input"][edit["lane"]])
            old[key]={"physical_head":0,"raw_score":prepared["raw"],"static_cp":core.child_cp(prepared)}
            new[key]={"physical_head":0,"raw_score":raw,"static_cp":core.child_cp(prepared,edit)}
        proxy,actual=v.reports(ctx,b,captures,old,new,allow_synthetic=True)
        a=actual["metrics_on_matched_rows"]
        self.assertGreater(a["candidate_loss"]["numerator"]/a["candidate_loss"]["denominator"],a["baseline_loss"]["numerator"]/a["baseline_loss"]["denominator"])
        self.assertFalse(actual["holdout_improvement_gate_applied"])
        self.assertEqual(len(actual["rows"]),1)
        wrong=deepcopy(new); next(iter(wrong.values()))["static_cp"]+=1
        with self.assertRaises(core.ContractError): v.reports(ctx,b,captures,old,wrong,allow_synthetic=True)

    def test_local_entry_refuses_without_opening_inputs_or_running_native(self):
        with mock_patch.dict(hosted.os.environ,{"GITHUB_ACTIONS":"false"}),mock_patch.object(native,"capture",side_effect=AssertionError("native called")):
            with self.assertRaises(ValueError): hosted.run(Path("."),Path("missing.json"),"0"*64,Path("unused-work"),Path("unused-out"))

    def test_public_source_benchmark_roster_has_51_exact_roots(self):
        specs=native.benchmark_specs(Path(__file__).with_name("benchmark.cpp").read_bytes())
        self.assertEqual(len(specs),51)
        self.assertTrue(any(board.chess960 for board in specs))

if __name__=="__main__": unittest.main()
