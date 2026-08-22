#include <stddef.h>
#include <stdio.h>

__attribute__((weak)) char *get_fh_if_prefix(char *ret_prefix, size_t ret_bsize)
{
	if (ret_prefix == NULL || ret_bsize == 0)
		return NULL;

	snprintf(ret_prefix, ret_bsize, "wl0");
	return ret_prefix;
}

__attribute__((weak)) char *get_fh_if_prefix_by_unit(int unit, char *ret_prefix, size_t ret_bsize)
{
	if (ret_prefix == NULL || ret_bsize == 0)
		return NULL;

	if (unit < 0)
		unit = 0;
	snprintf(ret_prefix, ret_bsize, "wl%d", unit);
	return ret_prefix;
}
