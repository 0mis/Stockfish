import copy,hashlib,struct,tempfile,unittest
from pathlib import Path
from qualify_common import embedded_elf,parse_bench,parse_perft,source_pair

def fixture_bench():
 stdout='\n'.join('info depth 1 score cp 0 nodes 1 nps 1 time 1 pv e2e4\nbestmove e2e4' for _ in range(51))
 stderr='\n'.join('Position: '+str(i)+'/51 (synthetic)' for i in range(1,52))+'\nTotal time (ms) : 1000\nNodes searched : 51\nNodes/second : 51\n'
 return stdout,stderr

class PureQualificationTests(unittest.TestCase):
 def test_complete_default_bench(self):
  stdout,stderr=fixture_bench();result=parse_bench(stdout,stderr);self.assertEqual(result['nodes'],51)
  self.assertEqual(result['search_signature_sha256'],parse_bench(stdout.replace('nps 1 time 1','nps 500 time 90'),stderr)['search_signature_sha256'])
 def test_incomplete_bench_rejected(self):
  stdout,stderr=fixture_bench()
  with self.assertRaises(ValueError):parse_bench(stdout.rsplit('bestmove',1)[0],stderr)
 def test_inconsistent_node_summary_rejected(self):
  stdout,stderr=fixture_bench()
  with self.assertRaises(ValueError):parse_bench(stdout,stderr.replace('Nodes searched : 51','Nodes searched : 52'))
 def test_changed_pv_changes_functional_signature(self):
  stdout,stderr=fixture_bench();self.assertNotEqual(parse_bench(stdout,stderr)['search_signature_sha256'],parse_bench(stdout.replace('pv e2e4','pv d2d4',1),stderr)['search_signature_sha256'])
 def test_source_single_default_path_only(self):
  before={'src/evaluate.h':{'mode':'100644','sha':'a'},'src/search.cpp':{'mode':'100644','sha':'b'}}
  after=copy.deepcopy(before);after['src/evaluate.h']['sha']='c';source_pair(before,after)
  after['src/search.cpp']['sha']='d'
  with self.assertRaises(ValueError):source_pair(before,after)
 def test_source_mode_change_rejected(self):
  before={'src/evaluate.h':{'mode':'100644','sha':'a'}};after={'src/evaluate.h':{'mode':'100755','sha':'b'}}
  with self.assertRaises(ValueError):source_pair(before,after)
 def test_elf_exact_embedded_net_and_size(self):
  data=b'only synthetic bytes';size=len(data);vaddr=0x400000;offset=0x200
  raw=bytearray(0x300);ident=b'\x7fELF\x02\x01\x01'+bytes(9)
  raw[:64]=struct.pack('<16sHHIQQQIHHHHHH',ident,2,62,1,0,64,0,0,64,56,1,0,0,0)
  raw[64:120]=struct.pack('<IIQQQQQQ',1,4,offset,vaddr,vaddr,0x100,0x100,0x1000)
  raw[offset:offset+size]=data;raw[offset+size:offset+size+4]=struct.pack('<I',size)
  nm=f'{vaddr:x} R gEmbeddedNNUEData\n{vaddr+size:x} R gEmbeddedNNUEEnd\n{vaddr+size:x} R gEmbeddedNNUESize\n'
  with tempfile.TemporaryDirectory(prefix='net-only-pure-') as directory:
   p=Path(directory)/'synthetic.elf';p.write_bytes(raw);net={'bytes':size,'sha256':hashlib.sha256(data).hexdigest()}
   self.assertEqual(embedded_elf(p,nm,net)['sha256'],net['sha256'])
   with self.assertRaises(ValueError):embedded_elf(p,nm,{**net,'sha256':'0'*64})
   with self.assertRaises(ValueError):embedded_elf(p,nm,{**net,'bytes':size+1})
 def test_perft_total_and_divide_checked(self):
  moves=[a+b for a in ('a2','b2','c2','d2','e2','f2','g2','h2') for b in (a[0]+'3',a[0]+'4')]+['b1a3','b1c3','g1f3','g1h3']
  output='\n'.join(move+': '+str(197262 if i==0 else 1) for i,move in enumerate(moves))+'\nNodes searched: 197281\n'
  self.assertEqual(parse_perft(output)['nodes'],197281)
  with self.assertRaises(ValueError):parse_perft(output.replace('197281','197282'))

if __name__=='__main__':unittest.main()
