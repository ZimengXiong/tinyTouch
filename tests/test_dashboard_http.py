"""Execute the real HTTP request guards with a fake ESP HTTP transport."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / 'firmware/tiny_touch_unified/main'


@unittest.skipUnless(shutil.which('cc'), 'host C compiler unavailable')
class DashboardHttpTests(unittest.TestCase):
    def test_cross_origin_requests_and_unrelated_sessions_are_rejected(self):
        source = (MAIN / 'web_dashboard.c').read_text()
        guards = source[source.index('static bool host_ok('):source.index('static void enrollment_prompt(')]
        harness = r'''
#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include "dashboard_api.h"
#define ESP_OK 0
#define ESP_FAIL -1
#define HTTP_GET 0
#define HTTP_POST 1
#define HTTPD_RESP_USE_STRLEN -1
typedef int esp_err_t;
typedef struct {
  int method, content_len;
  const char *host, *origin, *type, *cookie, *body;
} httpd_req_t;
static int64_t now_us(void) { return 1000; }
static const char *header(httpd_req_t *r, const char *name) {
  if (!strcmp(name,"Host")) return r->host;
  if (!strcmp(name,"Origin")) return r->origin;
  if (!strcmp(name,"Content-Type")) return r->type;
  return r->cookie;
}
static size_t httpd_req_get_hdr_value_len(httpd_req_t *r, const char *name) {
  const char *value=header(r,name); return value ? strlen(value) : 0;
}
static int httpd_req_get_hdr_value_str(httpd_req_t *r, const char *name, char *out, size_t cap) {
  const char *value=header(r,name);
  if (!value || strlen(value)>=cap) return ESP_FAIL;
  strcpy(out,value); return ESP_OK;
}
static void httpd_resp_set_type(httpd_req_t *r, const char *s) { (void)r; (void)s; }
static void httpd_resp_set_hdr(httpd_req_t *r, const char *k, const char *v) { (void)r;(void)k;(void)v; }
static void httpd_resp_send(httpd_req_t *r, const char *s, int n) { (void)r;(void)s;(void)n; }
static void httpd_resp_set_status(httpd_req_t *r, const char *s) { (void)r;(void)s; }
static int httpd_req_recv(httpd_req_t *r, char *out, int n) { memcpy(out,r->body,n);return n; }
static void random_bytes(void *out,size_t n) { memset(out,0xaa,n); }
'''
        checks = r'''
int main(void) {
  httpd_req_t r = {.method=HTTP_POST,.host="192.168.7.1",.type="application/json"};
  assert(require_host(&r));
  assert(!require_unlock(&r));
  r.origin="https://attacker.example"; assert(!require_host(&r));
  r.origin="null"; assert(!require_host(&r));
  r.origin="http://192.168.7.1"; assert(require_host(&r));
  r.type="text/plain"; assert(!require_host(&r));
  r.type="application/json"; r.host="attacker.example"; assert(!require_host(&r));
  r.host="192.168.7.1";
  char token[DASHBOARD_SESSION_HEX], cookie[100];
  dashboard_session_grant(token,sizeof(token),1000,random_bytes);
  assert(!session_ok(&r));
  snprintf(cookie,sizeof(cookie),"other_tt_session=%s",token);r.cookie=cookie;
  assert(!session_ok(&r));
  snprintf(cookie,sizeof(cookie),"other=x; tt_session=%s",token);
  assert(require_unlock(&r));
  strcat(cookie,"a");assert(!session_ok(&r));
  dashboard_session_clear();assert(!require_unlock(&r));
  char body[8];r.body="{}";r.content_len=2;assert(read_body(&r,body,sizeof(body))==ESP_OK);
  r.body="{}\0x";r.content_len=4;assert(read_body(&r,body,sizeof(body))==ESP_FAIL);
  r.content_len=8;assert(read_body(&r,body,sizeof(body))==ESP_FAIL);
  return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            c = Path(directory) / 'test.c'
            exe = Path(directory) / 'test'
            c.write_text(harness + guards + checks)
            subprocess.run(['cc','-std=c11','-Wall','-Werror',f'-I{MAIN}',str(c),str(MAIN / 'dashboard_api.c'),'-o',str(exe)],check=True)
            subprocess.run([str(exe)],check=True)
