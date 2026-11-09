"""Small synthetic tests only. No archive, engine, or private inputs."""
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
import chess
import chess.pgn
import discover as d


class DiscoveryTests(unittest.TestCase):
    def plan(self):
        return json.loads((Path(__file__).parent/'plan.json').read_text())

    def test_canonical_legal_ep_and_clocks(self):
        board=chess.Board();board.push_uci('e2e4')
        self.assertEqual(d.canonical(board).split()[-1],'-')
        alternate=chess.Board(board.fen(en_passant='fen'));alternate.halfmove_clock=83;alternate.fullmove_number=92
        self.assertEqual(d.position_id(board),d.position_id(alternate))
        capture=chess.Board('4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1')
        self.assertEqual(d.canonical(capture).split()[-1],'d6')
        self.assertEqual(d.position_id(capture),hashlib.sha256(d.canonical(capture).encode('ascii')).hexdigest())

    def test_visitor_discards_labels_comments_and_variations(self):
        a='[White "synthetic-a"]\n[Result "1-0"]\n\n1. e4 {[%eval 9.99]} e5 (1... c5) 2. Nf3 Nc6 1-0\n'
        b='[White "synthetic-b"]\n[Result "0-1"]\n\n1. e4 e5 2. Nf3 Nc6 0-1\n'
        one=chess.pgn.read_game(io.StringIO(a),Visitor=d.MainlineOnly)
        two=chess.pgn.read_game(io.StringIO(b),Visitor=d.MainlineOnly)
        self.assertEqual(one,two)
        self.assertEqual(one['moves'],['e2e4','e7e5','g1f3','b8c6'])
        self.assertNotIn('synthetic',json.dumps(one))

    def test_variant_setup_null_and_illegal_rejected(self):
        for text in ('[Variant "Atomic"]\n\n1. e4 *\n','[SetUp "1"]\n\n1. e4 *\n','[FEN "4k3/8/8/8/8/8/8/4K3 w - - 0 1"]\n\n*\n'):
            self.assertTrue(chess.pgn.read_game(io.StringIO(text),Visitor=d.MainlineOnly)['metadata_rejected'])
        for text in ('1. -- *\n','1. e5 *\n'):
            self.assertTrue(chess.pgn.read_game(io.StringIO(text),Visitor=d.MainlineOnly)['errors'])

    def test_first_records_have_no_quality_replacements(self):
        plan=self.plan();plan['bounds']['source_records']=2;plan['quality'].update(minimum_plies=8,candidate_plies=[4,8]);plan['family']['anchor_ply']=2
        plan['quality'].update(minimum_pieces=2,minimum_nonpawn_nonking=0,minimum_pawns=0,minimum_queens=0)
        valid='1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 *\n\n'
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'synthetic.pgn';path.write_text('[Variant "Atomic"]\n\n1. e4 *\n\n'+valid+valid)
            counts,families,owners,origins=d.read_census(path,plan,time.monotonic())
        self.assertEqual(counts['source_records'],2)
        self.assertEqual(counts['nonordinary_or_variant_records'],1)
        self.assertEqual(counts['unique_quality_controlled_games'],1)
        self.assertEqual(origins[0]['source_record'],2)
        self.assertEqual(len(families),1)
        self.assertEqual(len(owners),2)

    def test_child_bridge_component_and_complete_children(self):
        plan=self.plan();board=chess.Board();child=board.copy();child.push_uci('e2e4')
        roots=[d.position_id(board),d.position_id(child)];anchors=['1'*64,'2'*64]
        def row(board,root,family):
            return {'fen':board.fen(en_passant='legal'),'root_id':root,'family_id':family,'game_id':'a'*64,'source_record':1,'ply':0,'prefix_uci':[]}
        families={anchors[0]:{roots[0]:row(board,roots[0],anchors[0])},anchors[1]:{roots[1]:row(child,roots[1],anchors[1])}}
        candidates,components,counts,mapping=d.guarded_components(families,dict(zip(roots,anchors)),plan,time.monotonic())
        self.assertEqual(len(components),1)
        self.assertEqual(mapping[anchors[0]],mapping[anchors[1]])
        first=next(r for r in candidates if r['root_id']==roots[0])
        self.assertEqual(len(first['children']),20)
        self.assertIn(roots[1],first['children'])
        self.assertEqual(counts['generated_child_boards'],40)

    def test_union_repeated_transitive_find(self):
        union=d.Union('abcdef')
        union.join('f','e');union.join('d','c');union.join('e','d');union.join('a','f')
        for _ in range(4):self.assertEqual({union.find(x) for x in 'acdef'},{'a'})
        self.assertEqual(union.find('b'),'b')

    def test_component_split_and_round_robin_are_deterministic(self):
        plan=self.plan();components={}
        for number in range(80):
            cid=hashlib.sha256(str(number).encode()).hexdigest()
            components[cid]={str(i):{'root_id':str(i),'component_id':cid} for i in range(10)}
        plan['partitions']['quotas']={key:13 for key in ('TRAIN','DEV','FINAL','SCREEN')}
        one,counts=d.select(components,plan);two,_=d.select(dict(reversed(list(components.items()))),plan)
        self.assertEqual(one,two)
        for split,rows in one.items():
            self.assertEqual(len(rows),13)
            self.assertTrue(all(d.split_for(row['component_id'],plan['seed'])==split for row in rows))
            self.assertLessEqual(max(sum(r['component_id']==row['component_id'] for r in rows) for row in rows),8)

    def test_id_stream_exact_sorted_unique_framing(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'parents.ids';meta=d.ids_file(path,{'f'*64,'0'*64,'a'*64})
            raw=path.read_bytes()
            self.assertEqual(raw,b'0'*64+b'\n'+b'a'*64+b'\n'+b'f'*64+b'\n')
            self.assertEqual(meta['bytes'],195);self.assertEqual(meta['count'],3)
            self.assertEqual(meta['sha256'],hashlib.sha256(raw).hexdigest())
            with self.assertRaises(FileExistsError):d.ids_file(path,set())
            with self.assertRaises(ValueError):d.ids_file(Path(directory)/'bad.ids',{'A'*64})


if __name__=='__main__':unittest.main()
