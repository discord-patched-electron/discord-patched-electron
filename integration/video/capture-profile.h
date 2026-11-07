// SPDX-License-Identifier: MIT
#ifndef DPE_CAPTURE_PROFILE_H
#define DPE_CAPTURE_PROFILE_H
#include <stdbool.h>
#include <stdint.h>
// IDs shared with the Rust workers. Each scope records inclusive wall/thread CPU
// time only when DPE_CAPTURE_PROFILE=1. Counters never contain source identities.
enum dpe_profile_id {
  DPE_PROFILE_RECEIVE,
  DPE_PROFILE_DEVICE,
  DPE_PROFILE_ACQUIRE,
  DPE_PROFILE_CONVERT,
  DPE_PROFILE_ACK,
  DPE_PROFILE_DELIVER,
  DPE_PROFILE_IMPORT,
  DPE_PROFILE_OUTPUT,
  DPE_PROFILE_VPP_SUBMIT,
  DPE_PROFILE_VPP_WAIT,
  DPE_PROFILE_ENCODER_IMPORT,
  DPE_PROFILE_ENCODER_PADDING,
  DPE_PROFILE_ENCODER_SEND,
  DPE_PROFILE_ENCODER_DRAIN,
  DPE_PROFILE_ENCODED_CALLBACK,
  DPE_PROFILE_KMS_REQUEST,
  DPE_PROFILE_KMS_METADATA,
  DPE_PROFILE_KMS_SEND,
  DPE_PROFILE_GAME_RECEIVE,
  DPE_PROFILE_GAME_SEND,
  DPE_PROFILE_GAME_CONTROL,
  DPE_PROFILE_PREVIEW_IMPORT,
  DPE_PROFILE_PREVIEW_SUBMIT,
  DPE_PROFILE_PREVIEW_WAIT,
  DPE_PROFILE_PREVIEW_READ,
  DPE_PROFILE_PREVIEW_DELIVER,
  DPE_PROFILE_CPU_READ,
  DPE_PROFILE_KMS_EXPORT
};
struct dpe_profile_stamp {
  uint64_t wall, cpu;
};
struct dpe_profile_scope {
  uint32_t stage;
  struct dpe_profile_stamp start;
};
struct dpe_profile_stamp dpe_profile_begin(uint32_t);
void dpe_profile_end(uint32_t, struct dpe_profile_stamp);
bool dpe_profile_stage(uint32_t, uint64_t[4]);
static inline void dpe_profile_finish(struct dpe_profile_scope *scope) {
  dpe_profile_end(scope->stage, scope->start);
}
#define DPE_PROFILE(name, id)                                                                      \
  struct dpe_profile_scope name                                                                    \
      __attribute__((cleanup(dpe_profile_finish))) = {id, dpe_profile_begin(id)}
#endif
