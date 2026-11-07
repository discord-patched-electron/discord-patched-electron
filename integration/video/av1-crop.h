// AOM AV1 crop metadata proposal #346 (type 32). Unknown metadata is ignored
// by ordinary decoders. This header is also carried in the owned Electron patch.
#ifndef DPE_AV1_CROP_H
#define DPE_AV1_CROP_H
// Shared C ABI parser: each indexed read is guarded by the explicit byte length.
#ifdef UNSAFE_BUFFERS_BUILD
#pragma allow_unsafe_buffers
#endif
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

struct dpe_av1_crop {
  uint32_t x, y, width, height;
};
struct dpe_av1_crop_state {
  struct dpe_av1_crop active;
  struct dpe_av1_crop pending;
  bool sequence_known, reduced_still, pending_valid;
};
struct dpe_av1_obu {
  unsigned type, layer;
  const uint8_t *data;
  size_t size, start;
};

static inline struct dpe_av1_crop dpe_av1_crop_empty(void) {
  struct dpe_av1_crop result = {0, 0, 0, 0};
  return result;
}

static inline bool dpe_av1_leb(const uint8_t *data, size_t size, size_t *at, size_t *value) {
  *value = 0;
  for (unsigned shift = 0; shift < 56 && shift < sizeof(size_t) * 8; shift += 7) {
    if (*at >= size)
      return false;
    unsigned byte = data[(*at)++];
    if ((size_t)(byte & 127) > (SIZE_MAX >> shift))
      return false;
    *value |= (size_t)(byte & 127) << shift;
    if (!(byte & 128))
      return true;
  }
  return false;
}
static inline bool dpe_av1_obu_next(const uint8_t *data, size_t size, size_t *at,
                                    struct dpe_av1_obu *obu) {
  if (*at >= size)
    return false;
  obu->start = *at;
  unsigned header = data[(*at)++];
  if (header & 0x81)
    return false;
  obu->type = (header >> 3) & 15;
  obu->layer = 0;
  if (header & 4) {
    if (*at >= size || (data[*at] & 7))
      return false;
    obu->layer = data[(*at)++];
  }
  size_t length = size - *at;
  if ((header & 2) && !dpe_av1_leb(data, size, at, &length))
    return false;
  if (length > size - *at)
    return false;
  obu->data = data + *at;
  obu->size = length;
  *at += length;
  return true;
}
static inline bool dpe_av1_crop_valid(struct dpe_av1_crop crop, uint32_t width, uint32_t height) {
  return crop.width && crop.height && !((crop.x | crop.y | crop.width | crop.height) & 1) &&
         crop.x <= width && crop.y <= height && crop.width <= width - crop.x &&
         crop.height <= height - crop.y;
}
static inline bool dpe_av1_crop_payload(const uint8_t *data, size_t size,
                                        struct dpe_av1_crop *crop) {
  if (size != 5 && size != 9)
    return false;
  crop->x = crop->y = 0;
  crop->width = ((uint32_t)data[0] << 8 | data[1]) + 1;
  crop->height = ((uint32_t)data[2] << 8 | data[3]) + 1;
  if (data[4] & 128) {
    if (size != 9 || (data[8] & 127) != 64)
      return false;
    crop->x = ((uint32_t)(data[4] & 127) << 9) | ((uint32_t)data[5] << 1) | (data[6] >> 7);
    crop->y = ((uint32_t)(data[6] & 127) << 9) | ((uint32_t)data[7] << 1) | (data[8] >> 7);
  } else if (size != 5 || data[4] != 64) {
    return false;
  }
  return dpe_av1_crop_valid(*crop, 65536, 65536);
}
// Scan bounded frame headers/metadata, never tile pixels. Crop state expires at
// a new sequence/keyframe. A new crop preceding that keyframe then replaces it.
static inline struct dpe_av1_crop dpe_av1_crop_read(const uint8_t *data, size_t size,
                                                    struct dpe_av1_crop_state *state) {
  struct dpe_av1_crop pending = state->pending;
  bool has_pending = state->pending_valid, frame = false;
  size_t at = 0;
  unsigned count = 0;
  while (at < size) {
    struct dpe_av1_obu obu;
    if (++count > 1024 || !dpe_av1_obu_next(data, size, &at, &obu))
      goto invalid;
    if (obu.layer)
      continue;
    if (obu.type == 1) {
      if (!obu.size)
        goto invalid;
      memset(state, 0, sizeof(*state));
      state->sequence_known = true;
      state->reduced_still = (obu.data[0] & 8) != 0;
      has_pending = false;
    } else if (obu.type == 5) {
      size_t offset = 0, type;
      if (!dpe_av1_leb(obu.data, obu.size, &offset, &type))
        goto invalid;
      if (type == 32) {
        if (!dpe_av1_crop_payload(obu.data + offset, obu.size - offset, &pending))
          goto invalid;
        has_pending = true;
      }
    } else if (obu.type == 3 || obu.type == 6) {
      if (!obu.size || frame)
        goto invalid;
      frame = true;
      if (state->reduced_still || (!(obu.data[0] & 128) && !(obu.data[0] & 0x60)))
        memset(&state->active, 0, sizeof(state->active));
      if (has_pending && state->sequence_known)
        state->active = pending;
      has_pending = false;
    }
  }
  state->pending = pending;
  state->pending_valid = has_pending;
  return frame ? state->active : dpe_av1_crop_empty();
invalid:
  memset(state, 0, sizeof(*state));
  return state->active;
}
static inline size_t dpe_av1_crop_write(uint8_t out[12], struct dpe_av1_crop crop) {
  if (!dpe_av1_crop_valid(crop, 65536, 65536) || crop.x > 65535 || crop.y > 65535)
    return 0;
  memset(out, 0, 12);
  out[0] = 0x2a; // OBU_METADATA, size field present.
  out[1] = 10;
  out[2] = 32;
  out[3] = (crop.width - 1) >> 8;
  out[4] = (crop.width - 1) & 255;
  out[5] = (crop.height - 1) >> 8;
  out[6] = (crop.height - 1) & 255;
  out[7] = 128 | (crop.x >> 9);
  out[8] = (crop.x >> 1) & 255;
  out[9] = ((crop.x & 1) << 7) | (crop.y >> 9);
  out[10] = (crop.y >> 1) & 255;
  out[11] = ((crop.y & 1) << 7) | 64; // trailing_one_bit, then zero padding.
  return 12;
}
static inline bool dpe_av1_crop_position(const uint8_t *data, size_t size, size_t *position) {
  size_t at = 0;
  unsigned count = 0;
  while (at < size) {
    struct dpe_av1_obu obu;
    if (++count > 1024 || !dpe_av1_obu_next(data, size, &at, &obu))
      return false;
    if (obu.type == 3 || obu.type == 6) {
      *position = obu.start;
      return true;
    }
  }
  return false;
}
#endif
