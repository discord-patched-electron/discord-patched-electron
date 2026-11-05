// Reviewed x86-64 dav1d public C ABI prefixes. No host libdav1d is linked.
#ifndef DPE_AV1_SOFTWARE_CROP_H
#define DPE_AV1_SOFTWARE_CROP_H
#include <stddef.h>
#include <stdint.h>
struct dpe_dav1d_data {
  const uint8_t *data;
  size_t size;
  void *reference;
  int64_t timestamp;
};
struct dpe_dav1d_picture {
  void *sequence, *frame;
  uint8_t *plane[3];
  ptrdiff_t stride[2];
  int width, height, layout, depth;
  int64_t timestamp;
};
_Static_assert(offsetof(struct dpe_dav1d_data, timestamp) == 24, "Reviewed dav1d input metadata");
_Static_assert(offsetof(struct dpe_dav1d_picture, width) == 56, "Reviewed dav1d picture size");
_Static_assert(offsetof(struct dpe_dav1d_picture, timestamp) == 72,
               "Reviewed dav1d output metadata");
int dpe_software_av1_install(const char *filename, const uint32_t offsets[10]);
void dpe_software_av1_stats(uint64_t result[6]);
#endif
