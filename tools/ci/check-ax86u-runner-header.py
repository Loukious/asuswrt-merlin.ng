#!/usr/bin/env python3
"""Check the imported Runner header and its exact donor reconstruction."""

import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HEADER = ROOT / (
    "release/src-rt-5.02L.07p2axhnd/bcmdrivers/opensource/net/wfd/impl1/"
    "runner-compat/rdp_cpu_ring_defs.h"
)
EXPECTED_SHA256 = "b38dc5495af47f40bd8285da04bd58daade6fe2d1b9ca50634ad7d3e3870e255"
DONOR_BLOB = "fad9c51607f957bff5642667e559baf94ccef9ba"
ASSERTIONS_MARKER = (
    b"/* RT-AX86U prebuilt wfd.o DWARF: legacy Runner descriptor, not XRDP. */\n"
)
TRAILER = b"#endif /*_RDP_CPU_RING_DEFS_H */\n"
ADAPTED_FIELD = b"            uint32_t    is_exception:1;\n            uint32_t    reserved1:4;"
DONOR_FIELD = b"            uint32_t    reserved1:5;"


def main():
    data = HEADER.read_bytes()
    if hashlib.sha256(data).hexdigest() != EXPECTED_SHA256:
        raise SystemExit("Runner header hash changed; review ABI and donor provenance")
    if data.count(ADAPTED_FIELD) != 1 or data.count(ASSERTIONS_MARKER) != 1:
        raise SystemExit("Runner header adaptation is not the reviewed one-field split")
    original, assertions = data.split(ASSERTIONS_MARKER)
    if not assertions.endswith(TRAILER):
        raise SystemExit("Runner descriptor assertion boundary changed")
    original = original.replace(ADAPTED_FIELD, DONOR_FIELD) + TRAILER
    digest = hashlib.sha1(b"blob " + str(len(original)).encode() + b"\0" + original)
    if digest.hexdigest() != DONOR_BLOB:
        raise SystemExit("Runner donor reconstruction differs from pinned Git blob")
    print("Runner header provenance and exact ABI adaptation verified.")


if __name__ == "__main__":
    main()
