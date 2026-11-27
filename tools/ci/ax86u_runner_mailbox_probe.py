#!/usr/bin/env python3
"""Mark worker assignment, mailbox clear and retirement in the Pico B image."""

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
PROBE_IMAGE_HASH = "5b652d7df26d84451f1c85e6cb408424c6ac8e0be8506527dc8f9b74bf5ebee0"
NEARBY_WORDS = (0x211A4000, 0xA4A44A80, 0x067448E1, 0xFC000000, 0xBCB00FF4)
RETIREMENT_WORDS = (0x22BB2110, 0x8CB2E0A6, 0xFC000000, 0x2ABB2110, 0x2A0A8A40)
WORKER_REGISTER = 10
NOP = 0xFC000000
CLEAR_PC = 0x0CC4
CONTINUE_PC = 0x0CD4
TRAMPOLINE_PC = 0x7F00
CLEAR_WORDS = (0xBCB00FF4, 0x22B2879C, 0x2A0287B4, 0x280A4000)
PROBE_SITES = (
    (0x0CC0, 0xB400),                # accepted assignment
    (TRAMPOLINE_PC + 8, 0xB40C),    # after the first load
    (TRAMPOLINE_PC + 16, 0xB610),   # after the first store
    (TRAMPOLINE_PC + 24, 0xB408),   # after the mailbox clear
    (0x1048, 0xB404),                # before updating availability
)


def jump(pc):
    if pc % 4 or not 0 <= pc < 32768:
        raise ValueError("Jump target lies outside Pico B instruction memory")
    return 0x10000000 | (pc >> 2)


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
    if (struct.unpack_from("<4I", runner, CLEAR_PC) != CLEAR_WORDS
            or struct.unpack_from("<I", runner, CONTINUE_PC)[0] != 0x0A32E74D):
        raise ValueError("Mailbox clear and continuation instructions changed")
    if struct.unpack_from("<3I", runner, 8) != (0x1000001B, NOP, NOP):
        raise ValueError("Known absolute jump instruction changed")
    if any(word != NOP for (word,) in struct.iter_unpack("<I", runner[TRAMPOLINE_PC:])):
        raise ValueError("The trampoline no longer falls inside NOP padding")
    for name in "ABCD":
        _, _, other = runner_image(elf, name)
        for (word,) in struct.iter_unpack("<I", other):
            address = (word >> 4) & 0xFFFF
            if ((word & 0xFE000000) in (0x22000000, 0x2A000000)
                    and (0xB400 <= address < 0xB410 or 0xB610 <= address < 0xB61C)):
                raise ValueError(f"Direct memory reference conflicts with probe scratch in Runner {name}")
    # Move only the four straight-line instructions. Keep the conditional at
    # CONTINUE_PC in place and place NOPs in both delay slots of each jump.
    setup, load, store, clear = CLEAR_WORDS
    stub = (setup, load, NOP, store, NOP, clear, NOP, jump(CONTINUE_PC), NOP, NOP)
    relocated = {
        CLEAR_PC: jump(TRAMPOLINE_PC),
        CLEAR_PC + 4: NOP,
        CLEAR_PC + 8: NOP,
        **{TRAMPOLINE_PC + i * 4: word for i, word in enumerate(stub)},
    }
    for pc in [pc for pc, _ in PROBE_SITES] + list(relocated):
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
    for pc, word in relocated.items():
        struct.pack_into("<I", patched, start + pc, word)
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
        "mailbox_clear_entry": f"0x{CLEAR_PC:04x}",
        "mailbox_clear_original_continuation": f"0x{CONTINUE_PC:04x}",
        "mailbox_clear_trampoline": f"0x{TRAMPOLINE_PC:04x}",
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
