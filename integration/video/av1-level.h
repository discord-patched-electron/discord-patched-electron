#ifndef DPE_AV1_LEVEL_H
#define DPE_AV1_LEVEL_H
#include <stdint.h>

// AV1 Annex A level limits, Main tier, one spatial/temporal layer. Native
// cros-codecs emits one tile and one frame header per frame. These are the
// picture, display-rate and bitrate constraints used by FFmpeg's level guesser;
// this does not certify compression-ratio/instantaneous bitstream conformance.
struct dpe_av1_level_limit {
  uint8_t index;
  uint32_t pixels, width, height;
  uint64_t samples_per_second, bitrate;
};
static const struct dpe_av1_level_limit dpe_av1_levels[] = {
    {0, 147456, 2048, 1152, 4423680, 1500000},
    {1, 278784, 2816, 1584, 8363520, 3000000},
    {4, 665856, 4352, 2448, 19975680, 6000000},
    {5, 1065024, 5504, 3096, 31950720, 10000000},
    {8, 2359296, 6144, 3456, 70778880, 12000000},
    {9, 2359296, 6144, 3456, 141557760, 20000000},
    {12, 8912896, 8192, 4352, 267386880, 30000000},
    {13, 8912896, 8192, 4352, 534773760, 40000000},
    {14, 8912896, 8192, 4352, 1069547520, 60000000},
    {16, 35651584, 16384, 8704, 1069547520, 60000000},
    {17, 35651584, 16384, 8704, 2139095040, 100000000},
    {18, 35651584, 16384, 8704, 4278190080, 160000000},
};

static uint8_t dpe_av1_level(uint32_t width, uint32_t height, uint32_t fps, uint64_t bitrate) {
  // 31 is the spec's explicitly unconstrained level. Use it for malformed or
  // unbounded input rather than incorrectly claiming a constrained level.
  if (!width || !height || !fps || width > 65536 || height > 65536 || fps > 1000 || !bitrate)
    return 31;
  uint64_t pixels = (uint64_t)width * height;
  for (unsigned i = 0; i < sizeof(dpe_av1_levels) / sizeof(*dpe_av1_levels); i++) {
    const struct dpe_av1_level_limit *level = &dpe_av1_levels[i];
    if (pixels <= level->pixels && width <= level->width && height <= level->height &&
        pixels * fps <= level->samples_per_second && bitrate <= level->bitrate &&
        fps <= (level->index < 8 ? 150u : 300u))
      return level->index;
  }
  return 31;
}
#endif
