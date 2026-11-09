"""Fetch and verify the four public inputs for the Windows layout experiment."""

from __future__ import annotations

import hashlib
import json
import mmap
from dataclasses import dataclass
from pathlib import Path
import stat
import struct
import subprocess
import tempfile


SOURCE_COMMIT = "6831500a4c6b22d64109a38bb0dc5bbf5b6f8fe0"
GAMES_REPOSITORY = "0mis/stockfish-weight-space-lab"
RUN_ID = 42543250921
ARTIFACT_NAME = "test1757-prefetch-windows-bmi2-49672d7b3c2b0cb544f4bf7cc70515249fe047e3"
EXE_MEMBER = "stockfish-windows-x86-64-bmi2.exe"
CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class PinnedFile:
    name: str
    size: int
    sha256: str

    def verify(self, size: int, digest: str) -> None:
        if size != self.size or digest != self.sha256:
            raise ValueError(f"{self.name} has unexpected length or SHA-256")


REFERENCE = PinnedFile(
    "reference.exe", 142322823, "12a8e7bfdd9b1189b4350bd9ebef3501be1e89bd5fa41dcb56921f85ba23869a"
)
NETWORK = PinnedFile(
    "parent.nnue", 138360528, "18cd32e11a9cac7c595bc6ebf1301088da8e1f2c7b31efda88dbc74c71f5d2cf"
)
NETWORK_HEADER = struct.pack("<III", 0x6A448AFA, 0x9A4FCB66, 84)
PREFIX_SIZE = 512
PREFIX_SHA256 = "9f9a9166181aaaf78b61d6a5d2fac88b18d4169e85b6f94c50c9573d1fecd933"
PGNS = (
    (PinnedFile("edited.pgn", 918097, "8bd56ec5f58ef65881ed5ae2d7ef6683683893c34c10b94b3bb0f2d5fc83dde9"),
     "results/test1757-edited-screen/games-utc.pgn"),
    (PinnedFile("parent.pgn", 935475, "1acfc5b48a1c20aa43947fbde0ec0ea6c413f8bf76a7265dbe3d8e47c711de5d"),
     "results/test1757-parent-screen/games-utc.pgn"),
)


def transfer(source, destination, limit: int) -> tuple[int, str]:
    size = 0
    digest = hashlib.sha256()
    while chunk := source.read(min(CHUNK_BYTES, limit - size + 1)):
        size += len(chunk)
        if size > limit:
            raise ValueError("Public input exceeded its byte limit")
        destination.write(chunk)
        digest.update(chunk)
    return size, digest.hexdigest()


def find_network_offset(image: mmap.mmap) -> int:
    offset = None
    position = image.find(NETWORK_HEADER)
    while position >= 0:
        prefix = image[position:position + PREFIX_SIZE]
        if len(prefix) == PREFIX_SIZE and hashlib.sha256(prefix).hexdigest() == PREFIX_SHA256:
            if offset is not None:
                raise ValueError("Multiple embedded network prefixes match")
            offset = position
        position = image.find(NETWORK_HEADER, position + 1)
    if offset is None:
        raise ValueError("The pinned embedded network prefix is missing")
    if offset + NETWORK.size > len(image):
        raise ValueError("The embedded network extends beyond the executable")
    return offset


def extract_network(directory: Path) -> dict:
    with (directory / REFERENCE.name).open("rb") as source:
        with mmap.mmap(source.fileno(), 0, access=mmap.ACCESS_READ) as image:
            offset = find_network_offset(image)
        source.seek(offset)
        digest = hashlib.sha256()
        remaining = NETWORK.size
        with (directory / NETWORK.name).open("xb") as target:
            while remaining:
                chunk = source.read(min(CHUNK_BYTES, remaining))
                if not chunk:
                    raise ValueError("The embedded network ended prematurely")
                target.write(chunk)
                digest.update(chunk)
                remaining -= len(chunk)
        size = source.tell() - offset
    sha256 = digest.hexdigest()
    NETWORK.verify(size, sha256)
    return {
        "bytes": size, "sha256": sha256, "source": "embedded in verified reference.exe",
        "executable_sha256": REFERENCE.sha256, "offset_bytes": offset,
        "prefix_bytes": PREFIX_SIZE, "prefix_sha256": PREFIX_SHA256,
    }


def git(checkout: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(checkout), *args], stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, timeout=45, check=True,
    ).stdout


def read_game_blob(checkout: Path, directory: Path, spec: PinnedFile, path: str) -> dict:
    # Git's object database supplies the committed bytes independently of checkout EOL settings.
    revision = SOURCE_COMMIT + ":" + path
    blob = git(checkout, "rev-parse", "--verify", revision).decode("ascii").strip()
    if len(blob) != 40 or any(c not in "0123456789abcdef" for c in blob):
        raise ValueError("Invalid PGN blob ID")
    if int(git(checkout, "cat-file", "-s", blob)) != spec.size:
        raise ValueError(f"{spec.name} Git blob has an unexpected size")
    contents = git(checkout, "cat-file", "blob", blob)
    digest = hashlib.sha256(contents).hexdigest()
    spec.verify(len(contents), digest)
    with (directory / spec.name).open("xb") as out:
        out.write(contents)
    return {
        "repository": GAMES_REPOSITORY, "commit": SOURCE_COMMIT, "path": path,
        "git_blob": blob, "bytes": len(contents), "sha256": digest,
    }


def fetch_inputs(workspace: Path) -> dict:
    inputs = workspace / "inputs"
    evidence = workspace / "evidence"
    if any(p.exists() or p.is_symlink() for p in (inputs, evidence)):
        raise FileExistsError("The runner requires fresh inputs and evidence directories")
    reference = workspace / "accepted-reference" / EXE_MEMBER
    for path in (reference.parent, reference):
        status = path.lstat()
        if stat.S_ISLNK(status.st_mode) or (
            getattr(status, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise ValueError("Reference artifact contains a link")
    if not reference.is_file() or reference.stat().st_size != REFERENCE.size:
        raise ValueError("Missing or incorrectly sized reference artifact member")

    received = {}
    with tempfile.TemporaryDirectory(dir=workspace, prefix=".layout-fetch-") as temp:
        temporary = Path(temp)
        stage = temporary / "inputs"
        stage.mkdir()
        with reference.open("rb") as src, (stage / REFERENCE.name).open("xb") as out:
            size, digest = transfer(src, out, REFERENCE.size)
        REFERENCE.verify(size, digest)
        received[REFERENCE.name] = {
            "repository": "0mis/Stockfish", "run_id": RUN_ID, "artifact_name": ARTIFACT_NAME,
            "member": EXE_MEMBER, "bytes": size, "sha256": digest,
        }
        received[NETWORK.name] = extract_network(stage)
        checkout = workspace / "retired-public-games"
        if git(checkout, "rev-parse", "--verify", "HEAD").decode("ascii").strip() != SOURCE_COMMIT:
            raise ValueError("Public game checkout is at the wrong commit")
        for spec, path in PGNS:
            received[spec.name] = read_game_blob(checkout, stage, spec, path)
        stage.rename(inputs)
    return {"schema": "layout-public-inputs-v1", "inputs": received}


if __name__ == "__main__":
    print(json.dumps(fetch_inputs(Path.cwd()), indent=2, sort_keys=True))
