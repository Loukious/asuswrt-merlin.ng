#!/usr/bin/env python3
"""Actions check for exact, unchanged GPL BDMF header imports."""
import hashlib
import json
import re
from pathlib import Path


def main():
    repo = Path(__file__).resolve().parents[2]
    manifest = json.loads(Path(__file__).with_name(
        "wfd-bdmf-header-provenance.json").read_text())
    destination = repo / manifest["destination_prefix"]
    by_name = {Path(item["path"]).name for item in manifest["headers"]}
    for item in manifest["headers"]:
        path = destination / item["path"]
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise SystemExit(f"Changed GPL donor header: {path}")
        git_data = b"blob " + str(len(data)).encode() + b"\0" + data
        if hashlib.sha1(git_data).hexdigest() != item["git_blob"]:
            raise SystemExit(f"Donor Git blob mismatch: {path}")
        includes = re.findall(
            rb'^\s*#\s*include\s*[<"](bdmf_[^>"]+)[>"]', data, re.MULTILINE)
        for include in includes:
            if Path(include.decode()).name not in by_name:
                raise SystemExit(f"Unaccounted BDMF dependency {include!r}: {path}")
    if list(destination.rglob("*.c")):
        raise SystemExit("WFD compatibility directory must not import BDMF .c implementations")
    print(f"Verified {len(manifest['headers'])} unchanged GPL BDMF headers")


if __name__ == "__main__":
    main()
