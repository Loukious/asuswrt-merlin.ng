#!/usr/bin/env python3
"""Execute the actual original/patched HWA event handler in CI with stubs."""
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
    UC_ARM64_REG_W4,
    UC_ARM64_REG_X0,
    UC_ARM64_REG_X1,
    UC_ARM64_REG_X2,
    UC_ARM64_REG_X3,
    UC_ARM64_REG_X19,
    UC_ARM64_REG_X20,
    UC_ARM64_REG_X21,
    UC_ARM64_REG_X22,
    UC_ARM64_REG_X23,
    UC_ARM64_REG_X24,
    UC_ARM64_REG_X25,
    UC_ARM64_REG_X26,
    UC_ARM64_REG_X27,
    UC_ARM64_REG_X28,
    UC_ARM64_REG_X29,
)

ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = Path(__file__).resolve().parent / "ax86u-dhd-scheduler.json"
BASE, PUB, PROT, RING = 0x10000000, 0x20000000, 0x21000000, 0x22000000
HELPER, DEBUG, STACK = 0x23000000, 0x24000000, 0x25000000
BUS = 0x26000000
RET = struct.pack("<I", 0xD65F03C0)


def load_patcher(path):
    spec = importlib.util.spec_from_file_location("ax86u_hwa_patch", path)
    if not spec or not spec.loader:
        raise RuntimeError(f"Cannot import production patcher: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def branch_link(source, target):
    delta = target - source
    if delta % 4 or not -(1 << 27) <= delta < (1 << 27):
        raise AssertionError("Unencodable dependency relocation")
    return struct.pack("<I", 0x94000000 | ((delta // 4) & 0x3FFFFFF))


def run_handler(elf, reason, pending, debug, offloaded=True,
                reinit_status=0, nonoffload_path="early"):
    function = elf.get_symbol("dhd_prot_process_hwa_event")
    getter = elf.get_symbol("dhd_prot_dma_indx_get.isra.15")
    setter = elf.get_symbol("dhd_prot_dma_indx_set.isra.16")
    reset = elf.get_symbol("dhd_prot_ring_reset.isra.9")
    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    text_end = (elf.text.size + 0xFFF) & ~0xFFF
    stub_base = BASE + text_end
    names = ("dhd_runner_notify", "dhd_prot_rxpost_upd", "dhd_hwa_event",
             "printk", "osl_delay", "dhd_bus_cmn_readshared",
             "dhd_bus_cmn_writeshared", "unexpected_dependency")
    stubs = {name: stub_base + i * 0x1000 for i, name in enumerate(names)}
    stop = stub_base + len(names) * 0x1000
    uc.mem_map(BASE, text_end + (len(names) + 1) * 0x1000)
    uc.mem_write(BASE, elf.data[elf.text.offset:elf.text.offset + elf.text.size])
    for address, size in ((PUB, 0x4000), (PROT, 0x1000), (RING, 0x1000),
                          (HELPER, 0x1000), (DEBUG, 0x1000), (STACK, 0x10000)):
        uc.mem_map(address, size)
    for address in (*stubs.values(), stop):
        uc.mem_write(address, RET)
    getter_stub = BASE + getter.value
    uc.mem_write(getter_stub, RET)
    setter_stub = BASE + setter.value
    uc.mem_write(setter_stub, RET)

    # Resolve the handler's actual literal-pool relocations, including its
    # signed-byte switch table. Keep the table/data from the original object.
    symbol_tables = {}
    for section in elf.sections:
        if section.type != 2:
            continue
        strings_section = elf.sections[section.link]
        strings = elf.data[strings_section.offset:strings_section.offset + strings_section.size]
        records = []
        for position in range(section.offset, section.offset + section.size, 24):
            name, _info, _other, index, value, _size = struct.unpack_from(
                "<IBBHQQ", elf.data, position)
            end = strings.find(b"\0", name)
            records.append((strings[name:end].decode("utf-8", errors="replace"), index, value))
        symbol_tables[section.index] = records
    mapped_sections = {elf.text.index: BASE}

    def section_base(index):
        if index in mapped_sections:
            return mapped_sections[index]
        section = elf.sections[index]
        address = 0x30000000 + index * 0x1000000
        length = max(0x1000, (section.size + 0xFFF) & ~0xFFF)
        uc.mem_map(address, length)
        if section.type != 8:
            uc.mem_write(address, elf.data[section.offset:section.offset + section.size])
        mapped_sections[index] = address
        return address

    for section in elf.sections:
        if section.type != 4 or section.info != elf.text.index:
            continue
        for position in range(section.offset, section.offset + section.size, 24):
            offset, info, addend = struct.unpack_from("<QQq", elf.data, position)
            if not (function.value <= offset < function.value + function.size + 64 or
                    reset.value <= offset < reset.value + reset.size + 24):
                continue
            name, index, value = symbol_tables[section.link][info >> 32]
            relocation_type = info & 0xFFFFFFFF
            address = BASE + offset
            if relocation_type == 257:
                target = DEBUG if name == "dhd_msg_level" else section_base(index) + value + addend
                uc.mem_write(address, struct.pack("<Q", target))
            elif relocation_type == 283:
                if addend:
                    raise AssertionError("Unexpected dependency-call addend")
                target = stubs.get(name, stubs["unexpected_dependency"])
                uc.mem_write(address, branch_link(address, target))
            else:
                raise AssertionError(f"Unsupported handler relocation: {relocation_type}")

    uc.mem_write(DEBUG, struct.pack("<I", int(debug)))
    uc.mem_write(PUB + 8, struct.pack("<Q", BUS))
    uc.mem_write(PUB + 0x10, struct.pack("<Q", PROT))
    uc.mem_write(PUB + 0x24, struct.pack("<I", 1))
    uc.mem_write(PUB + 0x2A98, struct.pack("<Q", HELPER))
    uc.mem_write(PUB + 0x2AA0, struct.pack("<I", (1 << 2) if offloaded else 0))
    uc.mem_write(PROT + 0x228, struct.pack("<Q", RING))
    uc.mem_write(PROT + 0x798, b"\xaa")
    uc.mem_write(PROT + 0x799, b"\x01")
    uc.mem_write(RING + 2, struct.pack("<H", 4))
    if nonoffload_path not in ("early", "shared", "dma"):
        raise AssertionError("Unknown non-offloaded fixture path")
    if not offloaded and nonoffload_path != "early":
        # Activate the real handler's non-offloaded ring-reset/index paths.
        # An empty RxPost ring avoids packet freeing while exercising each
        # independent incoming branch to the shared RX-post-update block.
        uc.mem_write(PROT + 0x794, struct.pack("<I", 1 << 24))
        uc.mem_write(PROT + 0xC2, struct.pack("<H", 1))
        uc.mem_write(PROT + 0xC8, struct.pack("<H", 1))
        uc.mem_write(PROT + 0xCA, struct.pack("<H", 8))
        uc.mem_write(PROT + 0xCC, struct.pack("<H", 1024))
        dma = bytes([int(nonoffload_path == "dma")])
        uc.mem_write(PUB + 0x2A19, dma)
        uc.mem_write(PUB + 0x2A1A, dma)
    rd, wr = (5, 9) if pending else (9, 9)
    calls = {"reinit": 0, "update": 0, "hwa": 0, "print": 0, "delay": 0,
             "shared_read": 0, "shared_write": 0, "dma_write": 0, "reset": 0}

    def dependencies(machine, address, _size, _data):
        if address == BASE + reset.value:
            if offloaded or machine.reg_read(UC_ARM64_REG_X0) != PROT + 0xC0:
                raise AssertionError("Non-offloaded ring reset argument changed")
            # Execute the actual reset and its actual empty-buffer helper.
            calls["reset"] += 1
        elif address == getter_stub:
            if (machine.reg_read(UC_ARM64_REG_X0) != PROT or
                    machine.reg_read(UC_ARM64_REG_X1) != PUB + 0x2A98):
                raise AssertionError("HWA drain getter ABI changed")
            kind = machine.reg_read(UC_ARM64_REG_W2)
            ringid = machine.reg_read(UC_ARM64_REG_W3)
            if not offloaded and kind == 7 and ringid == 1:
                machine.reg_write(UC_ARM64_REG_W0, 0)
                return
            if ringid != 4:
                raise AssertionError("HWA drain ring ID changed")
            if kind not in (8, 9):
                raise AssertionError("Unexpected HWA drain index type")
            machine.reg_write(UC_ARM64_REG_W0, rd if kind == 9 else wr)
        elif address == stubs["dhd_runner_notify"]:
            if (machine.reg_read(UC_ARM64_REG_X0) != HELPER or
                    machine.reg_read(UC_ARM64_REG_W1) != 14 or
                    machine.reg_read(UC_ARM64_REG_X2) != 0 or
                    machine.reg_read(UC_ARM64_REG_X3) != 0):
                raise AssertionError("Unexpected HWA reinit operation")
            calls["reinit"] += 1
            machine.reg_write(UC_ARM64_REG_W0, reinit_status & 0xFFFFFFFF)
        elif address == stubs["dhd_prot_rxpost_upd"]:
            if machine.reg_read(UC_ARM64_REG_X0) != PUB:
                raise AssertionError("RX-post update pub argument changed")
            if offloaded and reason == 0 and calls["reinit"] != 1:
                raise AssertionError("RX-post update preceded its reinitialization")
            calls["update"] += 1
            machine.reg_write(UC_ARM64_REG_W0, 0xA5A5)
        elif address == stubs["dhd_hwa_event"]:
            if machine.reg_read(UC_ARM64_REG_X0) != PUB:
                raise AssertionError("HWA event pub argument changed")
            calls["hwa"] += 1
        elif address == stubs["printk"]:
            calls["print"] += 1
        elif address == stubs["osl_delay"]:
            if machine.reg_read(UC_ARM64_REG_W0) != 1000:
                raise AssertionError("HWA drain delay changed")
            calls["delay"] += 1
        elif address == stubs["dhd_bus_cmn_readshared"]:
            if (offloaded or machine.reg_read(UC_ARM64_REG_X0) != BUS or
                    machine.reg_read(UC_ARM64_REG_W2) != 0x13 or
                    machine.reg_read(UC_ARM64_REG_W3) != 1 or
                    machine.reg_read(UC_ARM64_REG_X1) != PROT + 0xC4):
                raise AssertionError("Non-offloaded shared reader ABI changed")
            machine.mem_write(PROT + 0xC4, struct.pack("<H", 0))
            calls["shared_read"] += 1
            machine.reg_write(UC_ARM64_REG_W0, 0x1234)
        elif address == stubs["dhd_bus_cmn_writeshared"]:
            kind = machine.reg_read(UC_ARM64_REG_W2)
            expected_pointer = PROT + (0xC4 if kind == 0x13 else 0xC6)
            if (offloaded or kind not in (0x13, 0x14) or
                    machine.reg_read(UC_ARM64_REG_X0) != BUS or
                    machine.reg_read(UC_ARM64_REG_X1) != expected_pointer or
                    machine.reg_read(UC_ARM64_REG_W3) != 1):
                raise AssertionError("Non-offloaded shared writer ABI changed")
            calls["shared_write"] += 1
            # Its void result is deliberately nonzero: these paths must not
            # be redirected through the reinitialization status guard.
            machine.reg_write(UC_ARM64_REG_W0, 0x1234)
        elif address == setter_stub:
            if (offloaded or machine.reg_read(UC_ARM64_REG_X0) != PROT or
                    machine.reg_read(UC_ARM64_REG_X1) != PUB + 0x2A98 or
                    machine.reg_read(UC_ARM64_REG_W2) != 0 or
                    machine.reg_read(UC_ARM64_REG_W3) not in (6, 7) or
                    machine.reg_read(UC_ARM64_REG_W4) != 1):
                raise AssertionError("Non-offloaded DMA writer ABI changed")
            calls["dma_write"] += 1
            machine.reg_write(UC_ARM64_REG_W0, 0x1234)
        elif address == stubs["unexpected_dependency"]:
            raise AssertionError("Unexpected dependency executed in isolated handler")

    uc.hook_add(UC_HOOK_CODE, dependencies)
    saved_registers = (UC_ARM64_REG_X19, UC_ARM64_REG_X20, UC_ARM64_REG_X21,
                       UC_ARM64_REG_X22, UC_ARM64_REG_X23, UC_ARM64_REG_X24,
                       UC_ARM64_REG_X25, UC_ARM64_REG_X26, UC_ARM64_REG_X27,
                       UC_ARM64_REG_X28, UC_ARM64_REG_X29)
    preserved = {register: 0xA100000000000000 + i * 0x1000
                 for i, register in enumerate(saved_registers)}
    for register, value in preserved.items():
        uc.reg_write(register, value)
    initial_sp = STACK + 0xFF00
    uc.reg_write(UC_ARM64_REG_X0, PUB)
    uc.reg_write(UC_ARM64_REG_W1, reason)
    uc.reg_write(UC_ARM64_REG_SP, initial_sp)
    uc.reg_write(UC_ARM64_REG_LR, stop)
    uc.emu_start(BASE + function.value, stop, count=100000)
    if uc.reg_read(UC_ARM64_REG_PC) != stop or uc.reg_read(UC_ARM64_REG_SP) != initial_sp:
        raise AssertionError("Handler did not return through its balanced epilogue")
    if uc.reg_read(UC_ARM64_REG_LR) != stop:
        raise AssertionError("Handler return address was not restored")
    for register, value in preserved.items():
        if uc.reg_read(register) != value:
            raise AssertionError("Handler failed to restore a callee-saved register")
    return calls, uc.mem_read(PROT + 0x798, 1)[0]


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: test-ax86u-dhd-hwa-timeout.py PRODUCTION_PATCHER.py")
    patcher = load_patcher(Path(sys.argv[1]).resolve())
    metadata = json.loads(SPEC_PATH.read_text())
    original = (ROOT / metadata["target"]).read_bytes()
    patched, _report = patcher.patch_blob(original, metadata)
    old, new = patcher.ElfFile(original), patcher.ElfFile(patched)
    if old.relocations != new.relocations:
        raise AssertionError("HWA guard changed relocation metadata")
    for offset in (0x2E560, 0x2E568):
        if original[old.text.offset + offset:old.text.offset + offset + 4] != patched[
                new.text.offset + offset:new.text.offset + offset + 4]:
            raise AssertionError("HWA guard changed an existing dependency-call instruction")
    for debug in (False, True):
        # The actual event switch and bcmevent.h identify RX STOP as reason 0.
        before, old_stop = run_handler(old, 0, pending=True, debug=debug)
        after, new_stop = run_handler(new, 0, pending=True, debug=debug)
        expected_old = {"reinit": 1, "update": 1, "hwa": 0,
                        "print": int(debug), "delay": 100,
                        "shared_read": 0, "shared_write": 0, "dma_write": 0, "reset": 0}
        expected_new = dict(expected_old, reinit=0, update=0)
        if before != expected_old or after != expected_new or (old_stop, new_stop) != (1, 1):
            raise AssertionError(f"Timeout guard changed: {before}/{after}")
        if run_handler(old, 0, pending=False, debug=debug) != run_handler(
                new, 0, pending=False, debug=debug):
            raise AssertionError("Successful RX drain behavior changed")
        for status in (-1, -2, -23, 1, 7):
            before, old_stop = run_handler(old, 0, pending=False, debug=debug,
                                           reinit_status=status)
            after, new_stop = run_handler(new, 0, pending=False, debug=debug,
                                          reinit_status=status)
            if before["reinit"] != 1 or before["update"] != 1:
                raise AssertionError("Original reinit-error suppression witness changed")
            if (after["reinit"] != 1 or after["update"] != 0 or
                    after["hwa"] != 0 or (old_stop, new_stop) != (1, 1)):
                raise AssertionError(f"Failed reinitialization still updates hardware: {after}")
        for reason in (1, 2, 3):
            if run_handler(old, reason, pending=False, debug=debug) != run_handler(
                    new, reason, pending=False, debug=debug):
                raise AssertionError("Non-STOP HWA event behavior changed")
        for path in ("early", "shared", "dma"):
            before = run_handler(old, 0, pending=False, debug=debug,
                                 offloaded=False, nonoffload_path=path)
            after = run_handler(new, 0, pending=False, debug=debug,
                                offloaded=False, nonoffload_path=path)
            if before != after or after[0]["update"] != 1 or after[0]["reinit"]:
                raise AssertionError(f"Non-offloaded STOP behavior changed: {path}")
            expected_writes = 0 if path == "early" else 2
            actual_writes = after[0]["shared_write"] + after[0]["dma_write"]
            if actual_writes != expected_writes:
                raise AssertionError(f"Did not exercise expected non-offloaded index path: {path}")
            if after[0]["reset"] != int(path != "early"):
                raise AssertionError(f"Did not execute expected non-offloaded ring reset: {path}")
    print("HWA STOP: timeout/reinit-error aborts preserve calls, arguments, stack and registers; normal/non-offloaded paths preserved")


if __name__ == "__main__":
    main()
