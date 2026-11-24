/* Exercise the production boot policy without router/NVRAM dependencies. */
#include <stdio.h>
#include <string.h>

#include "../../release/src/router/rc/ax86u_rxoffload_policy.h"

struct policy_case {
	const char *name;
	const char *productid;
	const char *configured;
	const char *expected;
};

int main(void)
{
	static const struct policy_case cases[] = {
		{ "missing setting", "RT-AX86U", NULL, "0:0" },
		{ "empty setting", "RT-AX86U", "", "0:0" },
		{ "existing workaround", "RT-AX86U", "0:0", NULL },
		{ "custom disabled ring", "RT-AX86U", "0:2048", NULL },
		{ "explicit RX opt-in", "RT-AX86U", "1:0", NULL },
		{ "custom enabled ring", "RT-AX86U", "1:1024", NULL },
		{ "other nonempty override", "RT-AX86U", "custom", NULL },
		{ "AX86S missing", "RT-AX86S", NULL, NULL },
		{ "AX86S empty", "RT-AX86S", "", NULL },
		{ "AX86S explicit", "RT-AX86S", "1:0", NULL },
		{ "AX86U PRO", "RT-AX86U_PRO", NULL, NULL },
		{ "AX88U", "RT-AX88U", NULL, NULL },
		{ "unknown model", NULL, NULL, NULL },
		{ "empty model", "", NULL, NULL },
		{ "model prefix", "RT-AX86", NULL, NULL },
		{ "model suffix", "RT-AX86U-other", NULL, NULL }
	};
	size_t i;
	int failures = 0;

	for (i = 0; i < sizeof(cases) / sizeof(cases[0]); ++i) {
		const struct policy_case *test = &cases[i];
		const char *actual = ax86u_rxoffload_boot_default(test->productid,
			test->configured);
		int matches = (!actual && !test->expected) ||
			(actual && test->expected && !strcmp(actual, test->expected));

		if (!matches) {
			fprintf(stderr, "%s: expected %s, got %s\n", test->name,
				test->expected ? test->expected : "no override",
				actual ? actual : "no override");
			++failures;
		}
	}

	if (failures)
		return 1;

	printf("RT-AX86U RX offload policy: %zu cases passed\n",
		sizeof(cases) / sizeof(cases[0]));
	return 0;
}
