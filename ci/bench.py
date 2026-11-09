import re
from common import require,sha,canonical

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


