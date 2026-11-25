#!/usr/bin/env python3
"""Demonstrate cached-reader retry suppression in the actual vendor code in CI.

Run the original scheduler and original get_read_addr in Unicorn. Only their
external dependencies are stubbed; relocation slots are linked to those stubs.
The production replacement is tested separately by test-ax86u-dhd-scheduler.c.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys

from unicorn import Uc, UC_ARCH_ARM64, UC_HOOK_CODE, UC_MODE_ARM
from unicorn.arm64_const import (
    UC_ARM64_REG_LR,
    UC_ARM64_REG_PC,
    UC_ARM64_REG_SP,
    UC_ARM64_REG_W0,
    UC_ARM64_REG_W1,
    UC_ARM64_REG_W2,
    UC_ARM64_REG_W3,
    UC_ARM64_REG_X0,
    UC_ARM64_REG_X1,
)

ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = Path(__file__).resolve().parent / "ax86u-dhd-scheduler.json"
BASE = 0x10000000
PUB = 0x20000000
PROT = 0x21000000
RING = 0x22000000
RX_DATA = 0x23000000
HELPER = 0x24000000
DEBUG = 0x25000000
STACK = 0x26000000
RET = struct.pack("<I", 0xD65F03C0)


def load_parser(path):
    spec = importlib.util.spec_from_file_location("ax86u_original_elf", path)
    if not spec or not spec.loader:
        raise RuntimeError(f"Cannot import ELF parser: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.ElfFile


def branch_link(source, target):
    delta = target - source
    if delta % 4 or not -(1 << 27) <= delta < (1 << 27):
        raise AssertionError("Unencodable dependency relocation")
    return struct.pack("<I", 0x94000000 | ((delta // 4) & 0x3FFFFFF))


def demonstrate(elf, ipend):
    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    text_end = (elf.text.size + 0xFFF) & ~0xFFF
    notify_stub = BASE + text_end
    cache_stub = notify_stub + 0x1000
    stop = notify_stub + 0x2000
    uc.mem_map(BASE, text_end + 0x3000)
    uc.mem_write(BASE, elf.data[elf.text.offset:elf.text.offset + elf.text.size])
    for address, size in ((PUB, 0x4000), (PROT, 0x1000), (RING, 0x1000),
                          (RX_DATA, 0x3000), (HELPER, 0x1000), (DEBUG, 0x1000),
                          (STACK, 0x10000)):
        uc.mem_map(address, size)

    scheduler = elf.get_symbol("dhd_prot_schedule_runner")
    reader = elf.get_symbol("dhd_prot_get_read_addr")
    getter = elf.get_symbol("dhd_prot_dma_indx_get.isra.15")
    getter_stub = BASE + getter.value
    uc.mem_write(getter_stub, RET)
    uc.mem_write(notify_stub, RET)
    uc.mem_write(cache_stub, RET)
    uc.mem_write(stop, RET)
    linked_notify = linked_cache = linked_debug = 0
    for relocation in elf.relocations:
        if relocation.section_index != elf.text.index:
            continue
        address = BASE + relocation.offset
        if scheduler.value <= relocation.offset < scheduler.value + scheduler.size:
            if relocation.symbol_name == "dhd_runner_notify":
                if relocation.type != 283 or relocation.addend:
                    raise AssertionError("Changed scheduler dependency relocation")
                uc.mem_write(address, branch_link(address, notify_stub))
                linked_notify += 1
        if reader.value <= relocation.offset < reader.value + reader.size + 24:
            if relocation.symbol_name == "osl_cache_inv":
                if relocation.type != 283 or relocation.addend:
                    raise AssertionError("Changed read-address dependency relocation")
                uc.mem_write(address, branch_link(address, cache_stub))
                linked_cache += 1
            if relocation.symbol_name == "dhd_msg_level":
                if relocation.type != 257 or relocation.addend:
                    raise AssertionError("Changed debug-data relocation")
                uc.mem_write(address, struct.pack("<Q", DEBUG))
                linked_debug += 1
    if (linked_notify, linked_cache, linked_debug) != (2, 1, 1):
        raise AssertionError("Original dependency shape changed")

    uc.mem_write(PUB + 0x10, struct.pack("<Q", PROT))
    uc.mem_write(PUB + 0x2A19, b"\x01")
    uc.mem_write(PUB + 0x2A1A, b"\x01")
    uc.mem_write(PUB + 0x2A98, struct.pack("<Q", HELPER))
    uc.mem_write(PUB + 0x2AA0, struct.pack("<I", 1 << 2))
    uc.mem_write(PROT + 0x228, struct.pack("<Q", RING))
    uc.mem_write(RING + 2, struct.pack("<H", 4))
    uc.mem_write(RING + 4, struct.pack("<H", 5))
    uc.mem_write(RING + 0xA, struct.pack("<H", 8))
    uc.mem_write(RING + 0xC, struct.pack("<H", 1024))
    uc.mem_write(RING + 0x18, struct.pack("<Q", RX_DATA))
    actual_dma_rd, actual_dma_wr = 5, 9
    calls = {"wr_reads": 0, "notify": 0, "delivered": 0, "cache": 0}

    def dependencies(machine, address, _size, _data):
        if address == getter_stub:
            if (machine.reg_read(UC_ARM64_REG_X0) != PROT or
                    machine.reg_read(UC_ARM64_REG_X1) != PUB + 0x2A98 or
                    machine.reg_read(UC_ARM64_REG_W2) != 8 or
                    machine.reg_read(UC_ARM64_REG_W3) != 4):
                raise AssertionError("Original reader no longer reads only WR8")
            calls["wr_reads"] += 1
            machine.reg_write(UC_ARM64_REG_W0, actual_dma_wr)
        elif address == notify_stub:
            if (machine.reg_read(UC_ARM64_REG_X0) != HELPER or
                    machine.reg_read(UC_ARM64_REG_W1) != 5):
                raise AssertionError("Unexpected original scheduling notification")
            calls["notify"] += 1
            calls["delivered"] += int(not ipend)
            machine.reg_write(UC_ARM64_REG_W0, 0)
        elif address == cache_stub:
            if (machine.reg_read(UC_ARM64_REG_X0) != RX_DATA + actual_dma_rd * 8 or
                    machine.reg_read(UC_ARM64_REG_W1) != 32):
                raise AssertionError("Original cached-consumer span changed")
            calls["cache"] += 1

    uc.hook_add(UC_HOOK_CODE, dependencies)
    for _ in range(2):
        uc.reg_write(UC_ARM64_REG_X0, PUB)
        uc.reg_write(UC_ARM64_REG_SP, STACK + 0xFF00)
        uc.reg_write(UC_ARM64_REG_LR, stop)
        uc.emu_start(BASE + scheduler.value, stop, count=1000)
        if uc.reg_read(UC_ARM64_REG_PC) != stop:
            raise AssertionError("Original scheduler did not return")
    cached_rd = struct.unpack("<H", uc.mem_read(RING + 4, 2))[0]
    if cached_rd != actual_dma_wr or actual_dma_rd == actual_dma_wr:
        raise AssertionError("Expected cached-reader/live-consumer mismatch")
    expected = {"wr_reads": 2, "notify": 1, "delivered": int(not ipend), "cache": 1}
    if calls != expected:
        raise AssertionError(f"Original retry suppression changed: {calls}")


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: test-ax86u-dhd-original-scheduler.py ELF_PARSER.py")
    metadata = json.loads(SPEC_PATH.read_text())
    original = (ROOT / metadata["target"]).read_bytes()
    if hashlib.sha256(original).hexdigest() != metadata["original_sha256"]:
        raise AssertionError("Original DHD object changed")
    elf = load_parser(Path(sys.argv[1]).resolve())(original)
    for check in metadata["function_checks"]:
        function = elf.get_symbol(check["symbol"])
        if function.value != check["symbol_value"] or function.size != check["size"]:
            raise AssertionError("Pinned function extent changed")
        if hashlib.sha256(elf.symbol_bytes(function.name)).hexdigest() != check["sha256"]:
            raise AssertionError("Pinned function body changed")
    demonstrate(elf, ipend=0)
    demonstrate(elf, ipend=1)
    print("Original DHD scheduler: actual cached-RD advance suppresses the next pending RX retry")


if __name__ == "__main__":
    main()
