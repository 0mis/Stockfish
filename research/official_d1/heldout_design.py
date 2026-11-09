"""Gated heldout design. Importing or inspecting this file reads no heldout data.

The caller supplies a Root-owned native parity callback. No internal native
launcher exists. No validation loss is reported until both measured native
baseline/candidate results match every measurable heldout child prediction.
"""
import json
from pathlib import Path
import study


def gate(cache_dir, freeze_path, recipe_path, candidate_path, edited_train_parity_path):
    manifest, mapping, _ = study.load_cache(cache_dir)
    frozen = json.loads(Path(freeze_path).read_bytes()); study.verify_freeze(frozen, mapping)
    study.need(frozen["cache_manifest"] == study.reference(Path(cache_dir) / "CACHE-MANIFEST.json"), "Frozen train cache changed")
    recipe = json.loads(Path(recipe_path).read_bytes())
    parity = json.loads(Path(edited_train_parity_path).read_bytes())
    study.need(not frozen["identity"] and frozen["edits"] and recipe["freeze_sha256"] == frozen["freeze_sha256"]
               and recipe["edits"] == frozen["edits"] and recipe["biases_unchanged"] is True
               and recipe["parent_sha256"] == recipe["inverse_sha256"] == study.weights.PARENT_SHA256,
               "Nonidentity joint freeze and exact byte-bound OFFICIAL candidate required")
    study.need(study.hash_file(candidate_path) == recipe["candidate_sha256"] and Path(candidate_path).stat().st_size == study.weights.FILE_BYTES,
               "Actual candidate bytes differ")
    study.need(parity["status"] == "OFFICIAL_NATIVE_PARITY_PASSED" and parity["rows"] == 586
               and parity["network_sha256"] == recipe["candidate_sha256"] and parity["source_commit"] == study.SOURCE
               and parity["source_tree"] == study.TREE and parity["engine_sha256"] == study.ENGINE
               and parity["cache_manifest"] == study.reference(Path(cache_dir) / "CACHE-MANIFEST.json"),
               "Edited current-official TRAIN native parity required before heldout access")
    return frozen, mapping, recipe


def validate(*, original_network_path, cache_dir, freeze_path, recipe_path, candidate_path,
             edited_train_parity_path, validation_path, output_dir, native_parity_callback):
    # All model/freeze/parity gates execute before opening validation_path.
    frozen, mapping, recipe = gate(cache_dir, freeze_path, recipe_path, candidate_path, edited_train_parity_path)
    study.need(callable(native_parity_callback), "Root-owned heldout native oracle callback required")
    plan = study.policy(); raw = Path(validation_path).read_bytes()
    study.need(len(raw) == plan["data"]["validation_bytes"] and __import__("hashlib").sha256(raw).hexdigest() == plan["data"]["validation_sha256"],
               "One fixed heldout input only")
    bundle = json.loads(raw)
    study.need(bundle["partition"] == "validation" and len(bundle["rows"]) == 68
               and frozen["component_partition_contract_sha256"] == study.partition_contract_digest()
               and all(study.component_binding(row) for row in bundle["rows"]),
               "Fixed68-root/component heldout partition required")
    children, parents = study.child_plan(bundle)
    model_raw = Path(original_network_path).read_bytes()
    study.need(__import__("hashlib").sha256(model_raw).hexdigest() == study.weights.PARENT_SHA256, "Original D1 changed")
    builder = study.forward.CacheBuilder(study.nnue_v19.Network(model_raw, expected_sha256=study.weights.PARENT_SHA256))
    records = []
    for child in children:
        row = builder.row(child)
        records.append({**row.record(), "parent_position_id": child["parent_position_id"], "rank": child["rank"]})
    edits = {edit["head"]: edit for edit in frozen["edits"]}
    # Callback contract includes actual source/engine/network/input identities;
    # it must reject any mismatch, partial stdout, error or missing child.
    baseline = native_parity_callback(records=records, network_path=original_network_path, network_sha256=study.weights.PARENT_SHA256,
                                      edits=(), source_commit=study.SOURCE, engine_sha256=study.ENGINE)
    candidate = native_parity_callback(records=records, network_path=candidate_path, network_sha256=recipe["candidate_sha256"],
                                       edits=frozen["edits"], source_commit=study.SOURCE, engine_sha256=study.ENGINE)
    study.need(baseline["all_rows_exact"] is candidate["all_rows_exact"] is True
               and baseline["rows"] == candidate["rows"] == len(records), "Heldout native structural parity failed; no proxy loss admitted")
    by_index = {r["source_root_index"]: r for r in records}
    results, original_losses, edited_losses = [], [], []
    for parent in parents:
        if parent["reasons"]:
            results.append({**parent, "status": "unavailable"}); continue
        a, b = [by_index[index] for index in parent["source_root_indices"]]
        original_margin = -study.score(a)[1] + study.score(b)[1]
        edited_margin = -study.score(a, edits.get(a["head"]))[1] + study.score(b, edits.get(b["head"]))[1]
        original_loss = study.huber20(max(0, min(60, parent["teacher_margin_cp"]) - original_margin))
        edited_loss = study.huber20(max(0, min(60, parent["teacher_margin_cp"]) - edited_margin))
        original_losses.append((parent["component_id"], original_loss)); edited_losses.append((parent["component_id"], edited_loss))
        results.append({**parent, "status": "matched", "original_margin_cp": original_margin, "edited_margin_cp": edited_margin,
                        "cross_head": a["head"] != b["head"], "touches_changed_head": a["head"] in edits or b["head"] in edits,
                        "original_loss": study.fraction(original_loss), "edited_loss": study.fraction(edited_loss)})
    result = {"schema": "official-d1-heldout-proxy-report/1", "freeze_sha256": frozen["freeze_sha256"], "candidate_sha256": recipe["candidate_sha256"],
              "root_denominator": 68, "matched_roots": len(original_losses), "unavailable_roots": 68 - len(original_losses), "rows": results,
              "original_component_mean": study.fraction(study.component_mean(original_losses)) if original_losses else None,
              "edited_component_mean": study.fraction(study.component_mean(edited_losses)) if edited_losses else None,
              "changed_and_unchanged_heads_and_cross_head_pairs_retained": True, "no_validation_reselection_or_fallback": True, "verified_gain": False}
    output = Path(output_dir); output.mkdir(exist_ok=False); study.put(output / "VALIDATION.json", result); return result
