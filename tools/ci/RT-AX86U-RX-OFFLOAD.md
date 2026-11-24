# RT-AX86U receive-offload fallback and investigation

The boot policy defaults an unconfigured **RT-AX86U** to
`dhd1_rnr_rxoffl=0:0`. This reproduces the successful same-firmware workaround
for the observed 5 GHz receive stall. It bypasses Runner receive offload; it
does **not** repair the enabled receive-offload engine or establish the
initiating defect.

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

## Why an enabled-offload repair is not claimed

[`dhd_runner_attach()`](../../release/src-rt-5.02L.07p2axhnd/bcmdrivers/broadcom/net/wl/shared/impl1/dhd_runner.c)
programs RX-completion doorbells to wake Runner directly when RX offload is
enabled. Control completions retain host interrupts. This permits working
control requests while receive completions stop progressing.

The RT-AX86U
[`hnd_dhd/Makefile`](../../release/src-rt-5.02L.07p2axhnd/router-sysdep.rt-ax86u/hnd_dhd/Makefile)
forces `REBUILD_DHD_MODULE=0` when `hnd_extra/prebuilt/dhd.o` exists, and links
that object. Consequently, changing the public `shared/impl1/dhd_runner.c`
would not change this product's shipped DHD driver. Its RX/TX configuration
parsers also ignore conversion counts and can use uninitialized values for
malformed input, but the original failure had no RX override and those parser
hazards do not explain it.

The GPL `rdpa_dhd_helper_rx_post_init`, `rx_post_uninit`, and `rx_post_reinit`
interfaces are BDMF attribute wrappers. The relevant implementation is supplied
in `router-sysdep.rt-ax86u/hnd_extra/prebuilt/rdpa.o`; the receive consumer also
depends on Runner/radio firmware. There is no complete editable implementation
of this receive path in the inspected tree.

Offline ELF relocation inspection and Capstone disassembly additionally found
that `rdd_dhd_rx_post_reinit()` in that RDPA object calls uninitialization and,
on success, initialization, then unconditionally returns zero. It discards
both operations' error status. `rdd_dhd_rx_post_init()` can return an allocation
error. This is a concrete recovery error-handling defect, but it does not prove
the cause of the original completion stall. A watchdog must not treat this
reinitialization API's success result as proof of recovery.

The DHD `hwa_rx_en` string is referenced by a deferred event handler that sends
an enabling firmware iovar. Its presence does not establish a safe boot NVRAM
control for disabling HWA while preserving Runner RX offload. No speculative
memory barriers, ring-size changes, hidden toggles, or binary patches were added.
These artifact observations came from ordinary offline tools; no REA provider
was connected or installed, and no live target was accessed.

An actual enabled-offload repair requires the matching vendor implementation
or an independently verified replacement fixing completion consumption and
buffer recycling. Useful future evidence is a synchronized record of firmware
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
```

The table exercises absent/empty configuration, preservation of explicit
enabled and disabled values and custom depths, and exact product matching
including AX86S, PRO, other models, unknown IDs, and prefix/suffix mismatches.
The firmware build must also compile the actual RTAX86U `rc/init.c` integration.
The host policy test does not simulate DHD, Runner, HWA hardware, or forwarding
ring teardown.

Local compilation and test execution were intentionally omitted. Hardware
validation of any resulting image is separate from CI and has not been
performed by this implementation work.
