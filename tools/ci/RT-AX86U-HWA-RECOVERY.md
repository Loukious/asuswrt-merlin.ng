# RT-AX86U HWA RX recovery guards

The pinned DHD handler continued RX-post recovery after a completion-drain
timeout and discarded a failed RX-post reinitialization status before updating
the dongle. The reviewed instruction fixes abort those paths through the
handler's existing epilogue. Successful recovery keeps the original calls,
arguments and hardware-update path.

## Status propagation

The target objects expose this concrete chain:

1. `rdd_dhd_rx_post_reinit` in `rdpa.o` calls uninitialization, then initialization
   when uninitialization succeeds. Its unconditional `MOV w0,0` at `.text+0x37f64`
   previously discarded either error. The RDPA patch replaces that instruction
   with NOP, preserving the result through the epilogue.
2. `dhd_helper_attr_rx_post_reinit_write_ex` calls that function at `0x25f38`.
   Its CSEL at `0x25f44` preserves success as zero and maps any nonzero failure
   to `BDMF_ERR_NOMEM` (-2). Its wrapper at `0x248b0` preserves that status.
3. The BDMF numeric setter preserves negative callback statuses; positive
   byte-count results are normalized to success. The RDPA callback's -2 mapping
   therefore remains a failure. `bdmf_attrelem_set_as_num` saves its result at
   `0xb588` and returns it after unlocking/tracing.
4. DHD notification operation 14 invokes attribute 17 at `.text+0x8b2c` and
   checks nonzero status at `0x8b30`. The source maps failure to `BCME_ERROR`.
5. `dhd_prot_process_hwa_event` calls the notifier at `0x2e560`, but immediately
   overwrote its result with the public DHD pointer at `0x2e564` before calling
   `dhd_prot_rxpost_upd` at `0x2e568`.

That update helper reads the RX-post producer and, when HWA index-DMA capability
is active, issues its existing hardware doorbell with value `0x50 | (WR << 16)`.
Skipping this update on failure prevents the handler from acknowledging failed
reinitialization as if its new RX-post producer were usable. The host's paused
RX-post state remains set; the guard does not force a halted engine to resume.

## Same-size argument-preserving guard

The nine new instruction replacements in
[`ax86u-dhd-scheduler.json`](ax86u-dhd-scheduler.json) reuse the duplicate
epilogue after the update call. They preserve both original CALL26 slots
(`0x2e560` and `0x2e568`) and all zero-valued notifier arguments.

| Offset in original `.text` | Original word | Replacement | Purpose |
| --- | --- | --- | --- |
| `0x2e564` | `aa1403e0` | `14000005` | Route reinitialization status to guard at `0x2e578` |
| `0x2e56c` | `a94153f3` | `17ffffaa` | Return after update through complete epilogue at `0x2e414` |
| `0x2e570` | `a9425bf5` | `aa1403e0` | Restore public DHD argument |
| `0x2e574` | `a94363f7` | `17fffffd` | Enter unchanged update call at `0x2e568` |
| `0x2e578` | `a9446bf9` | `35fff4e0` | Abort on any nonzero reinitialization status |
| `0x2e57c` | `a94573fb` | `17fffffd` | Continue successful reinitialization to argument restore |
| `0x2e5cc` | `36c7fcc0` | `36c7fd20` | Non-offloaded early path bypasses status guard |
| `0x2e748` | `17ffff87` | `17ffff8a` | Non-offloaded shared-index path bypasses status guard |
| `0x2e820` | `17ffff51` | `17ffff54` | Non-offloaded DMA-index path bypasses status guard |

The only original direct branches to the common argument-restore instruction
at `0x2e564` are `0x2e5cc`, `0x2e748` and `0x2e820`. No direct branch enters
the reused duplicate-epilogue words. The event switch table targets
`0x2e4ac`, `0x2e474`, `0x2e444`, `0x2e588`; the item-format table targets
`0x2e6d0`, `0x2e6d0`, `0x2e750`, `0x2e780`. Both tables avoid the reused words.
Non-offloaded paths jump to argument restore rather than treating a preceding
void writer's arbitrary return-register contents as a reinitialization status.

All sites are anchored to the unchanged original object SHA-256
`5324962629d4ddfff7efcdcf65f49a843edcbcd9ec743d5d18522f61fd25f173` and the HWA
function SHA-256
`e260c798a9f0fc38fa7ce359f60387fb2a2273087467e4fe0ac4456845ba32dc`.
The production patcher verifies each original word and rejects actual overlap
with any relocation. No function is resized and no relocation is moved.

## Actions validation

[`test-ax86u-dhd-hwa-timeout.py`](test-ax86u-dhd-hwa-timeout.py) executes the
actual original and patched AArch64 handler in Unicorn, with deterministic
dependency results. RX STOP is event reason **0**, confirmed by both the actual
event switch and `bcmevent.h`; the initial harness's use of reason 1 was corrected
before running the expanded checks.

The harness covers timeout in both logging modes; successful drain and
reinitialization; positive and negative failed reinitialization statuses;
unchanged other HWA events; and all three non-offloaded entrances to the update
block. The shared and DMA writers deliberately leave a nonzero return register,
so a misplaced error guard cannot pass by accident. It checks notifier/update
arguments, unchanged CALL26 instructions and relocation metadata, restored
callee-saved registers, return address, and balanced stack. The non-offloaded
fixtures execute the actual ring-reset helper on an empty buffer.

The tests run only in GitHub Actions. Target compilation and final packaged
module checks remain required. These changes fix observed recovery-time status
and ownership defects; they do not establish the initiating HWA loss of progress
or prove that every hardware stall can recover without restarting the device.
