"""Bind the reviewed source ref, prove its sole tree delta, and archive every Git file."""
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile

from capture_common import file_hash, require

HERE = Path(__file__).resolve().parent
WORKFLOW = ".github/workflows/test1757-history-floor-v1.yml"
FILES = ("build_and_capture_v1.py", "capture_common.py", "embedded_reference.py", "native_eval.py",
         "pgo_capture_v1.py", "probes-v1.json", "source-pin.json", "source_evidence.py", "test_history_floor.py", "README.md")
BASE = {"schema": "test1757-history-floor-source-v1", "repository": "0mis/Stockfish",
        "baseline_commit": "7a17b53bcaf93f54a23ad65f309c413585500c82",
        "baseline_tree": "8e3164caf60d281cb4b1d00a699364e04b354451",
        "old_search_blob": "6f85432cb918df5239a74d3d3d282a163b71ead5",
        "new_search_blob": "310063f887ef7a1c964391ba539c12524995007c"}
NEW_SEARCH_SHA = "afed34526d4d4356386ed9ccf03a0a132c16902702801200bb48353a065016b6"


def read_pin(path):
    record = json.loads(Path(path).read_bytes())
    require(set(record) == set(BASE) | {"source_commit", "source_tree"} and
            all(record[key] == value for key, value in BASE.items()), "Unexpected source record or comparator")
    for key in ("source_commit", "source_tree"):
        require(isinstance(record[key], str) and re.fullmatch("[0-9a-f]{40}", record[key]) and
                int(record[key], 16) != 0, f"Root must bind a full immutable {key}")
    require(record["source_commit"] != record["baseline_commit"] and record["source_tree"] != record["baseline_tree"],
            "Candidate and baseline identities coincide")
    return record


def git(checkout, *arguments, data=None):
    return subprocess.run(["git", "-C", str(checkout), *arguments], input=data,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=60).stdout


def revision(checkout, spec):
    return git(checkout, "rev-parse", "--verify", spec).decode("ascii").strip()


def tree(checkout, spec, recursive=False):
    arguments = ["ls-tree", "-z"] + (["-r"] if recursive else []) + [spec]
    result = {}
    for row in git(checkout, *arguments).split(b"\0"):
        if not row:
            continue
        attributes, name = row.split(b"\t", 1)
        mode, kind, sha = attributes.decode("ascii").split()
        name = name.decode("utf-8")
        require(name not in result and not PurePosixPath(name).is_absolute() and
                ".." not in PurePosixPath(name).parts, "Invalid Git path")
        result[name] = (mode, kind, sha)
    return result


def sole_delta(before, after, allowed):
    require(before.keys() == after.keys() and allowed in before, "Tree membership differs")
    require([name for name in before if before[name] != after[name]] == [allowed],
            "Source change differs from the historical search-only delta")
    require(before[allowed][:2] == after[allowed][:2], "Changed tree entry mode or type differs")


def check_source(pin, driver_commit):
    require(re.fullmatch("[0-9a-f]{40}", driver_commit), "Invalid triggering driver SHA")
    for directory, expected in (("driver", driver_commit), ("source", pin["source_commit"]), ("baseline", pin["baseline_commit"])):
        require(revision(directory, "HEAD") == expected, f"{directory} HEAD differs")
        git(directory, "diff", "--exit-code", "HEAD", "--")
    for directory, expected in (("source", pin["source_tree"]), ("baseline", pin["baseline_tree"])):
        require(revision(directory, "HEAD:") == expected, f"{directory} tree differs")
    commit_headers = git("source", "cat-file", "-p", "HEAD").split(b"\n\n", 1)[0].splitlines()
    require([line for line in commit_headers if line.startswith(b"parent ")] ==
            [("parent " + pin["baseline_commit"]).encode("ascii")], "Candidate commit parent differs")
    base_root, new_root = tree("baseline", "HEAD"), tree("source", "HEAD")
    sole_delta(base_root, new_root, "src")
    require(base_root["src"][:2] == ("040000", "tree"), "Expected src tree")
    old_src, new_src = tree("baseline", "HEAD:src"), tree("source", "HEAD:src")
    sole_delta(old_src, new_src, "search.cpp")
    require(old_src["search.cpp"] == ("100644", "blob", pin["old_search_blob"]) and
            new_src["search.cpp"] == ("100644", "blob", pin["new_search_blob"]) and
            file_hash(Path("source/src/search.cpp")) == NEW_SEARCH_SHA, "Selected historical source bytes differ")
    return {"source_delta": ["src/search.cpp"], "old_src_tree": base_root["src"][2], "new_src_tree": new_root["src"][2],
            "old_search_blob": old_src["search.cpp"][2], "new_search_blob": new_src["search.cpp"][2],
            "checked_out_search_sha256": NEW_SEARCH_SHA}


def add_file(archive, name, value, mode):
    info = tarfile.TarInfo(name)
    info.mode = int(mode, 8) & 0o777
    if mode == "120000":
        info.type = tarfile.SYMTYPE
        info.linkname = value.decode("utf-8")
        archive.addfile(info)
    else:
        info.size = len(value)
        archive.addfile(info, io.BytesIO(value))


def archive_source(output):
    entries = tree("source", "HEAD", recursive=True)
    request = "".join(entry[2] + "\n" for entry in entries.values()).encode("ascii")
    objects = io.BytesIO(git("source", "cat-file", "--batch", data=request))
    files = [WORKFLOW, *("research/ci/" + name for name in FILES)]
    driver = Path("driver")
    committed_driver = tree(driver, "HEAD", recursive=True)
    for name in files:
        raw = (driver / name).read_bytes()
        blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        require(committed_driver.get(name) == ("100644", "blob", blob), "Untracked or changed CI driver file: " + name)
    bindings = {name: file_hash(driver / name) for name in files}
    tar = output / "corresponding-source.tar"
    with tarfile.open(tar, "x:") as archive:
        for path, (mode, kind, blob) in entries.items():
            returned, object_type, size = objects.readline().decode("ascii").split()
            require(kind == object_type == "blob" and returned == blob and mode in ("100644", "100755", "120000"),
                    "Unsupported or inconsistent source object")
            value = objects.read(int(size))
            require(objects.read(1) == b"\n" and hashlib.sha1(b"blob " + str(len(value)).encode() + b"\0" + value).hexdigest() == blob,
                    "Source blob was truncated or changed")
            add_file(archive, "stockfish-source/" + path, value, mode)
        require(not objects.read(1), "Unexpected trailing Git object")
        for relative in files:
            add_file(archive, "ci-driver/" + relative, (driver / relative).read_bytes(), "100644")
    return {"file": tar.name, "sha256": file_hash(tar), "source_files": len(entries), "driver_files": bindings}
