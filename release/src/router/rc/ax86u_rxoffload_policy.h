/* RT-AX86U receive-offload boot policy. */
#ifndef AX86U_RXOFFLOAD_POLICY_H
#define AX86U_RXOFFLOAD_POLICY_H

#include <stddef.h>
#include <string.h>

/*
 * Return a default only for an unconfigured RT-AX86U. Explicit settings are
 * preserved, including an opt-in to Runner RX offload. RT-AX86S shares the
 * RTAX86U build target and must retain its existing default.
 *
 * This bypasses the observed RX completion stall; it does not repair Runner.
 */
static inline const char *ax86u_rxoffload_boot_default(const char *productid,
	const char *configured)
{
	if (productid && !strcmp(productid, "RT-AX86U") &&
	    (!configured || !*configured))
		return "0:0";

	return NULL;
}

#endif /* AX86U_RXOFFLOAD_POLICY_H */
