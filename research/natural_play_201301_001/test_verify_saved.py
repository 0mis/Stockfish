"""Tiny synthetic verifier checks; never fetch real archives locally."""
import hashlib
from pathlib import Path
import tempfile
import unittest
import chess
import verify_saved as v


class SavedAuditTests(unittest.TestCase):
    def test_canonical_id_uses_legal_ep_and_omits_clock(self):
        board=chess.Board();board.push_uci('e2e4')
        expected=hashlib.sha256(' '.join(board.fen(en_passant='legal').split()[:4]).encode()).hexdigest()
        self.assertEqual(v.pid(board),expected)
        board.halfmove_clock=99;board.fullmove_number=100
        self.assertEqual(v.pid(board),expected)

    def test_stream_hash_order_and_eof(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'ids';raw=b'0'*64+b'\n'+b'f'*64+b'\n';path.write_bytes(raw)
            ref={'bytes':len(raw),'count':2,'sha256':v.sha(raw)}
            self.assertEqual(v.read_ids(path,ref),{'0'*64,'f'*64})
            for malformed in (raw[:-1],raw[::-1],b'0'*64+b'\n'+b'0'*64+b'\n'):
                path.write_bytes(malformed)
                with self.assertRaises(ValueError):v.read_ids(path,{'bytes':len(malformed),'count':2,'sha256':v.sha(malformed)})

    def test_hash_mismatch_and_component_split(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'receipt';path.write_bytes(b'fixed')
            with self.assertRaises(ValueError):v.check_ref(path,{'bytes':5,'sha256':'0'*64})
        cid='a'*64
        self.assertEqual(v.split_of('fixed-seed',cid),v.split_of('fixed-seed',cid))
        self.assertIn(v.split_of('fixed-seed',cid),{'TRAIN','DEV','FINAL','SCREEN'})


if __name__=='__main__':unittest.main()
