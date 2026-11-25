#!/usr/bin/env python3
"""Apply reviewed, same-size instruction fixes to exact RT-AX86U build objects.

The original vendor object is read only. Section layout, symbol values, and
relocations are preserved; a changed input or a relocated patch site is rejected.
"""
import argparse
import hashlib
import json
import os
import re
import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ElfSection:
    index: int
    name: str
    type: int
    offset: int
    size: int
    link: int
    info: int
    entry_size: int
    flags: int = 0
    address: int = 0


@dataclass(frozen=True)
class ElfSymbol:
    name: str
    value: int
    size: int
    section_index: int
    info: int


@dataclass(frozen=True)
class ElfRelocation:
    section_index: int
    offset: int
    type: int
    symbol_name: str
    addend: int
    symbol_value: int = 0
    symbol_section_index: int = 0


class ElfFile:
    def __init__(self, data):
        self.data = data
        if len(data) < 64 or data[:7] != b"\x7fELF\x02\x01\x01":
            raise ValueError("Expected a little-endian ELF64 object")
        header = struct.unpack_from("<16sHHIQQQIHHHHHH", data)
        if header[1] != 1 or header[2] != 183:
            raise ValueError("Expected ET_REL / EM_AARCH64")
        self.machine = header[2]
        table_offset, entry_size, count, names_index = (
            header[6], header[11], header[12], header[13])
        if entry_size != 64 or not 0 < count <= 4096 or names_index >= count:
            raise ValueError("Unsupported ELF section table")
        self._slice(table_offset, count * entry_size)
        raw = [struct.unpack_from("<IIQQQQIIQQ", data,
               table_offset + i * entry_size) for i in range(count)]
        names = self._slice(raw[names_index][4], raw[names_index][5])
        self.sections = []
        for i, values in enumerate(raw):
            section = ElfSection(i, self._name(names, values[0]), values[1],
                                 values[4], values[5], values[6], values[7], values[9],
                                 values[2], values[3])
            if section.type != 8:  # SHT_NOBITS has no file payload.
                self._slice(section.offset, section.size)
            self.sections.append(section)
        texts = [section for section in self.sections if section.name == ".text"]
        if len(texts) != 1:
            raise ValueError("Expected one .text section")
        self.text = texts[0]
        self.symbols = {}
        self._candidates = {}
        tables = {}
        for section in self.sections:
            if section.type != 2:  # SHT_SYMTAB
                continue
            if section.entry_size != 24 or section.size % 24 or section.link >= count:
                raise ValueError("Invalid ELF symbol table")
            strings_section = self.sections[section.link]
            strings = self._slice(strings_section.offset, strings_section.size)
            symbols = []
            for position in range(section.offset, section.offset + section.size, 24):
                name, info, _, index, value, size = struct.unpack_from("<IBBHQQ", data, position)
                symbol = ElfSymbol(self._name(strings, name), value, size, index, info)
                symbols.append(symbol)
                if symbol.name and index != 0:
                    self.symbols[symbol.name] = symbol
                    self._candidates.setdefault(symbol.name, []).append(symbol)
            tables[section.index] = symbols
        self.relocations = []
        for section in self.sections:
            if section.type not in (4, 9):  # SHT_RELA / SHT_REL
                continue
            size = 24 if section.type == 4 else 16
            if section.entry_size != size or section.size % size or section.link not in tables:
                raise ValueError("Invalid ELF relocation table")
            symbols = tables[section.link]
            for position in range(section.offset, section.offset + section.size, size):
                offset, info = struct.unpack_from("<QQ", data, position)
                addend = struct.unpack_from("<q", data, position + 16)[0] if size == 24 else 0
                index = info >> 32
                if index >= len(symbols):
                    raise ValueError("Invalid relocation symbol index")
                self.relocations.append(ElfRelocation(section.info, offset,
                    info & 0xffffffff, symbols[index].name, addend,
                    symbols[index].value, symbols[index].section_index))

    def _slice(self, offset, size):
        if offset < 0 or size < 0 or offset + size > len(self.data):
            raise ValueError("ELF payload extends beyond file")
        return self.data[offset:offset + size]

    @staticmethod
    def _name(strings, offset):
        if offset >= len(strings):
            raise ValueError("ELF string offset extends beyond table")
        end = strings.find(b"\0", offset)
        if end < 0:
            raise ValueError("Unterminated ELF string")
        return strings[offset:end].decode("utf-8", errors="replace")

    def get_symbol(self, name):
        candidates = self._candidates.get(name, [])
        if len(candidates) != 1 or candidates[0].section_index != self.text.index:
            raise ValueError("Expected one .text symbol: " + name)
        return candidates[0]

    def symbol_bytes(self, name, length=None):
        symbol = self.get_symbol(name)
        size = symbol.size if length is None else length
        if size <= 0 or symbol.value + size > self.text.size:
            raise ValueError("Invalid function extent: " + name)
        return self._slice(self.text.offset + symbol.value, size)


def _integer(value):
    return int(value, 0) if isinstance(value, str) else int(value)


def _word(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{8}", value):
        raise ValueError("Instructions must be eight hex digits")
    return struct.pack("<I", int(value, 16))


def relocation_width(relocation_type):
    # ELF AArch64 data relocations can span multiple instruction-sized words.
    data_widths = {0: 0, 257: 8, 258: 4, 259: 2, 260: 8, 261: 4,
                   262: 2, 307: 8, 308: 4, 1025: 8, 1026: 8,
                   1027: 8, 1028: 8, 1029: 8, 1030: 8, 1031: 16, 1032: 8}
    if relocation_type in data_widths:
        return data_widths[relocation_type]
    # These ABI ranges encode a relocation inside a single A64 instruction.
    if 263 <= relocation_type <= 306 or 309 <= relocation_type <= 313 or 512 <= relocation_type <= 573:
        return 4
    raise ValueError("Unsupported AArch64 code relocation type: " + str(relocation_type))


def alternative_regions(elf):
    """Instruction ranges that the arm64 module loader can replace at boot."""
    ranges = []
    relocations = {}
    for relocation in elf.relocations:
        relocations.setdefault((relocation.section_index, relocation.offset), []).append(relocation)
    for section in elf.sections:
        if section.name != ".altinstructions":
            continue
        if section.size % 12:
            raise ValueError("Unexpected arm64 alternative record geometry")
        for offset in range(0, section.size, 12):
            origins = relocations.get((section.index, offset), [])
            if len(origins) != 1 or origins[0].type != 261:
                raise ValueError("Unresolved arm64 alternative origin")
            relocation = origins[0]
            if relocation.symbol_section_index != elf.text.index:
                continue
            length = elf.data[section.offset + offset + 10]
            replacement_length = elf.data[section.offset + offset + 11]
            start = relocation.symbol_value + relocation.addend
            if not 0 < length or replacement_length > length or start < 0 or start + length > elf.text.size:
                raise ValueError("Invalid arm64 alternative instruction range")
            ranges.append((start, start + length))
    return ranges


def validate_unreferenced_code(elf, regions, allowed_callers=()):
    """Reject reuse of an interior helper if the vendor routine has callers."""
    relocation_sites = {r.offset for r in elf.relocations
                        if r.section_index == elf.text.index}
    for region in regions:
        symbol = elf.get_symbol(region["symbol"])
        start, end = symbol.value, symbol.value + symbol.size
        if "__ksymtab_" + symbol.name in elf.symbols:
            raise ValueError("Reused vendor routine is exported: " + symbol.name)
        for relocation in elf.relocations:
            if relocation.symbol_section_index != elf.text.index:
                continue
            # Original-instruction pointers are loader metadata, not callers.
            # patch_blob separately rejects every overlap with their ranges.
            if (elf.sections[relocation.section_index].name == ".altinstructions"
                    and relocation.offset % 12 == 0 and relocation.type == 261):
                continue
            target = relocation.symbol_value + relocation.addend
            if (relocation.section_index == elf.text.index and
                    relocation.offset in allowed_callers and relocation.type in (282, 283)):
                continue
            if start <= target < end and not (
                    relocation.section_index == elf.text.index and
                    start <= relocation.offset < end):
                raise ValueError("Reused vendor routine has an address reference: " + symbol.name)
        for offset in range(0, elf.text.size - 3, 4):
            if start <= offset < end or offset in relocation_sites or offset in allowed_callers:
                continue
            word = struct.unpack_from("<I", elf.data, elf.text.offset + offset)[0]
            if word & 0x7c000000 == 0x14000000:  # B/BL
                immediate, bits = word & 0x3ffffff, 26
            elif (word & 0xff000010 == 0x54000000 or
                  word & 0x7e000000 == 0x34000000):  # B.cond/CBZ/CBNZ
                immediate, bits = (word >> 5) & 0x7ffff, 19
            elif word & 0x7e000000 == 0x36000000:  # TBZ/TBNZ
                immediate, bits = (word >> 5) & 0x3fff, 14
            elif word & 0x9f000000 == 0x10000000:  # ADR address-taking
                immediate = ((word >> 5) & 0x7ffff) << 2 | ((word >> 29) & 3)
                if immediate & (1 << 20):
                    immediate -= 1 << 21
                if start <= offset + immediate < end:
                    raise ValueError("Reused vendor routine has a raw address reference")
                continue
            else:
                continue
            if immediate & (1 << (bits - 1)):
                immediate -= 1 << bits
            if start <= offset + immediate * 4 < end:
                raise ValueError("Reused vendor routine has an unrelocated caller: " + symbol.name)


def patch_blob(data, spec):
    if spec.get("version") != 1 or spec.get("elf_machine") != 183 or spec.get("byte_order") != "little":
        raise ValueError("Unsupported patch specification")
    original_hash = hashlib.sha256(data).hexdigest()
    if original_hash != spec.get("original_sha256"):
        raise ValueError("Vendor object changed; instruction fixes require a new review")
    elf = ElfFile(data)
    for check in spec.get("function_checks", []):
        symbol = elf.get_symbol(check["symbol"])
        if symbol.value != _integer(check["symbol_value"]) or symbol.size != _integer(check["size"]):
            raise ValueError("Function identity changed: " + symbol.name)
        if hashlib.sha256(elf.symbol_bytes(symbol.name)).hexdigest() != check["sha256"]:
            raise ValueError("Function bytes changed: " + symbol.name)
    validate_unreferenced_code(elf, spec.get("unreferenced_code", []))
    alternatives = alternative_regions(elf)
    patched = bytearray(data)
    sites = set()
    changes = []
    for patch in spec["patches"]:
        symbol = elf.get_symbol(patch["symbol"])
        offset = _integer(patch["offset"])
        if symbol.value != _integer(patch["symbol_value"]) or offset < 0 or offset % 4:
            raise ValueError("Patch anchor changed: " + patch["name"])
        if offset + 4 > symbol.size:
            raise ValueError("Patch extends beyond function: " + patch["name"])
        address = symbol.value + offset
        if any(start < address + 4 and address < end for start, end in alternatives):
            raise ValueError("Patch overlaps a runtime arm64 alternative: " + patch["name"])
        if address in sites:
            raise ValueError("Overlapping instruction patches")
        sites.add(address)
        for relocation in elf.relocations:
            if relocation.section_index != elf.text.index:
                continue
            width = relocation_width(relocation.type)
            if width and relocation.offset < address + 4 and relocation.offset + width > address:
                raise ValueError("Patch overlaps a relocation: " + patch["name"])
        position = elf.text.offset + address
        expected, replacement = _word(patch["original"]), _word(patch["replacement"])
        if data[position:position + 4] != expected:
            raise ValueError("Unexpected instruction: " + patch["name"])
        patched[position:position + 4] = replacement
        changes.append({"name": patch["name"], "symbol": symbol.name,
                        "offset": offset, "text_offset": address,
                        "original": patch["original"], "replacement": patch["replacement"]})
    report = {"version": 1, "target": spec["target"],
              "original_sha256": original_hash,
              "patched_sha256": hashlib.sha256(patched).hexdigest(),
              "patches": changes}
    return bytes(patched), report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        parser.error("The original vendor object must not be overwritten")
    spec = json.loads(args.spec.read_text())
    patched, report = patch_blob(args.input.read_bytes(), spec)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".ax86u-object-", dir=str(args.output.parent))
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(patched)
        os.chmod(temporary, args.input.stat().st_mode & 0o777)
        os.replace(temporary, args.output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print("Applied {} reviewed fixes to {}: {}".format(
        len(report["patches"]), args.output, report["patched_sha256"]))


if __name__ == "__main__":
    main()
