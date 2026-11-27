#!/usr/bin/env python3
"""Mark worker assignment and retirement in the Pico B diagnostic image."""

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
PROBE_IMAGE_HASH = "05684eb526df43f8b27f1d1eca1899b13697cf734fe4c6aa84c272dd7fe525d5"
NEARBY_WORDS = (0x211A4000, 0xA4A44A80, 0x067448E1, 0xFC000000, 0xBCB00FF4)
RETIREMENT_WORDS = (0x22BB2110, 0x8CB2E0A6, 0xFC000000, 0x2ABB2110, 0x2A0A8A40)
PROBE_SITES = ((0x0CC0, 0xB400), (0x1048, 0xB404))
SCRATCH = 0xB400
WORKER_REGISTER = 10


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
    if struct.unpack_from("<5I", runner, 0x1040) != RETIREMENT_WORDS:
        raise ValueError("Worker retirement words changed")
    word_store = struct.unpack_from("<I", runner, 0x00B4)[0]
    if word_store != 0x2A8B1CC2:
        raise ValueError("Debug routine's 32-bit store changed")
    for name in "ABCD":
        _, _, other = runner_image(elf, name)
        for (word,) in struct.iter_unpack("<I", other):
            if ((word & 0xFE000000) in (0x22000000, 0x2A000000)
                    and SCRATCH <= ((word >> 4) & 0xFFFF) < SCRATCH + 16):
                raise ValueError(f"Direct memory reference conflicts with probe scratch in Runner {name}")
    for pc, _ in PROBE_SITES:
        patch_at = symbol.value + pc
        if any(rel.section_index == symbol.section_index and patch_at - 7 <= rel.offset < patch_at + 4
               for rel in elf.relocations):
            raise ValueError(f"A relocation overlaps the probe at {pc:#06x}")
    baseline = data
    if build_input and INPUTS[digest] == "vendor object":
        spec = json.loads((ROOT / "tools/ci/ax86u-rdpa-patches.json").read_text())
        baseline, host_fixes = patcher.patch_blob(data, spec)
        if not host_fixes["patches"]:
            raise ValueError("Expected the normal host RDPA fix")
    patched = bytearray(baseline)
    sites = []
    for pc, address in PROBE_SITES:
        # The debug routine provides the direct 32-bit store encoding. R10
        # contains the worker mask in bits 16–19 at the two instrumented sites.
        replacement = (word_store & 0xFE00000F) | (WORKER_REGISTER << 20) | (address << 4)
        struct.pack_into("<I", patched, start + pc, replacement)
        sites.append({
            "pico_b_pc": f"0x{pc:04x}",
            "file_offset": f"0x{start + pc:x}",
            "original_word": "0xfc000000",
            "replacement_word": f"0x{replacement:08x}",
            "candidate_register": WORKER_REGISTER,
            "candidate_private_b_word": f"0x{address:04x}",
        })
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
        "sites": sites,
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
