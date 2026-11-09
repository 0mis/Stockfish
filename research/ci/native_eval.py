"""Compare every displayed bucket, active marker, and native integer to the accepted run."""
import hashlib
import json
import re
import shutil

from capture_common import ERROR, NET_MESSAGE, commands_for, file_hash, require

NETWORK_NAME = "nn-18cd32e11a9c.nnue"
EVALFILE = "..\\src\\" + NETWORK_NAME
PROBES_SHA = "60237d711345475e7aeb846888faee49efc1ec4c9f4a3f17a1781332389e8349"
REFERENCE_FILES = {
    "donor-eval.stdout.txt": (71682, "a988c9215202a7759fd4ec2398b2196d1e28a6b7e86be26f3b458e90ce360f82"),
    "donor-eval.stderr.txt": (0, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
    "donor-eval.uci": (1907, "8eb57877cf91f46d6e4f7ee5555bed572e99de6c4f2400f5c3cbb596872a70ce"),
    "donor-eval.record.json": (602, "b650a5935fbc94965974b88da54072a0b15f4c41d7019a757495eea0403bfb0b"),
}
BORDER = "+------------+------------+"
BUCKET = re.compile(r"\|\s*(\d+)\s*\|\s*([+-]?)\s*(\d+\.\d{2})\s*\|\s*(<-- this bucket is used)?")
INTEGER = re.compile(r"NNUE evaluation\s+([+-]?\d+)\s+\(side to move, internal units\)")


def capture_tables(stdout, stderr, rows):
    require(not stderr and not ERROR.search(stdout), "Static eval reported an error")
    lines = stdout.splitlines()
    ready = [i for i, line in enumerate(lines) if line == "readyok"]
    require(len(ready) == len(rows) + 1 and lines.count("uciok") == 1 and lines.index("uciok") < ready[0],
            "Missing or reordered UCI trace boundaries")
    require(not any(line.strip() for line in lines[ready[-1] + 1:]), "Unexpected output after the last eval")
    require(lines.count("option name EvalFile type string default " + NETWORK_NAME) == 1, "Default network differs")
    result = []
    for probe, start, end in zip(rows, ready, ready[1:]):
        block = lines[start + 1:end]
        acknowledgments = [i for i, line in enumerate(block) if line.startswith("info string NNUE evaluation using ")]
        headings = [i for i, line in enumerate(block) if line.startswith("NNUE network contributions")]
        require(len(acknowledgments) == len(headings) == 1 and acknowledgments[0] < headings[0] and
                NET_MESSAGE.findall("\n".join(block)) == [EVALFILE], "Missing or wrong network in probe")
        head = headings[0]
        require(block[head] == f"NNUE network contributions (Normalized, {probe['side_to_move'].capitalize()} to move)" and
                block[head+1:head+4] == [BORDER, "|   Bucket   | Evaluation |", BORDER], "Invalid table heading")
        table = block[head+4:head+96]
        require(len(table) == 92 and block[head+96:head+97] == [BORDER] and
                sum(BUCKET.fullmatch(line) is not None for line in block) == 92, "Incomplete or duplicated bucket table")
        values, active = [], []
        for index, line in enumerate(table):
            match = BUCKET.fullmatch(line)
            require(match and int(match[1]) == index, "Missing or reordered bucket")
            require(match[2] or match[3] == "0.00", "Unsigned nonzero bucket")
            values.append(match[2] + match[3])
            if match[4]:
                active.append(index)
        require(active == [probe["physical"]], "Wrong active bucket")
        integers = [int(m[1]) for line in block if (m := INTEGER.fullmatch(line))]
        require(integers == [probe["donor"]] and any(INTEGER.fullmatch(line) for line in block[head+97:]),
                "Wrong native integer or integer position")
        result.append({"id": probe["id"], "integer": integers[0], "active_bucket": active[0],
                       "displayed_values": values})
    return result


def verify_reference_capture(reference, output, rows):
    destination = output / "reference"
    destination.mkdir()
    for name, (size, expected) in REFERENCE_FILES.items():
        source = reference / name
        require(source.is_file() and not source.is_symlink() and source.stat().st_size == size and
                file_hash(source) == expected, "Accepted reference capture differs: " + name)
        shutil.copyfile(source, destination / name)
    record = json.loads((destination / "donor-eval.record.json").read_bytes())
    require(record["returncode"] == 0 and record["timed_out"] is False and
            record["commands"]["sha256"] == REFERENCE_FILES["donor-eval.uci"][1] and
            all(record["logs"][key]["sha256"] == REFERENCE_FILES["donor-eval." + key + ".txt"][1]
                for key in ("stdout", "stderr")), "Reference exit or pipe binding differs")
    commands = commands_for(EVALFILE, "eval", rows).encode()
    require(commands == (destination / "donor-eval.uci").read_bytes(), "Reference probe command stream differs")
    return capture_tables((destination / "donor-eval.stdout.txt").read_text(encoding="utf-8"),
                          (destination / "donor-eval.stderr.txt").read_text(encoding="utf-8"), rows)


def compare_tables(actual, reference):
    require(actual == reference, "Reference and candidate native tables or integers differ")
    return {"probe_count": len(actual), "buckets_per_probe": 92, "displayed_buckets_compared": 92 * len(actual),
            "probes": [{"id": row["id"], "integer": row["integer"], "active_bucket": row["active_bucket"],
                        "displayed_table_sha256": hashlib.sha256("\n".join(row["displayed_values"]).encode()).hexdigest()}
                       for row in actual]}
