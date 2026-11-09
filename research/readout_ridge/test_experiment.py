"""Toy byte files and mocked measurements exercise the experiment's phase boundary."""
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("readout_ridge_experiment_test_target", Path(__file__).with_name("experiment.py"))
experiment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(experiment)
FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def printed(raw, material):
    m = material / 58
    a = ((-142.72052667 * m + 372.35176398) * m - 340.71073572) * m + 415.23490212
    cp = math.floor(abs(100 * raw / a) + 0.5)
    sign = "-" if raw < 0 else "+" if raw > 0 else ""
    return f"{sign}{cp // 100}.{cp % 100:02d}"


class ExperimentTests(unittest.TestCase):
    def test_signed_rounding_and_exact_zero_crossing(self):
        for material in range(17, 79):
            for raw in (-20, -2, -1, 0, 1, 2, 20):
                lo, hi = experiment._printed_raw_interval(material, printed(raw, material))
                self.assertTrue(lo <= raw <= hi)
        self.assertTrue(experiment._possible_shift((-1, -1), (17, 17), 512))
        self.assertFalse(experiment._possible_shift((-1, -1), (18, 18), 512))
        self.assertTrue(experiment._possible_shift((1, 1), (-17, -17), -512))
        self.assertFalse(experiment._possible_shift((1, 1), (-18, -18), -512))

    def test_frozen_before_holdout_and_identity_skips_it(self):
        for identity in (True, False):
            with self.subTest(identity=identity), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                evidence, work = base / "evidence", base / "private"
                evidence.mkdir()
                work.mkdir()
                exe, net = base / "fake-reference", base / "parent.nnue"
                exe.write_bytes(b"synthetic executable placeholder")
                net.write_bytes(b"synthetic parent placeholder")
                layout = SimpleNamespace(raw_to_physical=(0,) * 192)
                context = {"inputs":{"parent.nnue":experiment.ref(net),"reference.exe":experiment.ref(exe)}, "corpus_sha256":"a"*64}
                data = {split:[{"position_id":hashlib.sha256(f"{split}-{i}".encode()).hexdigest(), "fen":FEN, "family":f"{split}-{i % 6}"} for i in range(count)]
                        for split, count in (("optimization",18),("holdout",6))}
                vectors, calls = {}, []

                def fake_editor(parent, deltas, output, mode):
                    is_identity = not any(deltas)
                    vectors[output.name] = list(deltas)
                    original = experiment.ref(parent)["sha256"]
                    digest = original
                    if not is_identity:
                        output.write_text("synthetic " + mode)
                        digest = experiment.ref(output)["sha256"]
                    return {"mode":mode,"identity":is_identity,"output_written":not is_identity,"deltas":list(deltas),
                            "parent_sha256":original,"inverse_sha256":original,"candidate_sha256":digest,"raw_to_physical":list(layout.raw_to_physical)}

                def measure(network, rows, stem, searches):
                    calls.append(stem)
                    freeze = evidence / "FROZEN-EDIT.json"
                    if stem.startswith("holdout") or stem == "train-frozen-candidate":
                        self.assertTrue(freeze.is_file())
                        frozen = json.loads(freeze.read_bytes())
                        self.assertEqual(frozen["edit"]["candidate_sha256"], experiment.ref(work / "candidate.nnue")["sha256"])
                        self.assertFalse((work / "probe.nnue").exists())
                    else:
                        self.assertFalse(freeze.exists())
                    vec = vectors.get(network.name,[0]*92)
                    outputs = []
                    for row in rows:
                        is_holdout = row["role"] == "holdout"
                        cp = 6 if stem=="train-probe" else 12 if "frozen-candidate" in stem else 0
                        raw = [experiment.head_bias.raw_from_forwarded(d) for d in vec]
                        tables = [{"index":i,"used":i==0,"printed_value":printed(value,experiment._material(FEN))} for i,value in enumerate(raw)]
                        out = {**row,"static_cp":cp,"raw_score":raw[0],"used_bucket":0,"buckets":tables}
                        if searches:
                            out.update(parent_static_cp=cp, deep_cp=-20 if is_holdout else 0 if identity else 20, stable=True, reason="stable",
                                       shallow_search={"requested_depth":9}, deep_search={"requested_depth":13})
                        outputs.append(out)
                    rows_ref = experiment.save(evidence / (stem+".rows.json"), outputs)
                    experiment.save(evidence / (stem+".record.json"), {"status":"complete","returncode":0,"forced_cleanup":False,
                        "reader_errors":[],"log_readers_closed":True,"teacher":searches,"requested_depths":[9,13] if searches else [],
                        "requested_rows":len(rows),"completed_rows":len(rows),"inputs_before":{},"inputs_after":{},"files":{"rows":rows_ref}})
                    return outputs

                def native(searches):
                    return lambda exe, net, rows, output, wd, stem: measure(net, rows, stem, searches)

                with patch.object(experiment.bias_patch_set,"write_bias_patch_set",side_effect=fake_editor), \
                     patch.object(experiment.teacher,"collect_labels",side_effect=native(True)), \
                     patch.object(experiment.teacher,"static_evals",side_effect=native(False)), \
                     patch.object(experiment,"qualification",return_value={"bench_signatures":[12,12],"perft4":197281}) as qualify:
                    result = experiment.study(exe, net, layout, data, evidence, work, context)
                self.assertEqual(result["candidate_qualified_for_games"], not identity)
                self.assertEqual((evidence/"FROZEN-EDIT.json").is_file(), True)
                self.assertFalse((evidence/"probe.nnue").exists())
                if identity:
                    self.assertEqual(calls, ["train-original","train-probe"])
                    self.assertNotIn("candidate_sha256", result)
                    self.assertFalse((work/"candidate.nnue").exists())
                    qualify.assert_not_called()
                else:
                    self.assertEqual(calls, ["train-original","train-probe","train-frozen-candidate","holdout-original","holdout-frozen-candidate"])
                    self.assertEqual(result["candidate_sha256"], experiment.ref(work/"candidate.nnue")["sha256"])
                    held = json.loads((evidence/"HOLDOUT.json").read_bytes())
                    self.assertLess(held["equal_family_improvement_cp"]["numerator"], 0)
                    qualify.assert_called_once()


if __name__ == "__main__":
    unittest.main()
