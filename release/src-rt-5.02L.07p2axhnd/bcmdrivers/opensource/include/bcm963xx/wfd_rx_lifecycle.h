/* SPDX-License-Identifier: GPL-2.0 */
#ifndef WFD_RX_LIFECYCLE_H
#define WFD_RX_LIFECYCLE_H

/* Callers serialize these decisions with the object's IRQ lock. */
static inline unsigned long wfd_rx_active_work(int active,
        unsigned long pending, unsigned long owned)
{
    return active ? pending & owned : 0;
}

static inline int wfd_rx_queue_active(int active, unsigned long owned,
        unsigned int queue, unsigned int queue_count)
{
    return active && queue < queue_count &&
        queue < sizeof(owned) * 8 && (owned & (1UL << queue));
}

/* Consume only a set bit, preserving the high-priority-first policy. */
static inline unsigned int wfd_rx_take_highest(unsigned long *pending)
{
    unsigned long bits = *pending;
    unsigned int queue = 0;

    while (bits >>= 1)
        queue++;
    *pending &= ~(1UL << queue);
    return queue;
}

/* Failed hardware deconfiguration must retain DMA-owned allocations. */
static inline int wfd_rx_can_release(int active, int worker_present,
        int hardware_disable_status)
{
    return !active && !worker_present && !hardware_disable_status;
}

#endif
