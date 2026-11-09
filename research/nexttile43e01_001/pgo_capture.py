"""Preserve the exact standard PGO bench before upstream profileclean removes it."""
import json,os,subprocess,sys,time
from pathlib import Path
from qualify_common import dump,file_ref,require

def main():
 binary,prefix=map(Path,sys.argv[1:]);safe={'PATH','HOME','LANG','LC_ALL','TMPDIR','TEMP','TMP','LD_LIBRARY_PATH'}
 env={k:v for k,v in os.environ.items() if k in safe};env['LC_ALL']='C';started=time.monotonic()
 run=subprocess.run([str(binary.resolve()),'bench'],capture_output=True,timeout=180,env=env)
 prefix.with_suffix('.stdout').write_bytes(run.stdout);prefix.with_suffix('.stderr').write_bytes(run.stderr)
 dump(prefix.with_suffix('.process.json'),{'argv':['./stockfish','bench'],'returncode':run.returncode,'elapsed_seconds':time.monotonic()-started,'automatic_retry':False,'instrumented_binary':file_ref(binary)})
 sys.stdout.buffer.write(run.stdout);sys.stdout.buffer.write(run.stderr);require(run.returncode==0,'Native PGO default bench failed')

if __name__=='__main__':main()
