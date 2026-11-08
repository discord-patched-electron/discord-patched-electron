#include "../../integration/video/av1-crop.h"
#include <assert.h>
#include <stdio.h>

static void equal(struct dpe_av1_crop a, struct dpe_av1_crop b) {
  assert(a.x == b.x && a.y == b.y && a.width == b.width && a.height == b.height);
}
static size_t key(uint8_t *out, struct dpe_av1_crop crop) {
  const uint8_t sequence[] = {0x12, 0, 0x0a, 1, 0};
  memcpy(out, sequence, sizeof(sequence));
  size_t size = sizeof(sequence);
  size += dpe_av1_crop_write(out + size, crop);
  const uint8_t frame[] = {0x32, 1, 0x10};
  memcpy(out + size, frame, sizeof(frame));
  return size + sizeof(frame);
}
int main(void) {
  const struct dpe_av1_crop cases[] = {
      {0, 4, 1920, 1080}, {0, 4, 1918, 1078}, {20, 0, 1366, 768}, {16, 6, 1440, 900}};
  const uint8_t inter[] = {0x32, 1, 0x30}, next_key[] = {0x32, 1, 0x10};
  for (size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); i++) {
    uint8_t packet[64];
    size_t size = key(packet, cases[i]), position;
    struct dpe_av1_crop_state state = {0};
    assert(dpe_av1_crop_position(packet, size, &position) && position == size - 3);
    equal(dpe_av1_crop_read(packet, size, &state), cases[i]);
    equal(dpe_av1_crop_read(inter, sizeof(inter), &state), cases[i]);
    assert(dpe_av1_crop_valid(state.active, 8192, 8192));
    assert(!dpe_av1_crop_read(next_key, sizeof(next_key), &state).width);
    // A metadata-less new keyframe must never inherit a prior stream's crop.
    assert(!dpe_av1_crop_read(inter, sizeof(inter), &state).width);
    for (size_t n = 0; n < size; n++) {
      memset(&state, 0, sizeof(state));
      assert(!dpe_av1_crop_read(packet, n, &state).width);
    }
    // Metadata may arrive before the frame in a separate input buffer.
    memset(&state, 0, sizeof(state));
    assert(!dpe_av1_crop_read(packet, size - 3, &state).width);
    equal(dpe_av1_crop_read(next_key, sizeof(next_key), &state), cases[i]);
    // The header is valid only at even chroma coordinates and in frame bounds.
    assert(!dpe_av1_crop_valid(cases[i], cases[i].width, cases[i].height - 2));
    packet[16] ^= 1; // corrupt required trailing bits in the crop OBU
    memset(&state, 0, sizeof(state));
    assert(!dpe_av1_crop_read(packet, size, &state).width);
  }
  assert(!dpe_av1_crop_valid((struct dpe_av1_crop){0, 0, 0, 1080}, 1920, 1088));
  assert(!dpe_av1_crop_valid((struct dpe_av1_crop){0, 3, 1920, 1080}, 1920, 1088));
  assert(!dpe_av1_crop_valid((struct dpe_av1_crop){UINT32_MAX, 0, 2, 2}, 1920, 1088));
  uint8_t bytes[128];
  uint32_t random = 42;
  for (unsigned i = 0; i < 10000; i++) {
    for (size_t j = 0; j < sizeof(bytes); j++) {
      random = random * 1664525u + 1013904223u;
      bytes[j] = random >> 24;
    }
    struct dpe_av1_crop_state state = {0};
    struct dpe_av1_crop crop = dpe_av1_crop_read(bytes, i % sizeof(bytes), &state);
    assert(!crop.width || dpe_av1_crop_valid(crop, 65536, 65536));
  }
  puts("AV1 crop bounds, scope, resets, truncation and randomized inputs passed");
  return 0;
}
