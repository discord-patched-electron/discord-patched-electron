#define _GNU_SOURCE
#include "../../integration/video/av1-crop.h"
#include "../../integration/video/av1-software-crop.h"
#include <dlfcn.h>
#define SNAPSHOTS 128
#define CONTEXTS 128
static void (*query_stats)(uint64_t *);
static uint64_t count(unsigned index) {
  uint64_t values[6];
  query_stats(values);
  return values[index];
}
extern int opened(void **, const void *);
extern int sent(void *, struct dpe_dav1d_data *);
extern int received(void *, struct dpe_dav1d_picture *);
extern void closed(void **), closed_extra1(void **), closed_extra2(void **);

#include <assert.h>
#include <errno.h>
#include <stdio.h>

static uintptr_t next_context = 1;
static int send_error;
static int64_t input_ticket, output_ticket;
static uint8_t pixels[192 * 160 * 3];
__attribute__((used, noinline)) static int mock_open(void **context, const void *settings) {
  assert(settings == (void *)42);
  *context = (void *)next_context++;
  return 0;
}
__attribute__((used, noinline)) static void mock_close(void **context) { *context = NULL; }
__attribute__((used, noinline)) static int mock_send(void *context, struct dpe_dav1d_data *data) {
  assert(context && data->timestamp > 0);
  input_ticket = data->timestamp;
  if (send_error)
    return -send_error;
  memset(data, 0, sizeof(*data));
  return 0;
}
__attribute__((used, noinline)) static int mock_get(void *context,
                                                    struct dpe_dav1d_picture *picture) {
  assert(context);
  *picture =
      (struct dpe_dav1d_picture){.sequence = (void *)123,
                                 .frame = (void *)456,
                                 .plane = {pixels, pixels + 192 * 160, pixels + 192 * 160 * 2},
                                 .stride = {192, 192},
                                 .width = 192,
                                 .height = 160,
                                 .layout = 1,
                                 .depth = 8,
                                 .timestamp = output_ticket};
  return 0;
}
static size_t packet(uint8_t *bytes, struct dpe_av1_crop crop, bool keyframe) {
  size_t size = 0;
  if (keyframe) {
    const uint8_t sequence[] = {0x0a, 1, 0};
    memcpy(bytes, sequence, sizeof(sequence));
    size += sizeof(sequence);
  }
  size += dpe_av1_crop_write(bytes + size, crop);
  const uint8_t frame[] = {0x32, 1, keyframe ? 0x10 : 0x30};
  memcpy(bytes + size, frame, sizeof(frame));
  return size + sizeof(frame);
}
static int64_t submit(void *context, struct dpe_av1_crop crop, bool keyframe, int64_t timestamp) {
  uint8_t bytes[64];
  size_t size = packet(bytes, crop, keyframe);
  struct dpe_dav1d_data data = {.data = bytes, .size = size, .timestamp = timestamp};
  int result = sent(context, &data);
  assert(result == (send_error ? -send_error : 0));
  if (send_error) {
    assert(data.data == bytes && data.size == size && data.timestamp == timestamp);
  } else {
    assert(!data.data);
  }
  return input_ticket;
}
static void output(void *context, int64_t ticket, int64_t timestamp, bool cropped) {
  struct dpe_dav1d_picture picture;
  output_ticket = ticket;
  assert(!received(context, &picture));
  assert(picture.timestamp == timestamp);
  assert(picture.sequence == (void *)123 && picture.frame == (void *)456);
  assert(picture.stride[0] == 192 && picture.stride[1] == 192);
  assert(picture.width == (cropped ? 128 : 192));
  assert(picture.height == (cropped ? 128 : 160));
  assert(picture.plane[0] == pixels + (cropped ? 16 * 192 + 32 : 0));
  assert(picture.plane[1] == pixels + 192 * 160 + (cropped ? 8 * 192 + 16 : 0));
  for (size_t i = 0; i < sizeof(pixels); i++)
    assert(pixels[i] == 0x5a); // Views move; no pixel is read, copied or changed.
}
void fixture_run(void (*stats)(uint64_t *)) {
  query_stats = stats;
  memset(pixels, 0x5a, sizeof(pixels));
  void *a, *b;
  assert(!opened(&a, (void *)42) && !opened(&b, (void *)42));
  const struct dpe_av1_crop crop = {32, 16, 128, 128}, none = {0};
  int64_t first = submit(a, crop, true, 1234);
  int64_t second = submit(a, none, false, 5678);
  // A later keyframe clears active crop, but must not affect delayed pictures.
  int64_t third = submit(a, none, true, -1);
  output(a, second, 5678, true);
  output(a, second, 5678, true); // Multiple outputs may share one input metadata ticket.
  output(a, first, 1234, true);
  output(a, third, -1, false);
  output(b, submit(b, none, true, 1234), 1234, false);
  assert(count(3) == 3);
  // EAGAIN does not commit metadata or leak the internal timestamp to a retry.
  send_error = EAGAIN;
  submit(a, crop, true, 999);
  send_error = 0;
  output(a, submit(a, none, false, 999), 999, false);
  struct dpe_dav1d_picture picture;
  // Missing/evicted snapshots retain decoded geometry, never a later crop.
  first = submit(a, crop, true, 12);
  for (unsigned i = 0; i < SNAPSHOTS; i++)
    submit(a, none, false, i);
  output_ticket = first;
  assert(!received(a, &picture) && picture.width == 192 && picture.height == 160);
  assert(count(5) == 1);
  uintptr_t reused = (uintptr_t)a;
  closed(&a);
  assert(!a && count(1) == 1);
  next_context = reused;
  assert(!opened(&a, (void *)42));
  output(a, submit(a, none, false, 1234), 1234, false);
  closed(&a);
  closed(&b);
  assert(count(1) == 0);
  next_context = 100;
  void *contexts[CONTEXTS + 1];
  for (unsigned i = 0; i < CONTEXTS + 1; i++)
    assert(!opened(&contexts[i], (void *)42));
  assert(count(1) == CONTEXTS);
  for (unsigned i = 0; i < CONTEXTS + 1; i++)
    (i % 3 == 0 ? closed : i % 3 == 1 ? closed_extra1 : closed_extra2)(&contexts[i]);
  assert(count(1) == 0);
  puts("Software AV1 crop views, reordering, retry, bounds, isolation and teardown passed");
}

extern const uint8_t open_site[], send_site[], get_site[], close_site[], close_extra1_site[],
    close_extra2_site[];
void fixture_scope(uint32_t offsets[10]) {
  Dl_info module;
  assert(dladdr((void *)open_site, &module));
  const void *addresses[] = {mock_open,         mock_send,        mock_get, mock_close,
                             open_site,         send_site,        get_site, close_site,
                             close_extra1_site, close_extra2_site};
  for (unsigned i = 0; i < 10; i++) {
    uintptr_t offset = (uintptr_t)addresses[i] - (uintptr_t)module.dli_fbase;
    assert(offset && offset <= UINT32_MAX);
    offsets[i] = (uint32_t)offset;
  }
}
__asm__(".text\n.balign 4096\n"
        ".global opened\nopened:\nsub $8,%rsp\n.global open_site\nopen_site:\ncall mock_open\nadd "
        "$8,%rsp\nret\n"
        ".global sent\nsent:\nsub $8,%rsp\n.global send_site\nsend_site:\ncall mock_send\nadd "
        "$8,%rsp\nret\n"
        ".global received\nreceived:\nsub $8,%rsp\n.global get_site\nget_site:\ncall mock_get\nadd "
        "$8,%rsp\nret\n"
        ".global closed\nclosed:\n.global close_site\nclose_site:\njmp mock_close\n"
        ".global closed_extra1\nclosed_extra1:\nsub $8,%rsp\n.global "
        "close_extra1_site\nclose_extra1_site:\ncall mock_close\nadd $8,%rsp\nret\n"
        ".global closed_extra2\nclosed_extra2:\nsub $8,%rsp\n.global "
        "close_extra2_site\nclose_extra2_site:\ncall mock_close\nadd $8,%rsp\nret\n"
        ".balign 4096\n");
