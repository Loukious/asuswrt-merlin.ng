# RT-AX86U RRM crash: instruction repair and evidence

The recorded `wlconf` kernel crash is an unsafe RRM capability setter in the
proprietary native `wl` driver. It is separate from the 5 GHz dongle's HWA RX
completion assertion and the WFD receive-worker NULL dereference. The RX
offload workaround and WFD source changes must not be described as repairing
this RRM crash. The separate RRM repair is specified in
`tools/ci/ax86u-rrm-patches.json`: it corrects instructions in the existing
relocatable driver object. No RC shutdown replacement or RRM suppression is
included. GitHub Actions execution and target-build verification are pending;
the plan and regression source are not evidence of a passing build or hardware
validation.

## Recorded evidence and artifact identity

The saved `latest-syslog.log-1.txt`, lines 7628–7799, records:

- Process: `wlconf`, through a compat ioctl.
- Fault: NULL read at virtual address `0x72`.
- PC: `wlc_rrm_doiovar+0xf00/0x1488 [wl]`; LR: `+0xe84`.
- Faulting instruction: `7940e421`, `ldrh w1, [x1, #0x72]`; `x1 = 0`.

The local artifact reproducing that function and instruction is
`release/src-rt-5.02L.07p2axhnd/router-sysdep.rt-ax86u/hnd_extra/prebuilt/wl.o`:

- ELF64 AArch64; 9,403,744 bytes.
- Git blob: `610c2d294cc97fdfb7629861f5fc714815367b6e`.
- SHA-256: `87992acf632aeb2bf5906d91eedb7e22efb84d7492503e6d732dc18bc2b4b9c4`.
- `.text` file offset: `0x40`.
- `wlc_rrm_doiovar` symbol: `.text + 0x2f9498`, ELF size `0x1454`.
- The following literal pool extends to the next function at `+0x1488`,
  accounting for the kernel's reported function extent.

The decoded caller is
`release/src-rt-5.02L.07p2axhnd/router-sysdep.rt-ax86u/wlconf/prebuilt/wlconf`:

- ELF32 ARM executable; addresses below are ELF virtual addresses.
- SHA-256: `02370b1a3045eceaffb5ce39a82d998cae03afdada06be14bcdb1a5a2e397ed4`.
- Its dynamic imports include `wl_iovar_setint`, `wl_bssiovar_setint`,
  `wl_ioctl`, and `libshared.so`.

These are offline artifact observations. The saved kernel log does not retain
the process's complete argv or the particular BSS slot responsible for the
fault.

## The offending operation is `rrm` set

The `rrm_iovars` table at `.rodata + 0x2e20f0` assigns ID 1 to `rrm`.
Action 2 is get; action 3 is set. The action is saved from `w1` at function
entry. Action 3 branches to function offset `+0xde0`; the saved value of `x4`
at the fault is not the original action selector.

The setter copies and masks the supplied five-byte capability bitmap into
the selected BSS's RRM state. On the path with no effective RRM capabilities,
it clears the selected BSS's RRM capability and scans the entire BSS table:

```asm
+0xee8  ldr   x0, [x22, #0x2e8]
+0xeec  add   x2, x0, #0x80       // end of 16 pointer slots
+0xef0  ldr   x1, [x0]           // BSS configuration
+0xef4  add   x0, x0, #8
+0xef8  cbz   x1, next_slot
+0xefc  ldr   x1, [x1, #0xb0]    // per-BSS information
+0xf00  ldrh  w1, [x1, #0x72]    // fault: information pointer is NULL
+0xf04  tbnz  w1, #12, ...       // RRM capability bit 0x1000
```

The configuration pointer is checked; its information pointer is not. The
private member name `current_bss` is inferred from its layout and use, rather
than established by a public matching structure definition. The offset and
pointer behavior are directly established by instructions.

This is not the channel-load measurement request. Disabling RRM does not
avoid the setter: a zero capability bitmap reaches the failing scan.

## Caller sequence: `up`, not `down`

`wlconf` compares argv[2] with `up` and `down` in its entry function at
`0x11b64`:

| Operation | Branch | Implementation and relevant calls |
| --- | --- | --- |
| `up` | `0x11c8c` to `0x13498` | Primary `rrm` setter at `0x153dc`, per-BSS setter at `0x15778` |
| `down` | `0x11cac` to `0x1f7b0` | Calls `0x12054` after probe; core down and BSS-disable operations |

At `0x153dc`, the import target `0x11a74` is `wl_iovar_setint`, and the
resolved iovar string is `rrm` at `0x243ac`. At `0x15778`, import target
`0x11858` is `wl_bssiovar_setint`, with the same resolved string.
If the NVRAM `rrm` value is absent, branches at `0x1d744` and `0x16224`
substitute defaults and still execute the setters.

The decoded `down` implementation reads capabilities, adjusts disabled BSS
configuration, issues `WLC_DOWN` (command 3 at `0x12404`/`0x124c0`), and
disables indexed BSSs through the `bss` iovar. Its body contains no `rrm`
setter. Replacing RC's `wlconf down` with a direct `WLC_DOWN` ioctl therefore
does not bypass the observed operation, and would omit existing shutdown
behavior.

In editable RC source, `restart_wireless()` in `lan.c` stops authentication
services, calls `stop_lan_wl()`, resets defaults, then calls
`start_lan_wl()`. The startup interface loops call `wlconf(ifname, unit,
subunit)`. Other startup/configuration callers exist, including the explicit
`wlconf up` in `services.c`. A fix limited to one restart's shutdown call
would not cover these startup setters.

## BSS lifetime findings

The same `wl.o` contains two directly observable lifetime hazards relevant to
the scan. They establish candidate causes, not which cause produced this
saved crash:

1. `wlc_bsscfg_alloc_int` at `0x278058` publishes the configuration into the
   table at `0x278084`, before initialization and interface setup finish.
   Its caller `wlc_bsscfg_alloc` at `0x279538` subsequently allocates an extra
   block at `0x2795e0`. Failure there branches to `0x2795a4`, calls
   `wlc_bsscfg_mfree`, and returns NULL without clearing the published slot.
   Failure from the initialization call also reaches that rollback.
2. `wlc_bsscfg_free` at `0x279628` calls `wlc_bsscfg_mfree` at `0x2796a4`,
   then clears the table entry at `0x2796ac`. Inside `mfree`, the information
   allocation is freed at `0x279350` and its pointer cleared at `0x279354`;
   the configuration allocation itself is freed at `0x279418`. Thus table
   unpublication follows reclamation. Whether another reader can enter that
   interval depends on the proprietary driver locking/callback contract.

`wlc_bsscfg_malloc` allocates the `+0xb0` information object before returning
a successful new configuration. `wlc_bsscfg_deinit` itself does not clear
that information pointer. This does not support a claim that ordinary
authentication-service shutdown alone creates the NULL pointer. Driver
rollback, freeing order, callback re-entry, and concurrency require further
vendor-side validation.

## Public API and build boundaries

`WLC_GET_UP` checks a core's operational state. A successful query does not
establish that each of the 16 BSS slots has a valid information allocation.
Public `WLC_GET_BSS_INFO` and shared-library per-BSS iovar helpers do not expose
an atomic snapshot of all private allocations. Querying configured VIFs can
miss an orphaned slot and cannot hold the driver lifecycle lock across the
later setter. No safe complete preflight guard or public allocation repair
was established by the bounded source inspection.

`release/src/router/shared/wl.c` implements both imported setters, and
`shared/Makefile` includes `wl.o`/`wl_linux.o` in `libshared.so`. This is an
editable interception boundary, but rejecting all `rrm` writes would disable
requested configuration; skipping duplicate values would omit setter side
effects and would leave changed values exposed. Neither is a complete
driver repair.

The target's `wlconf/Makefile` copies and installs its prebuilt executable.
`hnd_wl/wl_generic.mk` selects source rebuilding only when the private
`wl/sys/wlc.c` exists and prebuilt reuse is not requested; otherwise it links
prebuilt driver objects. The matching `wlc_rrm.c` and `wlc_bsscfg.c` are not
present as editable target sources. The upstream candidate blob
`c5193d01ff3f379cabd9c39e17f2b02980523c01` was also inspected: its
`wlc_rrm_doiovar` still has the same unguarded pointer chain at `+0xefc/+0xf00`.
Replacing this blob alone is not an RRM NULL-guard fix.

The missing private C sources prevent a conventional source edit. They do not
prevent a pinned instruction correction in the supplied ELF object. The
manifest modifies 33 four-byte instructions across three existing functions,
without changing ELF geometry, symbol values, function sizes, or relocation
tables. It checks the original whole-file digest, function digests and original
words before applying the replacement words. It must reject another driver
version rather than infer compatible offsets.

## Exact instruction repair

The selected-BSS guard uses the existing setter's mask-prefix space
(`0x2fa278`–`0x2fa2bc`). It loads the information pointer, returns
`BCME_NOTREADY` (-25) through the original epilogue if NULL, and otherwise
initializes the same five mask bytes (`73 5f 00 0f 1c`) before entering the
original general masking loop at `0x2fa2c0`. This checks before parameter,
stored-capability or extended-capability mutation. Valid zero and nonzero RRM
configuration retains the original setter behavior.

The all-BSS scan compacts its load and cursor increment to make space for the
second pointer check:

```asm
0x2fa388  ldr   x1, [x0], #8
0x2fa38c  cbz   x1, 0x2fa3a0
0x2fa390  ldr   x1, [x1, #0xb0]
0x2fa394  cbz   x1, 0x2fa3a0
0x2fa398  ldrh  w1, [x1, #0x72]      // original instruction
0x2fa39c  tbnz  w1, #12, 0x2fa6dc  // original instruction
```

Both missing pointers now reach the original cursor/end comparison at
`0x2fa3a0`. An initialized configuration with the RRM bit still takes the
original feature-enabled path. The loop's existing backedge retains its entry
address.

Allocation rollback retargets the original `mfree` call at `0x2795ac` to six
instructions occupying the former failure epilogue at
`0x2795b8`–`0x2795cc`. They compare the published slot with the failed
configuration, clear it only if ownership matches, then tail-call the original
`wlc_bsscfg_mfree` with its original arguments and return address. A slot
occupied by another configuration is preserved. Initial allocation failure
still skips cleanup; initialization and final extra-allocation failure use the
new rollback. Success retains the existing path. The failure epilogue's entry
at `0x2795b4` now branches to the complete original success epilogue at
`0x279600`, which restores the same saved registers and stack.

Normal freeing reorders the three words at `0x2796a4`–`0x2796ac` to load the
slot table, clear the indexed entry, then call the original `mfree`. The
configuration and driver arguments and the remaining free behavior are
preserved. No partially initialized configuration is passed to the full
`wlc_bsscfg_free` routine as a substitute for allocation rollback.

Static inspection found no relocation at the modified instructions and no
incoming direct branch into the repurposed allocation epilogue. The
instruction replacements preserve their function entry points and the RRM
switch entry. Object/package verification must also ensure the kernel's
alternative-instruction machinery does not replace a patched site.

## CI regression and practical limits

`tools/ci/test-ax86u-rrm-patches.py` applies the production manifest using the
same ELF patcher as the firmware build, checks the complete changed-byte set,
and executes original and patched instruction streams in Unicorn. It covers:

- Original NULL-selected-BSS crashes for zero/nonzero bitmaps versus a patched
  pre-mutation -25 return.
- Full mask-prefix register, flags, parameter and scratch-state equivalence
  for zero/all-set masks, every individual bit, and deterministic random masks.
- Orphaned first/last BSS slots, ordinary empty slots, and valid RRM slots
  before or after an orphan, including cursor and feature-bit behavior.
- Initial/initialization/extra allocation failure, successful allocation,
  indices 0 and 15, another slot owner, return values and callee-saved registers.
- Normal primary/secondary frees, original published-at-`mfree` behavior versus
  patched unpublication, original cleanup arguments, and register/stack
  preservation. The emulated configuration page is unmapped at `mfree` to
  detect subsequent reads of the reclaimed object.

The mask, pointer checks, traversal, rollback, free ordering and function
prologues/epilogues execute actual object instructions. External services
(allocation, initialization, cleanup and capability-notification helpers) are
stubbed according to their observed call interfaces; this does not emulate
their entire implementation, interrupt scheduling or hardware.

The repair corrects the established NULL accesses and publication/rollback
ordering under the existing driver locking contract. It does not establish
that arbitrary concurrent readers cannot retain a pointer across reclamation,
or repair every private BSS reference. A NULL check on an already freed
configuration is insufficient without lifetime protection. The saved crash
does not identify which allocator/callback path produced the orphan.

GitHub Actions must execute these regressions, compile/package the corrected
target module, and verify the packaged instruction bytes. Hardware validation
is separate. No local firmware build, local Unicorn regression run, router
change, or direct modification of the tracked prebuilt object was performed
for this repair specification.
