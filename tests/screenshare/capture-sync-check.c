// Exercise the installed Rust redirect through the real native scalar ABI.
#define _GNU_SOURCE
#include <assert.h>
#include <dlfcn.h>
#include <errno.h>
#include <limits.h>
#include <node_api.h>
#include <stdint.h>
#include <time.h>
static int64_t observed_timeout;
static uintptr_t native_error;
__attribute__((used, noinline)) static uintptr_t native_wait(int drm, int sync, uint64_t point,
                                                             int64_t timeout) {
  assert(drm == 19 && sync == 37 && point == UINT64_C(0x123456789abcdef));
  assert(errno == EAGAIN);
  observed_timeout = timeout;
  errno = ENOSPC;
  return native_error;
}
extern uintptr_t fixture_sync_invoke(int, int, uint64_t, int64_t);
extern const uint8_t fixture_sync_site[];
__asm__(
    ".text\n.balign 4096\n"
    ".byte "
    "0x48,0xb9,0x00,0xf2,0x05,0x2a,0x01,0x00,0x00,0x00,0x89,0xef,0x44,0x89,0xf6,0x4c,0x89,0xfa\n"
    ".global fixture_sync_site\nfixture_sync_site:\ncall native_wait\nret\n"
    ".global fixture_sync_invoke\nfixture_sync_invoke:\ncall fixture_sync_site\nret\n.balign "
    "4096\n");
static void check(int64_t timeout) {
  errno = EAGAIN;
  assert(fixture_sync_invoke(19, 37, UINT64_C(0x123456789abcdef), timeout) == native_error);
  assert(errno == ENOSPC);
}
static napi_value run(napi_env env, napi_callback_info info) {
  (void)info;
  struct timespec before, after;
  assert(!clock_gettime(CLOCK_MONOTONIC, &before));
  check(INT64_C(5000000000));
  assert(!clock_gettime(CLOCK_MONOTONIC, &after));
  assert(observed_timeout >= before.tv_sec * INT64_C(1000000000) + before.tv_nsec + 5000000000);
  assert(observed_timeout <= after.tv_sec * INT64_C(1000000000) + after.tv_nsec + 5000000000);
  const int64_t unchanged[] = {0, -1, INT64_MAX, 123};
  native_error = UINT64_C(0xfedcba987654321);
  for (unsigned i = 0; i < sizeof(unchanged) / sizeof(unchanged[0]); i++) {
    check(unchanged[i]);
    assert(observed_timeout == unchanged[i]);
  }
  napi_value result;
  napi_get_undefined(env, &result);
  return result;
}
napi_value napi_register_module_v1(napi_env env, napi_value exports) {
  Dl_info module;
  assert(dladdr((void *)fixture_sync_site, &module));
  napi_value value;
  napi_create_uint32(env, (uintptr_t)fixture_sync_site - (uintptr_t)module.dli_fbase, &value);
  napi_set_named_property(env, exports, "site", value);
  napi_create_uint32(env, (uintptr_t)native_wait - (uintptr_t)module.dli_fbase, &value);
  napi_set_named_property(env, exports, "target", value);
  napi_create_function(env, "run", NAPI_AUTO_LENGTH, run, NULL, &value);
  napi_set_named_property(env, exports, "run", value);
  return exports;
}
