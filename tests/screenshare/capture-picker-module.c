// Real ELF/Node linkage with the three reviewed private-storage shapes.
// Only synthetic functions are called; no portal, GPU or account is involved.
#define _GNU_SOURCE
#include "../../integration/video/capture-abi.h"
#include <dlfcn.h>
#include <errno.h>
#include <node_api.h>
#include <pthread.h>
#include <stdio.h>
#include <string.h>
static unsigned callbacks;
__attribute__((section(".dpe_show"), visibility("default"))) int32_t
cc_linux_show_picker(const struct picker_options *options) {
  (void)options;
  // Keep the entire reviewed entry range inside this fixture RX segment.
  __asm__ volatile(".rept 32\n nop\n .endr");
  return 17;
}
int32_t cc_linux_capture_new(const struct capture_options *options, void **out) {
  (void)options;
  *out = NULL;
  return -EINVAL;
}
int32_t cc_linux_capture_destroy(void *capture) {
  (void)capture;
  return 0;
}
int32_t cc_linux_capture_update(void *capture, const struct capture_options *options) {
  (void)capture;
  (void)options;
  return 0;
}
void *cc_video_frame_clone_i420(void *frame, const uint8_t **bytes, size_t *length, size_t *y,
                                size_t *uv, size_t *u, size_t *v) {
  (void)frame;
  (void)bytes;
  (void)length;
  (void)y;
  (void)uv;
  (void)u;
  (void)v;
  return NULL;
}
void cc_i420_buffer_destroy(void *frame) { (void)frame; }
void cc_video_frame_destroy(void *frame) { (void)frame; }
struct selection {
  pthread_mutex_t mutex;
  uint8_t padding[40];
  struct pipewire_config config;
  bool present;
};
__attribute__((section(".dpe_selection"), used)) static struct selection selected = {
    .mutex = PTHREAD_MUTEX_INITIALIZER};
_Static_assert(offsetof(struct selection, config) == 0x50 &&
                   offsetof(struct selection, present) == 0x58,
               "fixture layout");
__attribute__((used)) static uintptr_t linkage[] = {
    (uintptr_t)cc_linux_show_picker, (uintptr_t)cc_linux_capture_new,
    (uintptr_t)cc_linux_capture_destroy, (uintptr_t)cc_linux_capture_update};
#ifdef LAZY_PICKER
extern char fixture_lazy_stub[];
__asm__(".text\n.globl fixture_lazy_stub\n.type fixture_lazy_stub,@function\n"
        "fixture_lazy_stub:\n.byte 0xff,0x25\n.long linkage-.-4\n"
        ".byte 0x68\n.long 0\n.byte 0xe9\n.long 0\n");
__attribute__((constructor)) static void lazy(void) {
  linkage[0] = (uintptr_t)fixture_lazy_stub + 6;
}
#endif
static void picked(const struct picker_result *result, const void *data) {
  (void)data;
  if (!result->success)
    callbacks++;
}
static napi_value run(napi_env env, napi_callback_info info) {
  (void)info;
  struct picker_options options = {.source_type = 1, .callback = picked};
  int status = ((show_fn)linkage[0])(&options);
  napi_value value;
  napi_create_int32(env, status, &value);
  return value;
}
static napi_value describe(napi_env env, napi_callback_info info) {
  (void)info;
  Dl_info module;
  dladdr(cc_linux_show_picker, &module);
  uintptr_t base = (uintptr_t)module.dli_fbase;
  const uintptr_t addresses[] = {(uintptr_t)&linkage[0],
                                 (uintptr_t)cc_linux_show_picker,
                                 (uintptr_t)&linkage[1],
                                 (uintptr_t)cc_linux_capture_new,
                                 (uintptr_t)&linkage[2],
                                 (uintptr_t)cc_linux_capture_destroy,
                                 (uintptr_t)&selected.mutex,
                                 (uintptr_t)&selected.config,
                                 (uintptr_t)&selected.present,
                                 (uintptr_t)&linkage[3],
                                 (uintptr_t)cc_linux_capture_update,
                                 (uintptr_t)cc_video_frame_clone_i420,
                                 (uintptr_t)cc_i420_buffer_destroy,
                                 (uintptr_t)cc_video_frame_destroy};
  char json[2048];
  size_t used = snprintf(json, sizeof(json), "{\"offsets\":[");
  for (unsigned i = 0; i < 14; i++)
    used += snprintf(json + used, sizeof(json) - used, "%s%llu", i ? "," : "",
                     (unsigned long long)(addresses[i] - base));
  bool intact = linkage[1] == (uintptr_t)cc_linux_capture_new &&
                linkage[2] == (uintptr_t)cc_linux_capture_destroy &&
                linkage[3] == (uintptr_t)cc_linux_capture_update;
#ifdef LAZY_PICKER
  intact &= linkage[0] == (uintptr_t)fixture_lazy_stub + 6;
#else
  intact &= linkage[0] == (uintptr_t)cc_linux_show_picker;
#endif
  snprintf(json + used, sizeof(json) - used, "],\"intact\":%s,\"callbacks\":%u}",
           intact ? "true" : "false", callbacks);
  napi_value value;
  napi_create_string_utf8(env, json, NAPI_AUTO_LENGTH, &value);
  return value;
}
__attribute__((visibility("default"))) napi_value napi_register_module_v1(napi_env env,
                                                                          napi_value exports) {
  napi_value function;
  napi_create_function(env, "describe", NAPI_AUTO_LENGTH, describe, NULL, &function);
  napi_set_named_property(env, exports, "describe", function);
  napi_create_function(env, "run", NAPI_AUTO_LENGTH, run, NULL, &function);
  napi_set_named_property(env, exports, "run", function);
  return exports;
}
