#include "shared.h"
#include <string.h>

#define ASUS_PRIVACY_POLICY_CURRENT_VERSION 5

__attribute__((weak)) int get_ASUS_privacy_policy_state(const int id)
{
	(void)id;
	return nvram_get_int("ASUS_privacy_policy") > 0;
}

__attribute__((weak)) int get_ASUS_privacy_policy(void)
{
	return ASUS_PRIVACY_POLICY_CURRENT_VERSION;
}

__attribute__((weak)) int get_ASUS_privacy_policy_ver(const int id)
{
	(void)id;
	return ASUS_PRIVACY_POLICY_CURRENT_VERSION;
}

__attribute__((weak)) void init_asus_pp_eula(void)
{
}

__attribute__((weak)) int mod_account(const char *const account, const char *const new_account, const char *const new_password)
{
	(void)account;
	(void)new_account;
	(void)new_password;
	return 0;
}

__attribute__((weak)) int b64_decode(const char *str, unsigned char *space, int size)
{
	static const char alphabet[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
	int val = 0, valb = -8, out = 0;
	const unsigned char *p = (const unsigned char *)str;

	if (str == 0 || space == 0 || size <= 0)
		return -1;

	for (; *p; ++p) {
		const char *d;
		int c;

		if (*p == '=')
			break;
		d = strchr(alphabet, *p);
		if (d == NULL)
			continue;
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
