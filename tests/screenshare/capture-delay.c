// Compile only into a disposable fixture helper. The packaged helper has no
// delay control. Sleeping this capture callback leaves PipeWire's data thread
// running, creating a queue without adding host CPU/GPU pressure.
#define _GNU_SOURCE
#include <stdatomic.h>
#include <time.h>
static atomic_uint pending_delay;
static void delay_callback(void) {
  unsigned milliseconds = atomic_exchange(&pending_delay, 0);
  if (milliseconds) {
    struct timespec delay = {.tv_nsec = milliseconds * 1000000};
    while (nanosleep(&delay, &delay) < 0) {
    }
  }
}
#define DPE_CAPTURE_BEFORE_DEQUEUE() delay_callback()
#include "../../integration/video/capture.c"

static napi_value set_delay(napi_env env, napi_callback_info info) {
  size_t count = 1;
  napi_value value;
  uint32_t ms;
  if (api_napi_get_cb_info(env, info, &count, &value, NULL, NULL) != napi_ok || count != 1 ||
      api_napi_get_value_uint32(env, value, &ms) != napi_ok || ms > 200)
    return fail(env, "Fixture delay must be at most 200 ms");
  atomic_store(&pending_delay, ms);
  return undefined(env);
}
__attribute__((visibility("default"))) napi_value napi_register_module_v1(napi_env env,
                                                                          napi_value exports) {
  if (!dpe_capture_register(env, exports))
    return NULL;
  napi_value fn;
  api_napi_create_function(env, "testCaptureDelay", NAPI_AUTO_LENGTH, set_delay, NULL, &fn);
  api_napi_set_named_property(env, exports, "testCaptureDelay", fn);
  return exports;
}
