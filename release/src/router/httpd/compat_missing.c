#include <stdio.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>

#include <bcmnvram.h>
#include <shared.h>
#include "httpd.h"

#define ASUS_PRIVACY_POLICY_CURRENT_VERSION 5

extern int do_json_decode(struct json_object *root);
extern char *safe_get_cgi_json(char *name, json_object *root);
extern void httpd_nvram_commit(void);

static void set_policy_time(const char *name)
{
	time_t now = time(NULL);
	char timebuf[100] = {0};

	rfctime(&now, timebuf, sizeof(timebuf));
	nvram_set(name, timebuf);
}

static int policy_was_read(const char *read_name, const char *time_name)
{
	return nvram_match(read_name, "1") || *nvram_safe_get(time_name) != '\0';
}

static void add_json_string(struct json_object *root, const char *name, const char *value)
{
	json_object_object_add(root, name, json_object_new_string(value ? value : ""));
}

static void add_json_int(struct json_object *root, const char *name, int value)
{
	json_object_object_add(root, name, json_object_new_int(value));
}

static int normalize_policy_version(const char *value)
{
	char *end = NULL;
	long version;

	if (value == NULL || *value == '\0')
		return -1;

	version = strtol(value, &end, 10);
	if (*end != '\0' || version < 0)
		return -1;
	if (version == 0)
		return 0;
	if (version == 1 || version > ASUS_PRIVACY_POLICY_CURRENT_VERSION)
		return ASUS_PRIVACY_POLICY_CURRENT_VERSION;

	return (int)version;
}

__attribute__((weak)) int webswrite(webs_t wp, const char *fmt, ...)
{
	int ret;
	va_list ap;

	va_start(ap, fmt);
	ret = vfprintf(wp, fmt, ap);
	va_end(ap);
	fflush(wp);
	return ret;
}

__attribute__((weak)) int nvram_modify_log(char *name, char *newv, char *oldv, struct json_object *nvram_modify_obj)
{
	(void)name;
	(void)newv;
	(void)oldv;
	(void)nvram_modify_obj;
	return 0;
}

__attribute__((weak)) void handle_nvram_modify_log(struct json_object *nvram_modify_obj)
{
	(void)nvram_modify_obj;
}

__attribute__((weak)) int set_ASUS_NEW_EULA(char *ASUS_NEW_EULA, char *from_service)
{
	time_t now;
	char timestamp[32] = {0};

	if (ASUS_NEW_EULA == NULL ||
	    (strcmp(ASUS_NEW_EULA, "0") != 0 && strcmp(ASUS_NEW_EULA, "1") != 0))
		return 4006;

	nvram_set("ASUS_NEW_EULA", ASUS_NEW_EULA);
	set_policy_time("ASUS_NEW_EULA_time");
	nvram_set("ASUS_NEW_EULA_read", "1");

	now = time(NULL);
	snprintf(timestamp, sizeof(timestamp), "%lu", (unsigned long)now);
	nvram_set("ASUS_NEW_EULA_ts", timestamp);
	if (from_service != NULL && strlen(from_service) < 200)
		nvram_set("ASUS_NEW_EULA_from", from_service);

	httpd_nvram_commit();
	return 200;
}

__attribute__((weak)) void bwdpi_cgi_mon_del_db(char *type, FILE *stream)
{
	(void)type;
	(void)stream;
}

__attribute__((weak)) void bwdpi_cgi_mon_to_json(char *type, char *start, char *end, FILE *stream)
{
	(void)type;
	(void)start;
	(void)end;
	(void)stream;
}

__attribute__((weak)) int ej_wps_info_5g(int eid, webs_t wp, int argc, char_t **argv)
{
	(void)eid;
	(void)argc;
	(void)argv;
	return websWrite(wp, "");
}

__attribute__((weak)) int ej_wps_info_5g_2(int eid, webs_t wp, int argc, char_t **argv)
{
	(void)eid;
	(void)argc;
	(void)argv;
	return websWrite(wp, "");
}

__attribute__((weak)) int ej_wl_chanspecs_6g_2(int eid, webs_t wp, int argc, char_t **argv)
{
	(void)eid;
	(void)argc;
	(void)argv;
	return websWrite(wp, "[]");
}

__attribute__((weak)) void do_set_ASUS_privacy_policy_cgi(char *url, FILE *stream)
{
	struct json_object *root = json_object_new_object();
	char version[16] = {0};
	char *value;
	int normalized;
	int status = 4003;

	(void)url;
	if (root == NULL) {
		websWrite(stream, "{\"statusCode\":\"%d\"}", status);
		return;
	}

	do_json_decode(root);
	value = safe_get_cgi_json("ASUS_privacy_policy", root);
	normalized = normalize_policy_version(value);
	if (normalized >= 0) {
		snprintf(version, sizeof(version), "%d", normalized);
		nvram_set("ASUS_privacy_policy", version);
		set_policy_time("ASUS_privacy_policy_time");
		nvram_set("ASUS_privacy_policy_read", "1");
		httpd_nvram_commit();
		status = 200;
	}

	json_object_put(root);
	websWrite(stream, "{\"statusCode\":\"%d\"}", status);
}

__attribute__((weak)) void do_get_ASUS_privacy_policy_cgi(char *url, FILE *stream)
{
	struct json_object *root = json_object_new_object();
	const char *eula;
	const char *privacy_policy;
	const char *ai_board_eula;
	int ai_board_read;

	(void)url;
	if (root == NULL) {
		websWrite(stream, "{}");
		return;
	}

	eula = nvram_safe_get("ASUS_NEW_EULA");
	privacy_policy = nvram_safe_get("ASUS_privacy_policy");
	add_json_string(root, "ASUS_NEW_EULA", *eula ? eula : "0");
	add_json_int(root, "ASUS_NEW_EULA_read",
		policy_was_read("ASUS_NEW_EULA_read", "ASUS_NEW_EULA_time"));
	add_json_int(root, "ASUS_NEW_EULA_allow_skip", 0);
	add_json_int(root, "ASUS_NEW_EULA_force_sign", 1);

	add_json_string(root, "ASUS_privacy_policy", privacy_policy);
	add_json_string(root, "ASUS_privacy_policy_time",
		nvram_safe_get("ASUS_privacy_policy_time"));
	add_json_int(root, "ASUS_privacy_policy_read",
		policy_was_read("ASUS_privacy_policy_read", "ASUS_privacy_policy_time"));
	add_json_int(root, "ASUS_privacy_policy_force_sign", 1);

#ifdef RTCONFIG_AI_SERVICE
	ai_board_eula = nvram_safe_get("AI_board_EULA");
	ai_board_read = policy_was_read("AI_board_EULA_read", "AI_board_EULA_time");
#else
	ai_board_eula = "";
	ai_board_read = 1;
#endif
	add_json_string(root, "AI_board_EULA", ai_board_eula);
	add_json_int(root, "AI_board_EULA_read", ai_board_read);
	add_json_int(root, "AI_board_EULA_allow_skip", 0);
	add_json_int(root, "AI_board_EULA_force_sign", 0);

	websWrite(stream, "%s", json_object_to_json_string(root));
	json_object_put(root);
}

__attribute__((weak)) void do_oauth_callback_cgi(char *url, FILE *stream)
{
	(void)url;
	(void)stream;
}

__attribute__((weak)) void do_set_security_update_cgi(char *url, FILE *stream)
{
	(void)url;
	(void)stream;
}

__attribute__((weak)) void do_get_security_update_cgi(char *url, FILE *stream)
{
	(void)url;
	(void)stream;
}
