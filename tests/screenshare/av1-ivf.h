// Preserve complete AV1 encoder packets without a raw-OBU demuxer's rewrite.
// Only a generated-fixture container; encoded bytes are written unchanged.
#ifndef DPE_TEST_AV1_IVF_H
#define DPE_TEST_AV1_IVF_H
#include <assert.h>
#include <stdint.h>
#include <stdio.h>

static void ivf_le(FILE *file, uint64_t value, unsigned bytes) {
  for (unsigned i = 0; i < bytes; i++)
    assert(fputc((value >> (8 * i)) & 255, file) != EOF);
}
static void ivf_header(FILE *file, unsigned width, unsigned height) {
  assert(fwrite("DKIF", 1, 4, file) == 4);
  ivf_le(file, 0, 2);
  ivf_le(file, 32, 2);
  assert(fwrite("AV01", 1, 4, file) == 4);
  ivf_le(file, width, 2);
  ivf_le(file, height, 2);
  ivf_le(file, 60, 4);
  ivf_le(file, 1, 4);
  ivf_le(file, 0, 8); // Unknown frame count, then unused bytes.
}
static void ivf_packet(FILE *file, const uint8_t *data, size_t size, uint64_t timestamp) {
  assert(size <= UINT32_MAX);
  ivf_le(file, size, 4);
  ivf_le(file, timestamp, 8);
  assert(fwrite(data, 1, size, file) == size);
}
#endif
