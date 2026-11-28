#!/usr/bin/env python3
"""Record Pico B worker checkpoints, availability writers and post-clear paths."""

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
PROBE_IMAGE_HASH = "155cf780debce194c20e1d24c45cf0ae52d5c6f60a32f44bdfc1b8859ab7ae7e"
NEARBY_WORDS = (0x211A4000, 0xA4A44A80, 0x067448E1, 0xFC000000, 0xBCB00FF4)
RETIREMENT_WORDS = (0x22BB2110, 0x8CB2E0A6, 0xFC000000, 0x2ABB2110, 0x2A0A8A40)
WORKER_REGISTER = 10
NOP = 0xFC000000
CLEAR_PC = 0x0CC4
CONTINUE_PC = 0x0CD4
TRAMPOLINE_PC = 0x7F00
CLEAR_WORDS = (0xBCB00FF4, 0x22B2879C, 0x2A0287B4, 0x280A4000)
DISPATCH_PC = 0x0850
DISPATCH_STUB = 0x7F40
DISPATCH_WORDS = (0x28C48000, 0x2898C000, 0x8230E012)
RETURN_PC = 0x1050
RETURN_STUB = 0x7F80
RETURN_WORDS = (0x2A0A8A40, 0x1800CB47, NOP, NOP)
TAKEN_PC = 0x0A0C
TAKEN_STUB = 0x7FA4
TAKEN_WORDS = (0x23628446, 0xA1339800, 0x22C2855C)
FALLTHROUGH_PC = 0x0CE8
FALLTHROUGH_STUB = 0x7FC8
FALLTHROUGH_WORDS = (0xA4B2E100, 0x86E2E020, 0xBCC92E04)
REORDER_ROUTE_PC = 0x0A18
REORDER_ROUTE_STUB = 0x7E40
REORDER_ROUTE_WORDS = (0x077591C4, 0x80B02000, 0x2AC28024)
REORDER_HEAD_PC = 0x112C
REORDER_HEAD_STUB = 0x7E80
REORDER_HEAD_WORDS = (0x20C0C000, 0x840320A6, 0x02004818, 0x8230E010, 0x22E28407)
PROBE_SITES = (
    (0x0CC0, 0xB400, WORKER_REGISTER),                # accepted assignment
    (TRAMPOLINE_PC + 8, 0xB40C, WORKER_REGISTER),     # after the first load
    (TRAMPOLINE_PC + 16, 0xB610, WORKER_REGISTER),    # after the first store
    (TRAMPOLINE_PC + 24, 0xB408, WORKER_REGISTER),    # after the mailbox clear
    (0x1048, 0xB404, WORKER_REGISTER),                # before updating availability
    (DISPATCH_STUB + 8, 0xB614, 17),                  # dispatch mask originally loaded
    (DISPATCH_STUB + 12, 0xB618, 11),                 # dispatch mask written
    (DISPATCH_STUB + 16, 0xB61C, 9),                  # worker selected by dispatch
    (RETURN_STUB + 4, 0xB620, 11),                    # retirement mask written
    (RETURN_STUB + 8, 0xB624, WORKER_REGISTER),        # worker past the retirement write
    (TRAMPOLINE_PC + 28, 0xB628, 11),                # R11 just before the post-clear conditional
    (TAKEN_STUB, 0xB62C, WORKER_REGISTER),            # progress at 0x0a0c
    (FALLTHROUGH_STUB, 0xB630, WORKER_REGISTER),      # progress at 0x0ce8
    (REORDER_ROUTE_STUB, 0xB634, 22),                # register read by the shortcut branch
    (REORDER_ROUTE_STUB + 16, 0xB638, WORKER_REGISTER),  # ordinary continuation at 0x0a24
    (REORDER_ROUTE_STUB + 32, 0xB63C, WORKER_REGISTER),  # shortcut into 0x1128
    (REORDER_HEAD_STUB + 4, 0xB640, 3),             # reorder cursor at owner load
    (REORDER_HEAD_STUB + 8, 0xB644, 12),            # owner byte read into R12
    (REORDER_HEAD_STUB + 12, 0xB648, WORKER_REGISTER),  # tag the reorder read
    (REORDER_HEAD_STUB + 32, 0xB64C, WORKER_REGISTER),  # compare continued to 0x1140
    (REORDER_HEAD_STUB + 48, 0xB650, WORKER_REGISTER),  # compare branched to 0x1194
)


def jump(pc):
    if pc % 4 or not 0 <= pc < 32768:
        raise ValueError("Jump target lies outside Pico B instruction memory")
    return 0x10000000 | (pc >> 2)


def redirect_relative(word, original_pc, original_target, pc, target):
    mask = 0x3FF
    immediate = word & mask
    original_offset = (immediate ^ 0x200) - 0x200
    if original_pc + 4 * original_offset != original_target:
        raise ValueError("Original relative branch no longer matches the decoded target")
    offset, unaligned = divmod(target - pc, 4)
    if unaligned or not -512 <= offset < 512:
        raise ValueError("Relocated conditional target lies outside its signed 10-bit range")
    return (word & ~mask) | (offset & mask)


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
    if struct.unpack_from("<4I", runner, RETURN_PC) != RETURN_WORDS:
        raise ValueError("Worker return words changed")
    if struct.unpack_from("<6I", runner, DISPATCH_PC - 4) != (
        0x2ABB2110, *DISPATCH_WORDS, 0x02002008, 0x8A30E04A
    ) or struct.unpack_from("<I", runner, 0x0814)[0] != 0x231B2110:
        raise ValueError("Dispatcher availability and publication words changed")
    word_store = struct.unpack_from("<I", runner, 0x00B4)[0]
    if word_store != 0x2A8B1CC2:
        raise ValueError("Debug routine's 32-bit store changed")
    if (struct.unpack_from("<4I", runner, CLEAR_PC) != CLEAR_WORDS
            or struct.unpack_from("<I", runner, CONTINUE_PC)[0] != 0x0A32E74D):
        raise ValueError("Mailbox clear and continuation instructions changed")
    if struct.unpack_from("<5I", runner, TAKEN_PC - 4) != (0x22E28407, *TAKEN_WORDS, 0x077591C4):
        raise ValueError("Post-clear branch target words changed")
    if struct.unpack_from("<5I", runner, FALLTHROUGH_PC - 4) != (
        0xC6000642, *FALLTHROUGH_WORDS, 0x22B28446
    ):
        raise ValueError("Post-clear fall-through words changed")
    if struct.unpack_from("<4I", runner, REORDER_ROUTE_PC) != (*REORDER_ROUTE_WORDS, 0x86032110):
        raise ValueError("Reorder shortcut branch or adjacent words changed")
    if struct.unpack_from("<7I", runner, REORDER_HEAD_PC - 4) != (0xBCD00294, *REORDER_HEAD_WORDS, 0x8A30E048):
        raise ValueError("Reorder owner check or adjacent words changed")
    if struct.unpack_from("<3I", runner, 0x1194) != (0xBCC00024, 0xC2031702, 0x180112C7):
        raise ValueError("Reorder retry words changed")
    if struct.unpack_from("<3I", runner, 8) != (0x1000001B, NOP, NOP):
        raise ValueError("Known absolute jump instruction changed")
    if any(word != NOP for (word,) in struct.iter_unpack("<I", runner[REORDER_ROUTE_STUB:])):
        raise ValueError("The trampoline no longer falls inside NOP padding")
    for name in "ABCD":
        _, _, other = runner_image(elf, name)
        for (word,) in struct.iter_unpack("<I", other):
            address = (word >> 4) & 0xFFFF
            if ((word & 0xFE000000) in (0x22000000, 0x2A000000)
                    and (0xB400 <= address < 0xB410 or 0xB610 <= address < 0xB65C)):
                raise ValueError(f"Direct memory reference conflicts with probe scratch in Runner {name}")
    # Move only the four straight-line instructions. Keep the conditional at
    # CONTINUE_PC in place and place NOPs in both delay slots of each jump.
    setup, load, store, clear = CLEAR_WORDS
    clear_stub = (setup, load, NOP, store, NOP, clear, NOP, NOP, jump(CONTINUE_PC), NOP, NOP)
    mailbox_store, reorder_store, next_value = DISPATCH_WORDS
    dispatch_stub = (mailbox_store, reorder_store, NOP, NOP, NOP, next_value,
                     jump(DISPATCH_PC + 12), NOP, NOP)
    dummy_store, return_to_worker, nop1, nop2 = RETURN_WORDS
    return_stub = (dummy_store, NOP, NOP, return_to_worker, nop1, nop2,
                   jump(RETURN_PC + 16), NOP, NOP)
    taken_stub = (NOP, *TAKEN_WORDS, jump(TAKEN_PC + 12), NOP, NOP)
    fallthrough_stub = (NOP, *FALLTHROUGH_WORDS, jump(FALLTHROUGH_PC + 12), NOP, NOP)
    shortcut, route_delay_1, route_delay_2 = REORDER_ROUTE_WORDS
    shortcut = redirect_relative(shortcut, REORDER_ROUTE_PC, 0x1128,
                                 REORDER_ROUTE_STUB + 4, REORDER_ROUTE_STUB + 32)
    reorder_route_stub = (NOP, shortcut, route_delay_1, route_delay_2,
                          NOP, jump(0x0A24), NOP, NOP, NOP, jump(0x1128), NOP, NOP)
    load_owner, compare_owner, retry, retry_delay_1, retry_delay_2 = REORDER_HEAD_WORDS
    retry = redirect_relative(retry, 0x1134, 0x1194, REORDER_HEAD_STUB + 20, REORDER_HEAD_STUB + 48)
    reorder_head_stub = (load_owner, NOP, NOP, NOP, compare_owner, retry, retry_delay_1, retry_delay_2,
                         NOP, jump(0x1140), NOP, NOP, NOP, jump(0x1194), NOP, NOP)
    relocated = {}
    for entry, target, stub in ((CLEAR_PC, TRAMPOLINE_PC, clear_stub),
                                (DISPATCH_PC, DISPATCH_STUB, dispatch_stub),
                                (RETURN_PC, RETURN_STUB, return_stub),
                                (TAKEN_PC, TAKEN_STUB, taken_stub),
                                (FALLTHROUGH_PC, FALLTHROUGH_STUB, fallthrough_stub),
                                (REORDER_ROUTE_PC, REORDER_ROUTE_STUB, reorder_route_stub),
                                (REORDER_HEAD_PC, REORDER_HEAD_STUB, reorder_head_stub)):
        relocated.update({entry: jump(target), entry + 4: NOP, entry + 8: NOP})
        relocated.update({target + i * 4: word for i, word in enumerate(stub)})
    for pc in [pc for pc, _, _ in PROBE_SITES] + list(relocated):
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
    for pc, address, register in PROBE_SITES:
        # The debug routine provides the direct 32-bit store encoding.
        replacement = (word_store & 0xFE00000F) | (register << 20) | (address << 4)
        struct.pack_into("<I", patched, start + pc, replacement)
        sites.append({
            "pico_b_pc": f"0x{pc:04x}",
            "file_offset": f"0x{start + pc:x}",
            "original_word": "0xfc000000",
            "replacement_word": f"0x{replacement:08x}",
            "candidate_register": register,
            "candidate_private_b_word": f"0x{address:04x}",
        })
    patched = bytes(patched)
    patched_hash = hashlib.sha256(patched[start:start + symbol.size]).hexdigest()
    if patched_hash != PROBE_IMAGE_HASH:
        raise ValueError(f"Unexpected instrumented Runner D image: {patched_hash}")
    return patched, {
        "input": INPUTS[digest],
        "input_sha256": digest,
        "baseline_sha256": hashlib.sha256(baseline).hexdigest(),
        "output_sha256": hashlib.sha256(patched).hexdigest(),
        "runner_d_before": IMAGE_HASH,
        "runner_d_after": patched_hash,
        "section": ".data",
        "sites": sites,
        "mailbox_clear_entry": f"0x{CLEAR_PC:04x}",
        "mailbox_clear_original_continuation": f"0x{CONTINUE_PC:04x}",
        "mailbox_clear_trampoline": f"0x{TRAMPOLINE_PC:04x}",
        "dispatch_trampoline": f"0x{DISPATCH_STUB:04x}",
        "worker_return_trampoline": f"0x{RETURN_STUB:04x}",
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
