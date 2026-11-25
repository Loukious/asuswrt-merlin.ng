#!/usr/bin/env python3
"""Execute original/patched RT-AX86U instructions in Unicorn; CI only.

Only external driver services are stubbed. The masking, NULL guards, table
walk, allocation rollback, function prologues/epilogues, and free ordering
execute the bytes from the pinned production ELF object.
"""

import hashlib
import importlib.util
import json
from pathlib import Path
import random
import struct
import unittest

from unicorn import Uc, UcError, UC_ARCH_ARM64, UC_MODE_ARM
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_INVALID
from unicorn import arm64_const as registers


ROOT = Path(__file__).resolve().parents[2]
CODE = 0x1000000
RAM = 0x2000000
RAM_SIZE = 0x100000
STOP = 0x1900000
WLC = RAM + 0x10000
CFG = RAM + 0x20000
INFO = RAM + 0x30000
TABLE = RAM + 0x40000
PARAM = RAM + 0x50000
RRM = RAM + 0x60000
PUBLIC = RAM + 0x70000
EXTCAP = RAM + 0x80000
PRIVATE = RAM + 0x90000
WLCIF = RAM + 0xA0000
OTHER = RAM + 0xB0000
EXTRA = RAM + 0xC0000
STACK = RAM + 0xFF000
X = [getattr(registers, "UC_ARM64_REG_X%d" % i) for i in range(31)]


class Machine:
    def __init__(self, code):
        self.uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        self.uc.mem_map(CODE, (len(code) + 4095) & ~4095)
        self.uc.mem_write(CODE, code)
        self.uc.mem_map(STOP, 4096)
        self.uc.mem_map(RAM, RAM_SIZE)
        self.callbacks = {}
        self.endpoints = {STOP}
        self.faults = []
        self.visited_slots = []
        self.mfree_calls = []
        self.extcap_calls = []
        for i, register in enumerate(X):
            self.uc.reg_write(register, 0x880000 + i * 0x101)
        self.uc.reg_write(registers.UC_ARM64_REG_SP, STACK)
        self.uc.reg_write(X[30], STOP)
        self.uc.reg_write(registers.UC_ARM64_REG_NZCV, 0xA0000000)
        self.uc.hook_add(UC_HOOK_CODE, self._code_hook)
        self.uc.hook_add(UC_HOOK_MEM_INVALID, self._invalid_memory)

    def _code_hook(self, uc, address, size, _):
        if address in self.endpoints:
            uc.emu_stop()
        elif address in self.callbacks:
            self.callbacks[address](self)
        elif address == CODE + 0x2FA388:
            self.visited_slots.append(self.reg(0))

    def _invalid_memory(self, uc, access, address, size, value, _):
        self.faults.append((address, size, uc.reg_read(registers.UC_ARM64_REG_PC)))
        return False

    def reg(self, number, value=None):
        if value is None:
            return self.uc.reg_read(X[number])
        self.uc.reg_write(X[number], value)

    def put64(self, address, value):
        self.uc.mem_write(address, struct.pack("<Q", value))

    def get64(self, address):
        return struct.unpack("<Q", self.uc.mem_read(address, 8))[0]

    def put32(self, address, value):
        self.uc.mem_write(address, struct.pack("<I", value & 0xFFFFFFFF))

    def return_stub(self, result=0):
        # AAPCS64 caller-saved registers may be clobbered by these services.
        target = self.reg(30)
        for i in range(19):
            self.reg(i, 0xCC0000 + i * 0x101)
        self.reg(0, result)
        self.uc.reg_write(registers.UC_ARM64_REG_PC, target)

    def callsite(self, offset, callback):
        def service(machine):
            machine.reg(30, CODE + offset + 4)
            callback(machine)
        self.callbacks[CODE + offset] = service

    def preserve(self):
        return tuple(self.reg(i) for i in range(19, 30)) + (
            self.uc.reg_read(registers.UC_ARM64_REG_SP),)

    def run(self, offset):
        self.uc.emu_start(CODE + offset, STOP + 4, count=10000)
        if self.uc.reg_read(registers.UC_ARM64_REG_PC) not in self.endpoints:
            raise AssertionError("Emulation did not reach a declared endpoint")

    def prepare_rrm(self, bitmap, selected_info=True):
        self.put64(CFG, WLC)
        self.put64(CFG + 0xB0, INFO if selected_info else 0)
        self.put64(CFG + 0x320, EXTCAP)
        self.put64(WLC, PUBLIC)
        self.put64(WLC + 0x2E8, TABLE)
        self.put32(RRM + 0x30, 0x400)
        self.uc.mem_write(PARAM, bitmap)
        self.uc.mem_write(CFG + 0x428, b"\xa5" * 5)
        self.uc.mem_write(PUBLIC + 0x68, b"\xa5")
        frame = STACK - 0xC0
        self.reg(29, frame)
        self.uc.reg_write(registers.UC_ARM64_REG_SP, frame)
        # Exact save layout from wlc_rrm_doiovar's original prologue.
        for i in range(19, 29):
            self.put64(frame + 0x10 + (i - 19) * 8, 0x770000 + i)
        self.put64(frame, 0x77001D)
        self.put64(frame + 8, STOP)
        self.reg(20, CFG)
        self.reg(21, RRM)
        self.reg(22, WLC)
        self.reg(27, CFG + 0x400)
        self.reg(28, PARAM)
        self.reg(0, 0x1C)

        def extcap(machine):
            machine.extcap_calls.append((machine.reg(0), machine.reg(1), machine.reg(2)))
            machine.return_stub()
        for offset in (0x2FA2F0, 0x2FA304, 0x2FA318):
            self.callsite(offset, extcap)
        for offset in (0x2F30E8, 0x2F3710):
            self.callbacks[CODE + offset] = lambda machine: machine.return_stub()

    def prepare_lifetime(self, index, primary=False):
        self.put64(WLC + 8, PRIVATE)
        self.put64(WLC + 0xB0, PRIVATE)
        self.put64(WLC + 0x2E8, TABLE)
        self.put64(WLC + 0x2F0, CFG if primary else OTHER)
        self.put32(PRIVATE + 0x48, 43)
        self.put64(CFG, WLC)
        self.put64(CFG + 0x10, WLCIF)
        self.put64(CFG + 0xB0, INFO)
        self.uc.mem_write(CFG + 0x48, bytes([index]))
        self.put64(TABLE + index * 8, CFG)

        def mfree(machine):
            machine.mfree_calls.append((machine.reg(0), machine.reg(1),
                machine.get64(TABLE + index * 8)))
            if machine.reg(0) != WLC or machine.reg(1) != CFG:
                raise AssertionError("Original mfree arguments were changed")
            # Detect any subsequent read of the freed configuration itself.
            machine.uc.mem_unmap(CFG, 4096)
            machine.return_stub()
        self.callbacks[CODE + 0x2792D0] = mfree


class RrmPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = ROOT / "tools/ci/patch-ax86u-drivers.py"
        loader = importlib.util.spec_from_file_location("ax86u_driver_patcher", path)
        module = importlib.util.module_from_spec(loader)
        # dataclasses and other module-aware code need this registration.
        import sys
        sys.modules[loader.name] = module
        loader.loader.exec_module(module)
        spec = json.loads((ROOT / "tools/ci/ax86u-rrm-patches.json").read_text())
        original = (ROOT / spec["target"]).read_bytes()
        if hashlib.sha256(original).hexdigest() != spec["original_sha256"]:
            raise AssertionError("RRM original object identity mismatch")
        patched, cls.report = module.patch_blob(original, spec)
        elf = module.ElfFile(original)
        patched_elf = module.ElfFile(patched)
        cls.original = original[elf.text.offset:elf.text.offset + elf.text.size]
        cls.patched = patched[patched_elf.text.offset:patched_elf.text.offset + patched_elf.text.size]
        if len(original) != len(patched) or elf.text.size != patched_elf.text.size:
            raise AssertionError("RRM repair unexpectedly changed ELF geometry")
        expected_changes = set()
        for entry in spec["patches"]:
            symbol = elf.symbols[entry["symbol"]]
            if symbol.value != entry["symbol_value"]:
                raise AssertionError("RRM symbol identity mismatch")
            at = symbol.value + entry["offset"]
            before = struct.pack("<I", int(entry["original"], 16))
            after = struct.pack("<I", int(entry["replacement"], 16))
            if cls.original[at:at + 4] != before or cls.patched[at:at + 4] != after:
                raise AssertionError("RRM manifest instruction mismatch")
            expected_changes.update(elf.text.offset + at + i for i in range(4)
                if before[i] != after[i])
        actual_changes = {i for i, (a, b) in enumerate(zip(original, patched)) if a != b}
        if actual_changes != expected_changes:
            raise AssertionError("Changes escaped the declared RRM instruction bytes")

    def machine(self, patched):
        return Machine(self.patched if patched else self.original)

    def test_selected_mask_normal_state_equivalence(self):
        rng = random.Random(20261125)
        bitmaps = [bytes(5), b"\xff" * 5]
        for bit in range(40):
            value = bytearray(5)
            value[bit // 8] = 1 << (bit % 8)
            bitmaps.append(bytes(value))
        bitmaps += [bytes(rng.randrange(256) for _ in range(5)) for _ in range(64)]
        for bitmap in bitmaps:
            with self.subTest(bitmap=bitmap.hex()):
                states = []
                for patched in (False, True):
                    machine = self.machine(patched)
                    machine.prepare_rrm(bitmap)
                    machine.endpoints = {CODE + 0x2FA2E0}
                    machine.run(0x2FA278)
                    states.append((bytes(machine.uc.mem_read(PARAM, 5)),
                        bytes(machine.uc.mem_read(STACK - 0x40, 5)),
                        tuple(machine.reg(i) for i in range(31)),
                        machine.uc.reg_read(registers.UC_ARM64_REG_NZCV)))
                self.assertEqual(states[0], states[1])
                self.assertEqual(states[1][0], bytes(a & b for a, b in
                    zip(bitmap, (0x73, 0x5F, 0, 0x0F, 0x1C))))

    def test_missing_selected_bss_original_fault_and_clean_rejection(self):
        for bitmap in (bytes(5), b"\xff" * 5):
            with self.subTest(bitmap=bitmap.hex()):
                original = self.machine(False)
                original.prepare_rrm(bitmap, selected_info=False)
                with self.assertRaises(UcError):
                    original.run(0x2FA278)
                self.assertEqual(original.faults[0][0], 0x72)
                patched = self.machine(True)
                patched.prepare_rrm(bitmap, selected_info=False)
                patched.run(0x2FA278)
                self.assertEqual(patched.reg(0), (-25) & 0xFFFFFFFF)
                self.assertEqual(bytes(patched.uc.mem_read(PARAM, 5)), bitmap)
                self.assertEqual(bytes(patched.uc.mem_read(CFG + 0x428, 5)), b"\xa5" * 5)
                self.assertEqual(patched.extcap_calls, [])
                self.assertFalse(patched.faults)

    def test_scan_orphans_and_valid_capability_preservation(self):
        cases = [(None, None), (0, None), (15, None), (3, 7), (15, 7)]
        for orphan, rrm_slot in cases:
            for patched in (False, True):
                with self.subTest(orphan=orphan, rrm_slot=rrm_slot, patched=patched):
                    machine = self.machine(patched)
                    machine.prepare_rrm(bytes(5))
                    if orphan is not None:
                        machine.put64(TABLE + orphan * 8, OTHER)
                        machine.put64(OTHER + 0xB0, 0)
                    if rrm_slot is not None:
                        machine.put64(TABLE + rrm_slot * 8, EXTRA)
                        machine.put64(EXTRA + 0xB0, INFO)
                        machine.uc.mem_write(INFO + 0x72, struct.pack("<H", 0x1000))
                    should_fault = not patched and orphan is not None and (
                        rrm_slot is None or orphan < rrm_slot)
                    if should_fault:
                        with self.assertRaises(UcError):
                            machine.run(0x2FA380)
                        self.assertEqual(machine.faults[0][0], 0x72)
                    else:
                        machine.run(0x2FA380)
                        self.assertEqual(machine.uc.mem_read(PUBLIC + 0x68, 1)[0],
                            int(rrm_slot is not None))
                        limit = 16 if rrm_slot is None else rrm_slot + 1
                        self.assertEqual(machine.visited_slots,
                            [TABLE + i * 8 for i in range(limit)])
                        self.assertFalse(machine.faults)

    def test_valid_selected_zero_and_nonzero_setters(self):
        for bitmap in (bytes(5), b"\xff" * 5):
            states = []
            for patched in (False, True):
                machine = self.machine(patched)
                machine.prepare_rrm(bitmap)
                machine.put64(TABLE, CFG)
                machine.run(0x2FA278)
                states.append((bytes(machine.uc.mem_read(CFG + 0x428, 5)),
                    bytes(machine.uc.mem_read(INFO + 0x72, 2)),
                    bytes(machine.uc.mem_read(PUBLIC + 0x68, 1)), machine.reg(0),
                    machine.extcap_calls))
            self.assertEqual(states[0], states[1])
            self.assertEqual(states[1][3], 0)
            self.assertEqual(struct.unpack("<H", states[1][1])[0] & 0x1000,
                0 if bitmap == bytes(5) else 0x1000)

    def test_allocation_failure_rollback_success_and_owner_preservation(self):
        cases = [("first_failure", True), ("init_failure", True),
            ("extra_failure", True), ("init_failure", False),
            ("extra_failure", False), ("success", True)]
        for index in (0, 15):
            for kind, owns_slot in cases:
                for patched in (False, True):
                    with self.subTest(index=index, kind=kind, owns=owns_slot, patched=patched):
                        machine = self.machine(patched)
                        machine.prepare_lifetime(index)
                        machine.put64(TABLE + index * 8, OTHER)
                        machine.reg(0, WLC)
                        machine.reg(1, index)
                        machine.reg(2, 0xABC)
                        machine.reg(3, 0xDEF)
                        machine.reg(4, 0x123)
                        machine.reg(5, 0)
                        before = machine.preserve()

                        def first_alloc(m):
                            m.return_stub(0 if kind == "first_failure" else CFG)
                        machine.callbacks[CODE + 0x279430] = first_alloc

                        def initialize(m):
                            self.assertEqual((m.reg(0), m.reg(1), m.reg(2)),
                                (WLC, CFG, index))
                            m.put64(TABLE + index * 8, CFG if owns_slot else OTHER)
                            m.return_stub(1 if kind == "init_failure" else 0)
                        machine.callbacks[CODE + 0x278058] = initialize
                        machine.callsite(0x2795E0, lambda m:
                            m.return_stub(0 if kind == "extra_failure" else EXTRA))
                        machine.run(0x279538)
                        self.assertEqual(machine.preserve(), before)
                        self.assertFalse(machine.faults)
                        if kind == "success":
                            self.assertEqual(machine.reg(0), CFG)
                            self.assertEqual(machine.get64(TABLE + index * 8), CFG)
                            self.assertEqual(machine.get64(CFG + 0x520), EXTRA)
                            self.assertEqual(struct.unpack("<I", machine.uc.mem_read(
                                WLCIF + 0x30, 4))[0], 43)
                            self.assertFalse(machine.mfree_calls)
                        elif kind == "first_failure":
                            self.assertEqual(machine.reg(0), 0)
                            self.assertEqual(machine.get64(TABLE + index * 8), OTHER)
                            self.assertFalse(machine.mfree_calls)
                        else:
                            self.assertEqual(machine.reg(0), 0)
                            expected = (0 if patched else CFG) if owns_slot else OTHER
                            self.assertEqual(machine.get64(TABLE + index * 8), expected)
                            self.assertEqual(machine.mfree_calls, [(WLC, CFG, expected)])
        for patched in (False, True):
            machine = self.machine(patched)
            machine.reg(0, WLC)
            machine.reg(1, 16)
            before = machine.preserve()
            machine.run(0x279538)
            self.assertEqual(machine.reg(0), 0)
            self.assertEqual(machine.preserve(), before)

    def test_free_unpublishes_before_original_mfree(self):
        for index in (0, 15):
            for primary in (False, True):
                for patched in (False, True):
                    with self.subTest(index=index, primary=primary, patched=patched):
                        machine = self.machine(patched)
                        machine.prepare_lifetime(index, primary)
                        # Skip the optional interface event/deletion branch.
                        machine.put64(CFG + 0x10, 0)
                        machine.reg(0, WLC)
                        machine.reg(1, CFG)
                        before = machine.preserve()
                        machine.callsite(0x279640, lambda m: m.return_stub())
                        machine.callsite(0x279690, lambda m: m.return_stub())
                        machine.run(0x279628)
                        self.assertEqual(machine.mfree_calls,
                            [(WLC, CFG, 0 if patched else CFG)])
                        self.assertEqual(machine.get64(TABLE + index * 8), 0)
                        self.assertEqual(machine.get64(WLC + 0x2F0),
                            0 if primary else OTHER)
                        self.assertEqual(machine.preserve(), before)
                        self.assertFalse(machine.faults)


if __name__ == "__main__":
    unittest.main(verbosity=2)
