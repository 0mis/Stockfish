"""Explicit Root-dispatched Windows-only official D1 heldout/qualification job."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
import stat
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from zipfile import ZipFile

import verify as v
import release_inputs

HERE = Path(__file__).resolve().parent
OUT = Path("official-d1-heldout-evidence").resolve()


def put(path, value):
    with Path(path).open("xb") as stream:
        stream.write(v.canonical(value) + b"\n"); stream.flush(); os.fsync(stream.fileno())
    return v.study.reference(path)


def checked_file(path, ref):
    v.need(Path(path).stat().st_size == ref["bytes"] and v.study.hash_file(path) == ref["sha256"], "Accepted artifact file differs")


def acceptance_gate(accepted, root):
    v.need(accepted["schema"] == "official-d1-sibling-train-ci-acceptance/1"
           and accepted["status"] == "OFFICIAL_D1_TRAIN_CI_ACCEPTED" and accepted["run_id"] == v.RUN,
           "Independent official training acceptance required; producer CI success is insufficient")
    v.need(accepted["source_commit"] == v.study.SOURCE and accepted["source_tree"] == v.study.TREE
           and accepted["engine_sha256"] == v.study.ENGINE and accepted["validation_read_or_predictions"] is False,
           "Independent accepted source/validation dispositions differ")
    v.need(accepted["identity"] is root.get("identity", False), "Root/independent identity dispositions differ")
    if not accepted["identity"]:
        v.need(accepted["biases_and_every_other_byte_unchanged"] is True
               and accepted["inverse_restore_sha256"] == v.study.weights.PARENT_SHA256
               and accepted["candidate"]["sha256"] == root["files"]["candidate"]["sha256"]
               and accepted["candidate"]["bytes"] == v.study.weights.FILE_BYTES, "Root/independently accepted candidate differs")


def native(executable, network, stem, commands):
    v.need(os.environ.get("GITHUB_ACTIONS") == "true" and sys.platform == "win32", "No local native execution")
    before = {"engine": v.study.reference(executable), "network": v.study.reference(network)}
    v.need(before["engine"]["sha256"] == v.study.ENGINE, "Exact official b67 executable required")
    stdin, stdout, stderr = (OUT / (stem + suffix) for suffix in (".uci", ".stdout.txt", ".stderr.txt"))
    with stdin.open("xb") as stream: stream.write(commands.encode())
    receipt = {"source_commit": v.study.SOURCE, "source_tree": v.study.TREE, "inputs_before": before,
               "commands": v.study.reference(stdin), "returncode": None, "timeout": False}
    started = time.monotonic()
    try:
        with stdout.open("xb") as out, stderr.open("xb") as err:
            result = subprocess.run([str(executable)], cwd=executable.parent, input=commands.encode(), stdout=out, stderr=err, timeout=600)
        receipt["returncode"] = result.returncode
    except BaseException as exc:
        receipt.update(timeout=isinstance(exc, subprocess.TimeoutExpired), error=str(exc)); raise
    finally:
        receipt.update(elapsed_seconds=time.monotonic() - started, stdout=v.study.reference(stdout), stderr=v.study.reference(stderr),
                       inputs_after={"engine": v.study.reference(executable), "network": v.study.reference(network)})
        put(OUT / (stem + ".record.json"), receipt)
    v.need(receipt["inputs_after"] == before and receipt["returncode"] == 0, "Native input mutation/failure")
    return stdout.read_bytes(), stderr.read_bytes(), receipt["returncode"], receipt


def setup(network):
    return ["uci", "setoption name NumaPolicy value none", "setoption name Threads value 1", "setoption name Hash value 16",
            "setoption name MultiPV value 1", "setoption name UCI_ShowWDL value false", "setoption name UCI_Chess960 value false",
            "setoption name UCI_LimitStrength value false", "setoption name Ponder value false", "setoption name SyzygyPath value <empty>",
            "setoption name SyzygyProbeLimit value 0", "setoption name EvalFile value " + str(network), "isready"]


def qualification(executable, network, prefix):
    benches = []
    for repetition in (1, 2):
        text = "\n".join(setup(network) + ["bench 16 1 13 default depth", "isready", "quit", ""])
        stdout, stderr, code, receipt = native(executable, network, f"{prefix}-bench-{repetition}", text)
        bench = v.parse_bench(stdout, stderr, code)
        bench["native_record"] = v.study.reference(OUT / f"{prefix}-bench-{repetition}.record.json")
        put(OUT / f"{prefix}-bench-{repetition}.parsed.json", bench); benches.append(bench)
    v.need(benches[0]["vector"] == benches[1]["vector"] and benches[0]["nodes"] == benches[1]["nodes"]
           and benches[0]["search_signature_sha256"] == benches[1]["search_signature_sha256"], "Complete canonical bench signatures must repeat")
    text = "\n".join(setup(network) + ["position startpos", "go perft 4", "isready", "quit", ""])
    stdout, stderr, code, receipt = native(executable, network, prefix + "-perft4", text)
    perft = v.parse_perft(stdout, stderr, code); put(OUT / (prefix + "-perft4.parsed.json"), perft)
    return {"network_sha256": v.study.hash_file(network), "bench_nodes": [b["nodes"] for b in benches],
            "repeated_full_search_signatures": [b["search_signature_sha256"] for b in benches], "bench_positions_each": 51,
            "perft4": perft, "structural_playing_qualification": True, "speed_or_strength_gate": False}


def gh_json(path):
    result = subprocess.run(["gh", "api", "--method", "GET", path], capture_output=True, timeout=90)
    v.need(result.returncode == 0, "Read-only accepted artifact metadata failed")
    return json.loads(result.stdout)


def fetch_training(root):
    run = gh_json(f"repos/0mis/Stockfish/actions/runs/{v.RUN}")
    artifact = gh_json(f"repos/0mis/Stockfish/actions/artifacts/{v.ARTIFACT}")
    v.need(run["status"] == "completed" and run["conclusion"] == "success" and run["head_sha"] == root["training_driver_commit"]
           and artifact["id"] == v.ARTIFACT and artifact["workflow_run"]["id"] == v.RUN
           and artifact["workflow_run"]["head_sha"] == root["training_driver_commit"]
           and artifact["digest"] == "sha256:" + root["training_artifact"]["sha256"] and not artifact["expired"], "Accepted training CI identity differs")
    archive = OUT / "accepted-training.zip"
    with archive.open("xb") as stream:
        done = subprocess.run(["gh", "api", "--method", "GET", f"repos/0mis/Stockfish/actions/artifacts/{v.ARTIFACT}/zip"], stdout=stream, stderr=subprocess.PIPE, timeout=600)
    v.need(done.returncode == 0, "Accepted training artifact download failed")
    checked_file(archive, root["training_artifact"])
    target = OUT / "accepted-training"; target.mkdir(exist_ok=False)
    with ZipFile(archive) as zipped:
        infos = zipped.infolist(); seen = set()
        v.need(sum(info.file_size for info in infos) <= 400 * 1024**2, "Bounded training artifact required")
        for info in infos:
            posix, windows = PurePosixPath(info.filename), PureWindowsPath(info.filename)
            v.need(not posix.is_absolute() and not windows.drive and ".." not in posix.parts and "\\" not in info.filename and ":" not in info.filename
                   and info.filename.casefold() not in seen and not info.flag_bits & 1
                   and stat.S_IFMT(info.external_attr >> 16) in (0, stat.S_IFREG, stat.S_IFDIR), "Unsafe accepted ZIP path/type")
            seen.add(info.filename.casefold())
            if info.is_dir(): continue
            path = target / info.filename; path.parent.mkdir(parents=True, exist_ok=True)
            with zipped.open(info) as source, path.open("xb") as output:
                shutil.copyfileobj(source, output, 1024 * 1024)
    for ref in root["files"].values(): checked_file(target / ref["member"], ref)
    return target


def fetch_validation(url, root):
    parsed = urllib.parse.urlparse(url)
    v.need(parsed.scheme == "https" and parsed.netloc == "raw.githubusercontent.com"
           and __import__("re").fullmatch(r"/0mis/(?:Stockfish|stockfish-weight-space-lab)/[0-9a-f]{40}/.+", parsed.path),
           "An immutable0mis raw GitHub URL is required for the fixed heldout file")
    request = urllib.request.Request(url, headers={"User-Agent": "0mis-official-d1-fixed-heldout"})
    with urllib.request.urlopen(request, timeout=90) as stream: raw = stream.read(root["validation_bytes"] + 1)
    v.need(len(raw) == 162650 and v.sha(raw) == v.VALIDATION_SHA, "Fixed heldout download bytes differ")
    with (OUT / "validation-input.json").open("xb") as stream: stream.write(raw)
    return raw


def run(*, root_sha, validation_url):
    # This guard precedes any actual acceptance, model or heldout file open.
    v.need(os.environ.get("GITHUB_ACTIONS") == "true" and sys.platform == "win32"
           and os.environ.get("GITHUB_REPOSITORY") == "0mis/Stockfish" and os.environ.get("GITHUB_ACTOR") == "0mis",
           "Only an explicitly Root-dispatched Windows job may execute this verifier")
    v.source_gate()
    root_raw = (HERE / "ROOT-ACCEPTANCE.json").read_bytes()
    root = v.root_gate(root_raw, root_sha)
    v.need(not OUT.exists(), "Preserve previous heldout evidence")
    OUT.mkdir()
    summary = {"schema": "official-d1-heldout-hosted-result/1", "status": "gates_running", "validation_opened": False,
               "source_commit": v.study.SOURCE, "engine_sha256": v.study.ENGINE, "root_acceptance_sha256": root_sha,
               "no_performance_gate": True, "no_training_or_reselection": True, "verified_gain": False}
    try:
        acceptance_path = HERE / "inputs/TRAIN-ACCEPTANCE.json"
        checked_file(acceptance_path, root["training_acceptance"])
        accepted = json.loads(acceptance_path.read_bytes())
        acceptance_gate(accepted, root)
        v.need(root["training_acceptance_assertions"], "Exact accepted-status assertions required")
        for assertion in root["training_acceptance_assertions"]:
            value = accepted
            for key in assertion["path"]: value = value[key]
            v.need(value == assertion["equals"], "Independent training acceptance assertion differs")
        training = fetch_training(root)
        cache = training / root["files"]["cache_manifest"]["member"]
        cache = cache.parent
        frozen_path = training / root["files"]["frozen"]["member"]
        frozen = json.loads(frozen_path.read_bytes())
        manifest, mapping, _ = v.study.load_cache(cache)
        v.study.verify_freeze(frozen, mapping)
        v.need(frozen["identity"] is accepted["identity"], "Frozen/independent identity dispositions differ")
        if frozen["identity"]:
            v.need(not frozen["edits"], "Identity has unexpected edits")
            summary.update(status="IDENTITY_STOPPED_BEFORE_HELDOUT", identity=True); return summary
        source = Path("source").resolve()
        v.need(subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip() == v.study.SOURCE
               and subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD:"], text=True).strip() == v.study.TREE,
               "Exact official source checkout required")
        source_manifest = json.loads((v.OFFICIAL / "official-source-manifest.json").read_bytes())
        for name, ref in source_manifest["files"].items(): checked_file(source / name, ref)
        for name in ("AUTHORS", "Copying.txt"): shutil.copyfile(source / name, OUT / name)
        executable, original, release_identity = release_inputs.prepare(Path("official-heldout-inputs").resolve())
        candidate_path = training / root["files"]["candidate"]["member"]
        recipe_path = training / root["files"]["recipe"]["member"]
        edited_parity = training / root["files"]["edited_train_parity"]["member"]
        recipe = json.loads(recipe_path.read_bytes())
        original_parity_path = training / root["files"]["original_train_parity"]["member"]
        v.need(frozen["parent_parity"] == v.study.reference(original_parity_path), "Frozen original-training parity reference differs")
        for path, net_sha in ((original_parity_path, v.study.weights.PARENT_SHA256), (edited_parity, recipe["candidate_sha256"])):
            receipt = json.loads(path.read_bytes())
            v.need(receipt["schema"] == "official-d1-train-native-parity/1" and receipt["status"] == "OFFICIAL_NATIVE_PARITY_PASSED"
                   and receipt["rows"] == 586 and receipt["source_commit"] == v.study.SOURCE and receipt["source_tree"] == v.study.TREE
                   and receipt["engine_sha256"] == v.study.ENGINE and receipt["network_sha256"] == net_sha
                   and receipt["cache_manifest"] == v.study.reference(cache / "CACHE-MANIFEST.json"), "Complete original/edited accepted TRAIN parity required")
        byte_proof = v.model_gate(original_path=original, candidate_path=candidate_path, frozen=frozen, recipe=recipe, mapping=mapping)
        # Reuse the source design's full586 edited-training gate, with its
        # actual current-official b67 receipt, before heldout data is fetched.
        v.heldout_design.gate(cache, frozen_path, recipe_path, candidate_path, edited_parity)
        put(OUT / "BEFORE-HELDOUT-GATE.json", {"root_acceptance_sha256": root_sha, "frozen": v.study.reference(frozen_path),
             "recipe": v.study.reference(recipe_path), "model_bytes": byte_proof, "edited_training_parity": v.study.reference(edited_parity),
             "source_plan_sha256": v.PLAN, "release": release_identity, "heldout_opened": False})
        raw = fetch_validation(validation_url, root); summary["validation_opened"] = True
        bundle = json.loads(raw)
        v.need(frozen["component_partition_contract_sha256"] == v.study.partition_contract_digest(), "Frozen component contract changed")
        children, parents, dispositions = v.child_plan(bundle, manifest["parents"])
        original_raw = original.read_bytes()
        v.need(v.sha(original_raw) == v.study.weights.PARENT_SHA256, "Original complete D1 changed")
        builder = v.study.forward.CacheBuilder(v.study.nnue_v19.Network(original_raw, expected_sha256=v.study.weights.PARENT_SHA256))
        records = [{**builder.row(child).record(), "parent_position_id": child["parent_position_id"], "rank": child["rank"]} for child in children]
        put(OUT / "VALIDATION-CACHE.json", {"records": records, "parents": parents, "child_dispositions": dispositions})
        parities = []
        for name, network, network_sha, edits in (("original", original, v.study.weights.PARENT_SHA256, ()),
                                                  ("candidate", candidate_path, recipe["candidate_sha256"], frozen["edits"])):
            request = v.parity_request(records, network, network_sha, edits)
            put(OUT / (name + "-heldout.request.json"), request)
            stdout, stderr, code, receipt = native(executable, network, name + "-heldout", request["stdin"])
            parity = v.accept_parity(request, stdout, stderr, code, records)
            put(OUT / (name + "-heldout.accepted.json"), parity); parities.append(parity)
        # No Huber loss or improvement decision occurs before both exact raw/CP
        # comparisons above have succeeded for every measurable heldout child.
        report = v.loss_report(parents, records, parities[0], parities[1], frozen["edits"], dispositions)
        put(OUT / "VALIDATION-REPORT.json", report)
        qualified = {"original": qualification(executable, original, "original"), "candidate": qualification(executable, candidate_path, "candidate")}
        put(OUT / "PLAYING-QUALIFICATION.json", qualified)
        summary.update(status="HELDOUT_NUMERIC_AND_PLAYING_QUALIFICATION_PASSED_NO_STRENGTH_DECISION", identity=False,
                       candidate_sha256=recipe["candidate_sha256"], frozen_sha256=frozen["freeze_sha256"], root_denominator=68,
                       matched_roots=report["matched_roots"], unavailable_roots=report["unavailable_roots"], measured_children=len(records),
                       validation_report=v.study.reference(OUT / "VALIDATION-REPORT.json"), playing_qualification=v.study.reference(OUT / "PLAYING-QUALIFICATION.json"))
        return summary
    except BaseException as exc:
        summary.update(status="FAILED_PRESERVED_NO_RESELECTION", failure={"type": type(exc).__name__, "message": str(exc)}); raise
    finally:
        put(OUT / "SUMMARY.json", summary)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--root-acceptance-sha256", required=True); parser.add_argument("--validation-url", required=True)
    arguments = parser.parse_args()
    print(json.dumps(run(root_sha=arguments.root_acceptance_sha256, validation_url=arguments.validation_url), sort_keys=True), flush=True)
