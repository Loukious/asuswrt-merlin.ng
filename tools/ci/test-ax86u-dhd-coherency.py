#!/usr/bin/env python3
"""Execute the exact capability publication window and its patched A64 helper."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys

from unicorn import Uc, UC_ARCH_ARM64, UC_HOOK_CODE, UC_MODE_ARM
from unicorn import arm64_const as registers

ROOT = Path(__file__).resolve().parents[2]
CI = Path(__file__).resolve().parent
BASE, PUB, BUS, IPC, STACK = 0x10000000, 0x20000000, 0x21000000, 0x22000000, 0x23000000
XREGS = [getattr(registers, f"UC_ARM64_REG_X{i}") for i in range(29)]
XREGS += [registers.UC_ARM64_REG_X29, registers.UC_ARM64_REG_LR]


def load_patcher():
    definition = importlib.util.spec_from_file_location("coherency_patcher", CI / "patch-ax86u-drivers.py")
    module = importlib.util.module_from_spec(definition)
    sys.modules[definition.name] = module
    definition.loader.exec_module(module)
    return module


def run_window(data, elf, hook, cap, runner_flags, patched, null_pub=False):
    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    uc.mem_map(BASE, (elf.text.size + 4095) & ~4095)
    uc.mem_write(BASE, data[elf.text.offset:elf.text.offset + elf.text.size])
    for address, size in [(PUB, 0x4000), (BUS, 0x1000), (IPC, 0x1000), (STACK, 0x10000)]:
        uc.mem_map(address, size)
    uc.mem_write(PUB, b"\xa5" * 0x4000)
    # Independent pinned ABI: this is also asserted by the production pub view
    # and used by the original vendor RX scheduler, rather than a test-only view.
    uc.mem_write(PUB + 0x2AA0, struct.pack("<I", runner_flags))
    uc.mem_write(BUS, struct.pack("<Q", 0 if null_pub else PUB))
    original_ipc = bytearray(b"\xa5" * 128)
    struct.pack_into("<I", original_ipc, 0x58, cap)
    uc.mem_write(IPC, bytes(original_ipc))
    sp = STACK + 0xFF00
    uc.mem_write(sp - 64, b"\x5a" * 128)
    initial = {reg: 0xA100000000000000 + i * 0x1234567 for i, reg in enumerate(XREGS)}
    initial[XREGS[19]], initial[XREGS[25]] = BUS, IPC
    for reg, value in initial.items():
        uc.reg_write(reg, value)
    uc.reg_write(registers.UC_ARM64_REG_SP, sp)
    uc.reg_write(registers.UC_ARM64_REG_NZCV, 0xA0000000)
    caller = elf.get_symbol(hook["caller_symbol"])
    site = caller.value + hook["caller_offset"]
    stop = BASE + site + 12
    visited = []
    handle = uc.hook_add(UC_HOOK_CODE, lambda _uc, address, _size, _data: visited.append(address))
    try:
        # LDR HCAP1; original ORR or new BL; unchanged host-high-address store;
        # unchanged HCAP1 publication. No firmware or MMIO stub is required.
        uc.emu_start(BASE + site - 4, stop, count=128)
        if uc.reg_read(registers.UC_ARM64_REG_PC) != stop:
            raise AssertionError("Capability guard did not return to the original store")
        expect_cap = (cap & ~0x200) if patched and not null_pub and runner_flags & 4 else cap | 0x200
        expected_ipc = bytearray(original_ipc)
        struct.pack_into("<I", expected_ipc, 0x58, expect_cap)
        struct.pack_into("<I", expected_ipc, 0x60, 0)
        if bytes(uc.mem_read(IPC, 128)) != bytes(expected_ipc):
            raise AssertionError("IPC publication changed unrelated fields or capability bits")
        for reg in XREGS[1:30]:
            if uc.reg_read(reg) != initial[reg]:
                raise AssertionError("Interior guard clobbered a caller register")
        if uc.reg_read(XREGS[0]) != expect_cap:
            raise AssertionError("HCAP result width/value changed")
        if uc.reg_read(registers.UC_ARM64_REG_SP) != sp or uc.reg_read(registers.UC_ARM64_REG_NZCV) != 0xA0000000:
            raise AssertionError("Stack or condition flags changed")
        expected_lr = BASE + site + 4 if patched else initial[XREGS[30]]
        if uc.reg_read(XREGS[30]) != expected_lr:
            raise AssertionError("Capability helper return address changed")
        if bytes(uc.mem_read(PUB + 0x2AA0, 4)) != struct.pack("<I", runner_flags):
            raise AssertionError("Guard changed offload ownership")
        if bytes(uc.mem_read(sp - 64, 48)) != b"\x5a" * 48 or bytes(uc.mem_read(sp, 64)) != b"\x5a" * 64:
            raise AssertionError("Guard wrote outside its reserved stack pair")
        donor = elf.get_symbol("pcie_coherent_accenable")
        helper = donor.value + hook["entry_interior_offset"]
        for offset in hook["unreachable_offsets"]:
            if BASE + helper + offset in visited:
                raise AssertionError("Guard executed the retained alternative/MMIO load")
    finally:
        uc.hook_del(handle)


def apply_retained_alternative(data, elf, origin):
    section = next(s for s in elf.sections if s.name == ".altinstructions")
    original = next(r for r in elf.relocations if r.section_index == section.index
                    and r.offset % 12 == 0 and r.symbol_section_index == elf.text.index
                    and r.symbol_value + r.addend == origin)
    replacement = next(r for r in elf.relocations if r.section_index == section.index
                       and r.offset == original.offset + 4)
    source = elf.sections[replacement.symbol_section_index]
    length = data[section.offset + original.offset + 11]
    result = bytearray(data)
    source_offset = source.offset + replacement.symbol_value + replacement.addend
    result[elf.text.offset + origin:elf.text.offset + origin + length] = data[source_offset:source_offset + length]
    return bytes(result)


def expect_failure(callback, message):
    try:
        callback()
    except ValueError as exc:
        if message not in str(exc):
            raise AssertionError("Unexpected validation failure: " + str(exc)) from exc
    else:
        raise AssertionError("Unsafe instruction reuse was accepted")


def main():
    patcher = load_patcher()
    spec = json.loads((CI / "ax86u-dhd-scheduler.json").read_text())
    original = (ROOT / spec["target"]).read_bytes()
    patched, _ = patcher.patch_blob(original, spec)
    elf = patcher.ElfFile(original)
    hook = spec["hcap_hook"]
    donor = elf.get_symbol("pcie_coherent_accenable")
    alternative = donor.value + hook["entry_interior_offset"] + 28
    runtime_variant = apply_retained_alternative(patched, elf, alternative)
    caps = [0, 0x200, 0xFFFFFFFF, 0xF8E0283] + [1 << bit for bit in range(32)]
    cases = 0
    for cap in caps:
        for flags in [0, 2, 4, 6, 0xFFFFFFFF]:
            run_window(original, elf, hook, cap, flags, False)
            run_window(patched, elf, hook, cap, flags, True)
            run_window(runtime_variant, elf, hook, cap, flags, True)
            cases += 3
        run_window(patched, elf, hook, cap, 4, True, null_pub=True)
        cases += 1
    # Establish when the flag is populated, rather than assuming the runtime
    # dump's flags already existed at capability-negotiation time.
    boot = hook["boot_order"]
    caller = elf.get_symbol(hook["caller_symbol"])
    preinit_call = caller.value + boot["preinit_call_offset"]
    if not any(r.section_index == elf.text.index and r.offset == preinit_call
               and r.type == 283 and r.symbol_name == "dhd_prot_preinit"
               for r in elf.relocations):
        raise AssertionError("Protocol preinit no longer runs before capability filtering")
    branch_site = caller.value + boot["preinit_success_branch_offset"]
    word = struct.unpack_from("<I", original, elf.text.offset + branch_site)[0]
    immediate = (word >> 5) & 0x7FFFF
    if immediate & 0x40000:
        immediate -= 0x80000
    if (word & 0xFF00001F != 0x34000000 or
            branch_site + immediate * 4 != caller.value + boot["preinit_success_target_offset"]):
        raise AssertionError("Protocol preinit success no longer reaches the capability window")
    for enabled_rx in [False, True]:
        machine = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        machine.mem_map(BASE, (elf.text.size + 4095) & ~4095)
        machine.mem_write(BASE, original[elf.text.offset:elf.text.offset + elf.text.size])
        machine.mem_map(PUB, 0x4000)
        machine.mem_map(STACK, 0x10000)
        frame = STACK + 0x8000
        machine.mem_write(PUB + 0x2AA0, struct.pack("<I", 3))
        machine.mem_write(frame + 0x74, bytes([2 if enabled_rx else 0]))
        machine.reg_write(registers.UC_ARM64_REG_X20, PUB)
        machine.reg_write(registers.UC_ARM64_REG_X29, frame)
        stop = boot["preinit_rx_flag_window_end"] if enabled_rx else boot["preinit_no_rx_target"]
        machine.emu_start(BASE + boot["preinit_rx_flag_window"], BASE + stop, count=16)
        flags = struct.unpack("<I", machine.mem_read(PUB + 0x2AA0, 4))[0]
        if flags != (7 if enabled_rx else 3):
            raise AssertionError("Actual preinit RX flag publication changed")
    # A future patch must not be silently overwritten by the arm64 loader.
    overlapping = copy.deepcopy(spec)
    overlapping["patches"].append({
        "name": "unsafe_alternative_overlap", "symbol": donor.name,
        "symbol_value": donor.value, "offset": alternative - donor.value,
        "original": f"{struct.unpack_from('<I', original, elf.text.offset + alternative)[0]:08x}",
        "replacement": "d503201f",
    })
    expect_failure(lambda: patcher.patch_blob(original, overlapping), "alternative")
    # The donor can never acquire a new ordinary function/address reference.
    data_section = next(s for s in elf.sections if s.name == ".data")
    elf.relocations.append(patcher.ElfRelocation(data_section.index, 0, 257, "",
                                               donor.value, 0, elf.text.index))
    expect_failure(lambda: patcher.validate_unreferenced_code(elf, spec["unreferenced_code"]), "address reference")
    elf.relocations.pop()
    # Exercise the unrelocated-branch guard independently of the input hash.
    pinned = [(e["symbol_value"], e["symbol_value"] + e["size"]) for e in spec["function_checks"]]
    relocations = {r.offset for r in elf.relocations if r.section_index == elf.text.index}
    source = next(offset for offset in range(0, elf.text.size - 3, 4)
                  if offset not in relocations and not any(a <= offset < b for a, b in pinned)
                  and struct.unpack_from("<I", original, elf.text.offset + offset)[0] == 0xD503201F)
    changed = bytearray(original)
    branch = 0x94000000 | (((donor.value - source) // 4) & 0x3FFFFFF)
    struct.pack_into("<I", changed, elf.text.offset + source, branch)
    with_caller = copy.deepcopy(spec)
    with_caller["original_sha256"] = hashlib.sha256(changed).hexdigest()
    expect_failure(lambda: patcher.patch_blob(bytes(changed), with_caller), "unrelocated caller")
    print(f"A64 coherency publication: {cases} execution cases passed; alias, alternative and donor guards checked")


if __name__ == "__main__":
    main()
