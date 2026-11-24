/* SPDX-License-Identifier: GPL-2.0 */
#ifndef RUNNER_WFD_RING_LIFECYCLE_H
#define RUNNER_WFD_RING_LIFECYCLE_H

/* The worker is stopped and RDPA has detached the queue before deletion.
 * The lock also protects the statistics callback from detached descriptors.
 */
static DEFINE_SPINLOCK(wfd_ring_lifetime_lock);

static inline void wfd_free_runner_ring(WFD_RING_S *ring)
{
    volatile CPU_RX_DESCRIPTOR *travel = ring->base;
    uint32_t entry;

    if (travel)
        for (entry = 0; entry < ring->ring_size; entry++, travel++)
            if (travel->word2)
            {
#ifdef _BYTE_ORDER_LITTLE_ENDIAN_
                travel->word2 = swap4bytes(travel->word2 | 0x80);
#else
                travel->ownership = OWNERSHIP_HOST;
#endif
                bdmf_sysb_databuf_free((void *)phys_to_virt(
                    (phys_addr_t)travel->host_buffer_data_pointer), 0);
                travel->word2 = 0;
            }

    if (ring->buff_cache)
    {
        while (ring->buff_cache_idx >= 0)
            bdmf_sysb_databuf_free(ring->buff_cache[ring->buff_cache_idx--], 0);
        kfree(ring->buff_cache);
    }
    while (ring->skbs_cache)
    {
        struct sk_buff *skb = ring->skbs_cache;
        ring->skbs_cache = skb->next;
        skb_header_free(skb);
    }
    if (ring->base)
        rdp_mm_aligned_free((void *)NONCACHE_TO_CACHE(ring->base),
            ring->ring_size * sizeof(CPU_RX_DESCRIPTOR));
    memset(ring, 0, sizeof(*ring));
}

static inline int wfd_delete_runner_ring(int ring_id)
{
    WFD_RING_S detached;
    unsigned long flags;

    if (ring_id < 0 || ring_id >= WFD_NUM_QUEUE_SUPPORTED)
        return -1;
    spin_lock_irqsave(&wfd_ring_lifetime_lock, flags);
    detached = wfd_rings[ring_id];
    memset(&wfd_rings[ring_id], 0, sizeof(wfd_rings[ring_id]));
    spin_unlock_irqrestore(&wfd_ring_lifetime_lock, flags);
    wfd_free_runner_ring(&detached);
    return 0;
}

static inline int wfd_create_runner_ring(int ring_id, uint32_t size,
        uint32_t **ring_base)
{
    WFD_RING_S staging;
    volatile CPU_RX_DESCRIPTOR *travel;
    uint32_t entry;
    bdmf_phys_addr_t phy_addr;
    unsigned long flags;
    void *data;

    if (ring_id < 0 || ring_id >= WFD_NUM_QUEUE_SUPPORTED || !size ||
        !ring_base || wfd_rings[ring_id].ring_size)
        return -1;
    *ring_base = NULL;
    memset(&staging, 0, sizeof(staging));
    staging.ring_size = size;
    staging.descriptor_size = sizeof(CPU_RX_DESCRIPTOR);
    staging.buff_cache_idx = -1;
    staging.buff_cache = kmalloc(sizeof(void *) * WFD_RING_MAX_BUFF_IN_CACHE,
        GFP_ATOMIC);
    if (!staging.buff_cache)
        goto failure;
    staging.base = rdp_mm_aligned_alloc(sizeof(CPU_RX_DESCRIPTOR) * size,
        &phy_addr);
    if (!staging.base)
        goto failure;

    /* Rollback must never interpret an uninitialized descriptor as a buffer. */
    memset(staging.base, 0, sizeof(CPU_RX_DESCRIPTOR) * size);
    for (travel = staging.base, entry = 0; entry < size; travel++, entry++)
    {
        data = wfd_databuf_alloc(&staging);
        if (!data)
            goto failure;
#if defined(CONFIG_BCM963138) || defined(_BCM963138_) || defined(CONFIG_BCM963148) || defined(_BCM963148_) || defined(CONFIG_BCM94908) || defined(_BCM94908_)
        travel->word2 = swap4bytes((uint32_t)virt_to_phys(data)) & ~0x80;
#else
        travel->host_buffer_data_pointer = VIRT_TO_PHYS(data);
        travel->ownership = OWNERSHIP_RUNNER;
#endif
    }
    staging.head = staging.base;
    staging.end = staging.base + size;
    spin_lock_irqsave(&wfd_ring_lifetime_lock, flags);
    wfd_rings[ring_id] = staging;
    spin_unlock_irqrestore(&wfd_ring_lifetime_lock, flags);
    *ring_base = (uint32_t *)(uintptr_t)phy_addr;
    return 0;

failure:
    wfd_free_runner_ring(&staging);
    return -1;
}

static inline int wfd_ring_get_queued(uint32_t ring_id)
{
    volatile CPU_RX_DESCRIPTOR *travel, *end;
    unsigned long flags;
    uint32_t packets = 0;

    if (ring_id >= WFD_NUM_QUEUE_SUPPORTED)
        return 0;
    spin_lock_irqsave(&wfd_ring_lifetime_lock, flags);
    travel = wfd_rings[ring_id].base;
    end = wfd_rings[ring_id].end;
    if (travel)
        while (travel != end)
        {
            uint32_t ownership = (swap4bytes(travel->word2) & 0x80000000) ? 1 : 0;
            packets += ownership == OWNERSHIP_HOST;
            travel++;
        }
    spin_unlock_irqrestore(&wfd_ring_lifetime_lock, flags);
    return packets;
}

static inline int wfd_config_rx_queue(int wfd_idx, int qid, uint32_t qsize,
        enumWFD_WlFwdHookType hook_type)
{
    rdpa_cpu_rxq_cfg_t rxq_cfg;
    uint32_t *ring_base = NULL;
    int rc;
    int qidx = qid - first_pci_queue;

    if (qidx < 0 || qidx >= WFD_NUM_QUEUE_SUPPORTED)
        return -1;
    bdmf_lock();
    rc = rdpa_cpu_rxq_cfg_get(rdpa_cpu_obj, qid, &rxq_cfg);
    if (rc)
        goto out;
    if (qsize)
    {
        rc = wfd_create_runner_ring(qidx, qsize, &ring_base);
        if (rc)
            goto out;
    }
    /* Keep the SDK's configured threshold on disable; a NULL ring_head is
     * its detach operation. Do not release descriptors before it succeeds.
     */
    rxq_cfg.size = WFD_WLAN_QUEUE_MAX_SIZE;
    rxq_cfg.isr_priv = qidx;
    rxq_cfg.rx_isr = qsize ? wfd_dev_rx_isr_callback : 0;
    rxq_cfg.ring_head = ring_base;
    rxq_cfg.ic_cfg.ic_enable = qsize ? true : false;
    rxq_cfg.ic_cfg.ic_timeout_us = WFD_INTERRUPT_COALESCING_TIMEOUT_US;
    rxq_cfg.ic_cfg.ic_max_pktcnt = WFD_INTERRUPT_COALESCING_MAX_PKT_CNT;
    rxq_cfg.rxq_stat = wfd_rxq_stat;
    rc = rdpa_cpu_rxq_cfg_set(rdpa_cpu_obj, qid, &rxq_cfg);
    if (!qsize)
    {
        if (!rc)
            wfd_delete_runner_ring(qidx);
    }
    else if (rc)
    {
        /* A failed setter may have partially installed a DMA ring. Explicit
         * detach must succeed before rolling back its allocation.
         */
        rxq_cfg.rx_isr = 0;
        rxq_cfg.ring_head = NULL;
        rxq_cfg.ic_cfg.ic_enable = false;
        if (!rdpa_cpu_rxq_cfg_set(rdpa_cpu_obj, qid, &rxq_cfg))
            wfd_delete_runner_ring(qidx);
    }
out:
    bdmf_unlock();
    return rc;
}

#endif
