# Scoped BDMF headers for the RT-AX86U WFD source build

The initial Actions source build stopped in the target `rdpa_api.h` because
`bdmf_interface.h` is absent from this GPL SDK. Its normal WFD build uses a
prebuilt object and therefore never compiles this dependency. The matching WFD
source needs both the public BDMF interface and its Linux CE buffer headers.

Six unchanged GPL headers are imported from
[blackfuel/asuswrt-rt-ax88u at 481642dfe5fa62cc2ccf2b9e656396395181ac58](https://github.com/blackfuel/asuswrt-rt-ax88u/tree/481642dfe5fa62cc2ccf2b9e656396395181ac58/release/src-rt-5.02axhnd/rdp/drivers/bdmf).
This is the older BCM4908 ASUS GPL SDK, rather than a newer XRDP SDK. The
headers retain their original licensing and match the donor Git blobs exactly.
The machine-readable manifest records all Git blob IDs and SHA-256 digests.

| Donor path beneath `rdp/drivers/bdmf` | Git blob |
| --- | --- |
| `framework/bdmf_interface.h` | `37e272e48713c0eb48f926f3e9af2a35c0f0f0f2` |
| `framework/bdmf_data_types.h` | `3be17c7b8dd6f478e690fa147fa1f9c0dacd48e8` |
| `system/bdmf_queue.h` | `678fb42128920d857a8a4ec6e47d33c4c19e384b` |
| `system/bdmf_errno.h` | `13512574ac8f378672f7e29d53de61efc0328dd9` |
| `system/linux/bdmf_system_common.h` | `bc1ca511c9b044322f7159880c0b4558cab6ceaa` |
| `system/linux/ce/bdmf_system.h` | `b2bceab17a105ccc1008383d8d23c7e746144eaf` |

The copies live only in the target WFD module's `impl1/bdmf-compat` directory.
Include paths are scoped to the RT-AX86U WFD source build. The firmware continues
to use its existing BDMF implementation; no BDMF `.c` sources or other models'
include paths are replaced.

## Compatibility evidence

The installed-source RT-AX86U `hnd_extra/prebuilt/wfd.o` retains DWARF. Its file
table identifies the original WL4908 BDMF framework, Linux common and CE
headers. The relevant target types resolve as follows:

- `bdmf_number`: signed 64-bit; `bdmf_index`: signed 64-bit `long`;
  `bdmf_boolean`: one-byte `char`; `bdmf_phys_addr_t`: unsigned 32-bit;
  `bdmf_attr_id`: unsigned 16-bit. The imported types match. The WL4908
  physical-address exception must remain active even in the AArch64 kernel.
- `bdmf_attr_val_t`: 24 bytes, type at offset 0 and value union at 8.
  The attribute enum values are 0 through 15 in the same order.
- `bdmf_mattr`: 32 bytes, offsets `magic=0`, `oper=4`, `drv=8`,
  `max_entries=16`, `num_entries=20`, `dynamic=24`, `entries=32`.
  Its entry is 40 bytes with `aid=0`, `index=8`, `val=16`.
- The target's existing `rdpa_cpu_rxq_cfg_t` is 80 bytes, with callback/pointer
  and flag offsets recorded by the target compile assertions. Existing target
  RDPA aggregate headers remain in use.

`wfd_bdmf_abi.h`, included by the actual BCM94908 WFD translation unit, checks
these sizes, offsets, enum values, and the imported function signatures at
compile time. It therefore fails the actual target build if configuration or
headers change the relevant ABI.

The old CE buffer functions also agree with WFD's DWARF signatures. Their donor
and target declaration lines have a uniform eight-line shift: skb allocation
111/119, skb initialization 129/137, packet allocation 242/250, packet release
305/313. The BPM branch uses `gbpm_alloc_mult_buf_ex`, `gbpm_alloc_mult_skb` and
`gbpm_attach_skb`, whose target kernel declarations have matching signatures.
Allocation returns the requested count or zero. Free invalidates the DMA data
range and uses the existing headroom conversions before returning the buffer to
BPM. WFD's relocations reference `gbpm_g` and `bdmf_sysb_recycle`, consistent
with these inlined paths.

The target BDMF prebuilt object exports `bdmf_attrelem_get_as_buf`,
`bdmf_attrelem_set_as_buf`, `bdmf_new_and_set`, `bdmf_type_num_attrs`,
`bdmf_sysb_recycle`, `bdmf_lock`, `bdmf_unlock`, and `bdmf_destroy`. It is
stripped, so its own argument types and implementation internals cannot be
verified through DWARF. Matching WFD type/layout evidence, old SDK declarations,
exported symbols, and the target build constrain compatibility; they do not
prove all BDMF behavior or hardware operation.

The inspected WFD object's SHA-256 is
`57e24dade654607a698ea41f77c37286863c43a6e4708b2f34ffabf50b0359ce`;
the BDMF object's SHA-256 is
`fa54447feacd567196362fa4b0e53ca79f91e9f35ba81541805e3a06f940a8dc`.

## Actions validation

`python3 tools/ci/check-wfd-bdmf-headers.py` verifies the unchanged donor blobs,
SHA-256 digests, and the six-header BDMF dependency closure. The real AArch64 WFD
build then compiles the ABI assertions with the target kernel/RDPA headers.
Final module-marker checks still apply. No local compilation or firmware build
is needed for these validations.
