// Reuse the USB host fixture and run its existing visibility cases first.
#define main visibility_test_main
#include "usb_test.c"
#undef main

static void receive_packet(const uint8_t *data, size_t length) {
  assert(out_buffer && length <= out_capacity);
  memcpy(out_buffer, data, length);
  ccid_xfer_cb(0, CCID_EP_OUT, XFER_RESULT_SUCCESS, length);
}

static void start_transport(void) {
  mode = DEVICE_MODE_PIV; preference = true;
  transfer_queued = true; complete_login = false; response_size = 2;
  usb_ccid_start(apdu);
  ccid_reset(0);
  usb_ccid_touch_begin(); update_usb_policy(); check_descriptor(true);
}

int main(void) {
  assert(visibility_test_main() == 0);
  start_transport();

  // Reset abandons the old login reply. An unrelated reply on the new bus
  // must not close the current touch window.
  uint8_t status[10] = {0x65, 0,0,0,0, 0,7,0,0,0};
  usb_ccid_login_complete();
  ccid_reset(0);
  int64_t lease = touch_until;
  handle_message(status, sizeof(status));
  ccid_xfer_cb(0, CCID_EP_IN, XFER_RESULT_SUCCESS, 10);
  assert(touch_until == lease && !login_response_pending);

  // A 64-byte command has no short packet. Receive one USB packet at a time
  // and use the CCID length, so dispatch never waits for a host ZLP.
  start_transport();
  assert(out_capacity == 64);
  uint8_t command[128] = {0x6f, 54,0,0,0, 0,8,0,0,0};
  int old_apdus = apdus;
  receive_packet(command, 64);
  assert(apdus == old_apdus + 1 && in_busy && in_length == 12);
  ccid_xfer_cb(0, CCID_EP_IN, XFER_RESULT_SUCCESS, 12);

  // Multiple full packets form one command; its buffer is immutable until
  // the reply completes, and OUT is not rearmed during that reply.
  command[1] = 118;
  old_apdus = apdus;
  receive_packet(command, 64);
  assert(apdus == old_apdus && !in_busy);
  int old_out = out_requests;
  receive_packet(command + 64, 64);
  assert(apdus == old_apdus + 1 && out_requests == old_out && in_busy);
  ccid_xfer_cb(0, CCID_EP_IN, XFER_RESULT_SUCCESS, 12);

  // A short final packet completes a longer command. Reset and transport
  // errors discard partial frames before accepting another command.
  command[1] = 60;
  receive_packet(command, 64);
  old_apdus = apdus;
  receive_packet(command + 64, 6);
  assert(apdus == old_apdus + 1);
  ccid_xfer_cb(0, CCID_EP_IN, XFER_RESULT_SUCCESS, 12);
  command[1] = 118;
  receive_packet(command, 64);
  ccid_reset(0); check_descriptor(true);
  receive_packet(status, sizeof(status));
  assert(in_busy && tx_buf[0] == 0x81 && tx_buf[6] == 7);
  ccid_xfer_cb(0, CCID_EP_IN, XFER_RESULT_SUCCESS, 10);
  receive_packet(command, 64);
  ccid_xfer_cb(0, CCID_EP_OUT, 1, 0);
  receive_packet(status, sizeof(status));
  assert(in_busy && tx_buf[0] == 0x81);
  ccid_xfer_cb(0, CCID_EP_IN, XFER_RESULT_SUCCESS, 10);

  // A full-size IN reply needs a ZLP. Do not accept another command or hide
  // the token until the host has received that terminator.
  for (size_t length = 64; length <= 128; length += 64) {
    start_transport(); response_size = length - 10; complete_login = true;
    command[1] = 4;
    receive_packet(command, 14);
    assert(login_response_pending && in_length == length);
    old_out = out_requests;
    int old_in = in_requests;
    ccid_xfer_cb(0, CCID_EP_IN, XFER_RESULT_SUCCESS, length);
    assert(in_busy && in_length == 0 && in_requests == old_in + 1);
    assert(login_response_pending && out_requests == old_out);
    ccid_xfer_cb(0, CCID_EP_IN, XFER_RESULT_SUCCESS, 0);
    assert(!in_busy && !login_response_pending && touch_until == 0);
    assert(out_requests == old_out + 1);
    update_usb_policy();
  }

  // Rejected or failed ZLPs cancel early hiding and permit the next command.
  for (int rejected = 0; rejected <= 1; rejected++) {
    start_transport(); response_size = 54; complete_login = true;
    receive_packet(command, 14);
    lease = touch_until;
    transfer_queued = !rejected;
    ccid_xfer_cb(0, CCID_EP_IN, XFER_RESULT_SUCCESS, 64);
    transfer_queued = true;
    if (!rejected) ccid_xfer_cb(0, CCID_EP_IN, 1, 0);
    assert(!in_busy && !login_response_pending && touch_until == lease);
  }

  // Selection is not discoverable until its response is queued to the host.
  start_transport();
  uint8_t select[] = {0x6f, 4,0,0,0, 0,9,0,0,0, 0,0xa4,4,0};
  transfer_queued = false;
  handle_message(select, sizeof(select));
  assert(!piv_selected && !login_response_pending);
  transfer_queued = true;
  handle_message(select, sizeof(select));
  assert(piv_selected && in_busy);
  ccid_xfer_cb(0, CCID_EP_IN, XFER_RESULT_SUCCESS, 12);

  // Maximum-size messages fill the assembly buffer exactly. Oversized and
  // truncated frames produce length errors without entering the APDU handler.
  start_transport();
  uint8_t maximum[CCID_BUF_SIZE] = {0x6f, 0,0,0,0, 0,10,0,0,0};
  put_le32(maximum + 1, sizeof(maximum) - 10);
  old_apdus = apdus;
  for (size_t offset = 0; offset < sizeof(maximum); offset += 64) {
    receive_packet(maximum + offset, 64);
    assert(in_busy == (offset + 64 == sizeof(maximum)));
  }
  assert(apdus == old_apdus + 1);
  ccid_xfer_cb(0, CCID_EP_IN, XFER_RESULT_SUCCESS, 12);
  for (int oversized = 0; oversized <= 1; oversized++) {
    old_apdus = apdus;
    put_le32(maximum + 1, oversized ? UINT32_MAX : 100);
    receive_packet(maximum, 64);
    if (!oversized) receive_packet(maximum + 64, 6);
    assert(apdus == old_apdus && in_busy && tx_buf[7] == 0x42 && tx_buf[8] == 1);
    ccid_xfer_cb(0, CCID_EP_IN, XFER_RESULT_SUCCESS, 10);
    receive_packet(status, sizeof(status));
    assert(in_busy && tx_buf[7] == 0 && tx_buf[6] == 7);
    ccid_xfer_cb(0, CCID_EP_IN, XFER_RESULT_SUCCESS, 10);
  }
  return 0;
}
