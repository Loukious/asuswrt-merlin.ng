# RT-AX86U receive-offload scheduling fixes and fallback

Hardware validation of `9b70fdcce2` reproduced the RX stall despite the scheduler
and recovery fixes. Subsequent bounded captures located frozen Runner input
indices with an empty host queue, enabled IRQ, and remaining HWA credits.
The new [coherency comparison](RT-AX86U-RX-COHERENCY.md) tests the capability
negotiation boundary; it still requires enabled-offload hardware validation.

The enabled receive-offload path now has a source-built scheduling correction:
RX scheduling checks the real consumer and producer indices without advancing
the host's cached reader, and the existing watchdog periodically retries pending
RX work under the driver's perimeter lock. A separate RDPA instruction fix
preserves RX-post reinitialization errors. These correct demonstrated software
defects; an enabled-offload hardware stability test remains necessary.

The boot policy still defaults an unconfigured **RT-AX86U** to
`dhd1_rnr_rxoffl=0:0`, reproducing the successful same-firmware workaround.
That default bypasses Runner receive offload and is separate from the new
enabled-path fixes. The original initiating hardware/firmware stall is not
established by the retained evidence.

## Scope and configuration

The policy is called from `init_nvram()` before wireless modules are loaded.
It changes only a missing or empty `dhd1_rnr_rxoffl` setting on the exact runtime
product ID `RT-AX86U`. A nonempty setting is preserved, including the already
tested `0:0` workaround and explicit RX-offload opt-ins such as `1:0` or
`1:1024`. The RT-AX86S shares the RTAX86U compilation target but does not receive
this default. RT-AX86U PRO and other products are also excluded.

No NVRAM commit is added. The default is reapplied at boot when needed, including
after factory defaults are restored. TX offload, the 2.4 GHz setting, SSIDs,
authentication settings, and band steering are unaffected by this policy.
Receiving through the host may increase CPU use or reduce peak throughput;
PHY rates alone do not measure that cost.

With this policy installed, unsetting `dhd1_rnr_rxoffl` returns to the new safe
default on the next boot. A future explicitly authorized comparison of enabled
RX offload must use a nonempty enabling value, rather than merely deleting the
key. Configuration is read when DHD attaches; changing NVRAM does not switch an
active receive path. The current working router setting was not changed while
implementing this patch.

Production code:

- [`init.c`](../../release/src/router/rc/init.c), `MODEL_RTAX86U` case.
- [`ax86u_rxoffload_policy.h`](../../release/src/router/rc/ax86u_rxoffload_policy.h),
  the independent policy used by production and CI.

## Evidence supporting the fallback

The locally retained 2026-11-24 investigation recorded reception starting and
RX FIFO overflows increasing while data/beacon receive counters stopped. The
radio firmware asserted:

```text
Queue_rxcomplete: alloc wi space failed for HWA2b RxCPLE fast
```

The saved core had all 1,023 usable 8-byte CWI blocks outstanding, held by 75
pending HWA descriptors. The HWA hardware consumer register lies outside the
RAM dump. The evidence therefore cannot distinguish a stopped HWA engine from
backpressure elsewhere in the completion path. These blocks are not WFD's
separate host forwarding receive ring.

Disabling only Runner RX offload on the same installed firmware restored WPA2
authentication and usable 5 GHz data service. Runtime status showed `rxoffl 0`
and `txoffl 1`; data counters advanced, FIFO overflow counters remained zero,
external HTTPS returned HTTP 200, and 31 consecutive gateway pings succeeded.
That short observation verifies the workaround, not stability over multiple
days or a throughput limit. No dump or device identifiers are included here.

## Enabled-path scheduling correction

[`dhd_runner_attach()`](../../release/src-rt-5.02L.07p2axhnd/bcmdrivers/broadcom/net/wl/shared/impl1/dhd_runner.c)
programs RX-completion doorbells to wake Runner directly when RX offload is
enabled. Control completions retain host interrupts. This permits working
control requests while receive completions stop progressing.

The original `dhd_prot_schedule_runner()` uses `dhd_prot_get_read_addr()` to
check Runner-owned completion rings. That helper reads the producer's DMA WR
index, compares it to the cached host RD, and advances that cached RD as though
the host consumed the entries. Runner consumes the offloaded RX ring instead.
If a notification is suppressed or fails to start consumption, the cached RD
can equal WR while the actual DMA consumer remains behind; subsequent host
scheduling checks then omit the needed retry.

This behavior is confirmed in the exact vendor object's machine code, not
inferred solely from a neighboring SDK source version. The recorded host RX
indices went from RD 48 / WR 15 to RD 48 / WR 47: with the confirmed 1,024-entry
ring, the latter has all 1,023 usable entries pending. The recorded fallback
notification count advanced once. Those observations are consistent with the
scheduling defect, but the count omits direct firmware-to-Runner doorbells and
does not prove which event first stopped progress.

[`ax86u_rx_schedule.c`](../../release/src-rt-5.02L.07p2axhnd/router-sysdep.rt-ax86u/hnd_dhd/ax86u_rx_schedule.c)
provides a strong replacement scheduler. TX retains the original read-address
helper and op6 notification. RX peeks at the actual RD9 and WR8 indices and
sends the original op5 notification when they differ. Each side uses its own
DMA capability flag; an unsupported side uses the matching shared-memory
index read. The original getter retains negotiated 2/4-byte index strides.
The peek reads no completion payload and changes no ring reader, writer,
format, depth, or buffer ownership.

The existing `dhd_bus_watchdog()` is also wrapped, retaining its original call
and return value. Active RX instances get a periodic RX-only peek while the
original perimeter lock is held, after checking state again under the lock.
This provides retries even without another control interrupt. No new timer or
protocol lifetime extension is introduced. Timer/thread shutdown precedes
protocol detach in the actual object; the DPC's trap-time watchdog call first
sets busstate DOWN and is excluded by the active precheck.

The original `dhd_runner_notify()` CPU-queue `ipend` guard is retained. A pending
RX notification can be retried after that guard clears; the patch does not
force a busy Runner or guarantee recovery from a halted HWA engine.

## Build integration and exact binary boundary

The RT-AX86U
[`hnd_dhd/Makefile`](../../release/src-rt-5.02L.07p2axhnd/router-sysdep.rt-ax86u/hnd_dhd/Makefile)
uses a vendor DHD object when full core source is unavailable. Merely changing
the public `shared/impl1/dhd_runner.c` would not change that object. Its RX/TX configuration
parsers also ignore conversion counts and can use uninitialized values for
malformed input, but the original failure had no RX override and those parser
hazards do not explain it.

The build now prepares a copy of that exact object, adds global aliases to the
reviewed private helpers, weakens the original scheduler/watchdog symbols, and
links the new source alongside it. All original scheduler/watchdog callers use
symbol relocations that resolve to the strong replacements. Preparation pins
the whole input SHA-256, individual function bodies, field offsets, and incoming
call relocations, and rejects raw branches that would bypass the replacements.
The opaque structure views and private optimized getter ABI are specific to
that reviewed object; they are not generic vendor header replacements.
The original tracked blob is unchanged.

The implementation retains `DHD_RX_SCHEDULE_FIX_V1` in the compiled object.
Final verification must establish strong replacement symbols and caller
relocation targets as well as that marker. The preparation metadata is
[`ax86u-dhd-scheduler.json`](ax86u-dhd-scheduler.json).

The GPL `rdpa_dhd_helper_rx_post_init`, `rx_post_uninit`, and `rx_post_reinit`
interfaces are BDMF attribute wrappers. The relevant implementation is supplied
in `router-sysdep.rt-ax86u/hnd_extra/prebuilt/rdpa.o`; the receive consumer also
depends on Runner/radio firmware. A neighboring SDK in the same repository
contains the host helper's source, corroborating the reviewed recovery function;
its allocation path differs from this blob and is not substituted wholesale.

Offline ELF relocation inspection and Capstone disassembly additionally found
that `rdd_dhd_rx_post_reinit()` in that RDPA object calls uninitialization and,
on success, initialization, then unconditionally returns zero. It discards
both operations' error status. `rdd_dhd_rx_post_init()` can return an allocation
error. The reviewed patch replaces the final unconditional `MOV w0,0` with
`NOP`, so the existing epilogue returns either callee's status. The patch is
anchored to the function, checks its complete 64-byte body, and touches no
relocation. This is a concrete recovery error-handling correction, not proof
of the original completion stall's cause. Metadata is
[`ax86u-rdpa-patches.json`](ax86u-rdpa-patches.json).

The original HWA RX-stop handler logs a completion-drain timeout and then
reinitializes RX-post anyway. Two reviewed branch corrections now return through
its normal epilogue after a timeout, in both logging modes, so outstanding RX
completions do not lead to premature RX-post buffer recycling. Normal successful
drain and other HWA event paths are preserved. Persistent failure can leave
firmware RX paused; the guard does not forcibly recover a deadlocked HWA engine.
The ignored notify status and synchronous wait's interaction with DPC queue
servicing remain separate concerns. No unproven lock release or asynchronous
event-order change is introduced here.

The DHD `hwa_rx_en` string is referenced by a deferred event handler that sends
an enabling firmware iovar. Its presence does not establish a safe boot NVRAM
control for disabling HWA while preserving Runner RX offload. No speculative
memory barriers, ring-size changes, or hidden HWA toggles were added.
These artifact observations came from ordinary offline tools; no REA provider
was connected or installed, and no live target was accessed.

The demonstrated scheduling and error-return defects have targeted fixes.
If progress still stops with enabled RX offload, useful evidence is a synchronized record of firmware
CWI allocation/release counts, HWA producer/consumer state, host/Runner RX
completion read/write indices, and RX-post refill progress **before** any radio
reset. Recovery-time queue assertions and WFD kernel faults alone cannot
identify which stage first stopped progressing.

## GitHub Actions validation

Run the following only in the GitHub workflow, before the firmware build:

```sh
cc -std=c99 -Wall -Wextra -Werror -O2 \
  tools/ci/test-ax86u-rxoffload-policy.c \
  -o "$RUNNER_TEMP/test-ax86u-rxoffload-policy"
"$RUNNER_TEMP/test-ax86u-rxoffload-policy"

cc -std=c99 -Wall -Wextra -Werror -O2 \
  tools/ci/test-ax86u-dhd-scheduler.c \
  release/src-rt-5.02L.07p2axhnd/router-sysdep.rt-ax86u/hnd_dhd/ax86u_rx_schedule.c \
  -o "$RUNNER_TEMP/test-ax86u-dhd-scheduler"
"$RUNNER_TEMP/test-ax86u-dhd-scheduler"

python3 tools/ci/test-ax86u-dhd-original-scheduler.py \
  tools/ci/patch-ax86u-drivers.py
python3 tools/ci/test-ax86u-rdpa-patches.py tools/ci/patch-ax86u-drivers.py
python3 tools/ci/test-ax86u-dhd-hwa-timeout.py tools/ci/patch-ax86u-drivers.py
```

The table exercises absent/empty configuration, preservation of explicit
enabled and disabled values and custom depths, and exact product matching
including AX86S, PRO, other models, unknown IDs, and prefix/suffix mismatches.
The firmware build must also compile the actual RTAX86U `rc/init.c` integration.
The production-shim harness exercises the actual source with injected vendor
dependencies: cached-reader/live-consumer mismatches, pending retries after
`ipend` clears, real consumer progress, independent DMA capabilities, both
index strides, WI sizes, unchanged TX behavior, periodic return values, state
changes at lock acquisition, null/reset gates, and lock balance. The same
source/harness can be cross-compiled statically with `aarch64-linux-gnu-gcc`
and executed with `qemu-aarch64` in CI without kernel header dependencies.

The Unicorn checks execute the actual original scheduler and read-address
function to reproduce retry suppression, and the original/patched RDPA recovery
function with success and failure returns. They do not simulate Runner/HWA
hardware or establish throughput and stability. Final firmware verification
must prove the scheduler/watchdog callers resolve to the new strong symbols
and the RDPA patched return instruction is present. The HWA handler test runs
the actual original/patched control flow, verifying timeout aborts, preserved
logging, successful drain, other event types, and non-offloaded behavior.

Local compilation and test execution were intentionally omitted. Hardware
validation of any resulting image is separate from CI and has not been
performed by this implementation work.
