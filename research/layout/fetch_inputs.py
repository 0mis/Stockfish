"""Fetch and verify the four public inputs for the Windows layout experiment."""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
import stat
import tempfile
import urllib.request


SOURCE_COMMIT = "6831500a4c6b22d64109a38bb0dc5bbf5b6f8fe0"
PUBLIC_RESULTS = (
    "https://raw.githubusercontent.com/0mis/stockfish-weight-space-lab/"
    + SOURCE_COMMIT + "/results/"
)
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
NETWORK_URL = "https://data.stockfishchess.org/nn/nn-18cd32e11a9c.nnue"
PGNS = (
    (PinnedFile("edited.pgn", 918097, "8bd56ec5f58ef65881ed5ae2d7ef6683683893c34c10b94b3bb0f2d5fc83dde9"),
     PUBLIC_RESULTS + "test1757-edited-screen/games-utc.pgn"),
    (PinnedFile("parent.pgn", 935475, "1acfc5b48a1c20aa43947fbde0ec0ea6c413f8bf76a7265dbe3d8e47c711de5d"),
     PUBLIC_RESULTS + "test1757-parent-screen/games-utc.pgn"),
)


class FixedOrigin(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise ValueError("Unexpected redirect for a pinned public input")


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


def download(url: str, path: Path, limit: int, accept_encoding: str) -> tuple[int, str]:
    request = urllib.request.Request(url, headers={"Accept-Encoding": accept_encoding})
    with urllib.request.build_opener(FixedOrigin()).open(request, timeout=45) as response:
        if response.status != 200 or response.geturl() != url:
            raise ValueError("Unexpected response for a pinned public URL")
        header = response.headers.get("Content-Length")
        if header is not None and not (0 < int(header) <= limit):
            raise ValueError("Public input has an unexpected Content-Length")
        with path.open("xb") as out:
            size, digest = transfer(response, out, limit)
        if header is not None and size != int(header):
            raise ValueError("Public input ended before its declared length")
    return size, digest


def fetch_network(directory: Path, temporary: Path) -> dict:
    wire = temporary / "network.wire"
    # The official endpoint can serve the network raw or as a gzip member.
    wire_size, wire_sha = download(NETWORK_URL, wire, NETWORK.size + CHUNK_BYTES, "gzip, identity")
    with wire.open("rb") as raw, (directory / NETWORK.name).open("xb") as output:
        compressed = raw.read(2) == b"\x1f\x8b"
        raw.seek(0)
        if compressed:
            with gzip.GzipFile(fileobj=raw, mode="rb") as decoded:
                size, digest = transfer(decoded, output, NETWORK.size)
        else:
            size, digest = transfer(raw, output, NETWORK.size)
    NETWORK.verify(size, digest)
    return {
        "url": NETWORK_URL, "bytes": size, "sha256": digest,
        "encoding": "gzip" if compressed else "raw",
        "wire_bytes": wire_size, "wire_sha256": wire_sha,
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
        received[NETWORK.name] = fetch_network(stage, temporary)
        for spec, url in PGNS:
            size, digest = download(url, stage / spec.name, spec.size, "identity")
            spec.verify(size, digest)
            received[spec.name] = {"url": url, "bytes": size, "sha256": digest}
        stage.rename(inputs)
    return {"schema": "layout-public-inputs-v1", "inputs": received}


if __name__ == "__main__":
    print(json.dumps(fetch_inputs(Path.cwd()), indent=2, sort_keys=True))
