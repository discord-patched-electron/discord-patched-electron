#ifndef DPE_HEVC_FIXTURE_H
#define DPE_HEVC_FIXTURE_H
#include "../../integration/video/encoder-abi.h"
#include <pthread.h>
#include <stdatomic.h>
struct fixture_context {
  atomic_uint created, destroyed, submitted, leases;
  bool fail_import, update_entered, finish_update, update_active, reenter;
  atomic_bool destroy_started, destroy_finished;
  void *wrapper;
  const uint8_t *pixels;
  pthread_mutex_t mutex;
  pthread_cond_t condition;
};
#endif
