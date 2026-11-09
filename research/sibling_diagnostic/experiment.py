"""Training-only baseline sibling diagnostic; native execution is CI-only.

Reuses the pinned public corpus, native parser, interactive session and router.
No network editor, fitting, model-selection or games are present.
SPDX-License-Identifier: GPL-3.0-or-later
"""

import argparse
from collections import Counter
from contextlib import ExitStack
import hashlib
import json
import mmap
import os
from pathlib import Path
import queue
import sys
import time

import chess
import corpus
import fetch_inputs
import native_check as native
import teacher

import diagnostic as diag


HERE = Path(__file__).resolve().parent
RESEARCH = HERE.parent
BASE = "08abd7bd1c3fa9a504a0fa8556a6bef6633933cc"
PLAN_SHA = "554ea5a418980ea9b21dc72e2835e9e367534d321052b67c3d9686f825b566ac"
CORPUS_SHA = "f70679592a71b4803c3a2ba7bc80ac7c120780c83f29ed742abb9e4414ab211c"
BASE_SOURCES = {
    "head_calibration/head_bias.py": "600b912c01a95b306dcd42b6508ffe5d51b9bfd5dee749a1017047685f739051",
    "head_calibration/teacher.py": "53b577b4f8ec6c15409cacff1ee7b57e4cfa107a5098c230be6402a4aa3af1f4",
    "layout/corpus.py": "3496b596389e625b57beafb8a46a46f1fee9b60abcd33f04b222fb19bd61f1d1",
    "layout/features_v19.py": "b494c4e11dafdfc9598c3c591c0dca720f41498ae5944d8dc384f03737a58e6e",
    "layout/fetch_inputs.py": "beab24bbfff53e4f919451a1114e3900fd68eb99ee71bd618f09cda080a0c75d",
    "layout/native_check.py": "580eef4ef8a33dc3ccc0634e44bef7716755eb77aa16a58ed0e922b12948b745",
    "layout/permute_nnue.py": "8cedd68e2d36437939e4987413a217ff7c3e21c560441feb94464828ada68d70",
}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def ref(path):
    return native._receipt(Path(path))


def save(output, name, value):
    path = output / name
    native._json(path, value)
    return ref(path)


def source_refs():
    refs = {}
    for name, pinned in BASE_SOURCES.items():
        entry = ref(RESEARCH / name)
        diag.require(entry["sha256"] == pinned, "Pinned inherited research source changed: " + name)
        refs[name] = entry
    for name in ("experiment.py", "diagnostic.py", "test_diagnostic.py"):
        refs["sibling_diagnostic/" + name] = ref(HERE / name)
    diag.require((teacher.SHALLOW_DEPTH, teacher.DEEP_DEPTH) == diag.DEPTHS, "Inherited teacher depth contract changed")
    return refs


def plan_document(path, supplied_digest):
    raw = path.read_bytes()
    diag.require(digest(raw) == supplied_digest == PLAN_SHA, "Immutable sibling plan changed")
    plan = json.loads(raw)
    diag.require(plan["schema"] == "test1757-sibling-diagnostic-v1" and plan["base_driver"] == BASE,
                 "Wrong sibling plan/base")
    diag.require(plan["engine_sha256"] == fetch_inputs.REFERENCE.sha256
                 and plan["network_sha256"] == fetch_inputs.NETWORK.sha256, "Wrong engine/network plan pins")
    diag.require(plan["resources"] == {"max_wall_seconds": 2400, "max_search_seconds": 60,
                                       "threads": 1, "hash_mb": 16}, "Resource plan changed")
    return plan


def prepare(inputs, output):
    before = {}
    for arm, (_, size, pinned) in corpus.SOURCES.items():
        before[arm + ".pgn"] = ref(inputs / (arm + ".pgn"))
        diag.require((before[arm + ".pgn"]["bytes"], before[arm + ".pgn"]["sha256"]) == (size, pinned),
                     "Pinned public PGN changed: " + arm)
    population = corpus.build_corpus(inputs / "edited.pgn", inputs / "parent.pgn")
    # This exact public membership document contains provenance/FENs, not labels.
    canonical = (json.dumps(population, allow_nan=False, indent=2, sort_keys=True) + "\n").encode()
    diag.require(digest(canonical) == CORPUS_SHA, "Original public family/position selection changed")
    diag.require(len(population["optimization"]) == 1536
                 and len({r["family"] for r in population["optimization"]}) == 48, "Wrong training population")
    selection = diag.select_roots(population["optimization"])
    diag.require(selection["counts"]["families"] == 48 and len(selection["selected"]) <= 192,
                 "Root-selection family/cap changed")
    selection_ref = save(output, "SELECTION.json", selection)
    return selection, selection_ref, before


class Session(teacher._Session):
    """Small MultiPV/budget adaptation of the existing interactive capture."""

    def __init__(self, exe, work, logs, deadline, max_reply_seconds):
        super().__init__(exe, work, logs)
        self.deadline = deadline
        self.max_reply_seconds = max_reply_seconds

    def receive(self, predicate):
        deadline = min(self.deadline, time.monotonic() + self.max_reply_seconds)
        lines = []
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError("Sibling diagnostic reply/search or global wall budget exhausted")
            try:
                kind, line = self.events.get(timeout=max(0, deadline - time.monotonic()))
            except queue.Empty as exc:
                raise TimeoutError("Sibling diagnostic reply/search or global wall budget exhausted") from exc
            if kind != "line":
                raise RuntimeError(line)
            lines.append(line)
            if predicate(line):
                return lines

    def configure(self, net):
        super().configure(net)
        self.send("setoption name MultiPV value 2")
        self.send("setoption name UCI_ShowWDL value false")
        self.quiet_ready()

    def search_pair(self, net, row, depth):
        self.send("ucinewgame")
        self.quiet_ready()
        self.send("position fen " + row["fen"])
        self.send(f"go depth {depth}")
        lines = self.receive(lambda line: line.startswith("bestmove"))
        self.quiet_ready()
        return diag.parse_search(lines, chess.Board(row["fen"]), net, depth)

    def evaluate_child(self, net, row):
        self.send("ucinewgame")
        self.quiet_ready()
        self.send("position fen " + row["fen"])
        self.send("eval")
        return diag.parse_static(self.prefix, self.ready(), net, row)


def counts(rows):
    return {"roots": len(rows), "ordered_top_two_stable": sum(r["ordered_top_two_stable"] for r in rows),
            "teacher_clear": sum(r["teacher_clear"] for r in rows),
            "both_child_statics_available": sum(r["both_child_statics_available"] for r in rows),
            "inversion_assessable": sum(r["inversion_assessable"] for r in rows),
            "gross_inversions": sum(r["gross_inversion"] for r in rows),
            "teacher_exclusions": dict(sorted(Counter(reason for r in rows for reason in r["teacher_exclusions"]).items())),
            "child_exclusions": dict(sorted(Counter(c["reason"] for r in rows for c in r["children"] if c["status"] != "measured").items()))}


def report_groups(rows, selection):
    families = [{"family": family["family"], "selected_roots": family["selected_positions"],
                 **counts([r for r in rows if r["family"] == family["family"]])} for family in selection["families"]]
    heads = []
    for head in range(92):
        affected = [r for r in rows if any(c.get("physical_head") == head for c in r["children"])]
        children = [c for r in rows for c in r["children"] if c.get("physical_head") == head]
        heads.append({"physical_head": head, "root_pairs_touching_head": len(affected),
                      "child_occurrences": len(children), "measured_children": sum(c["status"] == "measured" for c in children),
                      "families": len({c["family"] for c in children}),
                      "gross_inversion_pairs_touching_head": sum(r["gross_inversion"] for r in affected),
                      "child_exclusions": dict(sorted(Counter(c["reason"] for c in children if c["status"] != "measured").items()))})
    return families, heads


def capture(inputs, output, selection, deadline, max_reply_seconds):
    # Routing/geometry uses the existing numpy-backed sources only in actual CI
    # capture. Pure parser/sign tests and prepare-only need no numpy or engine.
    import head_bias
    import permute_nnue

    exe, network = (inputs / name for name in ("reference.exe", "parent.nnue"))
    before = {"executable": ref(exe), "network": ref(network)}
    fetch_inputs.REFERENCE.verify(before["executable"]["bytes"], before["executable"]["sha256"])
    fetch_inputs.NETWORK.verify(before["network"]["bytes"], before["network"]["sha256"])
    with network.open("rb") as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
        layout = permute_nnue.parse_layout(data)
    work = output / "work"
    work.mkdir()
    relative = os.path.relpath(network, work)
    if not relative.startswith("."):
        relative = ".\\" + relative
    diag.require(relative.isascii() and relative.isprintable() and not Path(relative).is_absolute()
                 and (work / relative).resolve(strict=True) == network, "Invalid external EvalFile path")
    paths = {key: output / ("sibling-original." + suffix) for key, suffix in
             (("uci", "uci"), ("stdout", "stdout.txt"), ("stderr", "stderr.txt"))}
    record = {"schema": "test1757-sibling-native-capture-v1", "status": "incomplete", "inputs_before": before,
              "argv": [str(exe)], "cwd": str(work), "evalfile": relative,
              "max_wall_deadline_monotonic": deadline, "max_reply_seconds": max_reply_seconds,
              "requested_roots": len(selection["selected"]), "searches": 0, "child_eval_attempts": 0,
              "completed_roots": 0, "steps": []}
    rows, exposure = [], {}
    session = None
    success = False
    try:
        with ExitStack() as stack:
            logs = {key: stack.enter_context(path.open("xb")) for key, path in paths.items()}
            session = Session(exe, work, logs, deadline, max_reply_seconds)
            try:
                session.start()
                session.configure(relative)
                for root in selection["selected"]:
                    diag.require(time.monotonic() < deadline, "Global sibling wall budget exhausted")
                    searches = []
                    for depth in diag.DEPTHS:
                        started = time.monotonic()
                        observed = session.search_pair(relative, root, depth)
                        searches.append(observed)
                        record["searches"] += 1
                        record["steps"].append({"root_position_id": root["position_id"], "kind": "search", "depth": depth,
                                                "elapsed_seconds": round(time.monotonic() - started, 6)})
                        diag.add_exposures(exposure, root, observed)
                    pair = diag.teacher_pair(*searches)
                    children = []
                    for child in diag.candidate_children(root, searches[1]):
                        raw, head = head_bias.head_for_fen(child["fen"], layout)
                        child.update(raw_route=raw, physical_head=head)
                        if child["static_exclusion"]:
                            children.append({**child, "status": "static_unavailable", "reason": child["static_exclusion"],
                                             "static_cp_stm": None, "static_cp_parent": None, "used_bucket": None})
                            continue
                        started = time.monotonic()
                        measured = session.evaluate_child(relative, child)
                        record["child_eval_attempts"] += 1
                        record["steps"].append({"root_position_id": root["position_id"], "kind": "child_static", "rank": child["rank"],
                                                "elapsed_seconds": round(time.monotonic() - started, 6)})
                        children.append(measured)
                    rows.append({"position_id": root["position_id"], "family": root["family"], "fen": root["fen"],
                                 "searches": searches, **pair, "children": children, **diag.inversion(pair, children)})
                    record["completed_roots"] += 1
                success = True
            finally:
                session.close(success)
                record.update(returncode=session.process.returncode if session.process is not None else None,
                              forced_cleanup=session.forced, reader_errors=session.reader_errors,
                              log_readers_closed=all(not thread.is_alive() for thread in session.readers))
        diag.require(record["returncode"] == 0 and not record["forced_cleanup"] and not record["reader_errors"]
                     and record["log_readers_closed"], "Unclean sibling native capture")
        lines = paths["stdout"].read_text(encoding="utf-8").splitlines()
        commands = paths["uci"].read_text(encoding="utf-8").splitlines()
        expected = record["searches"] + record["child_eval_attempts"]
        diag.require([line for line in lines if line.startswith(native.ACK_PREFIX)] ==
                     [native.ACK_PREFIX + relative + native.ACK_SUFFIX] * expected, "Unbound network acknowledgements")
        diag.require(lines.count("readyok") == commands.count("isready") and lines.count("uciok") == commands.count("uci")
                     and sum(line.startswith("bestmove") for line in lines) == record["searches"] and commands[-1:] == ["quit"],
                     "Complete transcript/control counts disagree")
        record["inputs_after"] = {"executable": ref(exe), "network": ref(network)}
        diag.require(record["inputs_after"] == before, "Engine/network changed during diagnostic")
        record["status"] = "complete"
    except Exception as exc:
        record["error"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        record["files"] = {key: ref(path) for key, path in paths.items() if path.exists()}
        save(output, "ROWS.json", rows)
        save(output, "EXPOSURE.json", {"schema": "public-sibling-pv-exposure-v1", "complete": record["status"] == "complete",
                                        "scope": "Only public optimization roots, their emitted search PVs and actual candidate children.",
                                        "position_id": "SHA256 legal-EP FEN fields 1-4; request provenance retains root/depth/rank/line/ply.",
                                        "positions": [{**row, "requests": sorted(row["requests"], key=repr)}
                                                      for _, row in sorted(exposure.items())]})
        save(output, "CAPTURE.json", record)
    return rows, record


def run(args):
    started = time.monotonic()
    output = args.output.resolve(strict=True)
    inputs = args.inputs.resolve(strict=True)
    result = {"schema": "test1757-sibling-diagnostic-result-v1", "status": "incomplete",
              "candidate_bins_created": False, "verified_elo_gain": False, "heldout_labels_opened": False,
              "native_executed": False, "truncations": [], "coverage": {}, "counts": {}}
    selection = None
    try:
        plan = plan_document(args.plan.resolve(strict=True), args.plan_sha256)
        sources = source_refs()
        source_ref = save(output, "SOURCES.json", sources)
        selection, selection_ref, before = prepare(inputs, output)
        save(output, "PREPARE.json", {"plan_sha256": PLAN_SHA, "base_driver": BASE, "sources": source_ref,
                                      "public_corpus_sha256": CORPUS_SHA, "selection": selection_ref,
                                      "public_inputs": before, "selected_ids_frozen_before_search": True,
                                      "heldout_search_label_files_opened": []})
        result["coverage"].update(original_optimization_families=48, original_optimization_positions=1536,
                                   selected_families=len(selection["families"]), selected_roots=len(selection["selected"]),
                                   requested_searches=2 * len(selection["selected"]), completed_roots=0, completed_searches=0)
        if not args.execute:
            result.update(status="prepared_not_executed", families=selection["families"], heads=[])
            return result
        diag.require(os.name == "nt" and os.environ.get("GITHUB_ACTIONS") == "true",
                     "Native sibling captures require explicit --execute on Windows GitHub Actions")
        result["native_executed"] = True
        rows, captured = capture(inputs, output, selection, started + plan["resources"]["max_wall_seconds"],
                                 plan["resources"]["max_search_seconds"])
        families, heads = report_groups(rows, selection)
        result.update(status="baseline_diagnostic_complete", counts=counts(rows), families=families, heads=heads)
        result["coverage"].update(completed_roots=len(rows), completed_searches=captured["searches"],
                                   candidate_children=sum(len(row["children"]) for row in rows),
                                   child_eval_attempts=captured["child_eval_attempts"],
                                   measured_child_statics=sum(c["status"] == "measured" for r in rows for c in r["children"]),
                                   roots_without_two_candidate_children=sum(len(r["children"]) != 2 for r in rows))
        after = {name: ref(inputs / name) for name in before}
        diag.require(after == before, "Public PGNs changed during diagnostic")
        diag.require(len(rows) == len(selection["selected"]) and captured["searches"] == 2 * len(rows),
                     "Partial captures cannot be reported as complete")
        return result
    except Exception as exc:
        result.update(status="failed_preserved_no_reselection", error={"type": type(exc).__name__, "message": str(exc)})
        if isinstance(exc, TimeoutError) or "budget exhausted" in str(exc):
            result["truncations"].append("resource_budget_exhausted_no_successful_completion")
        capture_path = output / "CAPTURE.json"
        if capture_path.exists():
            captured = json.loads(capture_path.read_bytes())
            result["coverage"].update(completed_roots=captured["completed_roots"], completed_searches=captured["searches"])
            rows_path = output / "ROWS.json"
            if rows_path.exists() and selection is not None:
                partial_rows = json.loads(rows_path.read_bytes())
                families, heads = report_groups(partial_rows, selection)
                result.update(counts=counts(partial_rows), families=families, heads=heads)
        raise
    finally:
        result.update(plan_sha256=PLAN_SHA, base_driver=BASE, elapsed_seconds=round(time.monotonic() - started, 6),
                      artifacts={path.name: ref(path) for path in sorted(output.iterdir())
                                 if path.is_file() and path.suffix in (".json", ".txt", ".uci")})
        save(output, "RESULT.json", result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--plan", type=Path, default=HERE / "plan.json")
    parser.add_argument("--plan-sha256", default=PLAN_SHA)
    parser.add_argument("--execute", action="store_true", help="Run only on Windows GitHub Actions; default just freezes selection.")
    args = parser.parse_args()
    doc = run(args)
    print(json.dumps({key: doc[key] for key in ("schema", "status", "counts", "coverage", "candidate_bins_created", "verified_elo_gain")}, sort_keys=True))


if __name__ == "__main__":
    main()
