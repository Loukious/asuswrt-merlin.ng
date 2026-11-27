#!/usr/bin/env python3
"""Prepare a single-word Pico B mailbox diagnostic object for bench testing."""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys


ROOT = Path(__file__).resolve().parents[2]
VENDOR_OBJECT = ROOT / (
    "release/src-rt-5.02L.07p2axhnd/router-sysdep.rt-ax86u/"
    "hnd_extra/prebuilt/rdpa.o"
)
INPUTS = {
    "fa7c264e0ac174cd3da9c1fdaaeb1cbdbf692681d8740e92c31a9654ba5f4662": "vendor object",
    "1dd85d4200747f5cbf5699ffef1339f13b91622105b44b9af16c3c83576f2bfc": "installed module backup",
}
IMAGE_HASH = "449e860e7cdfce5ab121e2624b0652fd20dd382299d49602126aa6791d228b7e"
PROBE_IMAGE_HASH = "b313441fa3594388fc5a811bd90e7c007d9f8a2673c6b39e176b44eb1ca6bd76"
NEARBY_WORDS = (0x211A4000, 0xA4A44A80, 0x067448E1, 0xFC000000, 0xBCB00FF4)
PATCH_PC = 0x0CC0
SCRATCH = 0xB400
AVAILABILITY_STORE_PC = 0x104C


def load_driver_patcher():
    path = ROOT / "tools/ci/patch-ax86u-drivers.py"
    spec = importlib.util.spec_from_file_location("ax86u_driver_patcher", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def runner_image(elf, name):
    candidates = elf._candidates.get(f"firmware_binary_{name}", [])
    if len(candidates) != 1:
        raise ValueError(f"Expected one Runner {name} image symbol")
    symbol = candidates[0]
    section = elf.sections[symbol.section_index]
    if (symbol.info & 15) != 1 or symbol.size != 32768 or section.name != ".data":
        raise ValueError(f"Unexpected Runner {name} image layout")
    if symbol.value + symbol.size > section.size:
        raise ValueError(f"Runner {name} image extends beyond its section")
    start = section.offset + symbol.value
    return symbol, start, elf.data[start:start + symbol.size]


def make_probe(data, *, build_input=False):
    digest = hashlib.sha256(data).hexdigest()
    if digest not in INPUTS:
        raise ValueError("RDPA artifact does not match either reviewed input")
    patcher = load_driver_patcher()
    elf = patcher.ElfFile(data)
    symbol, start, runner = runner_image(elf, "D")
    if hashlib.sha256(runner).hexdigest() != IMAGE_HASH:
        raise ValueError("Runner D microcode identity changed")
    if struct.unpack_from("<5I", runner, 0x0CB4) != NEARBY_WORDS:
        raise ValueError("Mailbox check and branch words changed")
    store = struct.unpack_from("<I", runner, AVAILABILITY_STORE_PC)[0]
    if store != 0x2ABB2110:
        raise ValueError("Calibrated availability byte store changed")
    for name in "ABCD":
        _, _, other = runner_image(elf, name)
        for (word,) in struct.iter_unpack("<I", other):
            if ((word & 0xFE000000) in (0x22000000, 0x2A000000)
                    and SCRATCH <= ((word >> 4) & 0xFFFF) < SCRATCH + 16):
                raise ValueError(f"Direct memory reference conflicts with probe scratch in Runner {name}")
    patch_at = symbol.value + PATCH_PC
    if any(rel.section_index == symbol.section_index and patch_at - 7 <= rel.offset < patch_at + 4
           for rel in elf.relocations):
        raise ValueError("A relocation overlaps the probe instruction")

    # Preserve the observed byte-store family and access mode. Substitute the
    # register observed at the mailbox branch and the reviewed scratch address.
    replacement = (store & 0xFE00000F) | (17 << 20) | (SCRATCH << 4)
    baseline = data
    if build_input and INPUTS[digest] == "vendor object":
        spec = json.loads((ROOT / "tools/ci/ax86u-rdpa-patches.json").read_text())
        baseline, host_fixes = patcher.patch_blob(data, spec)
        if not host_fixes["patches"]:
            raise ValueError("Expected the normal host RDPA fix")
    patched = bytearray(baseline)
    offset = start + PATCH_PC
    struct.pack_into("<I", patched, offset, replacement)
    if patched[:offset] != baseline[:offset] or patched[offset + 4:] != baseline[offset + 4:]:
        raise AssertionError("Probe changed bytes outside its instruction word")
    patched = bytes(patched)
    if hashlib.sha256(patched[start:start + symbol.size]).hexdigest() != PROBE_IMAGE_HASH:
        raise ValueError("Unexpected instrumented Runner D image")
    return patched, {
        "input": INPUTS[digest],
        "input_sha256": digest,
        "baseline_sha256": hashlib.sha256(baseline).hexdigest(),
        "output_sha256": hashlib.sha256(patched).hexdigest(),
        "runner_d_before": IMAGE_HASH,
        "runner_d_after": hashlib.sha256(patched[start:start + symbol.size]).hexdigest(),
        "section": ".data",
        "pico_b_pc": f"0x{PATCH_PC:04x}",
        "file_offset": f"0x{offset:x}",
        "original_word": f"0x{NEARBY_WORDS[3]:08x}",
        "replacement_word": f"0x{replacement:08x}",
        "candidate_register": 17,
        "candidate_private_b_byte": f"0x{SCRATCH:04x}",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=VENDOR_OBJECT)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--build-input", action="store_true",
                        help="Include the existing host RDPA fix before adding the probe")
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        parser.error("Input and output must be different")
    try:
        patched, report = make_probe(args.input.read_bytes(), build_input=args.build_input)
        with args.output.open("xb") as output:
            output.write(patched)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
