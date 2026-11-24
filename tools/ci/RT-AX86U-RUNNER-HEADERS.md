# RT-AX86U legacy Runner descriptor dependency

The source-built WFD module requires `rdp_cpu_ring_defs.h`, which the target
SDK omits for its non-XRDP Runner path. The available target XRDP_CFE headers
describe a different CPU ring format and cannot satisfy this dependency.
`rdpa_cpu_helper.h`, `rdp_mm.h`, and `access_macros.h` already exist in the
target SDK and remain in use.

The scoped copy lives at
`release/src-rt-5.02L.07p2axhnd/bcmdrivers/opensource/net/wfd/impl1/runner-compat/rdp_cpu_ring_defs.h`.
Its donor is the older ASUS GPL release
[blackfuel/asuswrt-rt-ax88u, commit 481642dfe5fa62cc2ccf2b9e656396395181ac58](https://github.com/blackfuel/asuswrt-rt-ax88u/blob/481642dfe5fa62cc2ccf2b9e656396395181ac58/release/src-rt-5.02axhnd/rdp/drivers/rdp_subsystem/BCM96838/rdp_cpu_ring_defs.h).
Although the donor directory is named BCM96838, its little-endian legacy
descriptor matches the relevant RT-AX86U layout, subject to the one named-field
adaptation below. Directory names and equal total size alone are not the
compatibility evidence.

| Identity | Value |
| --- | --- |
| Donor Git blob | `fad9c51607f957bff5642667e559baf94ccef9ba` |
| Donor bytes | 4198 |
| Donor SHA-256 | `b25376373ab321b33f9db31e3036b72b340dc85508aaf8231fb65fe0b1ff16ba` |
| Adapted Git blob | `fa8ab397f60ce7d0e3c59dfdca086cbdb01f2b5a` |
| Adapted SHA-256 | `b38dc5495af47f40bd8285da04bd58daade6fe2d1b9ca50634ad7d3e3870e255` |

The donor has no individual copyright/license comment. Its original contents
are preserved except for the documented field split and appended ABI
assertions; no new claim of Broadcom authorship or licensing is inserted.
Its target `access_macros.h` dependency retains the existing Broadcom GPLv2
notice and linking exception.

## Layout evidence

The installed `installed-wfd.ko` has no DWARF. The inspected target
`router-sysdep.rt-ax86u/hnd_extra/prebuilt/wfd.o` retains DWARF, and its loaded
code/data sections were matched to the installed module during the crash
investigation. The target object's SHA-256 is
`57e24dade654607a698ea41f77c37286863c43a6e4708b2f34ffabf50b0359ce`.
`pahole -C CPU_RX_DESCRIPTOR` resolves the following 16-byte layout; bit
positions below are relative to the named 32-bit word:

| Word / byte offset | Relevant fields and bit positions |
| --- | --- |
| `word0` / 0 | `packet_length` 0–13; `source_port` 14–18; checksum 19; `flow_id` 20–31 |
| `word1` / 4 | descriptor type 0–3; `is_exception` 4; reserved 5–8; destination SSID 9–24; reason 25–30; payload flag 31 |
| `word2` / 8 | data pointer 0–30; ownership 31 |
| `word3` / 12 | flow ring 0–9; SSID 10–13; reserved 14–15; timestamp 16–19; reserved 20–25; WLAN priority 26–29; unicast 30; RX offload 31 |

Within `word3`, `wl_metadata`, `ssid_vector`, and `free_index` alias the
16-bit value at byte offset 12. The alternate NIC view is `wl_chain_id` in
bits 0–13 and `wl_tx_priority` in bits 14–15. The donor matches these fields
and the alternate IPsec view of `word0` exactly.

The sole descriptor adaptation splits the little-endian donor's
`reserved1:5` into `is_exception:1` and `reserved1:4`. This preserves every
subsequent field's bit position and supplies the name used by the existing
target `rdpa_cpu_helper.h`. The target DWARF explicitly identifies the
exception bit at bit 4. The unused big-endian donor declaration is preserved;
the appended alias assertions intentionally reject selecting that branch for
this target.

The existing `struct WFD_RING` also agrees with the target DWARF: 64 bytes;
`head=0`, `buff_cache=8`, `buff_cache_idx=16`, `skbs_cache_cnt=20`,
`skbs_cache=24`, `base=32`, `end=40`, `ring_size=48`, `descriptor_size=52`.
No ring structure or helper implementation is imported as part of this
dependency repair.

## Dependency and validation scope

The source-build include paths must select `impl1/runner-compat` and the
existing target `rdp/drivers/rdp_subsystem/rdp` directory. The latter supplies
`access_macros.h`. That file's kernel dependency is supplied by the target
kernel. `rdp_mm.h` uses the independently imported BDMF data types and the
target kernel's device header. No newer XRDP ring helper is substituted.

Production `_Static_assert` checks in the descriptor header validate size,
all four word offsets, and the data/WLAN aliases in the actual WFD target
translation unit. `tools/ci/test-ax86u-runner-descriptor.c` includes this
production header directly and exercises every declared little-endian
bitfield in both encoding and decoding directions, using the target DWARF
positions as expected values. Actions builds this test with the pinned AArch64
firmware compiler and executes it with `qemu-aarch64`. It also verifies ownership
enum values and metadata aliases. Only the MMIO `access_macros.h` dependency is
suppressed for this userspace layout test; the descriptor is not copied into a
test fixture.

`python3 tools/ci/check-ax86u-runner-header.py` checks the final header digest,
then removes precisely the appended assertions and reverses the named-field
split to reconstruct the pinned donor Git blob. These checks belong in
GitHub Actions alongside the real AArch64 module compilation and final
module-marker verification. No local compilation, router access, or hardware
validation was performed for this dependency repair.
