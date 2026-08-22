#include "rc.h"

void tpvpn_gen_hma_list(void)
{
	/* No-op fallback when proprietary TPVPN implementation is unavailable. */
}

#ifdef RTCONFIG_HMA
int hmavpn_main(int argc, char **argv)
{
	(void)argc;
	(void)argv;
	return 0;
}
#endif

#ifdef RTCONFIG_NORDVPN
int nordvpn_main(int argc, char **argv)
{
	(void)argc;
	(void)argv;
	return 0;
}
#endif

#ifdef RTCONFIG_SURFSHARK
int surfshark_main(int argc, char **argv)
{
	(void)argc;
	(void)argv;
	return 0;
}
#endif
