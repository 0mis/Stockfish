"""Mocked process regression; no actual native process is launched."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import recover


class NamespaceRecoveryTests(unittest.TestCase):
    def test_same_qualification_stems_use_disjoint_engine_namespaces(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);work=root/'work';work.mkdir();observed=[]
            def fake_run(argv,**kwargs):
                observed.append((argv,kwargs['cwd'],kwargs['input']));kwargs['stdout'].write(b'only synthetic output\n');return SimpleNamespace(returncode=0)
            with patch.object(recover.pilot.subprocess,'run',side_effect=fake_run):
                for side in 'AB':
                    evidence=root/side;evidence.mkdir()
                    for stem in ('qualify-perft','qualify-bench','warmup'):
                        recover.namespaced_native(root/'bin'/(side+'-stockfish'),'fixture\n',work,evidence,stem,0)
                self.assertEqual(len(observed),6)
                self.assertEqual({path.parent.name for argv,path,stdin in observed},{'A-stockfish','B-stockfish'})
                self.assertTrue(all(stdin==b'fixture\n' for argv,path,stdin in observed))
                with self.assertRaises(FileExistsError):
                    recover.namespaced_native(root/'bin/A-stockfish','fixture\n',work,root/'A','qualify-perft',0)
                self.assertEqual(len(observed),6)

    def test_original_native_and_frozen_plan_are_unmodified(self):
        import hashlib,json
        here=Path(__file__).parent;raw=(here/'plan.json').read_bytes();plan=json.loads(raw)
        self.assertEqual(hashlib.sha256(raw).hexdigest(),'201bc0b8c03fdf4cb837a87b4a8c6ca0dfeefe979a220d0e093eed615ce657dc')
        for name,digest in plan['code_sha256'].items():self.assertEqual(hashlib.sha256((here/name).read_bytes()).hexdigest(),digest)
        self.assertIs(recover.ORIGINAL_NATIVE,recover.pilot.native)


if __name__=='__main__':unittest.main()
