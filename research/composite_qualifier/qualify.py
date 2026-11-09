"""Hosted readiness for one fixed LOCAL source+NNUE composite; no build/games.
SPDX-License-Identifier: GPL-3.0-or-later
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.request import Request, urlopen

import chess
import native_check as native


HERE = Path(__file__).resolve().parent
BASE = "39fd08831c65a61df4da6a4102f071a4320e37bd"
PLAN_SHA = "0b85f4d61224bc5a7852bd920539115487228ed112fabace78602a043bb7cb88"
SOURCES = {
    "native_check.py": "580eef4ef8a33dc3ccc0634e44bef7716755eb77aa16a58ed0e922b12948b745",
    "fetch_inputs.py": "beab24bbfff53e4f919451a1114e3900fd68eb99ee71bd618f09cda080a0c75d",
}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def ref(path):
    return native._receipt(Path(path))


def save(output, name, value):
    path = output / name
    native._json(path, value)
    return ref(path)


def plan_document():
    raw = (HERE / "plan.json").read_bytes()
    require(sha(raw) == PLAN_SHA, "Immutable local-composite plan changed")
    plan = json.loads(raw)
    require(plan["parent_driver_commit"] == BASE and plan["verified_elo_gain"] is False,
            "Wrong qualifier source tier/claim")
    return plan


def source_receipts():
    layout = HERE.parent / "layout"
    receipts = {}
    for name, digest in SOURCES.items():
        got = ref(layout / name)
        require(got["sha256"] == digest, "Pinned inherited parser changed: " + name)
        receipts["layout/" + name] = got
    require(Path(native.__file__).resolve() == (layout / "native_check.py").resolve(), "Imported another native parser")
    for name in ("qualify.py", "test_qualify.py", "plan.json"):
        receipts["composite_qualifier/" + name] = ref(HERE / name)
    return receipts


def validate_artifact_metadata(actual, expected):
    for key in ("id", "name", "size_in_bytes", "digest"):
        require(actual.get(key) == expected[key], "Artifact identity changed: " + key)
    require(actual.get("expired") is False, "Required artifact expired")
    for key, value in expected["workflow_run"].items():
        require(actual.get("workflow_run", {}).get(key) == value, "Artifact run identity changed: " + key)
    return {key: actual[key] for key in ("id", "name", "size_in_bytes", "digest", "expired", "workflow_run")}


def artifact_metadata(plan, output):
    require(os.environ.get("GITHUB_ACTIONS") == "true", "Artifact metadata retrieval is hosted-only")
    token = os.environ.get("GITHUB_TOKEN")
    require(bool(token), "Hosted actions:read token unavailable")
    result = []
    for expected in plan["artifacts"]:
        url = f"https://api.github.com/repos/{plan['repository']}/actions/artifacts/{expected['id']}"
        request = Request(url, headers={"User-Agent": "Local-Composite-Qualifier/1", "Accept": "application/vnd.github+json",
                                       "Authorization": "Bearer " + token, "X-GitHub-Api-Version": "2022-11-28"})
        with urlopen(request, timeout=30) as incoming:
            require(incoming.status == 200, "Artifact metadata HTTP failure")
            raw = incoming.read(65537)
        require(len(raw) <= 65536, "Artifact metadata exceeded bound")
        result.append(validate_artifact_metadata(json.loads(raw), expected))
    save(output, "ARTIFACTS.json", {"repository": plan["repository"], "artifacts": result})
    return result


def verify_inputs(root, plan):
    paths, receipts = {}, {}
    for role, pin in plan["inputs"].items():
        path = (root / pin["path"]).resolve(strict=True)
        require(path.is_relative_to(root) and path.is_file() and not path.is_symlink(), "Input escaped workspace")
        current = ref(path)
        require((current["bytes"], current["sha256"]) == (pin["bytes"], pin["sha256"]), "Downloaded input changed: " + role)
        paths[role], receipts[role] = path, current
    source = json.loads(paths["history_source"].read_bytes())
    for field in ("source_commit", "source_tree", "baseline_commit"):
        mapped = {"source_commit": "history_source_commit", "source_tree": "history_source_tree",
                  "baseline_commit": "baseline_source_commit"}[field]
        require(source[field] == plan["source_context"][mapped], "History source context changed")
    probes = json.loads(paths["probes"].read_bytes())
    rows = probes["rows"]
    require(len(rows) == 22 and len({r["fen"] for r in rows}) == 22 and probes["buckets_per_eval"] == 92,
            "Established retired probe roster changed")
    native._validated_rows(rows)
    require(all(type(row["physical"]) is int and 0 <= row["physical"] < 92 for row in rows), "Invalid physical probe route")
    return paths, receipts, rows


def board_for_bench(fen):
    fields = fen.split()
    require(len(fields) == 6, "Benchmark marker is not exact FEN6")
    frc = bool(re.search(r"[A-Ha-h]", fields[2]))
    board = chess.Board(fen, chess960=frc)
    require(board.is_valid(), "Invalid canonical benchmark root")
    return board


def replay_line(board, moves):
    replay = board.copy(stack=False)
    ids = []
    for text in moves:
        require(re.fullmatch(native.MOVE, text), "Malformed benchmark PV move")
        move = replay.parse_uci(text)
        require(replay.is_legal(move), "Illegal benchmark PV move")
        replay.push(move)
        canonical_fen = " ".join(replay.fen(en_passant="legal").split()[:4])
        ids.append(sha(canonical_fen.encode()))
    return ids


def parse_bench(stdout, stderr, net, expected_roster_sha):
    block, = native._ready_blocks(stdout, stderr, 1)
    chunks = native._ack_blocks(block, net, 51)
    markers = re.findall(r"^Position: (\d+)/51 \(([^()]+)\)\s*$", stderr, re.M)
    require([int(i) for i, _ in markers] == list(range(1, 52)), "Incomplete canonical 51-position roster")
    roster = [fen for _, fen in markers]
    require(sha(canonical(roster)) == expected_roster_sha, "Canonical benchmark FEN roster changed")
    positions, pv_ids, lines_count = [], set(), 0
    for chunk, (index, fen) in zip(chunks, markers, strict=True):
        parsed = native._bench_search(chunk, int(index), fen)
        board = board_for_bench(fen)
        pv_ids.add(sha(" ".join(board.fen(en_passant="legal").split()[:4]).encode()))
        for line in chunk:
            if line.startswith("info depth ") and " pv " in line:
                pv = line.split(" pv ", 1)[1].split()
                pv_ids.update(replay_line(board, pv))
                lines_count += 1
        if parsed["bestmove"] != "(none)":
            pv_ids.update(replay_line(board, [parsed["bestmove"]] + ([parsed["ponder"]] if parsed["ponder"] else [])))
        else:
            require(board.is_game_over(claim_draw=False), "Nonterminal canonical root has no bestmove")
        positions.append(parsed)
    total = sum(row["nodes"] for row in positions)
    require(total > 0 and re.findall(r"^Nodes searched\s*:\s*(\d+)\s*$", stderr, re.M) == [str(total)],
            "Canonical bench total/per-position sum disagree")
    vector = [row["nodes"] for row in positions]
    return {"nodes": total, "positions": positions, "per_position_nodes": vector,
            "node_vector_sha256": sha(canonical(vector)), "search_signature_sha256": sha(canonical(positions)),
            "canonical_roster_sha256": expected_roster_sha, "all_reported_pv_lines_legally_replayed": lines_count,
            "all_root_pv_and_bestmove_position_ids": sorted(pv_ids)}


def compare_benches(first, second):
    require(first["nodes"] == second["nodes"] and first["positions"] == second["positions"]
            and first["node_vector_sha256"] == second["node_vector_sha256"]
            and first["search_signature_sha256"] == second["search_signature_sha256"], "Mixed benchmark did not repeat exactly")


def compare_evals(candidate, reference, rows):
    require(candidate == reference, "History/ridge and prefetch/ridge static outputs differ")
    require(len(candidate["rows"]) == len(rows) == 22, "Parity cohort incomplete")
    for measured, row in zip(candidate["rows"], rows, strict=True):
        require(measured["fen"] == row["fen"] and measured["used_bucket"] == row["physical"]
                and len(measured["buckets"]) == 92, "Retired probe route/bucket output mismatch")
    return {"status": "passed", "roots": 22, "printed_buckets_per_root": 92,
            "raw_stm_and_white_final_output_equal": True, "all_bucket_values_and_used_heads_equal": True}


def run(root, output, execute):
    started = time.monotonic()
    result = {"schema": "test1757-local-composite-qualifier-result-v1", "status": "incomplete", "verified_elo_gain": False,
              "official_superiority_claimed": False, "mixed_match_played": False, "compiled_here": False,
              "fishtest_submission_made": False, "local_same_host_launch_qualified": False, "native_processes": 0}
    try:
        plan = plan_document()
        save(output, "PLAN.json", plan)
        save(output, "SOURCES.json", source_receipts())
        if not execute:
            result["status"] = "plan_only_no_native"
            return result
        require(os.name == "nt" and os.environ.get("GITHUB_ACTIONS") == "true", "Native qualification is Windows hosted CI only")
        artifact_metadata(plan, output)
        paths, before, rows = verify_inputs(root, plan)
        save(output, "INPUTS.json", {"files": before, "artifact_inputs": plan["inputs"], "source_context": plan["source_context"],
                                    "public_game_context_only": plan["context_only"]["public_game_context"]})
        save(output, "RETIRED-PROBES.json", [{"id": r["id"], "fen": r["fen"], "physical": r["physical"]} for r in rows])
        work = output / "work"
        work.mkdir()
        net = os.path.relpath(paths["network"], work)
        if not net.startswith("."):
            net = ".\\" + net
        require(net.isascii() and net.isprintable() and not Path(net).is_absolute()
                and (work / net).resolve(strict=True) == paths["network"], "Invalid explicit external EvalFile")
        result["evalfile"] = net
        captures, benches = {}, []

        def capture(role, stem, mode, cohort=()):
            result["native_processes"] += 1
            require(result["native_processes"] <= 5, "Unexpected extra native process")
            stdout, stderr = native._capture(paths[role], work, output, stem, native._commands(net, mode, cohort), net,
                                             before[role]["sha256"], before["network"]["sha256"])
            captures[stem] = ref(output / (stem + ".record.json"))
            return stdout, stderr

        for index in (1, 2):
            stem = f"composite-bench-{index}"
            stdout, stderr = capture("history_exe", stem, "bench")
            measured = parse_bench(stdout, stderr, net, plan["qualification"]["bench_roster_compact_json_sha256"])
            save(output, stem + ".parsed.json", measured)
            benches.append(measured)
        compare_benches(*benches)
        result.update(mixed_bench_nodes=benches[0]["nodes"], node_vector_sha256=benches[0]["node_vector_sha256"],
                      mixed_search_signature_sha256=benches[0]["search_signature_sha256"], bench_repetitions=2,
                      bench_positions=51)
        stdout, stderr = capture("history_exe", "composite-perft4", "perft")
        perft = native._parse_perft(stdout, stderr, net)
        save(output, "composite-perft4.parsed.json", perft)
        result["perft4_nodes"] = perft["nodes"]
        evaluated = []
        for role, stem in (("history_exe", "composite-eval22"), ("reference_exe", "prefetch-same-network-eval22")):
            stdout, stderr = capture(role, stem, "eval", rows)
            measured = native._parse_eval(stdout, stderr, net, rows)
            save(output, stem + ".parsed.json", measured)
            evaluated.append(measured)
        result["parity"] = compare_evals(*evaluated, rows)
        after = {role: ref(path) for role, path in paths.items()}
        require(after == before, "Inputs changed during hosted qualification")
        save(output, "INPUTS-AFTER.json", after)
        result.update(status="hosted_local_composite_readiness_passed", captures=captures,
                      history_executable_sha256=before["history_exe"]["sha256"], network_sha256=before["network"]["sha256"],
                      reference_executable_sha256=before["reference_exe"]["sha256"], source_context=plan["source_context"],
                      component_context_only=plan["context_only"], text_evidence_only=True)
        return result
    except Exception as exc:
        result.update(status="failed_evidence_preserved", error={"type": type(exc).__name__, "message": str(exc)})
        raise
    finally:
        result.update(driver_parent=BASE, driver_commit=os.environ.get("GITHUB_SHA"), plan_sha256=PLAN_SHA,
                      elapsed_seconds=round(time.monotonic() - started, 6),
                      evidence={path.name: ref(path) for path in sorted(output.iterdir())
                                if path.is_file() and path.suffix in (".json", ".txt", ".uci")})
        save(output, "RESULT.json", result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, default=Path("evidence"))
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    root, output = args.workspace.resolve(strict=True), args.output.resolve(strict=True)
    require(output.is_relative_to(root), "Evidence path escaped workspace")
    result = run(root, output, args.execute)
    print(json.dumps({key: result[key] for key in ("schema", "status", "verified_elo_gain", "native_processes")}, sort_keys=True))


if __name__ == "__main__":
    main()
