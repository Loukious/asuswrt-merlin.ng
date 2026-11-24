# RT-AX86U WFD receive-ring lifecycle patch

The diagnostic fault instruction `b9400820` is an AArch64 load from `[X1,#8]`
with `X1=0`, consistent with the observed `head->word2` access in the WFD
receive path. This patch fixes independently visible lifecycle defects in that
path. It does not establish WFD as the cause of the earlier wireless firmware
RX stall or repair the separate proprietary `wlc_rrm_doiovar` fault.

## Source provenance and build scope

The RT-AX86U SDK `src-rt-5.02L.07p2axhnd` originally builds WFD from a model
prebuilt object; editing its inline header alone has no effect. The matching
GPL source imported here comes from this repository's
`src-rt-5.02axhnd.675x/bcmdrivers/opensource/net/wfd/impl1/wfd_dev.c` at
`63813bdea`. It includes the BCM94908 Runner backend used by RT-AX86U. The
prebuilt object's DWARF matches the donor's 320-byte `wfd_object` at line 267,
worker at line 378, bind at line 1109, and unbind at line 1353. The donor rate
limiter is also imported for configurations that enable it; its header matches
the target header.

The omitted BDMF and legacy Runner descriptor headers are supplied only to this
module, with pinned source provenance and target ABI checks documented in
`RT-AX86U-WFD-BDMF-HEADERS.md` and `RT-AX86U-RUNNER-HEADERS.md`. The unused
`rdpa_mw_blog_parse.h` include is removed; it declared middleware routines
that this WFD header does not call.

`WFD_BUILD_FROM_SOURCE=1` selects the imported source only for RT-AX86U in this
SDK. Other models retain their existing prebuilt module build. The init log
references `WFD_RX_LIFECYCLE_FIX_V1`, explicitly placed in `.rodata`; CI verifies
the marker and AArch64 machine type in both the compiled module and final root
filesystem module before firmware packaging.

## Lifecycle guarantees within bind/unbind

- Bind reserves and validates a slot, initializes its waitqueue before ISR
  registration, keeps IRQs masked and the object inactive during construction,
  and activates only after the worker and queues exist.
- ISR and worker IRQ re-enable decisions share the object's IRQ lock. Pending
  work is filtered to active owned queues and only actual set bits are visited.
- Unbind and bind rollback retire/mask the object, stop its worker, and then
  detach RDPA queues before freeing their descriptor rings.
- Ring allocation uses staging and zeroes every descriptor before packet
  allocation; rollback frees only initialized resources. Deletion detaches all
  pointers, frees cached packet/SKB resources, and is idempotent.
- Statistics descriptor scans share a spinlock with ring publication/detach.
  The normal packet path relies on worker quiescence for ring lifetime.
- Failed RDPA deconfiguration retains its allocations and reserves the object
  slot. A later unbind can retry. An inactive object rejects further IRQ work
  and software-queue producer scheduling while the module remains loaded.

The worker never takes the bind/unbind mutex. The existing API already called
`kthread_stop`; moving it before queue release fixes the free-before-stop window.
The proprietary WLAN forward-hook perimeter behavior is not established by
these source checks. Software-queue destruction continues to require producers
to be quiescent after the vendor context-unregister contract. Quarantine is
scoped to bind/unbind while the module remains loaded: it cannot preserve
registered callback text after an explicit module unload, whose exit callback
cannot reject unloading. No safe generic unload claim is made.

## Validation

All compilation and execution is performed in GitHub Actions, including the
full target kernel module/firmware build. No local firmware build is required.

`test-wfd-rx-lifecycle.c` directly includes production ring allocation,
deconfiguration/rollback, work-mask, and release-decision functions. Vendor APIs
and allocators are deterministic substitutes. Its failure injections cover
cache, descriptor and packet allocation; dirty uninitialized descriptor memory;
partial hardware configuration; failed hardware detach with retained DMA
allocations; successful retry; and cached packet/SKB cleanup. It also covers
work-mask holes, stale/unowned bits, activation/retirement IRQ decisions, and
rejection of release while a worker remains present. ASan/UBSan check these host
paths. These tests exercise production functions, but do not simulate the
kernel scheduler, real DMA, IRQ delivery, or the proprietary WLAN hook locks.

The full source build and final module marker are required separately. Passing
CI is not evidence that the router's original RX firmware stall has been fixed;
hardware validation after installing the resulting firmware is still required.
