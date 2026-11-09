"""Pinned official7cdd release transport. No network/model I/O on import."""
import hashlib
from pathlib import Path
import struct
import urllib.request
import zipfile

URL = "https://github.com/official-stockfish/Stockfish/releases/download/stockfish-dev-20261106-7cdd5e89/stockfish-windows-x86-64-universal.zip"
ZIP_BYTES, ZIP_SHA = 119111575, "37c5fff649e820e3bcb5ac3450a1363cf0d75045da0dd15559873f76e53055d1"
EXE_BYTES, EXE_SHA = 140839068, "b67c37d0d45e3540e311392aeeb699e3a745f16e172bc3fc6f85e0782093842b"
NET_BYTES, NET_SHA, OFFSET = 136247652, "d1ee674a62ca754648fb8c2907ff4e92f0c3acff28f59afa0f5bd4e3949538f1", 3248160
MEMBER = "stockfish/stockfish-windows-x86-64-universal.exe"


def require(ok, reason):
    if not ok: raise ValueError(reason)


def copy_checked(source, target, size, expected):
    digest, count = hashlib.sha256(), 0
    with Path(target).open("xb") as output:
        while block := source.read(min(1024**2, size - count + 1)):
            count += len(block); require(count <= size, "Input exceeds pinned length")
            output.write(block); digest.update(block)
    require(count == size and digest.hexdigest() == expected, "Official input length/SHA differs")


def prepare(directory):
    directory = Path(directory); directory.mkdir(exist_ok=False)
    archive, executable, network = directory / "official.zip", directory / "official.exe", directory / "nn-d1ee674a62ca.nnue"
    request = urllib.request.Request(URL, headers={"User-Agent": "0mis-official-d1-parity"})
    with urllib.request.urlopen(request, timeout=90) as source: copy_checked(source, archive, ZIP_BYTES, ZIP_SHA)
    with zipfile.ZipFile(archive) as zipped:
        info = zipped.getinfo(MEMBER)
        require(info.file_size == EXE_BYTES and not info.flag_bits & 1, "Unexpected official release member")
        with zipped.open(info) as source: copy_checked(source, executable, EXE_BYTES, EXE_SHA)
    with executable.open("rb") as source:
        source.seek(OFFSET)
        # A bounded view prevents consuming trailing executable data.
        class Slice:
            left = NET_BYTES
            def read(self, size):
                raw = source.read(min(size, self.left)); self.left -= len(raw); return raw
        copy_checked(Slice(), network, NET_BYTES, NET_SHA)
    with network.open("rb") as source:
        require(struct.unpack("<3I", source.read(12)) == (0x6A448AFA, 0xC0357A83, 84), "Original D1 format differs")
    return executable, network, {"release_asset_id": 717703279, "release_sha256": ZIP_SHA, "engine_sha256": EXE_SHA,
                                 "network_sha256": NET_SHA, "network_offset": OFFSET, "source_commit": "7cdd5e89a39fe3071b67d91c7bb07d712044bf76"}
