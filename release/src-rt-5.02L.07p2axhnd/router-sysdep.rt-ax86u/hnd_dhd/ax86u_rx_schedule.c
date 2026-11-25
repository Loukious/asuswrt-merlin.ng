/* SPDX-License-Identifier: GPL-2.0-only */
#include "ax86u_rx_schedule.h"

const char ax86u_rx_schedule_fix_marker[] __attribute__((used)) =
	"DHD_RX_SCHEDULE_FIX_V1";

#define AX86U_RNR_TXSTS_OFFLOAD (1U << 1)
#define AX86U_RNR_RXCMPL_OFFLOAD (1U << 2)
#define AX86U_H2R_RX_COMPL_NOTIF 5
#define AX86U_H2R_TX_COMPL_NOTIF 6
#define AX86U_D2H_DMA_INDX_WR_UPD 8
#define AX86U_D2H_DMA_INDX_RD_UPD 9
#define AX86U_RING_RD_UPD 0x13
#define AX86U_RING_WR_UPD 0x14
#define AX86U_DHD_BUS_DATA 2

/*
 * Strong replacement for the pinned vendor scheduler. TX keeps the original
 * helper and notification behavior. RX merely peeks at the consumer/producer
 * indices: Runner owns this receive ring, so a scheduling check must not advance
 * the host's cached reader as if it had consumed the completion entries.
 *
 * Repeated host checks can retry a pending RX notification. The existing
 * dhd_runner_notify ipend guard is retained; this does not force a busy Runner
 * or guarantee recovery from a stopped HWA engine.
 */
static void ax86u_schedule_rx(struct ax86u_dhd_pub_view *pub)
{
	struct ax86u_dhd_prot_view *prot;
	struct ax86u_msgbuf_ring_view *ring;
	ax86u_u16 rd, wr;

	if (!pub || !pub->prot)
		return;
	prot = pub->prot;

	if (!(pub->runner_flags & AX86U_RNR_RXCMPL_OFFLOAD))
		return;
	if (!pub->runner_hlp || !pub->bus)
		return;
	/* The pinned Runner supports the first RX completion ring. */
	ring = prot->rx_completion_ring;
	if (!ring)
		return;

	if (pub->dma_h2d_ring_upd_support) {
		/* The original getter handles its negotiated 2/4-byte index stride. */
		rd = ax86u_dhd_dma_indx_get(prot, &pub->runner_hlp,
			AX86U_D2H_DMA_INDX_RD_UPD, ring->id);
	} else {
		dhd_bus_cmn_readshared(pub->bus, &rd, AX86U_RING_RD_UPD, ring->id);
	}
	if (pub->dma_d2h_ring_upd_support) {
		wr = ax86u_dhd_dma_indx_get(prot, &pub->runner_hlp,
			AX86U_D2H_DMA_INDX_WR_UPD, ring->id);
	} else {
		dhd_bus_cmn_readshared(pub->bus, &wr, AX86U_RING_WR_UPD, ring->id);
	}

	if (rd != wr)
		dhd_runner_notify(pub->runner_hlp, AX86U_H2R_RX_COMPL_NOTIF, 0, 0);
}

void dhd_prot_schedule_runner(struct ax86u_dhd_pub_view *pub)
{
	struct ax86u_dhd_prot_view *prot;
	ax86u_u32 available_len;

	if (!pub || !pub->prot)
		return;
	prot = pub->prot;
	if (pub->runner_flags & AX86U_RNR_TXSTS_OFFLOAD) {
		if (ax86u_dhd_legacy_get_read_addr(pub, prot->tx_completion_ring,
			&available_len))
			dhd_runner_notify(pub->runner_hlp, AX86U_H2R_TX_COMPL_NOTIF,
				0, 0);
	}
	ax86u_schedule_rx(pub);
}

static int ax86u_rx_is_active(struct ax86u_dhd_pub_view *pub)
{
	return pub->info && pub->up && !pub->dongle_reset && pub->iswl &&
		pub->busstate == AX86U_DHD_BUS_DATA &&
		(pub->runner_flags & AX86U_RNR_RXCMPL_OFFLOAD);
}

/*
 * The existing watchdog timer/thread is stopped before protocol detach. Its
 * original callback already uses this same perimeter lock for msgbuf work.
 * Preserve that callback and return value, then retry RX under the lock while
 * the protocol remains active. No new timer, ownership write, or TX work.
 * The inactive precheck also excludes the DPC's trap-time watchdog invocation,
 * which sets busstate DOWN before calling this function.
 */
ax86u_u8 dhd_bus_watchdog(struct ax86u_dhd_pub_view *pub)
{
	ax86u_u8 result;

	if (!pub)
		return 0;
	result = ax86u_dhd_legacy_bus_watchdog(pub);
	if (!ax86u_rx_is_active(pub))
		return result;

	dhd_perim_lock(pub);
	if (ax86u_rx_is_active(pub))
		ax86u_schedule_rx(pub);
	dhd_perim_unlock(pub);
	return result;
}
