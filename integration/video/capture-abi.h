// SPDX-License-Identifier: MIT
#ifndef DPE_CAPTURE_ABI_H
#define DPE_CAPTURE_ABI_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
struct pipewire_config {
  int32_t fd;
  uint32_t node;
};
struct picker_result {
  bool success;
  uint8_t padding[3];
  struct pipewire_config config;
  uint32_t padding2;
  const char *error;
};
typedef void (*picker_callback)(const struct picker_result *, const void *);
struct picker_options {
  uint32_t source_type, padding;
  picker_callback callback;
  const void *data;
};
typedef void (*frame_handler_fn)(void *, uint32_t, const char *, void *);
struct capture_options {
  uint32_t source_id, frame_rate, width, height;
  frame_handler_fn frame_handler;
  void *data;
  const struct pipewire_config *config;
};
_Static_assert(sizeof(struct picker_result) == 24 && offsetof(struct picker_result, config) == 4 &&
                   offsetof(struct picker_result, error) == 16,
               "reviewed picker result ABI");
_Static_assert(sizeof(struct picker_options) == 24 &&
                   offsetof(struct picker_options, callback) == 8,
               "reviewed picker options ABI");
_Static_assert(sizeof(struct capture_options) == 40 &&
                   offsetof(struct capture_options, config) == 32,
               "reviewed capture options ABI");
typedef int32_t (*show_fn)(const struct picker_options *);
typedef int32_t (*new_fn)(const struct capture_options *, void **);
typedef int32_t (*destroy_fn)(void *);
typedef int32_t (*update_fn)(void *, const struct capture_options *);
typedef void *(*clone_i420_fn)(void *, const uint8_t **, size_t *, size_t *, size_t *, size_t *,
                               size_t *);
typedef void (*release_frame_fn)(void *);
#endif
