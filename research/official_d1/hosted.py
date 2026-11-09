"""Future official-release parity/TRAIN-fit CI. Refuses local native execution."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import time

import study
import release_inputs

HERE = Path(__file__).resolve().parent
OUT = Path("official-d1-evidence").resolve()


def capture(argv, cwd, stem, text):
    record = {"argv": list(map(str, argv)), "cwd": str(cwd), "returncode": None, "timed_out": False}
    stdin, stdout, stderr = (OUT / (stem + suffix) for suffix in (".uci", ".stdout.txt", ".stderr.txt"))
    with stdin.open("xb") as stream: stream.write(text.encode("utf-8"))
    record["commands"] = study.reference(stdin); started = time.monotonic()
    try:
        with stdout.open("xb") as out, stderr.open("xb") as err:
            done = subprocess.run(list(map(str, argv)), cwd=cwd, input=text.encode("utf-8"), stdout=out, stderr=err, timeout=600, check=False)
        record["returncode"] = done.returncode
        study.need(done.returncode == 0, "Official native process failed")
    except BaseException as exc:
        record["timed_out"] = isinstance(exc, subprocess.TimeoutExpired); record["error"] = str(exc); raise
    finally:
        record["elapsed_seconds"] = time.monotonic() - started
        record["stdout"], record["stderr"] = study.reference(stdout), study.reference(stderr)
        study.put(OUT / (stem + ".record.json"), record)
    return stdout.read_bytes(), stderr.read_bytes(), record


def parity(executable, network, cache, rows, stem, edits=()):
    expected_network = study.hash_file(network)
    study.need(study.hash_file(executable) == study.ENGINE, "Wrong actual official7cdd oracle")
    request = study.parity_request(rows, network, expected_network, edits)
    study.put(OUT / (stem + ".request.json"), request)
    stdout, stderr, record = capture([executable], executable.parent, stem, request["stdin"])
    study.need(study.hash_file(executable) == study.ENGINE and study.hash_file(network) == expected_network, "Native inputs changed")
    native = {"source_commit": study.SOURCE, "source_tree": study.TREE, "engine_sha256": study.ENGINE,
              "network_sha256": expected_network, "stdin_sha256": request["stdin_sha256"], "process_record": study.reference(OUT / (stem + ".record.json")),
              "inputs_rehashed_before_after": True}
    return study.accept_parity(cache, request, stdout, stderr, record["returncode"], native, OUT / (stem + ".accepted.json"))


def run(driver_commit):
    study.need(os.environ.get("GITHUB_ACTIONS") == "true" and sys.platform == "win32", "Native oracle runs only in the explicitly dispatched Windows CI")
    study.need(not OUT.exists(), "Preserve prior job output")
    OUT.mkdir(); summary = {"schema": "official-d1-sibling-hosted-fit/1", "source_commit": study.SOURCE,
                           "source_tree": study.TREE, "driver_commit": driver_commit, "status": "running", "validation_read": False, "verified_gain": False}
    try:
        study.policy()
        # Bind the exact original official source checkout independently of the
        # older Python feature implementation before using the release oracle.
        manifest = json.loads((HERE / "official-source-manifest.json").read_bytes())
        source = Path("source").resolve()
        head = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
        tree = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD:"], text=True).strip()
        actual_driver = subprocess.check_output(["git", "-C", "driver", "rev-parse", "HEAD"], text=True).strip()
        study.need(head == study.SOURCE and tree == study.TREE and actual_driver == driver_commit, "Exact official source/driver checkout required")
        study.put(OUT / "SOURCE-IDENTITY.json", {"source_commit": head, "source_tree": tree, "driver_commit": actual_driver,
                                               "source_manifest": study.reference(HERE / "official-source-manifest.json"), "plan_sha256": study.PLAN_SHA})
        for name, ref in manifest["files"].items():
            body = (source / name).read_bytes()
            study.need(len(body) == ref["bytes"] and hashlib.sha256(body).hexdigest() == ref["sha256"], "Official source bytes changed: " + name)
        with tarfile.open(OUT / "corresponding-source.tar", "w") as archive:
            for name in sorted(manifest["files"]): archive.add(source / name, arcname="official-source/" + name, recursive=False)
            for path in HERE.rglob("*.py"): archive.add(path, arcname="python-driver/" + path.relative_to(HERE).as_posix(), recursive=False)
        for name in ("AUTHORS", "Copying.txt"): shutil.copyfile(source / name, OUT / name)
        executable, original, identity = release_inputs.prepare(Path("official-d1-inputs").resolve())
        study.put(OUT / "INPUTS.json", identity)
        controls = "\n".join(["uci", "setoption name Threads value 1", "isready", "position startpos", "go perft 4", "isready", "quit", ""])
        stdout, stderr, _ = capture([executable], executable.parent, "official-perft4", controls)
        study.need(not stderr and b"Nodes searched: 197281" in stdout, "Actual official perft4 differs")
        cache = OUT / "train-cache"
        study.cache_train(original, HERE / "train.json", cache)
        _, mapping, rows = study.load_cache(cache)
        parity(executable, original, cache, rows, "original-training-parity")
        # No costs/choices are computed until every original row passes above.
        fit = OUT / "training-fit"
        frozen = study.fit_train(cache, OUT / "original-training-parity.accepted.json", fit)
        summary["joint_freeze"] = study.reference(fit / "FROZEN.json")
        if frozen["identity"]:
            summary.update(status="identity_selected_no_candidate", original_all586_parity=True, identity=True)
        else:
            candidate = OUT / "candidate.nnue"
            recipe = study.apply_candidate(original, cache, fit / "FROZEN.json", candidate)
            parity(executable, candidate, cache, rows, "edited-training-parity", frozen["edits"])
            summary.update(status="nonidentity_frozen_bound_and_train_parity_passed_pending_independent_review", identity=False,
                           candidate=study.reference(candidate), recipe=study.reference(candidate.with_suffix(".recipe.json")),
                           original_all586_parity=True, edited_all586_parity=True)
        summary["engine"] = {"bytes": release_inputs.EXE_BYTES, "sha256": study.ENGINE}
    except BaseException as exc:
        summary.update(status="failed_hold_no_reselection", failure={"type": type(exc).__name__, "message": str(exc)})
        raise
    finally:
        study.put(OUT / "SUMMARY.json", summary)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(); cli.add_argument("--driver-commit", required=True)
    run(cli.parse_args().driver_commit)
