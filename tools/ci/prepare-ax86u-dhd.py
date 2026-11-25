#!/usr/bin/env python3
"""Prepare an exact vendor DHD build copy for a source-built RX scheduler."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile


def load_patcher():
    location = Path(__file__).with_name("patch-ax86u-drivers.py")
    definition = importlib.util.spec_from_file_location("ax86u_dhd_elf", location)
    module = importlib.util.module_from_spec(definition)
    sys.modules[definition.name] = module
    definition.loader.exec_module(module)
    return module


def validate_original(data, specification, patcher):
    # Reuse the instruction patcher's full object and function identity checks.
    identities = dict(specification, patches=specification.get("patches", []))
    patched, report = patcher.patch_blob(data, identities)
    elf = patcher.ElfFile(data)
    weakened = set(specification["weaken_symbols"])
    for name in weakened:
        if elf.get_symbol(name).info >> 4 != 1:
            raise ValueError("Expected an original global definition: " + name)
    expected = sorted((entry["section"], entry["offset"], entry["type"],
                       entry["symbol"], entry["addend"])
                      for entry in specification["caller_checks"])
    actual = sorted((elf.sections[entry.section_index].name, entry.offset,
                     entry.type, entry.symbol_name, entry.addend)
                    for entry in elf.relocations if entry.symbol_name in weakened)
    if actual != expected:
        raise ValueError("DHD replacement callsites changed")
    relocation_sites = {entry.offset for entry in elf.relocations
                        if entry.section_index == elf.text.index}
    entries = {elf.get_symbol(name).value for name in weakened}
    for offset in range(0, elf.text.size - 3, 4):
        word = struct.unpack_from("<I", data, elf.text.offset + offset)[0]
        if word & 0x7c000000 != 0x14000000 or offset in relocation_sites:
            continue
        immediate = word & 0x3ffffff
        if immediate & 0x2000000:
            immediate -= 0x4000000
        if offset + 4 * immediate in entries:
            raise ValueError("Unrelocated branch would bypass the source replacement")
    for alias in specification["aliases"]:
        symbol = elf.get_symbol(alias["symbol"])
        if symbol.value != alias["symbol_value"]:
            raise ValueError("DHD helper alias changed: " + alias["name"])
        if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9.$]*", alias["name"]):
            raise ValueError("Invalid helper alias name")
        if alias["name"] in elf.symbols:
            raise ValueError("DHD helper alias already exists")
    return elf, report, patched


def validate_prepared(original, prepared, specification, patcher):
    before, after = patcher.ElfFile(original), patcher.ElfFile(prepared)
    # No machine instructions, data, or allocated section geometry may change.
    for section in before.sections:
        if not section.flags & 2:  # SHF_ALLOC
            continue
        matches = [candidate for candidate in after.sections if candidate.name == section.name]
        if len(matches) != 1 or (matches[0].size, matches[0].flags) != (section.size, section.flags):
            raise ValueError("Allocated DHD section changed: " + section.name)
        if section.type != 8:
            old = original[section.offset:section.offset + section.size]
            new = prepared[matches[0].offset:matches[0].offset + matches[0].size]
            if old != new:
                raise ValueError("DHD code/data changed while adding helper aliases")
    relocations = lambda elf: sorted((elf.sections[entry.section_index].name,
                                    entry.offset, entry.type, entry.symbol_name, entry.addend)
                                   for entry in elf.relocations)
    if relocations(before) != relocations(after):
        raise ValueError("DHD relocation targets changed")
    for name in specification["weaken_symbols"]:
        if after.get_symbol(name).info >> 4 != 2:
            raise ValueError("Vendor DHD definition was not weakened: " + name)
    for alias in specification["aliases"]:
        symbol = after.get_symbol(alias["name"])
        if symbol.value != alias["symbol_value"] or symbol.info != 0x12:
            raise ValueError("DHD alias does not identify the reviewed helper")
    return after


def main():
    arguments = argparse.ArgumentParser(description=__doc__)
    arguments.add_argument("--spec", type=Path, required=True)
    arguments.add_argument("--input", type=Path, required=True)
    arguments.add_argument("--output", type=Path, required=True)
    arguments.add_argument("--objcopy", required=True)
    arguments.add_argument("--report", type=Path)
    args = arguments.parse_args()
    if args.input.resolve() == args.output.resolve():
        arguments.error("The vendor input must remain unchanged")
    patcher = load_patcher()
    specification = json.loads(args.spec.read_text())
    original = args.input.read_bytes()
    _, report, patched = validate_original(original, specification, patcher)
    command = [args.objcopy]
    command.extend("--weaken-symbol=" + name for name in specification["weaken_symbols"])
    command.extend("--add-symbol={}=.text:0x{:x},global,function".format(
        alias["name"], alias["symbol_value"]) for alias in specification["aliases"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".ax86u-dhd-", dir=str(args.output.parent)) as temporary:
        input_path = Path(temporary) / "reviewed-dhd.o"
        input_path.write_bytes(patched)
        result_path = Path(temporary) / "dhd.o"
        subprocess.run(command + [str(input_path), str(result_path)], check=True)
        prepared = result_path.read_bytes()
        validate_prepared(patched, prepared, specification, patcher)
        os.chmod(result_path, args.input.stat().st_mode & 0o777)
        os.replace(result_path, args.output)
    report.update({"prepared_sha256": hashlib.sha256(prepared).hexdigest(),
                   "source": specification["source"],
                   "weaken_symbols": specification["weaken_symbols"],
                   "aliases": specification["aliases"]})
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print("Prepared DHD helper aliases and source replacement: " + report["prepared_sha256"])


if __name__ == "__main__":
    main()
