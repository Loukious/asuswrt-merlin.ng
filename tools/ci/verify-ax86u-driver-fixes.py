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
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
