"""Prospective Windows Actions qualification. Import starts no process.

This module is runner-only. It builds pristine and diagnostic copies, never a
production replacement, fitted network, optimizer, game, or external teacher.
"""
import argparse
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import time

import chess
import patch_source
import protocol as p
import reference_transport as transport

HERE = Path(__file__).resolve().parent
TREE = "8e3164caf60d281cb4b1d00a699364e04b354451"
OUT = Path("artifact-fc2-activations").resolve()
TRANSPORT_SHA = "beab24bbfff53e4f919451a1114e3900fd68eb99ee71bd618f09cda080a0c75d"


def sha(raw): return hashlib.sha256(raw).hexdigest()


def hash_file(path):
    with path.open("rb") as stream: return hashlib.file_digest(stream, "sha256").hexdigest()


def emit(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as out:
        json.dump(value, out, sort_keys=True, indent=2, allow_nan=False)
        out.write("\n")


def reference(path): return {"path": path.name, "bytes": path.stat().st_size, "sha256": hash_file(path)}


def capture(argv, cwd, stem, commands=None, timeout=900):
    stdout, stderr = (OUT / (stem + suffix) for suffix in (".stdout.txt", ".stderr.txt"))
    record = {"argv": list(map(str, argv)), "cwd": str(cwd), "returncode": None, "timed_out": False}
    started = time.monotonic()
    if commands is not None:
        control = OUT / (stem + ".uci")
        with control.open("xb") as out: out.write(commands.encode("ascii"))
        record["commands"] = reference(control)
    try:
        with stdout.open("xb") as out, stderr.open("xb") as err:
            result = subprocess.run(list(map(str, argv)), cwd=cwd, input=commands.encode("ascii") if commands is not None else None,
                                    stdin=subprocess.DEVNULL if commands is None else None, stdout=out, stderr=err,
                                    timeout=timeout, check=False)
        record["returncode"] = result.returncode
        p.need(result.returncode == 0, "Hosted command failed: " + stem)
    except BaseException as exc:
        record["timed_out"] = isinstance(exc, subprocess.TimeoutExpired)
        record["error"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        record["elapsed_seconds"] = time.monotonic() - started
        record["logs"] = {"stdout": reference(stdout), "stderr": reference(stderr)}
        emit(OUT / (stem + ".record.json"), record)
    return stdout.read_text(encoding="utf-8-sig"), stderr.read_text(encoding="utf-8-sig")


def commands(network, tail):
    return "\n".join(["uci", "setoption name NumaPolicy value none", "setoption name Threads value 1", "setoption name Hash value 64",
                      "setoption name MultiPV value 1", "setoption name UCI_Chess960 value false", "setoption name UCI_LimitStrength value false",
                      "setoption name Ponder value false", "setoption name SyzygyPath value <empty>", "setoption name SyzygyProbeLimit value 0",
                      "setoption name EvalFile value " + str(network), "isready", *tail, "quit", ""])


def byte_map(network, mapping):
    heads = []
    for head in range(92):
        bias, weights = p.serialized_readout(network, head)
        bias_offset = p.NETWORK_BYTES - 92 * 35208 + head * 35208 + 35076
        heads.append({"physical_head": head, "bias_offset": bias_offset, "bias_before": bias,
                      "weight_offsets": list(range(bias_offset + 4, bias_offset + 132)), "weights": weights,
                      "serialized_to_native": [p.physical_slot(index) for index in range(128)]})
    return {"schema": "test1757-fc2-byte-map/1", "verified": False, "parent_sha256": p.NETWORK, "parent_bytes": p.NETWORK_BYTES,
            "stored_heads": 92, "head_width": 128, "raw_to_physical": mapping,
            "quantizer": {"numerator": 9600, "first_divisor": 16384, "second_divisor": 16, "rounding": "toward_zero", "intermediate_bits": 32},
            "heads92": heads, "source_commit": p.SOURCE, "source_tree": TREE}


def run(driver_commit, collect_train=False):
    p.need(sys.platform == "win32" and chess.__version__ == "1.11.2", "Use pinned Windows hosted environment")
    p.need(not OUT.exists(), "Preserve prior hosted evidence")
    OUT.mkdir()
    source = Path("source").resolve()
    driver = Path("driver").resolve()
    manifest = json.loads((HERE / "source-manifest.json").read_bytes())
    summary = {"schema": "test1757-fc2-hosted-qualification/1", "status": "running", "source_commit": p.SOURCE,
               "source_tree": TREE, "driver_commit": driver_commit, "native_qualification_passed": False,
               "training_or_weight_edit": False, "verified_gain": False}
    try:
        for stem, path, rev, expected in (("source-head", source, "HEAD", p.SOURCE), ("source-tree", source, "HEAD:", TREE),
                                           ("driver-head", driver, "HEAD", driver_commit)):
            out, err = capture(["git", "rev-parse", rev], path, stem)
            p.need(out.strip() == expected and not err.strip(), "Hosted source/driver Git identity differs")
        capture(["git", "diff", "--exit-code", "HEAD", "--"], source, "source-clean-before")
        for name, expected in manifest["source_files"].items():
            raw = (source / name).read_bytes()
            p.need(len(raw) == expected["bytes"] and sha(raw) == expected["sha256"], "Exact source bytes changed: " + name)
        for name, expected in manifest["changes"].items():
            p.need(hash_file(HERE / "overlay" / name) == expected["after_sha256"], "Reviewed overlay changed: " + name)
        p.need(hash_file(HERE / "fc2-activations.patch") == manifest["patch_sha256"], "Diagnostic patch changed")
        # Preserve corresponding source/licensing before the first compiler, so
        # even a failed later step never leaves a distributed executable alone.
        with tarfile.open(OUT / "corresponding-source.tar", "w") as archive:
            for name in sorted(manifest["source_files"]): archive.add(source / name, arcname="stockfish-source/" + name, recursive=False)
            for name in sorted(manifest["changes"]): archive.add(HERE / "overlay" / name, arcname="diagnostic-overlay/" + name, recursive=False)
            for path in sorted(HERE.glob("*.py")): archive.add(path, arcname="ci-driver/" + path.name, recursive=False)
        for name in ("AUTHORS", "Copying.txt"): shutil.copyfile(source / name, OUT / name)
        shutil.copyfile(HERE / "source-manifest.json", OUT / "source-manifest.json")
        # Reuse the already proven artifact transport. Do not fall back to the
        # public NNUE data host, which returned403 in earlier hosted CI.
        p.need(hash_file(HERE / "reference_transport.py") == TRANSPORT_SHA, "Reviewed embedded-network reader changed")
        netdir = Path("fc2-network").resolve()
        netdir.mkdir()
        received = Path("accepted-reference").resolve() / transport.EXE_MEMBER
        for path in (received.parent, received):
            details = path.lstat()
            p.need(not stat.S_ISLNK(details.st_mode) and not getattr(details, "st_file_attributes", 0) & 0x400,
                   "Accepted artifact contains a linked path")
        p.need(received.is_file() and received.stat().st_size == transport.REFERENCE.size, "Missing accepted BMI2 executable")
        with received.open("rb") as source_image, (netdir / transport.REFERENCE.name).open("xb") as staged:
            size, digest = transport.transfer(source_image, staged, transport.REFERENCE.size)
        transport.REFERENCE.verify(size, digest)
        network_receipt = transport.extract_network(netdir)
        network = netdir / transport.NETWORK.name
        p.need(network.stat().st_size == p.NETWORK_BYTES and hash_file(network) == p.NETWORK
               and network_receipt["offset_bytes"] == 1251840, "Extracted original18cd differs")
        emit(OUT / "INPUT-TRANSPORT.json", {"schema": "test1757-fc2-verified-reference-transport/1",
             "repository": "0mis/Stockfish", "run_id": 42543250921, "artifact_id": 13642730885,
             "artifact_name": transport.ARTIFACT_NAME, "executable_bytes": size, "executable_sha256": digest,
             "reader_sha256": TRANSPORT_SHA, "network": network_receipt, "reference_executed": False,
             "direct_NNUE_host_contacted": False})
        mapping = p.network_layout(network)
        output_map = byte_map(network, mapping)
        emit(OUT / "FC2-MAP-PROVISIONAL.json", output_map)
        builds = {}
        for role in ("pristine", "diagnostic"):
            build = Path("fc2-" + role).resolve()
            p.need(not build.exists(), "Existing isolated build directory")
            shutil.copytree(source, build, ignore=shutil.ignore_patterns(".git"))
            shutil.copyfile(network, build / "src/nn-18cd32e11a9c.nnue")
            if role == "diagnostic":
                for name in patch_source.TARGETS:
                    original = (build / name).read_bytes()
                    changed = patch_source.apply(name, original)
                    p.need(changed == (HERE / "overlay" / name).read_bytes(), "Independent patch materialization differs")
                    (build / name).write_bytes(changed)
                for name in patch_source.HEADERS:
                    shutil.copyfile(HERE / "overlay" / name, build / name)
            flags = "-DNNUE_EMBEDDING_OFF -mmove-max=128" + (" -D" + patch_source.MACRO if role == "diagnostic" else "")
            capture(["make", "-j2", "build", "ARCH=x86-64-bmi2", "COMP=mingw", "COMPCXX=g++", "EXTRACXXFLAGS=" + flags],
                    build / "src", role + "-build", timeout=1200)
            executable = build / "src/stockfish.exe"
            builds[role] = executable
            shutil.copyfile(executable, OUT / (role + "-stockfish.exe"))
            summary[role + "_executable"] = reference(OUT / (role + "-stockfish.exe"))
        capture(["g++", "--version"], source, "compiler-version")
        rows = json.loads((HERE / "smoke-rows.json").read_bytes())
        boards = []
        requests = []
        for index, row in enumerate(rows):
            board = chess.Board(row["fen"])
            for token in row["moves"]:
                move = board.parse_uci(token)
                p.need(move in board.legal_moves, "Invalid smoke replay prefix")
                board.push(move)
            p.need(board.is_valid() and any(board.legal_moves) and not board.is_check(), "Smoke parity roots must support ordinary eval")
            boards.append(board)
            requests.append("capture " + str(index) + " fen " + row["fen"] + (" moves " + " ".join(row["moves"]) if row["moves"] else ""))
        raw, errors = capture([builds["diagnostic"], "--fc2-activations-v1", "--network", network],
                              builds["diagnostic"].parent, "fc2-capture", "\n".join(requests + ["quit", ""]), timeout=180)
        p.need(not errors.strip(), "Unexpected diagnostic stderr")
        captured = [json.loads(line) for line in raw.splitlines()]
        p.need([row["row_id"] for row in captured] == list(range(len(rows))), "Missing/extra/reordered capture rows")
        for row, board in zip(captured, boards, strict=True):
            p.validate(row, mapping=mapping, readout=p.serialized_readout(network, row["physical_head"]), expected_board=board)
        eval_commands = commands(network, [token for board in boards for token in ("position fen " + board.fen(en_passant="legal"), "eval", "isready")])
        plain, errors = capture([builds["pristine"]], builds["pristine"].parent, "pristine-raw-eval", eval_commands, timeout=180)
        p.need(not errors.strip(), "Unexpected pristine stderr")
        ordinary_raw = list(map(int, re.findall(r"NNUE evaluation\s+([+-]?\d+)\s+\(side to move, internal units\)", plain)))
        ordinary_white_cp = [int(Decimal(value) * 100) for value in re.findall(r"Final evaluation\s+([+-]?\d+\.\d{2})\s+\(white side\)", plain)]
        p.need(len(ordinary_raw) == len(ordinary_white_cp) == len(captured), "Pristine ordinary eval omitted rows")
        for index, row in enumerate(captured):
            p.need(row["raw_nnue"] == ordinary_raw[index] and row["ordinary_static_cp"] == ordinary_white_cp[index] * (1 if boards[index].turn else -1),
                   "Pristine default raw/CP parity failed")
        signatures = {}
        for role, executable in builds.items():
            stdout, stderr = capture([executable], executable.parent, role + "-bench", commands(network, ["bench 16 1 13 default depth", "isready"]), timeout=240)
            signatures[role] = [re.sub(r"\b(?:nps|time) \d+", "", line) for line in stdout.splitlines()
                                if line.startswith(("info depth ", "info currmove ", "bestmove "))]
            p.need(re.findall(r"Nodes searched\s*:\s*(\d+)", stderr) == ["3464885"], "Declared original-source benchmark signature differs")
        p.need(signatures["pristine"] == signatures["diagnostic"], "Unused observer changed deterministic search output")
        p.need(hash_file(network) == p.NETWORK, "Original network changed during qualification")
        output_map["verified"] = True
        output_map["native_parameter_parity_smoke_heads"] = sorted({row["physical_head"] for row in captured})
        emit(OUT / "FC2-BYTE-MAP.json", output_map)
        if collect_train:
            from collect_training import collect
            summary["training_capture"] = collect(builds["diagnostic"], network, mapping, OUT, capture, emit)
        p.need(hash_file(network) == p.NETWORK, "Original network changed during diagnostics")
        summary.update(status="hosted_native_qualification_passed_pending_independent_review", native_qualification_passed=True,
                       capture_rows=len(captured), all_49_offsets_replayed=True, pristine_raw_and_cp_exact=True,
                       unused_observer_bench_exact=True, original_network_sha256=p.NETWORK,
                       byte_map=reference(OUT / "FC2-BYTE-MAP.json"), corresponding_source=reference(OUT / "corresponding-source.tar"))
    except BaseException as exc:
        summary.update(status="failed_preserve_all_evidence", failure={"type": type(exc).__name__, "message": str(exc)})
        raise
    finally:
        emit(OUT / "SUMMARY.json", summary)


if __name__ == "__main__":
    cli = argparse.ArgumentParser()
    cli.add_argument("--driver-commit", required=True)
    cli.add_argument("--collect-train", action="store_true", help="Capture the one hash-frozen train-only input after qualification")
    args = cli.parse_args()
    run(args.driver_commit, args.collect_train)
