"""Hosted Windows BMI2 PGO build and evidence collection for the pinned history-floor source."""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sys

from capture_common import BUILD_ARGS, ERROR, NET_MESSAGE, capture, commands_for, file_hash, parse_bench, require, write_json
from embedded_reference import NETWORK, REFERENCE, install_reference_network, verify_finished_embedding
from native_eval import EVALFILE, NETWORK_NAME, PROBES_SHA, capture_tables, compare_tables, verify_reference_capture
from source_evidence import HERE, archive_source, check_source, git, read_pin

OUTPUT = Path("artifact")
SOURCE = Path("source")
REFERENCE_DIR = Path("reference")
EXE_NAME = "stockfish-windows-x86-64-bmi2.exe"
PGO_WRAPPER_SHA = "fba218e437ca9b6e017fc78e81d81a966777270b0f6495c81b300e0d42fb7e11"
MAKEFILE_SHA = "183332fb8c9c6d9d5f9caff4513e6f88f511086066229ba9ab4cd0852255113e"


def prepare(driver_commit, pin):
    OUTPUT.mkdir()
    source_delta = check_source(pin, driver_commit)
    require(file_hash(HERE / "probes-v1.json") == PROBES_SHA and file_hash(HERE / "pgo_capture_v1.py") == PGO_WRAPPER_SHA,
            "Reviewed probe roster or PGO capture wrapper changed")
    archive = archive_source(OUTPUT)
    for name in ("AUTHORS", "Copying.txt"):
        shutil.copyfile(SOURCE / name, OUTPUT / name)
    for name in ("probes-v1.json", "source-pin.json"):
        shutil.copyfile(HERE / name, OUTPUT / name)
    write_json(OUTPUT / "SOURCE.json", {"source": pin, "driver_commit": driver_commit, "comparison": source_delta,
               "corresponding_source": archive, "gpl_sha256": file_hash(OUTPUT / "Copying.txt"),
               "authors_sha256": file_hash(OUTPUT / "AUTHORS")})


def build_profile(source_dir, output):
    makefile = source_dir / "Makefile"
    require(file_hash(makefile) == MAKEFILE_SHA and "PGOBENCH = $(RUN_PREFIX) ./$(EXE) bench" in makefile.read_text(),
            "Reviewed default profile recipe changed")
    require(not os.environ.get("RUN_PREFIX"), "Unexpected PGO execution prefix")
    capture(["g++", "--version"], source_dir, output, "compiler-version")
    capture(["g++", "-v"], source_dir, output, "compiler-identity")
    wrapper = " ".join(shlex.quote(arg) for arg in (Path(sys.executable).as_posix(),
                       (HERE / "pgo_capture_v1.py").as_posix(), "--output", output.resolve().as_posix()))
    capture(BUILD_ARGS + ["PGOBENCH=" + wrapper], source_dir, output, "profile-build", timeout=1800)
    record = json.loads((output / "pgo-native.record.json").read_bytes())
    require(record["returncode"] == 0 and not record["timed_out"] and
            record["instrumented_sha256_before"] == record["instrumented_sha256_after"], "Instrumented bench failed")
    for key in ("stdout", "stderr"):
        log = output / f"pgo-native.{key}.txt"
        require(record["logs"][key] == {"path": log.name, "sha256": file_hash(log)}, "Instrumented pipe binding differs")
    stdout = (output / "pgo-native.stdout.txt").read_text(encoding="utf-8")
    stderr = (output / "pgo-native.stderr.txt").read_text(encoding="utf-8")
    require(not ERROR.search(stdout + "\n" + stderr) and set(NET_MESSAGE.findall(stdout)) == {NETWORK_NAME},
            "Instrumented bench engine or default network differs")
    bench = parse_bench(stdout, stderr)
    require(bench["positions"] == 51, "PGO benchmark position roster differs")
    return bench


def ordinary_benches(executable, directory):
    benches = []
    for repetition in (1, 2):
        stdout, stderr = capture([str(executable)], directory, OUTPUT, f"candidate-bench-{repetition}",
                                 commands_for(EVALFILE, "bench"))
        require(not ERROR.search(stdout + "\n" + stderr) and set(NET_MESSAGE.findall(stdout)) == {EVALFILE},
                "Canonical bench error or unexpected network")
        bench = parse_bench(stdout, stderr)
        require(bench["positions"] == 51, "Canonical benchmark position roster differs")
        benches.append(bench)
    require(benches[0]["per_position_nodes"] == benches[1]["per_position_nodes"] and
            benches[0]["nodes"] == benches[1]["nodes"], "Candidate canonical search signatures did not repeat")
    return benches


def run(driver_commit, pin):
    summary = {"schema": "test1757-history-floor-native-v1", "status": "incomplete",
               "driver_commit": driver_commit, "source": pin, "strength_claim": False}
    try:
        prepared = json.loads((OUTPUT / "SOURCE.json").read_bytes())
        require(prepared["source"] == pin and prepared["driver_commit"] == driver_commit and
                prepared["comparison"] == check_source(pin, driver_commit) and
                file_hash(OUTPUT / "corresponding-source.tar") == prepared["corresponding_source"]["sha256"],
                "Packaged source differs from current inputs")
        require(all(file_hash(Path("driver") / name) == digest for name, digest in
                    prepared["corresponding_source"]["driver_files"].items()), "Prepared driver changed")
        source_dir = SOURCE / "src"
        require('#define EvalFileDefaultName "' + NETWORK_NAME + '"' in (source_dir / "evaluate.h").read_text(),
                "Pinned original network is not the source default")
        reference = REFERENCE_DIR / EXE_NAME
        net = source_dir / NETWORK_NAME
        extracted = install_reference_network(reference, net)
        require(net.stat().st_size == NETWORK.size and file_hash(net) == NETWORK.sha256, "Installed network differs")
        summary["original_network"] = extracted
        summary["reference"] = {"repository": "0mis/Stockfish", "run_id": 42543250921, "artifact_id": 13642730885,
                                "artifact_name": "test1757-prefetch-windows-bmi2-49672d7b3c2b0cb544f4bf7cc70515249fe047e3",
                                "member": EXE_NAME, "bytes": REFERENCE.size, "sha256": REFERENCE.sha256}
        probes = json.loads((HERE / "probes-v1.json").read_bytes())
        require(len(probes["rows"]) == 22 and probes["buckets_per_eval"] == 92 and
                probes["arms"]["donor"]["external_evalfile"]["sha256"] == NETWORK.sha256 and
                probes["arms"]["donor"]["external_evalfile"]["path"] == EVALFILE, "Public probe identity differs")
        reference_tables = verify_reference_capture(REFERENCE_DIR, OUTPUT, probes["rows"])
        summary["pgo"] = build_profile(source_dir, OUTPUT)
        executable = (OUTPUT / EXE_NAME).resolve()
        shutil.copyfile(source_dir / "stockfish.exe", executable)
        executable_sha = file_hash(executable)
        summary["engine"] = {"path": EXE_NAME, "bytes": executable.stat().st_size, "sha256": executable_sha,
                             "embedded_original_network": verify_finished_embedding(executable)}
        compiler, compiler_err = capture([str(executable)], source_dir, OUTPUT, "engine-compiler", "uci\ncompiler\nisready\nquit\n")
        require("uciok" in compiler.splitlines() and "readyok" in compiler.splitlines() and
                not ERROR.search(compiler + "\n" + compiler_err), "Compiler capture protocol failed")
        summary["canonical_benches"] = ordinary_benches(executable, source_dir)
        perft, errors = capture([str(executable)], source_dir, OUTPUT, "candidate-perft", commands_for(EVALFILE, "perft"))
        require(not ERROR.search(perft + "\n" + errors) and re.findall(r"^Nodes searched\s*:\s*(\d+)\s*$", perft, re.M) == ["197281"],
                "Start-position perft4 differs")
        summary["perft4_nodes"] = 197281
        stdout, stderr = capture([str(executable)], source_dir, OUTPUT, "candidate-eval", commands_for(EVALFILE, "eval", probes["rows"]))
        native = compare_tables(capture_tables(stdout, stderr, probes["rows"]), reference_tables)
        write_json(OUTPUT / "NATIVE-EVAL.json", native)
        summary["static_eval"] = {"file": "NATIVE-EVAL.json", "sha256": file_hash(OUTPUT / "NATIVE-EVAL.json"),
                                  "integers": native["probe_count"], "bucket_values": native["displayed_buckets_compared"]}
        require(file_hash(executable) == executable_sha and file_hash(net) == NETWORK.sha256 and
                file_hash(reference) == REFERENCE.sha256, "An executable or the original NNUE changed during capture")
        git(SOURCE, "diff", "--exit-code", "HEAD", "--")
        summary["status"] = "native_evidence_captured_pending_independent_review"
    except Exception as exc:
        summary["error"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        write_json(OUTPUT / "SUMMARY.json", summary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("pin").add_argument("--github-output", type=Path, required=True)
    for action in ("prepare", "build"):
        actions.add_parser(action).add_argument("--driver-commit", required=True)
    args = parser.parse_args()
    pin = read_pin(HERE / "source-pin.json")
    if args.action == "pin":
        with args.github_output.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(f"source_commit={pin['source_commit']}\nsource_tree={pin['source_tree']}\n")
    elif args.action == "prepare":
        prepare(args.driver_commit, pin)
    else:
        run(args.driver_commit, pin)


if __name__ == "__main__":
    main()
