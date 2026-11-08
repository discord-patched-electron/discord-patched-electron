#ifndef DPE_HEVC_LEVEL_H
#define DPE_HEVC_LEVEL_H
#include <stddef.h>
#include <stdint.h>

// HEVC Main tier, Tables A.6/A.7. Unlike FFmpeg's geometry/bitrate-only
// auto-selection, include luma samples per second and session-lifetime maxima.
// Reference: FFmpeg libavcodec/h265_profile_level.c (standard table values).
static inline uint32_t dpe_hevc_level(uint32_t width, uint32_t height, uint32_t fps,
                                      uint64_t bitrate) {
  static const struct {
    uint32_t idc, picture, samples, bitrate;
  } levels[] = {
      {30, 36864, 552960, 128000},
      {60, 122880, 3686400, 1500000},
      {63, 245760, 7372800, 3000000},
      {90, 552960, 16588800, 6000000},
      {93, 983040, 33177600, 10000000},
      {120, 2228224, 66846720, 12000000},
      {123, 2228224, 133693440, 20000000},
      {150, 8912896, 267386880, 25000000},
      {153, 8912896, 534773760, 40000000},
      {156, 8912896, 1069547520, 60000000},
      {180, 35651584, 1069547520, 60000000},
      {183, 35651584, 2139095040, 120000000},
      {186, 35651584, 4278190080u, 240000000},
  };
  uint64_t pixels = (uint64_t)width * height;
  if (!pixels || !fps || pixels > UINT64_MAX / fps)
    return 0;
  for (size_t i = 0; i < sizeof(levels) / sizeof(*levels); i++)
    if (pixels <= levels[i].picture && pixels * fps <= levels[i].samples &&
        (uint64_t)width * width <= (uint64_t)levels[i].picture * 8 &&
        (uint64_t)height * height <= (uint64_t)levels[i].picture * 8 &&
        bitrate <= levels[i].bitrate)
      return levels[i].idc;
  return 0;
}
#endif
