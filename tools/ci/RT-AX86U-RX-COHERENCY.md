# RT-AX86U Runner RX coherency comparison

The boot capability guard clears `PCIE_IPC_HCAP1_HW_COHERENCY` when Runner
owns RX, retaining the original capability advertisement when the host owns
RX. This is a targeted hardware comparison based on a captured visibility
failure; it is not yet a verified repair. The RX-disabled boot fallback remains
in place until enabled-offload hardware validation succeeds.

## Captured failure boundary

An on-router bounded capture recorded the transition with TX/RX offload both
enabled. Runner cached RX completion RD/WR froze at 205/205 while firmware DMA
WR advanced from 237 to 432. Host RX notifications continued from 401 to 13,989,
but delivered RX packets remained 64. The host's actual 128-entry exception
queue stayed empty and its interrupt remained enabled. Ring/index addresses
agreed, buffer allocation mutex was zero, and HWA retained completion credits.

This excludes a full host queue in that captured failure and demonstrates that
Runner's input view stops progressing before RPH/credit exhaustion. It does
not distinguish a Runner scheduling/fetch failure from memory visibility alone.

The installed radio firmware is the vendor-selected **43684b0** image: the
exact loader maps silicon revisions 2 and 3 to B0. Its live assertion code
matches the original saved crash, including SVC at 0xcc7e. The C0 file is also
packaged but was not the selected image in these tests.

Firmware's exact B0 IPC parser extracts hcap1 bit9 and applies it to both
completion-data and producer-index PCIe write templates. The original DHD
`dhd_bus_init` unconditionally ORs bit0x200 into hcap1. Runner accesses the
same ring/index backing memory through its accelerator path. Clearing that
advertisement tests whether CPU-coherent writes are causing the stale input
view. No speculative cache flush or DMA pointer dereference is added.

## Exact patch and execution boundary

The original DHD object is pinned by SHA-256 and complete function hashes in
`ax86u-dhd-scheduler.json`. The original ORR at `.text+0x35d64` becomes a BL to
a 60-byte guard at `.text+0x4f174`, inside an unused vendor helper. The guard
preserves x1..x29, flags, and stack balance; it changes only the HCAP result.
No module is resized and no existing relocation is moved.

The unused helper has no incoming execution branches, ordinary address
references, or kernel export. Preparation checks all of those. Its retained
arm64 alternative instruction at0x4f190 and adjacent barrier remain unchanged
and unreachable from the new entry. The patcher rejects any instruction change
overlapping an arm64 loader alternative. Final packaged-module verification
rechecks the call destination, helper words, alias, marker, and incoming calls.

Protocol preinit runs before the HCAP hook and publishes the enabled Runner RX
bit in the public DHD flags. This ordering is established from the actual call
relocation, success branch, and executed preinit RX-flag instructions. Null
public state keeps the legacy advertised capability. Normal no-offload behavior
and all unrelated capability bits are preserved.

## Validation

GitHub Actions executes `test-ax86u-dhd-coherency.py` against actual original
and patched A64 instructions using Unicorn. It covers every single capability
bit, all-bits/mixed values, TX-only/RX-only/combined ownership, null public state,
register/flag/stack preservation, neighboring IPC stores, and the actual runtime
alternative replacement. Negative checks reject alternative overlap, new
ordinary references, and unrelocated callers into the reused function.

No local firmware build is required or performed. Hardware acceptance requires
installed patch verification, runtime RX offload one, HCAP coherency zero,
advancing Runner and DMA RX indices, successful 5 GHz authentication and data
traffic, and no repeat firmware assertion. A green workflow alone is insufficient.
