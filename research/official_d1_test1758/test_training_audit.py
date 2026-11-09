from decimal import Decimal
import hashlib
import unittest
import chess
import corpus
import training_audit


class MemoryBundle:
    def __init__(self,bodies,documents):
        self.bodies=bodies;self.documents=documents;self.transports={}
    def read(self,name):return self.bodies[name]
    def json(self,name):return self.documents[name]


def parity_fixture(cp="0.12"):
    fen=chess.STARTING_FEN
    text=("uci\nsetoption name EvalFile value parent.nnue\nisready\nposition fen "+fen+"\neval\nisready\nquit\n").encode()
    plan=corpus.policy()
    request={"expected":[{"source_root_index":0,"head":31,"raw_nnue_i32":123,"static_cp_white":12}],
             "stdin_sha256":corpus.sha(text),"eval_file":"parent.nnue",
             "engine_sha256":plan["student"]["engine_sha256"],"eval_file_sha256":plan["student"]["network_sha256"]}
    accepted={"returncode":0,"inputs_rehashed_before_after":True,"rows":1,
              "engine":{"sha256":request["engine_sha256"]},"network":{"sha256":request["eval_file_sha256"]}}
    output=("uciok\nreadyok\ninfo string NNUE evaluation using parent.nnue (132MiB)\n"
            "NNUE evaluation +123 (side to move, internal units)\n"
            "Final evaluation "+cp+" (white side) [with scaled NNUE, ...]\nreadyok\n").encode()
    bundle=MemoryBundle({"p.stdin":text,"p.stdout":output,"p.stderr":b""},
                        {"p.request.json":request,"p.accepted.json":accepted})
    cache={0:{"root_fen6":fen,"head":31,"base_static_cp_stm":12,"side_to_move":"w","base_raw_nnue_i32":123}}
    return bundle,cache


class TranscriptAuditContracts(unittest.TestCase):
    def test_actual_plus_signed_raw_and_exact_cp(self):
        bundle,cache=parity_fixture()
        self.assertEqual(training_audit.parity_replay(bundle,"p",cache),[0])

    def test_one_cp_mismatch_refused(self):
        bundle,cache=parity_fixture("0.13")
        with self.assertRaises(ValueError):
            training_audit.parity_replay(bundle,"p",cache)

    def test_native_fen_join_mismatch_refused(self):
        bundle,cache=parity_fixture()
        cache[0]["root_fen6"]=cache[0]["root_fen6"].replace(" 0 1"," 1 1")
        with self.assertRaises(ValueError):
            training_audit.parity_replay(bundle,"p",cache)

    def test_unknown_native_source_index_refused(self):
        # A cache record may not replace the native composite child identity.
        bundle,cache=parity_fixture()
        bundle.documents["p.request.json"]["expected"][0]["source_root_index"]=1
        with self.assertRaises(ValueError):
            training_audit.parity_replay(bundle,"p",cache)


if __name__=="__main__":
    unittest.main()
