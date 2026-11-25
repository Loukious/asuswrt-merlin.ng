#!/usr/bin/env python3
"""Validate pinned DHD helper/caller identities before compiling the replacement."""
import importlib.util
import json
from pathlib import Path
import sys


def main():
    root = Path(__file__).resolve().parents[2]
    ci = Path(__file__).resolve().parent
    definition = importlib.util.spec_from_file_location(
        "ax86u_dhd_prepare", ci / "prepare-ax86u-dhd.py")
    preparation = importlib.util.module_from_spec(definition)
    sys.modules[definition.name] = preparation
    definition.loader.exec_module(preparation)
    specification = json.loads((ci / "ax86u-dhd-scheduler.json").read_text())
    original = (root / specification["target"]).read_bytes()
    preparation.validate_original(original, specification, preparation.load_patcher())
    source = (root / specification["source"]).read_text()
    if "DHD_RX_SCHEDULE_FIX_V1" not in source:
        raise SystemExit("Missing DHD source marker")
    print("Pinned DHD object, helpers, recovery fixes and replacement callsites verified")


if __name__ == "__main__":
    main()
