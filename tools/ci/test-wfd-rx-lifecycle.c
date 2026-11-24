/* SPDX-License-Identifier: GPL-2.0 */
/* Compile/run only in Actions. This includes the production allocation and
 * RDPA rollback functions, with deterministic vendor/allocator substitutes.
 */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../../release/src-rt-5.02L.07p2axhnd/bcmdrivers/opensource/include/bcm963xx/wfd_rx_lifecycle.h"

#define WFD_NUM_QUEUE_SUPPORTED 6
#define WFD_RING_MAX_BUFF_IN_CACHE 64
#define WFD_WLAN_QUEUE_MAX_SIZE 128
#define WFD_INTERRUPT_COALESCING_TIMEOUT_US 1000
#define WFD_INTERRUPT_COALESCING_MAX_PKT_CNT 63
#define CONFIG_BCM94908 1
#define _BYTE_ORDER_LITTLE_ENDIAN_ 1
#define OWNERSHIP_HOST 1
#define GFP_ATOMIC 0
#define NONCACHE_TO_CACHE(p) (p)
#define DEFINE_SPINLOCK(name) int name
#define spin_lock_irqsave(lock, flags) do { (void)(lock); (flags) = 0; } while (0)
#define spin_unlock_irqrestore(lock, flags) do { (void)(lock); (void)(flags); } while (0)

typedef uintptr_t bdmf_phys_addr_t;
typedef uintptr_t phys_addr_t;
typedef int bdmf_boolean;
typedef int extern_rxq_stat_t;
typedef union {
    uint32_t word2;
    struct {
        uint32_t host_buffer_data_pointer : 31;
        uint32_t ownership : 1;
    };
} CPU_RX_DESCRIPTOR;
struct sk_buff { struct sk_buff *next; };
typedef struct {
    CPU_RX_DESCRIPTOR *head;
    void **buff_cache;
    int buff_cache_idx;
    uint32_t skbs_cache_cnt;
    struct sk_buff *skbs_cache;
    CPU_RX_DESCRIPTOR *base;
    CPU_RX_DESCRIPTOR *end;
    uint32_t ring_size;
    uint32_t descriptor_size;
} WFD_RING_S;
static WFD_RING_S wfd_rings[WFD_NUM_QUEUE_SUPPORTED];
static const int first_pci_queue = 8;
static int rdpa_cpu_obj;
enum enumWFD_WlFwdHookType { WFD_WL_FWD_HOOKTYPE_SKB, WFD_WL_FWD_HOOKTYPE_FKB };
typedef enum enumWFD_WlFwdHookType enumWFD_WlFwdHookType;
typedef struct {
    uint32_t size;
    long isr_priv;
    void (*rx_isr)(long);
    uint32_t *ring_head;
    struct {
        bool ic_enable;
        int ic_timeout_us;
        int ic_max_pktcnt;
    } ic_cfg;
    void (*rxq_stat)(int, extern_rxq_stat_t *, bdmf_boolean);
} rdpa_cpu_rxq_cfg_t;

static void *buffers[256];
static unsigned int live_buffers, next_buffer, live_caches, live_rings, live_skbs;
static int cache_fail, ring_fail, buffer_limit = -1;
static int cfg_get_rc, cfg_status[4], cfg_calls;
static uint32_t *hardware_ring;

static uint32_t swap4bytes(uint32_t value) { return __builtin_bswap32(value); }
static void *kmalloc(size_t size, int flags)
{
    (void)flags;
    if (cache_fail)
        return NULL;
    live_caches++;
    return malloc(size);
}
static void kfree(void *ptr) { assert(live_caches); live_caches--; free(ptr); }
static void *rdp_mm_aligned_alloc(size_t size, bdmf_phys_addr_t *address)
{
    void *ptr;
    if (ring_fail)
        return NULL;
    ptr = malloc(size);
    assert(ptr);
    memset(ptr, 0xa5, size); /* Rollback must zero all descriptors first. */
    live_rings++;
    *address = 0x8000;
    return ptr;
}
static void rdp_mm_aligned_free(void *ptr, size_t size)
{
    (void)size;
    assert(!hardware_ring); /* Hardware may not access a freed descriptor. */
    assert(live_rings);
    live_rings--;
    free(ptr);
}
static uintptr_t virt_to_phys(void *ptr)
{
    unsigned int i;
    for (i = 0; i < next_buffer; i++)
        if (buffers[i] == ptr)
            return 0x1000 + i * 0x100;
    assert(0);
    return 0;
}
static void *phys_to_virt(phys_addr_t address)
{
    unsigned int index;
    assert(address >= 0x1000 && !(address & 0xff));
    index = (address - 0x1000) / 0x100;
    assert(index < next_buffer && buffers[index]);
    return buffers[index];
}
static void bdmf_sysb_databuf_free(void *ptr, int context)
{
    unsigned int index = (virt_to_phys(ptr) - 0x1000) / 0x100;
    (void)context;
    free(ptr);
    buffers[index] = NULL;
    assert(live_buffers);
    live_buffers--;
}
static void skb_header_free(struct sk_buff *skb)
{
    assert(live_skbs);
    live_skbs--;
    free(skb);
}
static void *wfd_databuf_alloc(WFD_RING_S *ring)
{
    unsigned int count;
    if (ring->buff_cache_idx < 0)
        for (count = 0; count < WFD_RING_MAX_BUFF_IN_CACHE; count++)
        {
            void *ptr;
            if (buffer_limit == 0)
                break;
            if (buffer_limit > 0)
                buffer_limit--;
            ptr = malloc(32);
            assert(ptr && next_buffer < 256);
            buffers[next_buffer++] = ptr;
            live_buffers++;
            ring->buff_cache[++ring->buff_cache_idx] = ptr;
        }
    if (ring->buff_cache_idx < 0)
        return NULL;
    return ring->buff_cache[ring->buff_cache_idx--];
}
static void bdmf_lock(void) {}
static void bdmf_unlock(void) {}
static int rdpa_cpu_rxq_cfg_get(int object, int qid, rdpa_cpu_rxq_cfg_t *cfg)
{
    (void)object;
    (void)qid;
    memset(cfg, 0, sizeof(*cfg));
    return cfg_get_rc;
}
static int rdpa_cpu_rxq_cfg_set(int object, int qid, rdpa_cpu_rxq_cfg_t *cfg)
{
    int rc;
    (void)object;
    (void)qid;
    assert(cfg_calls < 4);
    rc = cfg_status[cfg_calls++];
    /* Model partial publication on a failed enable, and failed detach. */
    if (cfg->ring_head || !rc)
        hardware_ring = cfg->ring_head;
    return rc;
}
static void wfd_dev_rx_isr_callback(long queue) { (void)queue; }
static void wfd_rxq_stat(int qid, extern_rxq_stat_t *stat, bdmf_boolean clear)
{ (void)qid; (void)stat; (void)clear; }

#include "../../release/src-rt-5.02L.07p2axhnd/bcmdrivers/opensource/include/bcm963xx/runner_wfd_ring_lifecycle.h"

static void assert_empty(void)
{
    unsigned int i;
    assert(!hardware_ring && !live_buffers && !live_caches && !live_rings && !live_skbs);
    for (i = 0; i < WFD_NUM_QUEUE_SUPPORTED; i++)
    {
        assert(!wfd_rings[i].head && !wfd_rings[i].base && !wfd_rings[i].end);
        assert(!wfd_rings[i].buff_cache && !wfd_rings[i].skbs_cache);
        assert(!wfd_rings[i].ring_size);
    }
}
static void reset_faults(void)
{
    assert_empty();
    next_buffer = 0;
    cache_fail = ring_fail = cfg_get_rc = cfg_calls = 0;
    buffer_limit = -1;
    memset(cfg_status, 0, sizeof(cfg_status));
}
static void test_allocation_rollback(void)
{
    uint32_t *base = NULL;
    reset_faults();
    cache_fail = 1;
    assert(wfd_create_runner_ring(0, 6, &base));
    assert_empty();
    reset_faults();
    ring_fail = 1;
    assert(wfd_create_runner_ring(0, 6, &base));
    assert_empty();
    reset_faults();
    buffer_limit = 3;
    assert(wfd_create_runner_ring(0, 6, &base));
    assert_empty();
    reset_faults();
    assert(!wfd_create_runner_ring(0, 4, &base));
    assert(base && wfd_rings[0].head == wfd_rings[0].base);
    assert(wfd_rings[0].end == wfd_rings[0].base + 4);
    assert(wfd_ring_get_queued(0) == 0);
    wfd_rings[0].base[2].word2 |= 0x80;
    assert(wfd_ring_get_queued(0) == 1);
    wfd_rings[0].skbs_cache = calloc(1, sizeof(struct sk_buff));
    wfd_rings[0].skbs_cache->next = calloc(1, sizeof(struct sk_buff));
    live_skbs = 2;
    assert(!wfd_delete_runner_ring(0));
    assert(!wfd_delete_runner_ring(0));
    assert(wfd_ring_get_queued(0) == 0);
    assert_empty();
    assert(wfd_create_runner_ring(-1, 4, &base));
    assert(wfd_create_runner_ring(WFD_NUM_QUEUE_SUPPORTED, 4, &base));
    assert(wfd_delete_runner_ring(-1));
    assert(wfd_ring_get_queued(WFD_NUM_QUEUE_SUPPORTED) == 0);
}
static void test_hardware_rollback(void)
{
    reset_faults();
    cfg_get_rc = -7;
    assert(wfd_config_rx_queue(0, 8, 4, WFD_WL_FWD_HOOKTYPE_SKB) == -7);
    assert(!cfg_calls);
    assert_empty();
    reset_faults();
    cfg_status[0] = -8; /* Partial attach, followed by successful detach. */
    assert(wfd_config_rx_queue(0, 8, 4, WFD_WL_FWD_HOOKTYPE_SKB) == -8);
    assert(cfg_calls == 2);
    assert_empty();
    reset_faults();
    cfg_status[0] = -8;
    cfg_status[1] = -9; /* Failed detach retains all resources. */
    assert(wfd_config_rx_queue(0, 8, 4, WFD_WL_FWD_HOOKTYPE_SKB) == -8);
    assert(hardware_ring && live_rings && wfd_rings[0].head);
    assert(!wfd_rx_can_release(0, 0, -9));
    assert(!wfd_config_rx_queue(0, 8, 0, WFD_WL_FWD_HOOKTYPE_SKB));
    assert_empty();
    reset_faults();
    assert(!wfd_config_rx_queue(0, 8, 4, WFD_WL_FWD_HOOKTYPE_SKB));
    cfg_status[1] = -10;
    assert(wfd_config_rx_queue(0, 8, 0, WFD_WL_FWD_HOOKTYPE_SKB) == -10);
    assert(hardware_ring && live_buffers && live_caches && live_rings);
    assert(!wfd_config_rx_queue(0, 8, 0, WFD_WL_FWD_HOOKTYPE_SKB));
    assert_empty();
}
static void test_work_and_retirement(void)
{
    unsigned long work = wfd_rx_active_work(1, (1UL << 5) | (1UL << 2) |
        (1UL << 1) | (1UL << 20), (1UL << 5) | (1UL << 2));
    assert(wfd_rx_take_highest(&work) == 5);
    assert(wfd_rx_take_highest(&work) == 2);
    assert(!work); /* No hole, unowned or stale high bit was consumed. */
    assert(!wfd_rx_queue_active(0, 3, 0, WFD_NUM_QUEUE_SUPPORTED));
    assert(!wfd_rx_active_work(0, 3, 3)); /* IRQ before activation/after retire. */
    assert(wfd_rx_queue_active(1, 3, 1, WFD_NUM_QUEUE_SUPPORTED));
    assert(!wfd_rx_queue_active(1, 3, 2, WFD_NUM_QUEUE_SUPPORTED));
    assert(!wfd_rx_queue_active(1, ~0UL, WFD_NUM_QUEUE_SUPPORTED, WFD_NUM_QUEUE_SUPPORTED));
    assert(!wfd_rx_queue_active(1, ~0UL, (unsigned int)-1, WFD_NUM_QUEUE_SUPPORTED));
    assert(!wfd_rx_can_release(1, 0, 0));
    assert(!wfd_rx_can_release(0, 1, 0)); /* Worker still consuming descriptors. */
    assert(!wfd_rx_can_release(0, 0, -1));
    assert(wfd_rx_can_release(0, 0, 0));
}
int main(void)
{
    test_work_and_retirement();
    test_allocation_rollback();
    test_hardware_rollback();
    puts("WFD production lifecycle tests passed");
    return 0;
}
