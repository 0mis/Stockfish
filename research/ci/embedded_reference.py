"""Extract the original 18cd network from the pinned accepted reference on the hosted runner.
SPDX-License-Identifier: GPL-3.0-or-later
"""
import hashlib
import mmap
import stat
import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path


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


def install_reference_network(reference: Path, target: Path) -> dict:
    for path in (reference.parent, reference):
        status = path.lstat()
        if stat.S_ISLNK(status.st_mode) or getattr(status, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError("Unexpected link in reference artifact")
    if not reference.is_file() or reference.stat().st_size != REFERENCE.size or target.exists() or target.is_symlink():
        raise ValueError("Wrong reference member or occupied original network destination")
    with tempfile.TemporaryDirectory(dir=target.parent, prefix=".reference-") as temporary:
        stage = Path(temporary)
        with reference.open("rb") as src, (stage / REFERENCE.name).open("xb") as out:
            REFERENCE.verify(*transfer(src, out, REFERENCE.size))
        record = extract_network(stage)
        (stage / NETWORK.name).rename(target)
    return record


def verify_finished_embedding(executable: Path) -> dict:
    digest = hashlib.sha256()
    with executable.open("rb") as source, mmap.mmap(source.fileno(), 0, access=mmap.ACCESS_READ) as image:
        offset = find_network_offset(image)
        end = offset + NETWORK.size
        for position in range(offset, end, CHUNK_BYTES):
            digest.update(image[position:min(position + CHUNK_BYTES, end)])
    NETWORK.verify(NETWORK.size, digest.hexdigest())
    return {"offset_bytes": offset, "bytes": NETWORK.size, "sha256": digest.hexdigest()}
