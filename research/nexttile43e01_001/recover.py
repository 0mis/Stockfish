"""Reviewed premeasurement path-only recovery; frozen driver/plan unchanged.

The failed first attempt reached no warmup or timing block. Scope only the
fresh native working parent by engine filename; retain original capture names,
commands, clocks, ordering, source pins and every frozen driver check.
"""
from pathlib import Path
import sys
import run as pilot
from qualify_common import dump

ORIGINAL_NATIVE=pilot.native


def namespaced_native(binary,commands,work,evidence,name,cpu,timeout=90):
    namespace=work/binary.name
    namespace.mkdir(exist_ok=True)
    return ORIGINAL_NATIVE(binary,commands,namespace,evidence,name,cpu,timeout)


def main():
    pilot.native=namespaced_native
    try:pilot.main()
    except BaseException as error:
        if '--root' in sys.argv:
            target=Path(sys.argv[sys.argv.index('--root')+1]).resolve()/'evidence'
            if target.exists() and not (target/'FAILURE.json').exists():dump(target/'FAILURE.json',{'status':'INCOMPLETE','exception':type(error).__name__,'error':str(error),'automatic_retry':False,'games_dispatched':0})
        raise


if __name__=='__main__':main()
