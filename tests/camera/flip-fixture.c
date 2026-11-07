#include <assert.h>
#include <node_api.h>
#include <stdint.h>
#include <string.h>

// Keep the full converter ABI, including ten stack arguments. The second call
// path represents screenshots/screenshare/remote video and must stay unchanged.
#define PARAMETERS                                                                                 \
  const uint8_t *src, size_t length, uint8_t *y, int ys, uint8_t *u, int us, uint8_t *v, int vs,   \
      int cx, int cy, int sw, int sh, int cw, int ch, int rotation, uint32_t fourcc
#define ARGUMENTS src, length, y, ys, u, us, v, vs, cx, cy, sw, sh, cw, ch, rotation, fourcc
__attribute__((noinline, visibility("hidden"))) int dpe_test_convert(PARAMETERS) {
  assert(length == 12 && ys == 4 && us == 2 && vs == 2 && cx == 0 && cy == 0);
  assert(sw == 4 && sh == 2 && cw == 4 && ch == 2 && rotation == 0 && fourcc == 0x30323449);
  memcpy(y, src, 8);
  memcpy(u, src + 8, 2);
  memcpy(v, src + 10, 2);
  return 0;
}
__attribute__((noinline, visibility("hidden"))) int dpe_test_camera(PARAMETERS) {
  return dpe_test_convert(ARGUMENTS);
}
static napi_value check(napi_env env, napi_callback_info info) {
  napi_value args[2], result;
  size_t count = 2;
  bool expected, camera;
  assert(napi_get_cb_info(env, info, &count, args, NULL, NULL) == napi_ok && count == 2);
  assert(napi_get_value_bool(env, args[0], &camera) == napi_ok);
  assert(napi_get_value_bool(env, args[1], &expected) == napi_ok);
  uint8_t src[] = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12}, y[8], u[2], v[2];
  int value;
  if (camera)
    value = dpe_test_camera(src, 12, y, 4, u, 2, v, 2, 0, 0, 4, 2, 4, 2, 0, 0x30323449);
  else
    value = dpe_test_convert(src, 12, y, 4, u, 2, v, 2, 0, 0, 4, 2, 4, 2, 0, 0x30323449);
  bool valid = value == 0 && y[0] == (expected ? 4 : 1) && y[3] == (expected ? 1 : 4) &&
               y[4] == (expected ? 8 : 5) && u[0] == (expected ? 10 : 9) &&
               v[0] == (expected ? 12 : 11);
  napi_get_boolean(env, valid, &result);
  return result;
}
NAPI_MODULE_INIT() {
  napi_value fn;
  napi_create_function(env, "check", NAPI_AUTO_LENGTH, check, NULL, &fn);
  napi_set_named_property(env, exports, "check", fn);
  return exports;
}
