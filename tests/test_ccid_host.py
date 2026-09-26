"""Execute the firmware's CCID message handler with a simulated USB transport."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("cc"), "host C compiler unavailable")
class CcidHostTests(unittest.TestCase):
    def test_hid_is_an_empty_reader_and_piv_has_a_valid_atr(self):
        source = (ROOT / "firmware/tiny_touch_unified/main/usb_ccid.c").read_text()
        handler = source[source.index("static void handle_message("):source.index("static void ccid_init(")]
        harness = r'''
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include <string.h>
#include <assert.h>
#define DEVICE_MODE_PIV 0
#define ESP_LOGW(...) ((void)0)
static uint8_t rx_buf[2048], tx_buf[2048];
static int mode = 1, calls;
static uint8_t reply_type, reply_status, reply_error, payload[32];
static size_t payload_len;
static int device_config_mode(void) { return mode; }
static uint32_t le32(const uint8_t *p) { return p[0] | p[1]<<8 | p[2]<<16 | p[3]<<24; }
static bool send_ccid(uint8_t type, uint8_t slot, uint8_t seq, uint8_t status,
                      uint8_t error, const uint8_t *data, size_t len) {
  (void)slot; (void)seq;
  reply_type=type; reply_status=status; reply_error=error; payload_len=len;
  if (len) memcpy(payload, data, len);
  return true;
}
static bool send_parameters(uint8_t slot, uint8_t seq) {
  return send_ccid(0x82, slot, seq, 0, 0, NULL, 0);
}
static bool apdu(const uint8_t *in, size_t len, uint8_t *out, size_t *out_len, size_t cap) {
  (void)in; (void)len; (void)cap; calls++; out[0]=0x90; out[1]=0; *out_len=2; return true;
}
static bool (*apdu_handler)(const uint8_t *, size_t, uint8_t *, size_t *, size_t) = apdu;
'''
        checks = r'''
int main(void) {
  uint8_t msg[10] = {0x62};
  handle_message(msg, sizeof(msg));
  assert(reply_type==0x80 && reply_status==0x42 && reply_error==0xfe && payload_len==0);
  msg[0]=0x65; handle_message(msg, sizeof(msg));
  assert(reply_type==0x81 && reply_status==2 && payload_len==0);
  msg[0]=0x6f; handle_message(msg, sizeof(msg)); assert(calls==0);
  mode=DEVICE_MODE_PIV; msg[0]=0x62; handle_message(msg, sizeof(msg));
  assert(reply_status==0 && payload_len==4 && payload[0]==0x3b);
  assert((payload[1]^payload[2]^payload[3])==0);
  msg[0]=0x6f; handle_message(msg, sizeof(msg)); assert(calls==1);
  return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            c = Path(directory) / "test.c"
            exe = Path(directory) / "test"
            c.write_text(harness + handler + checks)
            subprocess.run(["cc", "-std=c11", "-Wall", "-Werror", str(c), "-o", str(exe)], check=True)
            subprocess.run([str(exe)], check=True)
