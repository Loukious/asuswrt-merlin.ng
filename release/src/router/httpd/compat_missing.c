#include <stdio.h>
#include <stdarg.h>

#include "httpd.h"

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
	(void)ASUS_NEW_EULA;
	(void)from_service;
	return 0;
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
	(void)url;
	(void)stream;
}

__attribute__((weak)) void do_get_ASUS_privacy_policy_cgi(char *url, FILE *stream)
{
	(void)url;
	(void)stream;
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
