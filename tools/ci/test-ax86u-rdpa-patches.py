#!/usr/bin/env python3
"""Run the pinned RDPA reinit machine code with stubbed callees in CI.

The object uses unresolved ELF BL relocations. The harness resolves only those
two call sites to RET stubs, as a module linker would, then runs the original
and production-patched 64-byte function. It does not execute router hardware.
"""

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
    UC_ARM64_REG_W2,
    UC_ARM64_REG_X0,
    UC_ARM64_REG_X1,
    UC_ARM64_REG_X2,
    UC_ARM64_REG_X19,
    UC_ARM64_REG_X20,
)


ROOT = Path(__file__).resolve().parents[2]
CI_DIR = Path(__file__).resolve().parent
SPEC_PATH = CI_DIR / "ax86u-rdpa-patches.json"
CODE = 0x100000
UNINIT = CODE + 0x1000
INIT = CODE + 0x2000
RETURN = CODE + 0x3000
STACK = 0x200000
STACK_SIZE = 0x10000
CFG = 0x300000
RET = struct.pack("<I", 0xD65F03C0)


def load_patcher(path):
    spec = importlib.util.spec_from_file_location("ax86u_prebuilt_patcher", path)
    if not spec or not spec.loader:
        raise RuntimeError(f"Cannot import production patcher: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def branch_link(source, destination):
    delta = destination - source
    if delta % 4 or not -(1 << 27) <= delta < (1 << 27):
        raise AssertionError("Unencodable stub BL relocation")
    return struct.pack("<I", 0x94000000 | ((delta // 4) & 0x3FFFFFF))


def run_function(function, uninit_status, init_status, count):
    if len(function) != 64:
        raise AssertionError("Unexpected reinit function size")
    code = bytearray(function)
    # These ELF relocation slots call uninit(..., &count), then init(..., count).
    for offset, target in ((0x18, UNINIT), (0x2C, INIT)):
        if struct.unpack_from("<I", code, offset)[0] != 0x94000000:
            raise AssertionError("Unexpected BL relocation slot")
        code[offset:offset + 4] = branch_link(CODE + offset, target)

    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    uc.mem_map(CODE, 0x4000)
    uc.mem_map(STACK, STACK_SIZE)
    uc.mem_map(CFG, 0x1000)
    uc.mem_write(CODE, bytes(code))
    uc.mem_write(UNINIT, RET)
    uc.mem_write(INIT, RET)
    uc.mem_write(RETURN, RET)
    initial_sp = STACK + STACK_SIZE - 0x100
    uc.reg_write(UC_ARM64_REG_SP, initial_sp)
    uc.reg_write(UC_ARM64_REG_LR, RETURN)
    uc.reg_write(UC_ARM64_REG_X0, 1)  # 5 GHz DHD radio index.
    uc.reg_write(UC_ARM64_REG_X1, CFG)
    uc.reg_write(UC_ARM64_REG_X19, 0x19191919)
    uc.reg_write(UC_ARM64_REG_X20, 0x20202020)
    calls = []

    def stub(machine, address, _size, _data):
        if address not in (UNINIT, INIT):
            return
        if machine.reg_read(UC_ARM64_REG_X0) != 1:
            raise AssertionError("Radio index changed across reinit")
        if machine.reg_read(UC_ARM64_REG_X1) != CFG:
            raise AssertionError("Init configuration pointer changed")
        if address == UNINIT:
            calls.append("uninit")
            out = machine.reg_read(UC_ARM64_REG_X2)
            if not initial_sp - 0x30 <= out < initial_sp:
                raise AssertionError("Count output is not in the reinit stack frame")
            if uninit_status == 0:
                machine.mem_write(out, struct.pack("<I", count))
            machine.reg_write(UC_ARM64_REG_W0, uninit_status & 0xFFFFFFFF)
        else:
            calls.append("init")
            if machine.reg_read(UC_ARM64_REG_W2) != count:
                raise AssertionError("Reinit changed the posted-buffer count")
            machine.reg_write(UC_ARM64_REG_W0, init_status & 0xFFFFFFFF)

    uc.hook_add(UC_HOOK_CODE, stub)
    uc.emu_start(CODE, RETURN, count=1000)
    if uc.reg_read(UC_ARM64_REG_PC) != RETURN:
        raise AssertionError("Reinit did not return within its instruction budget")
    if uc.reg_read(UC_ARM64_REG_SP) != initial_sp:
        raise AssertionError("Reinit did not restore its stack")
    if uc.reg_read(UC_ARM64_REG_X19) != 0x19191919:
        raise AssertionError("Reinit clobbered callee-saved X19")
    if uc.reg_read(UC_ARM64_REG_X20) != 0x20202020:
        raise AssertionError("Reinit clobbered callee-saved X20")
    status = uc.reg_read(UC_ARM64_REG_W0)
    return status - (1 << 32) if status & (1 << 31) else status, calls


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: test-ax86u-rdpa-patches.py PRODUCTION_PATCHER.py")
    patcher = load_patcher(Path(sys.argv[1]).resolve())
    spec = json.loads(SPEC_PATH.read_text())
    original = (ROOT / spec["target"]).read_bytes()
    patched, _report = patcher.patch_blob(original, spec)
    original_elf = patcher.ElfFile(original)
    patched_elf = patcher.ElfFile(patched)
    symbol = "rdd_dhd_rx_post_reinit"
    before = original_elf.symbol_bytes(symbol)
    after = patched_elf.symbol_bytes(symbol)
    cases = (
        (0, 0, 1023),
        (0, 0, 0),
        (0, 0, 7),
        (2, 0, 1023),
        (-12, 0, 1023),
        (0, 2, 1023),
        (0, -12, 1023),
        (0, -5, 63),
        (-5, -12, 1023),
    )
    for uninit_status, init_status, count in cases:
        expected_calls = ["uninit"] + ([] if uninit_status else ["init"])
        old_status, old_calls = run_function(before, uninit_status, init_status, count)
        new_status, new_calls = run_function(after, uninit_status, init_status, count)
        expected_status = uninit_status or init_status
        if old_status != 0 or old_calls != expected_calls:
            raise AssertionError("Original behavior no longer matches the pinned defect")
        if new_status != expected_status or new_calls != expected_calls:
            raise AssertionError(
                f"Patched reinit: expected status/calls {expected_status}/{expected_calls}, "
                f"got {new_status}/{new_calls}"
            )
    print(f"RDPA RX-post reinit: {len(cases)} original/patched semantic cases passed")


if __name__ == "__main__":
    main()
