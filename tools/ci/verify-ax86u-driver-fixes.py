#!/usr/bin/env python3
"""Verify reviewed instruction fixes in built and packaged RT-AX86U modules."""
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys


ROOT = Path(__file__).resolve().parents[2]
CI = Path(__file__).resolve().parent
SDK = ROOT / "release/src-rt-5.02L.07p2axhnd"


def verify_dhd(patcher):
    specification = json.loads((CI / "ax86u-dhd-scheduler.json").read_text())
    original_data = (ROOT / specification["target"]).read_bytes()
    patcher.patch_blob(original_data, dict(specification, patches=specification.get("patches", [])))
    original = patcher.ElfFile(original_data)
    installed = list((SDK / "targets/962118GW/fs/lib/modules").rglob("dhd.ko"))
    if len(installed) != 1:
        raise SystemExit("Expected one packaged dhd.ko")
    built = SDK / "router-sysdep/hnd_dhd/dhd.ko"
    results = []
    for path in (built, installed[0]):
        data = path.read_bytes()
        elf = patcher.ElfFile(data)
        if not any(b"DHD_RX_SCHEDULE_FIX_V1" in data[section.offset:section.offset + section.size]
                   for section in elf.sections if section.name.startswith(".rodata")):
            raise SystemExit("Missing compiled DHD source marker: " + str(path))
        anchor_name = "dhd_prot_get_read_addr"
        base = elf.get_symbol(anchor_name).value - original.get_symbol(anchor_name).value
        for name in specification["weaken_symbols"]:
            symbol = elf.get_symbol(name)
            if symbol.info >> 4 != 1 or symbol.value == base + original.get_symbol(name).value:
                raise SystemExit("DHD still uses its original weak function: " + name)
        for alias in specification["aliases"]:
            symbol = elf.get_symbol(alias["name"])
            if symbol.info != 0x12 or symbol.value != base + alias["symbol_value"] + alias.get("entry_offset", 0):
                raise SystemExit("Linked DHD helper alias changed: " + alias["name"])
        hook = specification.get("hcap_hook")
        if hook:
            if not any(hook["marker"].encode() in data[s.offset:s.offset + s.size]
                       for s in elf.sections if s.name.startswith(".rodata")):
                raise SystemExit("Missing DHD coherency marker: " + str(path))
            caller = elf.get_symbol(hook["caller_symbol"])
            position = caller.value + hook["caller_offset"]
            word = struct.unpack_from("<I", data, elf.text.offset + position)[0]
            immediate = word & 0x3ffffff
            if immediate & 0x2000000:
                immediate -= 0x4000000
            destination = elf.get_symbol(hook["helper_alias"])
            if word & 0xfc000000 != 0x94000000 or position + immediate * 4 != destination.value:
                raise SystemExit("DHD coherency call does not enter the reviewed helper")
            patcher.validate_unreferenced_code(elf, specification["unreferenced_code"],
                                              allowed_callers={position})
        for call in specification["caller_checks"]:
            functions = [symbol for symbol in original.symbols.values()
                         if symbol.section_index == original.text.index and symbol.info & 15 == 2
                         and symbol.value <= call["offset"] < symbol.value + symbol.size]
            if not functions:
                raise SystemExit("Cannot anchor the original DHD callsite")
            caller = min(functions, key=lambda symbol: symbol.size)
            current = elf.get_symbol(caller.name)
            position = current.value + call["offset"] - caller.value
            destination = elf.get_symbol(call["symbol"])
            relocations = [entry for entry in elf.relocations
                           if entry.section_index == elf.text.index and entry.offset == position]
            if relocations:
                if len(relocations) != 1:
                    raise SystemExit("Ambiguous linked DHD call relocation")
                relocation = relocations[0]
                by_name = relocation.symbol_name == destination.name and relocation.addend == 0
                by_section = (relocation.symbol_section_index == elf.text.index and
                              relocation.symbol_value + relocation.addend == destination.value)
                if relocation.type != 283 or not (by_name or by_section):
                    raise SystemExit("DHD call is not linked to the source replacement")
            else:
                word = struct.unpack_from("<I", data, elf.text.offset + position)[0]
                immediate = word & 0x3ffffff
                if immediate & 0x2000000:
                    immediate -= 0x4000000
                if word & 0xfc000000 != 0x94000000 or position + immediate * 4 != destination.value:
                    raise SystemExit("Resolved DHD branch bypasses the source replacement")
        for modification in specification.get("patches", []):
            symbol = elf.get_symbol(modification["symbol"])
            position = elf.text.offset + symbol.value + patcher._integer(modification["offset"])
            if struct.unpack_from("<I", data, position)[0] != int(modification["replacement"], 16):
                raise SystemExit("Missing linked DHD instruction fix: " + modification["name"])
        results.append({"path": str(path.relative_to(ROOT)),
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "verified_strong_functions": specification["weaken_symbols"],
                        "verified_callsite_count": len(specification["caller_checks"]),
                        "verified_instruction_count": len(specification.get("patches", [])),
                        "verified_coherency_hook": bool(hook)})
    return results


def main():
    definition = importlib.util.spec_from_file_location(
        "ax86u_driver_patcher", CI / "patch-ax86u-drivers.py")
    patcher = importlib.util.module_from_spec(definition)
    sys.modules[definition.name] = patcher
    definition.loader.exec_module(patcher)
    report = {"modules": []}
    specifications = (
        ("ax86u-rrm-patches.json", "wl", SDK / "router-sysdep/hnd_wl/wl.ko"),
        ("ax86u-rdpa-patches.json", "rdpa", SDK / "rdp/projects/WL4908/target/rdpa/rdpa.ko"),
    )
    for filename, module_name, built in specifications:
        specification = json.loads((CI / filename).read_text())
        _, expected = patcher.patch_blob((ROOT / specification["target"]).read_bytes(), specification)
        installed = list((SDK / "targets/962118GW/fs/lib/modules").rglob(module_name + ".ko"))
        if len(installed) != 1:
            raise SystemExit("Expected one packaged " + module_name + ".ko")
        # The installed copy is the final packaging input, independent of link layout.
        # For WL, the source tree may retain the module at the component symlink.
        if not built.is_file() and module_name == "wl":
            candidates = list((SDK / "bcmdrivers/broadcom/net/wl").glob(
                "bcm94908/main/components/router-sysdep/hnd_wl/wl.ko"))
            if len(candidates) == 1:
                built = candidates[0]
        for path in (built, installed[0]):
            data = path.read_bytes()
            elf = patcher.ElfFile(data)
            for modification in specification["patches"]:
                symbol = elf.get_symbol(modification["symbol"])
                offset = patcher._integer(modification["offset"])
                if offset + 4 > symbol.size:
                    raise SystemExit("Linked function extent changed: " + symbol.name)
                position = elf.text.offset + symbol.value + offset
                instruction = struct.unpack_from("<I", data, position)[0]
                if instruction != int(modification["replacement"], 16):
                    raise SystemExit("Missing driver fix {} in {}".format(
                        modification["name"], path))
            report["modules"].append({
                "path": str(path.relative_to(ROOT)),
                "sha256": hashlib.sha256(data).hexdigest(),
                "verified_instruction_count": len(specification["patches"]),
                "vendor_input_sha256": expected["original_sha256"],
                "patched_object_sha256": expected["patched_sha256"],
            })
    report["modules"].extend(verify_dhd(patcher))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
