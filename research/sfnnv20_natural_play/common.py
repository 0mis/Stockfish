"""Current SFNNv20 trial metadata/admission helpers; no native work on import."""
import hashlib,json,os
from pathlib import Path

HERE=Path(__file__).resolve().parent
PLAN_SHA="2b336a8321196ea580d498a005b35f488e18a958aa3aa2ed95f1bdbba1d0159a"
BRANCH="research/sfnnv20-natural-play-calibration-001"
SOURCE="43e01e07210948e1130dc687c065d7a3742b2793"
TREE="97c77114c970b5573e33db9c1e90fbef943a6d42"
PARENT="18cd32e11a9cac7c595bc6ebf1301088da8e1f2c7b31efda88dbc74c71f5d2cf"
TEACHER="68752b755c93e419e714829c4aac1bbd66e9509a96cf175be28a00164b6d7f21"


def need(ok,why):
    if not ok:raise ValueError(why)


def sha(raw):return hashlib.sha256(raw).hexdigest()
def canonical(obj):return json.dumps(obj,sort_keys=True,separators=(",",":"),allow_nan=False).encode()


def file_sha(path):
    with Path(path).open("rb") as stream:return hashlib.file_digest(stream,"sha256").hexdigest()


def ref(path,base=None):
    path=Path(path)
    return {"path":path.name if base is None else path.relative_to(base).as_posix(),
            "bytes":path.stat().st_size,"sha256":file_sha(path)}


def put(path,obj):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("xb") as target:target.write(canonical(obj)+b"\n")


def policy():
    body=(HERE/"plan.json").read_bytes()
    need(sha(body)==PLAN_SHA,"Frozen natural-play method changed")
    return json.loads(body)


def hosted(mode):
    need(os.environ.get("GITHUB_ACTIONS")=="true" and os.environ.get("GITHUB_REPOSITORY")=="0mis/Stockfish"
         and os.environ.get("GITHUB_REF")=="refs/heads/"+BRANCH,"Exact isolated standard hosted branch required")
    plan=policy()
    if mode!="qualification":
        grant=json.loads((HERE/"ROOT-CLEARANCE.json").read_bytes())
        need(grant==plan["technical_admission"],"Exact Root technical admission changed")
        need(grant["status"]=="GRANTED_FOR_EXACT_PUBLIC_INPUT_COMMITMENTS"
             and grant["source_commit"]==SOURCE and grant["student_network_sha256"]==PARENT,
             "Root source/network clearance missing")
        need(grant["corpus_generator_commit"]=="87e0d14e8c8e1c0510865acef2b1ebf04b00f816"
             and grant["corpus_result_sha256"]=="120d2f683f89c5df6a596092c1de4f937ddc0e95cb29d0c44fa972d4cc6d7dbf"
             and grant["corpus_audit_sha256"]=="b8733a382e25e977ac610f2f824bc1cb2e2eb2d0abac9d0cb9611dc08084b76e",
             "Root exact corpus clearance missing")
    return plan


def raw_route(board):
    import chess
    band=(board.occupied.bit_count()-1)//4
    queens=2*bool(board.pieces(chess.QUEEN,board.turn))+bool(board.pieces(chess.QUEEN,not board.turn))
    left=sum(chess.BB_FILES[:4]);pawns=board.pawns
    layout=2 if not pawns else 0 if pawns&left and pawns&~left else 1
    officers=board.occupied&~(board.pawns|board.kings)
    many=max((officers&board.occupied_co[color]).bit_count() for color in chess.COLORS)>=2
    return ((band*4+queens)*3+layout)*2+int(many)


def position_id(board):
    return sha(" ".join(board.fen(en_passant="legal").split()[:4]).encode())
