/* SPDX-License-Identifier: GPL-2.0-only */
/* Link this harness with the actual production ax86u_rx_schedule.c in CI. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../../release/src-rt-5.02L.07p2axhnd/router-sysdep.rt-ax86u/hnd_dhd/ax86u_rx_schedule.h"

#define REQUIRE(condition) do { \
	if (!(condition)) { \
		fprintf(stderr, "line %d: %s\n", __LINE__, #condition); \
		exit(1); \
	} \
} while (0)

static struct ax86u_dhd_pub_view pub;
static struct ax86u_dhd_prot_view prot;
static struct ax86u_msgbuf_ring_view rx;
static unsigned char dma_rd[64], dma_wr[64];
static unsigned index_stride;
static ax86u_u16 shared_rd, shared_wr;
static int ipend, tx_pending, clear_rx_on_tx;
static unsigned rx_requests, rx_delivered, tx_requests, legacy_calls;
static unsigned dma_rd_reads, dma_wr_reads, shared_rd_reads, shared_wr_reads;
static ax86u_u8 watchdog_result;
static unsigned legacy_watchdog_calls, lock_calls, unlock_calls, lock_depth;
static int change_at_lock;
static unsigned cases;

static void put_index(unsigned char *array, ax86u_u16 index)
{
	unsigned offset = (rx.id - 2U) * index_stride;
	array[offset] = index & 0xff;
	array[offset + 1] = index >> 8;
}

static ax86u_u16 get_index(const unsigned char *array, ax86u_u16 ringid)
{
	unsigned offset = (ringid - 2U) * index_stride;
	REQUIRE(offset + 1 < sizeof(dma_rd));
	return (ax86u_u16)(array[offset] | ((unsigned)array[offset + 1] << 8));
}

/* Dependencies stand in for the pinned vendor aliases; no scheduling logic. */
ax86u_u16 ax86u_dhd_dma_indx_get(void *actual_prot, void **runner_hlp_addr,
	ax86u_u8 type, ax86u_u16 ringid)
{
	REQUIRE(actual_prot == &prot);
	REQUIRE(runner_hlp_addr == &pub.runner_hlp);
	REQUIRE(ringid == rx.id);
	if (legacy_watchdog_calls)
		REQUIRE(lock_depth == 1);
	if (type == 9) {
		REQUIRE(pub.dma_h2d_ring_upd_support != 0);
		++dma_rd_reads;
		return get_index(dma_rd, ringid);
	}
	REQUIRE(type == 8);
	REQUIRE(pub.dma_d2h_ring_upd_support != 0);
	++dma_wr_reads;
	return get_index(dma_wr, ringid);
}

void dhd_bus_cmn_readshared(void *bus, void *data, ax86u_u8 type,
	ax86u_u16 ringid)
{
	REQUIRE(bus == pub.bus);
	REQUIRE(ringid == rx.id);
	if (legacy_watchdog_calls)
		REQUIRE(lock_depth == 1);
	if (type == 0x13) {
		REQUIRE(pub.dma_h2d_ring_upd_support == 0);
		++shared_rd_reads;
		*(ax86u_u16 *)data = shared_rd;
	} else {
		REQUIRE(type == 0x14);
		REQUIRE(pub.dma_d2h_ring_upd_support == 0);
		++shared_wr_reads;
		*(ax86u_u16 *)data = shared_wr;
	}
}

ax86u_u8 *ax86u_dhd_legacy_get_read_addr(void *actual_pub, void *ring,
	ax86u_u32 *available_len)
{
	REQUIRE(actual_pub == &pub);
	REQUIRE(ring == prot.tx_completion_ring);
	++legacy_calls;
	*available_len = tx_pending ? 8 : 0;
	return tx_pending ? prot.tx_completion_ring : NULL;
}

int dhd_runner_notify(void *runner_hlp, int operation,
	unsigned long arg1, unsigned long arg2)
{
	REQUIRE(runner_hlp == pub.runner_hlp);
	REQUIRE(arg1 == 0 && arg2 == 0);
	if (operation == 6) {
		++tx_requests;
		if (clear_rx_on_tx)
			pub.runner_flags &= ~(1U << 2);
	} else {
		REQUIRE(operation == 5);
		if (legacy_watchdog_calls)
			REQUIRE(lock_depth == 1);
		++rx_requests;
		if (!ipend)
			++rx_delivered;
	}
	return 0;
}

ax86u_u8 ax86u_dhd_legacy_bus_watchdog(void *actual_pub)
{
	REQUIRE(actual_pub == &pub);
	REQUIRE(lock_depth == 0);
	++legacy_watchdog_calls;
	return watchdog_result;
}

void dhd_perim_lock(void *actual_pub)
{
	REQUIRE(actual_pub == &pub && pub.info != NULL);
	REQUIRE(lock_depth == 0 && legacy_watchdog_calls > 0);
	++lock_calls;
	lock_depth = 1;
	if (change_at_lock == 1)
		pub.up = 0;
	else if (change_at_lock == 2)
		pub.busstate = 0;
	else if (change_at_lock == 3)
		pub.prot = NULL;
	else if (change_at_lock == 4)
		pub.runner_hlp = NULL;
	else if (change_at_lock == 5)
		pub.dongle_reset = 1;
	else if (change_at_lock == 6)
		pub.runner_flags = 0;
}

void dhd_perim_unlock(void *actual_pub)
{
	REQUIRE(actual_pub == &pub && lock_depth == 1);
	++unlock_calls;
	lock_depth = 0;
}

static void reset_case(unsigned stride, unsigned item_size,
	int rd_dma_supported, int wr_dma_supported)
{
	memset(&pub, 0, sizeof(pub));
	memset(&prot, 0, sizeof(prot));
	memset(&rx, 0, sizeof(rx));
	memset(dma_rd, 0xa5, sizeof(dma_rd));
	memset(dma_wr, 0x5a, sizeof(dma_wr));
	pub.bus = (void *)&cases;
	pub.prot = &prot;
	pub.info = (void *)&cases;
	pub.up = 1;
	pub.busstate = 2;
	pub.iswl = 1;
	pub.runner_hlp = (void *)&ipend;
	pub.runner_flags = 1U << 2;
	pub.dma_h2d_ring_upd_support = rd_dma_supported;
	pub.dma_d2h_ring_upd_support = wr_dma_supported;
	prot.rx_completion_ring = &rx;
	rx.id = 4;
	rx.item_size = item_size;
	rx.max_items = 1024;
	/* Cached reader already caught the writer; Runner's consumer is behind. */
	rx.rd = rx.wr = 9;
	index_stride = stride;
	shared_rd = 5;
	shared_wr = 9;
	put_index(dma_rd, 5);
	put_index(dma_wr, 9);
	ipend = tx_pending = clear_rx_on_tx = 0;
	rx_requests = rx_delivered = tx_requests = legacy_calls = 0;
	dma_rd_reads = dma_wr_reads = shared_rd_reads = shared_wr_reads = 0;
	watchdog_result = 0;
	legacy_watchdog_calls = lock_calls = unlock_calls = lock_depth = 0;
	change_at_lock = 0;
	++cases;
}

static void schedule_without_mutation(void)
{
	unsigned char pub_before[sizeof(pub)], prot_before[sizeof(prot)];
	unsigned char rx_before[sizeof(rx)];
	unsigned char rd_before[sizeof(dma_rd)], wr_before[sizeof(dma_wr)];
	memcpy(pub_before, &pub, sizeof(pub_before));
	memcpy(prot_before, &prot, sizeof(prot_before));
	memcpy(rx_before, &rx, sizeof(rx_before));
	memcpy(rd_before, dma_rd, sizeof(rd_before));
	memcpy(wr_before, dma_wr, sizeof(wr_before));
	dhd_prot_schedule_runner(&pub);
	REQUIRE(!memcmp(&pub, pub_before, sizeof(pub)));
	REQUIRE(!memcmp(&prot, prot_before, sizeof(prot)));
	REQUIRE(!memcmp(&rx, rx_before, sizeof(rx)));
	REQUIRE(!memcmp(dma_rd, rd_before, sizeof(dma_rd)));
	REQUIRE(!memcmp(dma_wr, wr_before, sizeof(dma_wr)));
}

int main(void)
{
	unsigned stride, item_size;
	int rd_dma, wr_dma, gate, change;

	/* Independent capability flags, both index strides and WI sizes. */
	for (stride = 2; stride <= 4; stride += 2)
		for (item_size = 4; item_size <= 8; item_size += 4)
			for (rd_dma = 0; rd_dma <= 1; ++rd_dma)
				for (wr_dma = 0; wr_dma <= 1; ++wr_dma) {
					reset_case(stride, item_size, rd_dma, wr_dma);
					schedule_without_mutation();
					schedule_without_mutation();
					REQUIRE(rx_requests == 2 && rx_delivered == 2);
					REQUIRE(legacy_calls == 0 && tx_requests == 0);
					REQUIRE(dma_rd_reads == (rd_dma ? 2U : 0U));
					REQUIRE(dma_wr_reads == (wr_dma ? 2U : 0U));
					REQUIRE(shared_rd_reads == (rd_dma ? 0U : 2U));
					REQUIRE(shared_wr_reads == (wr_dma ? 0U : 2U));
				}

	/* A suppressed notification must remain retryable after ipend clears. */
	reset_case(2, 8, 1, 1);
	ipend = 1;
	schedule_without_mutation();
	REQUIRE(rx_requests == 1 && rx_delivered == 0);
	ipend = 0;
	schedule_without_mutation();
	REQUIRE(rx_requests == 2 && rx_delivered == 1);
	put_index(dma_rd, 9); /* Only the real consumer drains the queue. */
	schedule_without_mutation();
	REQUIRE(rx_requests == 2);

	/* Empty live ring wins over stale host cache; wrapped ring stays pending. */
	reset_case(4, 8, 1, 1);
	put_index(dma_rd, 9);
	rx.rd = 1;
	schedule_without_mutation();
	REQUIRE(rx_requests == 0);
	put_index(dma_rd, 1023);
	put_index(dma_wr, 15);
	schedule_without_mutation();
	REQUIRE(rx_requests == 1);

	/* Original TX helper and op6 behavior remain intact. */
	reset_case(2, 8, 1, 1);
	pub.runner_flags = 1U << 1;
	tx_pending = 1;
	schedule_without_mutation();
	REQUIRE(legacy_calls == 1 && tx_requests == 1 && rx_requests == 0);
	REQUIRE(dma_rd_reads == 0 && dma_wr_reads == 0);
	tx_pending = 0;
	schedule_without_mutation();
	REQUIRE(legacy_calls == 2 && tx_requests == 1);

	reset_case(2, 8, 1, 1);
	pub.runner_flags |= 1U << 1;
	tx_pending = 1;
	schedule_without_mutation();
	REQUIRE(legacy_calls == 1 && tx_requests == 1 && rx_requests == 1);

	reset_case(2, 8, 1, 1);
	pub.runner_flags |= 1U << 1;
	schedule_without_mutation();
	REQUIRE(legacy_calls == 1 && tx_requests == 0 && rx_requests == 1);

	/* Re-read RX enable state after the legacy TX callback. */
	reset_case(2, 8, 1, 1);
	pub.runner_flags |= 1U << 1;
	tx_pending = clear_rx_on_tx = 1;
	dhd_prot_schedule_runner(&pub);
	REQUIRE(tx_requests == 1 && rx_requests == 0);

	reset_case(2, 8, 1, 1);
	pub.runner_flags = 0;
	schedule_without_mutation();
	REQUIRE(legacy_calls == 0 && rx_requests == 0 && dma_rd_reads == 0);
	pub.prot = NULL;
	dhd_prot_schedule_runner(&pub);
	dhd_prot_schedule_runner(NULL);
	REQUIRE(rx_requests == 0);

	/* The existing watchdog supplies RX-only retries and preserves its return. */
	for (gate = 0; gate <= 1; ++gate) {
		reset_case(4, 8, 1, 1);
		pub.runner_flags |= 1U << 1;
		tx_pending = 1;
		watchdog_result = gate;
		ipend = 1;
		REQUIRE(dhd_bus_watchdog(&pub) == watchdog_result);
		REQUIRE(rx_requests == 1 && rx_delivered == 0);
		ipend = 0;
		REQUIRE(dhd_bus_watchdog(&pub) == watchdog_result);
		REQUIRE(rx_requests == 2 && rx_delivered == 1);
		put_index(dma_rd, 9);
		REQUIRE(dhd_bus_watchdog(&pub) == watchdog_result);
		REQUIRE(rx_requests == 2);
		REQUIRE(legacy_calls == 0 && tx_requests == 0);
		REQUIRE(legacy_watchdog_calls == 3);
		REQUIRE(lock_calls == 3 && unlock_calls == 3 && lock_depth == 0);
		REQUIRE(rx.rd == 9 && rx.wr == 9);
	}

	/* Inactive/trap/reset instances must not acquire a perimeter lock. */
	for (gate = 0; gate < 6; ++gate) {
		reset_case(2, 8, 1, 1);
		watchdog_result = 1;
		if (gate == 0) pub.up = 0;
		if (gate == 1) pub.busstate = 0;
		if (gate == 2) pub.iswl = 0;
		if (gate == 3) pub.dongle_reset = 1;
		if (gate == 4) pub.info = NULL;
		if (gate == 5) pub.runner_flags = 0;
		REQUIRE(dhd_bus_watchdog(&pub) == 1);
		REQUIRE(legacy_watchdog_calls == 1 && lock_calls == 0);
		REQUIRE(rx_requests == 0 && dma_rd_reads == 0);
	}

	/* State and protocol can change before locking; always recheck/balance. */
	for (change = 1; change <= 6; ++change) {
		reset_case(2, 8, 1, 1);
		watchdog_result = 1;
		change_at_lock = change;
		REQUIRE(dhd_bus_watchdog(&pub) == 1);
		REQUIRE(legacy_watchdog_calls == 1);
		REQUIRE(lock_calls == 1 && unlock_calls == 1 && lock_depth == 0);
		REQUIRE(rx_requests == 0 && dma_rd_reads == 0);
	}

	/* Missing RX context and a disabled RX flag cannot touch completion data. */
	for (gate = 0; gate < 4; ++gate) {
		reset_case(2, 8, 1, 1);
		if (gate == 0) pub.prot = NULL;
		if (gate == 1) pub.runner_hlp = NULL;
		if (gate == 2) pub.bus = NULL;
		if (gate == 3) pub.runner_flags = 1U << 1;
		REQUIRE(dhd_bus_watchdog(&pub) == 0);
		REQUIRE(lock_calls == (gate == 3 ? 0U : 1U));
		REQUIRE(unlock_calls == lock_calls && lock_depth == 0);
		REQUIRE(rx_requests == 0 && legacy_calls == 0);
		REQUIRE(dma_rd_reads == 0 && dma_wr_reads == 0);
	}
	REQUIRE(dhd_bus_watchdog(NULL) == 0);

	printf("RT-AX86U production DHD scheduler: %u scenarios passed\n", cases);
	return 0;
}
