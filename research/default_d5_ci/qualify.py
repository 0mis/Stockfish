"""Fresh compiled/default edited-net qualification; no training or match play."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import struct
import subprocess
import sys
import time
import traceback
import urllib.request
import zipfile

from benches import official_benchmark_specs, parse_bench

SOURCE = "cecfcfd5480afd16b31740b24387f5779d24c0db"
TREE = "c3efb0c3b57fe3fd2ad93422238e8629e1fd85c2"
NETWORK = "d5c257728ba53cb9808543ce2800080c860f1c878896cd1b17f75ed4b20aafee"
NET_NAME = "nn-d5c257728ba5.nnue"
NET_BYTES = 138360528
ZIP_SHA = "23b4df6759e361ea510e28f23abec04daeb14f08969a781653b08a916f0e4e8d"
ZIP_BYTES = 232285191
ZIP_URL = "https://github.com/0mis/Stockfish/releases/download/experimental-test1757-history-d5c257728ba5/stockfish-test1757-history-d5-windows-bmi2.zip"
BRANCH = "research/test1757-default-d5-ci-v1"
EXPECTED_BENCH = 4125162


def need(ok, why):
    if not ok:
        raise ValueError(why)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(obj, sort_keys=True, indent=2, allow_nan=False).encode() + b"\n")


def hash_span(path, offset=0, size=None):
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as stream:
        stream.seek(offset)
        while size is None or total < size:
            block = stream.read(1024 * 1024 if size is None else min(1024 * 1024, size - total))
            if not block:
                break
            digest.update(block)
            total += len(block)
    need(size is None or total == size, "Truncated authenticated file span")
    return total, digest.hexdigest()


def ref(path, base):
    size, digest = hash_span(path)
    return {"path": path.relative_to(base).as_posix(), "bytes": size, "sha256": digest}


def run(out, name, argv, cwd, cwd_label, stdin=b"", timeout=900, stdout_suffix=".stdout"):
    command = {"schema": "default-d5-process/1", "argv": list(argv), "cwd": cwd_label,
               "fresh_process": True, "stdin": None, "started_utc_epoch": time.time()}
    stdout_path = out / (name + stdout_suffix)
    stderr_path = out / (name + ".stderr")
    if stdin:
        input_path = out / (name + ".stdin")
        input_path.write_bytes(stdin)
        command["stdin"] = ref(input_path, out)
    write_json(out / (name + ".command.json"), command)
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        process = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr)
        command["pid"] = process.pid
        try:
            process.communicate(stdin, timeout=timeout)
            command["timed_out"] = False
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            command["timed_out"] = True
        command["returncode"] = process.returncode
    command["finished_utc_epoch"] = time.time()
    command["stdout"] = ref(stdout_path, out)
    command["stderr"] = ref(stderr_path, out)
    write_json(out / (name + ".command.json"), command)
    need(not command["timed_out"], name + " timed out")
    return command, stdout_path.read_bytes(), stderr_path.read_bytes()


def git_text(source, *args):
    return subprocess.check_output(["git", "-C", str(source), *args]).decode().strip()


def source_manifest(source):
    need(git_text(source, "rev-parse", "HEAD") == SOURCE, "Exact source commit differs")
    need(git_text(source, "rev-parse", "HEAD^{tree}") == TREE, "Exact complete source tree differs")
    need(not git_text(source, "status", "--porcelain", "--untracked-files=no"), "Tracked source is modified")
    raw = subprocess.check_output(["git", "-C", str(source), "ls-tree", "-rz", "--full-tree", "HEAD"])
    files = []
    for item in raw.split(b"\0"):
        if not item:
            continue
        header, relative = item.split(b"\t", 1)
        mode, kind, blob = header.decode().split()
        need(kind == "blob" and mode in ("100644", "100755"), "Unexpected tracked source kind")
        relative = relative.decode()
        data = (source / relative).read_bytes()
        actual_blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        need(actual_blob == blob, "Tracked file differs: " + relative)
        files.append({"path": relative, "mode": mode, "bytes": len(data),
                      "git_blob": blob, "sha256": sha(data)})
    header = (source / "src/evaluate.h").read_text()
    need(re.findall(r'^#define EvalFileDefaultName "([^"]+)"$', header, re.M) == [NET_NAME],
         "Compiled default filename differs")
    return {"schema": "exact-default-d5-source-manifest/1", "commit": SOURCE, "tree": TREE,
            "files": files, "tracked_source_clean": True, "default_evalfile": NET_NAME}


def fetch_net(source, out):
    download = source.parent / "downloads" / "public-experimental-release.zip"
    download.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(ZIP_URL, headers={"User-Agent": "0mis-default-net-qualification"})
    total = 0
    digest = hashlib.sha256()
    with urllib.request.urlopen(request, timeout=180) as response, download.open("wb") as target:
        while True:
            block = response.read(1024 * 1024)
            if not block:
                break
            total += len(block)
            need(total <= ZIP_BYTES, "Public release exceeds pinned size")
            digest.update(block)
            target.write(block)
    need(total == ZIP_BYTES and digest.hexdigest() == ZIP_SHA, "Public release ZIP hash/size differs")
    target = source / "src" / NET_NAME
    need(not target.exists(), "Refusing to overwrite an existing network")
    with zipfile.ZipFile(download) as archive:
        matches = [info for info in archive.infolist() if info.filename == NET_NAME]
        need(len(matches) == 1 and matches[0].file_size == NET_BYTES, "Unique exact NNUE member required")
        with archive.open(matches[0]) as stream, target.open("xb") as network:
            copied = 0
            while True:
                block = stream.read(1024 * 1024)
                if not block:
                    break
                copied += len(block)
                network.write(block)
        need(copied == NET_BYTES, "Truncated NNUE ZIP member")
        crc = matches[0].CRC
    size, digest = hash_span(target)
    need((size, digest) == (NET_BYTES, NETWORK), "Full extracted NNUE hash/size differs")
    result = {"schema": "public-default-d5-network-retrieval/1",
              "source_url": ZIP_URL, "archive_bytes": total, "archive_sha256": ZIP_SHA,
              "member": NET_NAME, "member_crc32": f"{crc:08x}",
              "member_read_to_crc_verified_eof": True, "bytes": size, "sha256": digest,
              "no_other_release_member_opened": True,
              "original_network_author": "Shawn Xu (xu-shawn)", "network_license": "CC0-1.0"}
    write_json(out / "NETWORK-RETRIEVAL.json", result)
    return result


def elf_embedded(binary, symbols):
    """Hash the actual ELF PT_LOAD file span named by incbin symbols."""
    addresses = {}
    for line in symbols.decode().splitlines():
        fields = line.split()
        if len(fields) == 3 and fields[2] in ("gEmbeddedNNUEData", "gEmbeddedNNUEEnd", "gEmbeddedNNUESize"):
            need(fields[2] not in addresses, "Duplicate embedded symbol")
            addresses[fields[2]] = int(fields[0], 16)
    need(len(addresses) == 3, "All three actual embedded NNUE symbols required")
    begin, end = addresses["gEmbeddedNNUEData"], addresses["gEmbeddedNNUEEnd"]
    need(end - begin == NET_BYTES, "Embedded NNUE length differs")
    with binary.open("rb") as stream:
        header = stream.read(64)
        need(header[:7] == b"\x7fELF\x02\x01\x01", "ELF64 little-endian executable required")
        fields = struct.unpack("<16sHHIQQQIHHHHHH", header)
        phoff, phentsize, phnum = fields[5], fields[9], fields[10]
        need(phentsize == 56 and 0 < phnum < 256, "Unexpected ELF program headers")
        loads = []
        for index in range(phnum):
            stream.seek(phoff + phentsize * index)
            program = struct.unpack("<IIQQQQQQ", stream.read(56))
            if program[0] == 1:
                loads.append({"offset": program[2], "address": program[3], "filesz": program[5]})
        def file_offset(address, count):
            matches = [p["offset"] + address - p["address"] for p in loads
                       if p["address"] <= address and address + count <= p["address"] + p["filesz"]]
            need(len(matches) == 1, "Embedded symbol span lacks a unique file-backed PT_LOAD")
            return matches[0]
        offset = file_offset(begin, NET_BYTES)
        size_offset = file_offset(addresses["gEmbeddedNNUESize"], 4)
        stream.seek(size_offset)
        declared_size = struct.unpack("<I", stream.read(4))[0]
    size, digest = hash_span(binary, offset, NET_BYTES)
    need(declared_size == size == NET_BYTES and digest == NETWORK, "Actual compiled-in NNUE bytes differ")
    return {"schema": "elf-embedded-default-d5-proof/1", "file_offset": offset,
            "bytes": size, "sha256": digest, "encoded_size": declared_size,
            "symbols": addresses, "exact_file_backed_span": True}


def check_default(stdout, require_use):
    text = stdout.decode()
    defaults = re.findall(r"^option name EvalFile type string default (.+)$", text, re.M)
    need(defaults == [NET_NAME], "Actual fresh UCI compiled/default EvalFile differs")
    uses = re.findall(r"NNUE evaluation using (\S+)", text)
    need((uses or not require_use) and set(uses) <= {NET_NAME}, "Actual benchmark NNUE differs")
    need(not re.search(r"\b(?:ERROR|CRITICAL)\b", text, re.I), "Native error reported")


def artifact_manifest(out):
    files = [ref(path, out) for path in sorted(out.rglob("*"))
             if path.is_file() and path.name != "ARTIFACT-MANIFEST.json"]
    write_json(out / "ARTIFACT-MANIFEST.json", {
        "schema": "default-d5-qualification-artifact-manifest/1", "files": files})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source, out = args.source.resolve(), args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    try:
        need(os.environ.get("GITHUB_REPOSITORY") == "0mis/Stockfish"
             and os.environ.get("GITHUB_REF") == "refs/heads/" + BRANCH
             and os.environ.get("GITHUB_EVENT_NAME") == "push", "Exact public driver push scope required")
        initial = source_manifest(source)
        write_json(out / "SOURCE-MANIFEST.json", initial)
        driver = Path(__file__).resolve().parents[2]
        tests, _, _ = run(out, "synthetic-tests", [sys.executable, "-B", "-m", "unittest", "-v", "test_qualification"],
                          driver / "research/default_d5_ci", "driver/research/default_d5_ci", timeout=120)
        need(tests["returncode"] == 0, "Synthetic qualification tests failed")
        driver_files = [".github/workflows/test1757-default-d5-ci.yml",
                        "research/default_d5_ci/qualify.py", "research/default_d5_ci/benches.py",
                        "research/default_d5_ci/test_qualification.py",
                        "research/default_d5_ci/requirements.txt", "research/default_d5_ci/README.md"]
        for relative in driver_files:
            target = out / "driver-source" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(driver / relative, target)
        write_json(out / "DRIVER.json", {
            "schema": "default-d5-driver-context/1", "commit": os.environ["GITHUB_SHA"],
            "branch": BRANCH, "repository": "0mis/Stockfish", "run_id": os.environ["GITHUB_RUN_ID"],
            "run_attempt": os.environ["GITHUB_RUN_ATTEMPT"], "runner_os": platform.system(),
            "machine": platform.machine(), "workflow": os.environ["GITHUB_WORKFLOW"],
            "permissions": {"contents": "read"}, "user_secrets_used": False,
            "paid_or_self_hosted_runner": False,
            "files": [ref(out / "driver-source" / p, out) for p in driver_files]})
        archive, _, _ = run(out, "source-archive",
                            ["git", "archive", "--format=tar.gz", "--prefix=stockfish-source/", "HEAD"],
                            source, "engine", stdout_suffix=".tar.gz")
        need(archive["returncode"] == 0, "Source archive failed")
        specs = official_benchmark_specs((source / "src/benchmark.cpp").read_bytes())
        write_json(out / "BENCH-SOURCE-SPECS.json", specs)
        network = fetch_net(source, out)
        flags = set(Path("/proc/cpuinfo").read_text().split("flags", 1)[1].split("\n", 1)[0].partition(":")[2].split())
        need({"avx2", "bmi1", "popcnt"} <= flags, "Runner cannot execute the declared AVX2 build")
        write_json(out / "HOST.json", {"architecture": platform.machine(), "cpu_flags": sorted(flags)})
        for name, command in (("compiler-version", ["g++", "--version"]),
                              ("make-version", ["make", "--version"]),
                              ("python-packages", [sys.executable, "-m", "pip", "freeze", "--all"])):
            process, _, _ = run(out, name, command, source, "engine")
            need(process["returncode"] == 0, name + " failed")
        build, _, _ = run(out, "build", ["make", "-j2", "build", "ARCH=x86-64-avx2", "COMP=gcc"],
                          source / "src", "engine/src", timeout=1200)
        need(build["returncode"] == 0, "Official Makefile build failed")
        need(source_manifest(source) == initial, "Build changed tracked source")
        need(hash_span(source / "src" / NET_NAME) == (NET_BYTES, NETWORK), "Build changed network")
        binary = source / "src/stockfish"
        symbols, stdout, _ = run(out, "embedded-symbols", ["nm", "--defined-only", "stockfish"],
                                 source / "src", "engine/src")
        need(symbols["returncode"] == 0, "ELF symbol extraction failed")
        embedded = elf_embedded(binary, stdout)
        write_json(out / "EMBEDDED-NETWORK.json", embedded)
        artifact_binary = out / "stockfish-linux-avx2"
        shutil.copyfile(binary, artifact_binary)
        runtime = Path(os.environ["RUNNER_TEMP"]) / "default-d5-isolated-runtime"
        runtime.mkdir(parents=True, exist_ok=False)
        (runtime / "bin").mkdir()
        shutil.copyfile(binary, runtime / "bin/stockfish")
        (runtime / "bin/stockfish").chmod(0o755)
        need(list((runtime / "bin").iterdir()) == [runtime / "bin/stockfish"], "Runtime bin is not isolated")
        benches = []
        for repetition in (1, 2):
            name = f"default-bench-{repetition}"
            cwd = runtime / name
            cwd.mkdir()
            stdin = b"uci\nisready\nbench\nisready\nquit\n"
            need(b"setoption" not in stdin and not list(cwd.iterdir()), "Bench default/empty runtime differs")
            process, stdout, stderr = run(out, name, ["../bin/stockfish"], cwd,
                                          "isolated-runtime/" + name, stdin, timeout=240)
            check_default(stdout, require_use=True)
            parsed = parse_bench(stdout, stderr, process["returncode"], specs=specs)
            write_json(out / (name + ".parsed.json"), parsed)
            benches.append({"process": ref(out / (name + ".command.json"), out),
                            "parsed": ref(out / (name + ".parsed.json"), out),
                            "nodes": parsed["nodes"], "positions": parsed["searches"],
                            "mode_aware_search_signature_sha256": parsed["search_signature_sha256"],
                            "all_reported_paths_legal": parsed["all_reported_pv_bestmove_and_ponder_paths_checked"],
                            "uci_default_evalfile": NET_NAME, "evalfile_setoption_sent": False,
                            "external_network_in_runtime_directory": False})
        cwd = runtime / "startpos-perft4"
        cwd.mkdir()
        process, stdout, stderr = run(out, "startpos-perft4", ["../bin/stockfish"], cwd,
                                      "isolated-runtime/startpos-perft4",
                                      b"uci\nisready\nposition startpos\ngo perft 4\nisready\nquit\n", timeout=120)
        check_default(stdout, require_use=False)
        perft_values = re.findall(rb"^Nodes searched:\s*(\d+)\s*$", stdout, re.M)
        need(process["returncode"] == 0 and perft_values == [b"197281"]
             and stdout.splitlines().count(b"uciok") == 1 and stdout.splitlines().count(b"readyok") == 2
             and not re.search(rb"\b(?:ERROR|CRITICAL)\b", stderr, re.I), "Startpos perft4 failed")
        nodes_match = [b["nodes"] for b in benches] == [EXPECTED_BENCH, EXPECTED_BENCH]
        signatures_match = len({b["mode_aware_search_signature_sha256"] for b in benches}) == 1
        passed = nodes_match and signatures_match
        result = {"schema": "fresh-compiled-default-d5-qualification/1",
                  "status": "DEFAULT_D5_NATIVE_QUALIFIED" if passed else "DEFAULT_D5_BENCH_MISMATCH",
                  "native_qualification_passed": passed, "source_commit": SOURCE, "source_tree": TREE,
                  "driver_commit": os.environ["GITHUB_SHA"], "network": network,
                  "engine": ref(artifact_binary, out), "build": ref(out / "build.command.json", out),
                  "build_target": "build", "profile_guided": False, "architecture": "x86-64-avx2",
                  "embedding": ref(out / "EMBEDDED-NETWORK.json", out), "benches": benches,
                  "expected_bench_nodes_frozen_before_run": EXPECTED_BENCH,
                  "actual_nodes_match_expected": nodes_match, "repeated_mode_aware_signatures_match": signatures_match,
                  "perft4": {"nodes": 197281, "process": ref(out / "startpos-perft4.command.json", out)},
                  "fresh_default_processes": True, "external_evalfile_override": False,
                  "tracked_source_unchanged_after_build": True, "real_game_or_farm_test_run": False,
                  "strength_gain_claimed": False}
        write_json(out / "RESULT.json", result)
        artifact_manifest(out)
        print(json.dumps({"status": result["status"], "bench_nodes": [b["nodes"] for b in benches],
                          "perft4": 197281, "engine_sha256": result["engine"]["sha256"]}))
        return 0 if passed else 1
    except BaseException as error:
        write_json(out / "FAILURE.json", {"schema": "default-d5-qualification-failure/1",
                   "source_commit": SOURCE, "source_tree": TREE,
                   "expected_bench_nodes_frozen_before_run": EXPECTED_BENCH,
                   "error_type": type(error).__name__, "message": str(error),
                   "traceback": traceback.format_exc(), "native_qualification_passed": False})
        artifact_manifest(out)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
