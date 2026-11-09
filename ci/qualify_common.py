"""Pure receipt parsers for the public net-only build qualification."""
import hashlib,json,re,struct
from pathlib import Path

def require(ok,message):
 if not ok:raise ValueError(message)

def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def sha(raw):return hashlib.sha256(raw).hexdigest()
def file_ref(path):
 h=hashlib.sha256();size=0
 with Path(path).open('rb') as f:
  while block:=f.read(1024*1024):h.update(block);size+=len(block)
 return {'bytes':size,'sha256':h.hexdigest()}
def dump(path,value):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 with path.open('x',encoding='utf-8',newline='\n') as f:json.dump(value,f,sort_keys=True,indent=2,allow_nan=False);f.write('\n')

def parse_bench(stdout,stderr):
 summaries=re.findall(r'Total time \(ms\)\s*:\s*(\d+)\s+Nodes searched\s*:\s*(\d+)\s+Nodes/second\s*:\s*(\d+)',stderr)
 markers=[(int(a),int(b)) for a,b in re.findall(r'^Position:\s*(\d+)/(\d+)\s',stderr,re.M)]
 require(len(summaries)==1 and markers==[(i,51) for i in range(1,52)],'Default bench is not the complete canonical51 workload')
 searches=[];last=None
 for line in stdout.splitlines():
  if line.startswith('info depth ') and ' score ' in line:last=re.sub(r'\s+(?:nps|time)\s+\d+','',line.strip())
  elif line.startswith('bestmove '):
   require(last is not None,'Native bestmove lacks final search info');node=re.search(r'\bnodes (\d+)\b',last)
   require(node is not None or last.startswith('info depth 0 score '),'Native final search info lacks nodes')
   searches.append({'final_info':last,'bestmove':line.strip(),'nodes':int(node[1]) if node else 0});last=None
 require(len(searches)==51 and last is None,'Default bench bestmove records are incomplete')
 elapsed,nodes,nps=map(int,summaries[0]);require(elapsed>0 and nodes>0 and sum(x['nodes'] for x in searches)==nodes,'Default bench node sum differs')
 require(nps==1000*nodes//elapsed,'Native bench NPS arithmetic differs')
 return {'nodes':nodes,'elapsed_ms':elapsed,'reported_nps':nps,'search_signature_sha256':sha(canonical(searches)),'positions':searches}

def parse_perft(stdout):
 nodes=re.findall(r'^Nodes searched:\s*(\d+)\s*$',stdout,re.M);divide={}
 for move,count in re.findall(r'^([a-h][1-8][a-h][1-8][qrbn]?):\s*(\d+)\s*$',stdout,re.M):
  require(move not in divide,'Duplicate native perft divide');divide[move]=int(count)
 require(nodes==['197281'] and len(divide)==20 and sum(divide.values())==197281,'Native startpos perft4 differs')
 return {'nodes':197281,'divide':dict(sorted(divide.items())),'divide_sha256':sha(canonical(dict(sorted(divide.items()))))}

def source_pair(before,after):
 require(set(before)==set(after),'Tracked source paths differ')
 changed=[x for x in before if before[x]!=after[x]]
 require(changed==['src/evaluate.h'],'Source differs beyond the single evaluate.h default')
 require(all(before[x]['mode']==after[x]['mode'] for x in before),'Tracked source modes differ')

def embedded_elf(path,nm_stdout,net_ref):
 symbols={}
 for addr,name in re.findall(r'^([0-9a-fA-F]+)\s+\w\s+(gEmbeddedNNUE(?:Data|End|Size))\s*$',nm_stdout,re.M):symbols[name]=int(addr,16)
 require(set(symbols)=={'gEmbeddedNNUEData','gEmbeddedNNUEEnd','gEmbeddedNNUESize'},'Native ELF embedding symbols are absent')
 with Path(path).open('rb') as f:
  header=f.read(64);require(header[:7]==b'\x7fELF\x02\x01\x01','Expected little-endian ELF64')
  fields=struct.unpack('<16sHHIQQQIHHHHHH',header);require(fields[2]==62,'Expected x86-64 ELF')
  phoff,phentsize,phnum=fields[5],fields[9],fields[10];require(phentsize==56 and 0<phnum<128,'Unexpected ELF program headers')
  segments=[]
  for i in range(phnum):
   f.seek(phoff+i*phentsize);ph=struct.unpack('<IIQQQQQQ',f.read(56))
   if ph[0]==1:segments.append((ph[3],ph[2],ph[5]))
  def offset(addr,size):
   matches=[off+addr-va for va,off,length in segments if va<=addr and addr+size<=va+length]
   require(len(matches)==1,'Embedded symbols do not map uniquely to file bytes');return matches[0]
  size=symbols['gEmbeddedNNUEEnd']-symbols['gEmbeddedNNUEData'];require(size==net_ref['bytes'],'Embedded net length differs from the actual public download')
  start=offset(symbols['gEmbeddedNNUEData'],size);f.seek(offset(symbols['gEmbeddedNNUESize'],4));require(struct.unpack('<I',f.read(4))[0]==size,'Embedded size marker differs')
  f.seek(start);h=hashlib.sha256();remaining=size
  while remaining:
   block=f.read(min(1024*1024,remaining));require(block,'Embedded net truncated');h.update(block);remaining-=len(block)
 require(h.hexdigest()==net_ref['sha256'],'Exact embedded network SHA differs')
 return {'file_offset':start,'bytes':size,'sha256':h.hexdigest(),'symbols':symbols,'elf_machine':62,'full_raw_network_bytes_verified':True}
