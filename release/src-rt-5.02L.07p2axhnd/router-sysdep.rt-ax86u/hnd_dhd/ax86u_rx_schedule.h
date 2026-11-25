/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef AX86U_RX_SCHEDULE_H
#define AX86U_RX_SCHEDULE_H

#include <stddef.h>

typedef unsigned char ax86u_u8;
typedef unsigned short ax86u_u16;
typedef unsigned int ax86u_u32;

/*
 * Views of the exact ELF64 DHD object pinned by ax86u-dhd-scheduler.json.
 * These are not replacement definitions of the complete vendor structures.
 * Every accessed offset is corroborated by the pinned scheduler/getter code.
 */
struct ax86u_msgbuf_ring_view {
	ax86u_u16 reserved;
	ax86u_u16 id;
	ax86u_u16 rd;
	ax86u_u16 wr;
	ax86u_u16 item_type;
	ax86u_u16 item_size;
	ax86u_u16 max_items;
};

struct ax86u_dhd_prot_view {
	ax86u_u8 before_tx_ring[0x1b0];
	ax86u_u8 tx_completion_ring[0x78];
	struct ax86u_msgbuf_ring_view *rx_completion_ring;
};

struct ax86u_dhd_pub_view {
	void *osh;
	void *bus;
	struct ax86u_dhd_prot_view *prot;
	void *info;
	ax86u_u8 before_up[0x28 - 0x20];
	ax86u_u8 up;
	ax86u_u8 txoff;
	ax86u_u8 dongle_reset;
	ax86u_u8 before_busstate;
	ax86u_u32 busstate;
	ax86u_u8 before_iswl[0x3d - 0x30];
	ax86u_u8 iswl;
	ax86u_u8 before_dma_support[0x2a19 - 0x3e];
	ax86u_u8 dma_d2h_ring_upd_support;
	ax86u_u8 dma_h2d_ring_upd_support;
	ax86u_u8 before_runner_hlp[0x2a98 - 0x2a1b];
	void *runner_hlp;
	ax86u_u32 runner_flags;
};

#define AX86U_LAYOUT_ASSERT(name, expression) \
	typedef char ax86u_layout_assert_##name[(expression) ? 1 : -1]
AX86U_LAYOUT_ASSERT(pointer_width, sizeof(void *) == 8);
AX86U_LAYOUT_ASSERT(u16_width, sizeof(ax86u_u16) == 2);
AX86U_LAYOUT_ASSERT(u32_width, sizeof(ax86u_u32) == 4);
AX86U_LAYOUT_ASSERT(pub_bus, offsetof(struct ax86u_dhd_pub_view, bus) == 0x8);
AX86U_LAYOUT_ASSERT(pub_prot, offsetof(struct ax86u_dhd_pub_view, prot) == 0x10);
AX86U_LAYOUT_ASSERT(pub_info, offsetof(struct ax86u_dhd_pub_view, info) == 0x18);
AX86U_LAYOUT_ASSERT(pub_up, offsetof(struct ax86u_dhd_pub_view, up) == 0x28);
AX86U_LAYOUT_ASSERT(pub_reset,
	offsetof(struct ax86u_dhd_pub_view, dongle_reset) == 0x2a);
AX86U_LAYOUT_ASSERT(pub_state,
	offsetof(struct ax86u_dhd_pub_view, busstate) == 0x2c);
AX86U_LAYOUT_ASSERT(pub_iswl, offsetof(struct ax86u_dhd_pub_view, iswl) == 0x3d);
AX86U_LAYOUT_ASSERT(pub_dma,
	offsetof(struct ax86u_dhd_pub_view, dma_d2h_ring_upd_support) == 0x2a19);
AX86U_LAYOUT_ASSERT(pub_dma_rd,
	offsetof(struct ax86u_dhd_pub_view, dma_h2d_ring_upd_support) == 0x2a1a);
AX86U_LAYOUT_ASSERT(pub_runner,
	offsetof(struct ax86u_dhd_pub_view, runner_hlp) == 0x2a98);
AX86U_LAYOUT_ASSERT(pub_flags,
	offsetof(struct ax86u_dhd_pub_view, runner_flags) == 0x2aa0);
AX86U_LAYOUT_ASSERT(prot_tx,
	offsetof(struct ax86u_dhd_prot_view, tx_completion_ring) == 0x1b0);
AX86U_LAYOUT_ASSERT(prot_rx,
	offsetof(struct ax86u_dhd_prot_view, rx_completion_ring) == 0x228);
AX86U_LAYOUT_ASSERT(ring_id, offsetof(struct ax86u_msgbuf_ring_view, id) == 2);
#undef AX86U_LAYOUT_ASSERT

/* Global aliases to pinned local functions, added to a build copy by CI. */
extern ax86u_u8 *ax86u_dhd_legacy_get_read_addr(void *pub, void *ring,
	ax86u_u32 *available_len);
extern ax86u_u16 ax86u_dhd_dma_indx_get(void *prot, void **runner_hlp_addr,
	ax86u_u8 type, ax86u_u16 ringid);
extern ax86u_u8 ax86u_dhd_legacy_bus_watchdog(void *pub);

extern int dhd_runner_notify(void *runner_hlp, int operation,
	unsigned long arg1, unsigned long arg2);
extern void dhd_bus_cmn_readshared(void *bus, void *data, ax86u_u8 type,
	ax86u_u16 ringid);
extern void dhd_perim_lock(void *pub);
extern void dhd_perim_unlock(void *pub);

void dhd_prot_schedule_runner(struct ax86u_dhd_pub_view *pub);
ax86u_u8 dhd_bus_watchdog(struct ax86u_dhd_pub_view *pub);

#endif /* AX86U_RX_SCHEDULE_H */
