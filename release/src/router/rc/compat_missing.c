#include "rc.h"

#include <stddef.h>
#include <string.h>

__attribute__((weak)) int send_reinit_bssinfo_to_conn_diag(void)
{
	return 0;
}

__attribute__((weak)) int chk_acscli2_cmds(const char *cmd)
{
	(void)cmd;
	return 0;
}

__attribute__((weak)) void avbl_reset_exclvalid(const char *wl_ifname)
{
	(void)wl_ifname;
}

__attribute__((weak)) int firmware_webs_update_main(int argc, char *argv[])
{
	(void)argc;
	(void)argv;
	return 0;
}

__attribute__((weak)) int noasusddns(void)
{
	return 0;
}

__attribute__((weak)) int detect_vul_scan(void)
{
	return 0;
}

__attribute__((weak)) void check_wlx_nband_type(void)
{
	char wl_ifnames[64] = {0};
	char word[32], *next = NULL;
	int unit = 0;
	int fiveg_idx = 0;
	int sixg_idx = 0;

	strlcpy(wl_ifnames, nvram_safe_get("wl_ifnames"), sizeof(wl_ifnames));
	foreach (word, wl_ifnames, next) {
		char nv[32];
		int nband;
		int nband_type;

		snprintf(nv, sizeof(nv), "wl%d_nband", unit);
		nband = nvram_get_int(nv);

		switch (nband) {
		case 2: /* 2.4G */
			nband_type = 0;
			break;
		case 1: /* 5G */
			nband_type = (fiveg_idx++ == 0) ? 1 : 3;
			break;
		case 4: /* 6G */
			nband_type = (sixg_idx++ == 0) ? 4 : 6;
			break;
		default:
			nband_type = nband;
			break;
		}

		snprintf(nv, sizeof(nv), "wl%d_nband_type", unit);
		nvram_set_int(nv, nband_type);
		unit++;
	}
}

__attribute__((weak)) char *get_s46_prefix_host(const char *prefix, char *host, size_t hostlen)
{
	if (host == NULL || hostlen == 0)
		return NULL;

	if (prefix == NULL)
		host[0] = '\0';
	else {
		strncpy(host, prefix, hostlen - 1);
		host[hostlen - 1] = '\0';
	}
	return host;
}

__attribute__((weak)) int is_mxl_dual_serdes_war(void)
{
	return 0;
}

__attribute__((weak)) int amas_set_lldpd_bind_ifnames_retry(char *ifnames, int fail_retry)
{
	(void)ifnames;
	(void)fail_retry;
	return 0;
}

__attribute__((weak)) void update_nbr_list(void)
{
}

__attribute__((weak)) void memleakdbg(void)
{
}

__attribute__((weak)) int validate_apply_input_value(char *name, char *value)
{
	(void)name;
	(void)value;
	return 1;
}

__attribute__((weak)) int wl_cap_mbss(int unit)
{
	(void)unit;
	return 0;
}

__attribute__((weak)) void wl_set_nbr_info(void)
{
}

__attribute__((weak)) int b64_decode(const char *str, unsigned char *space, int size)
{
	static const char alphabet[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
	int val = 0, valb = -8, out = 0;
	const unsigned char *p = (const unsigned char *)str;

	if (str == NULL || space == NULL || size <= 0)
		return -1;

	for (; *p; ++p) {
		const char *d;
		int c;

		if (*p == '=') {
			break;
		}
		d = strchr(alphabet, *p);
		if (d == NULL) {
			continue;
		}
		c = (int)(d - alphabet);
		val = (val << 6) + c;
		valb += 6;
		if (valb >= 0) {
			if (out >= size)
				return -1;
			space[out++] = (unsigned char)((val >> valb) & 0xFF);
			valb -= 8;
		}
	}

	return out;
}
