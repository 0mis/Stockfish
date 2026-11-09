"""Reproduce the fixed 0mis test1757 readout-bias transfer onto Shawn Xu test1758.

This is a byte-only translation of the published serialized-layout/bias editor,
not training or parameter selection. Original network credit: Shawn Xu (xu-shawn),
test1758, CC0-1.0. Fixed readout deltas: 0mis AI-assisted research on test1757.
"""
import hashlib,json,struct
from pathlib import Path

PARENT='68752b755c93e419e714829c4aac1bbd66e9509a96cf175be28a00164b6d7f21'
CANDIDATE='f92ca6117ec5ae7b0ca8c9242165aee06bd2417183daa4393b11f2df409e005d'
SIZE=138360528
VERSION,ROUTE_TAG,FT_TAG,STACK_TAG=0x6A448AFA,0x5A7AB1E5,0xA3060B95,0x63337116
ROUTE_SHA='166ea64eb3e4da682ba3394b0a7f68de6ca6e01daae472f8c85fbc5ad7e27dd7'
START=135121392
STRIDE=35208

def require(ok,message):
 if not ok:raise ValueError(message)
def sha(raw):return hashlib.sha256(raw).hexdigest()
def apply_edits(data,edits):
 out=bytearray(data);before=[];allowed=set()
 for offset,old,new in edits:
  require(offset%4==0 and 0<=offset<=len(data)-4 and not allowed.intersection(range(offset,offset+4)),'Bad edit interval')
  require(struct.unpack_from('<i',data,offset)[0]==old and -(1<<31)<=new<(1<<31),'Before/after int32 guard')
  struct.pack_into('<i',out,offset,new);before.append((offset,old));allowed.update(range(offset,offset+4))
 changed=[i for i,(a,b) in enumerate(zip(data,out)) if a!=b]
 require(set(changed)<=allowed,'Undeclared byte changed')
 inverse=bytearray(out)
 for offset,old in before:struct.pack_into('<i',inverse,offset,old)
 require(inverse==data,'Inverse differs')
 return out,changed

def reproduce(parent,output,recipe,binding):
 raw=Path(parent).read_bytes();require(len(raw)==SIZE and sha(raw)==PARENT,'Wrong exact test1758 donor')
 require(struct.unpack_from('<3I',raw)==(VERSION,ROUTE_TAG^FT_TAG^STACK_TAG,84),'NNUE header differs')
 require(struct.unpack_from('<3I',raw,96)==(ROUTE_TAG,192,92),'Routing header differs')
 fallback=struct.unpack_from('<192H',raw,108);roots=[i for i,f in enumerate(fallback) if i==f]
 require(len(roots)==92 and sha(raw[108:492])==ROUTE_SHA,'Routing checksum differs')
 indices={root:i for i,root in enumerate(roots)};require(all(f in indices for f in fallback),'Multihop/unknown route')
 mapping=[indices[f] for f in fallback]
 require(struct.unpack_from('<I',raw,492)[0]==FT_TAG and START+92*STRIDE==len(raw),'FT/dense EOF geometry differs')
 for i in range(92):require(struct.unpack_from('<I',raw,START+i*STRIDE)[0]==STACK_TAG,'Stored stack tag differs')
 heads=recipe['heads'];require(len(heads)==92 and [x['physical_head'] for x in heads]==list(range(92)),'Published physical heads differ')
 actual=[]
 for i,row in enumerate(heads):
  offset=START+(i+1)*STRIDE-132;routes=[j for j,p in enumerate(mapping) if p==i]
  require(row['bias_offset']==offset and row['raw_routes']==routes,'Published byte/route map differs')
  if row['delta']:
   old=struct.unpack_from('<i',raw,offset)[0];actual.append({'physical_head':i,'bias_offset':offset,'bias_before':old,'bias_after':old+row['delta'],'delta':row['delta'],'raw_routes':routes})
 expected=[{k:r[k] for k in actual[0]} for r in binding['details']]
 require(actual==expected and len(actual)==9,'Frozen transfer recipe differs')
 out,changed=apply_edits(raw,[(r['bias_offset'],r['bias_before'],r['bias_after']) for r in actual])
 require(len(changed)==11 and sha(out)==CANDIDATE,'Exact candidate/changed byte hash differs')
 output=Path(output);require(not output.exists(),'Candidate output already exists');output.write_bytes(out)
 return {'schema':'public-test1758-fixed-bias-transfer/1','parent_sha256':PARENT,'candidate_sha256':CANDIDATE,'bytes':SIZE,
         'changed_heads':9,'changed_bytes':len(changed),'inverse_sha256':sha(raw),'route_sha256':ROUTE_SHA,
         'donor_credit':'Shawn Xu (xu-shawn), test1758, CC0-1.0','delta_credit':'0mis AI-assisted test1757 readout study; fixed transfer, no new fit'}
