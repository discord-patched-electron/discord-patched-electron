// Real loaded ELF fixture: Rust validates and redirects its actual call site.
#define _GNU_SOURCE
#include <dlfcn.h>
#include <limits.h>
#include <node_api.h>
#include <stdint.h>
#include <stdio.h>
struct resolution {
  size_t width, height;
};
struct sink_wants {
  uint8_t rotation, black, padding[2];
  int32_t maximum, target;
  uint8_t has_target, target_padding[3];
  int32_t fps;
};
extern const uint8_t fixture_scale_site[];

#include <assert.h>
#include <math.h>
#include <stdlib.h>

static uint64_t observed_target;
__attribute__((used, noinline)) static struct resolution native_scale(size_t width, size_t height,
                                                                      uint64_t target) {
  observed_target = target;
  if (!((target >> 32) & 255))
    return (struct resolution){width, height};
  if (!width && !height) {
    width = 19200;
    height = 10800;
  }
  unsigned pixels = target;
  if (width * height <= pixels)
    return (struct resolution){width, height};
  double w = sqrt((double)width / height * pixels);
  return (struct resolution){(size_t)w, (size_t)(pixels / w)};
}
extern struct resolution fixture_scale_invoke(size_t, size_t, uint64_t, struct sink_wants *);
static struct resolution scaled(size_t width, size_t height, const struct sink_wants *wants,
                                uint64_t target) {
  return fixture_scale_invoke(width, height, target, (struct sink_wants *)wants);
}
static napi_value run(napi_env env, napi_callback_info info) {
  (void)info;
  struct sink_wants wants = {.maximum = INT_MAX, .fps = 60};
  struct resolution result = scaled(1920, 1080, &wants, 0);
  assert(result.width == 1920 && result.height == 1080 && observed_target == 0);
  result = scaled(0, 0, &wants, 0);
  assert(!result.width && !result.height); // Unbounded Source remains native.

  wants.maximum = 1244160; // WebRTC's 3/5 down-adaptation, with no target.
  result = scaled(1920, 1080, &wants, 0);
  assert(result.width == 1486 && result.height == 836);
  assert(result.width * result.height <= (unsigned)wants.maximum);
  assert(observed_target == ((UINT64_C(1) << 32) | 1244160));
  result = scaled(0, 0, &wants, 0);
  assert(result.width == 1486 && result.height == 836);

  wants.has_target = 1;
  wants.target = 2073600; // A target may not override a stricter maximum.
  result = scaled(1920, 1080, &wants, (UINT64_C(1) << 32) | wants.target);
  assert(result.width == 1486 && result.height == 836);
  wants.target = 921600;
  uint64_t native_target = (UINT64_C(1) << 32) | wants.target;
  result = scaled(1920, 1080, &wants, native_target);
  assert(result.width == 1280 && result.height == 720 && observed_target == native_target);

  wants.maximum = wants.target = 921599;
  native_target = (UINT64_C(1) << 32) | wants.target;
  result = scaled(1920, 1080, &wants, native_target);
  assert(result.width == 1278 && result.height == 718 && observed_target == native_target);
  size_t visible_width = (result.width + 1) & ~(size_t)1;
  size_t visible_height = (result.height + 1) & ~(size_t)1;
  assert(visible_width * visible_height <= (unsigned)wants.maximum);

  wants.has_target = 0;
  wants.maximum = INT_MAX;
  result = scaled(1919, 1079, &wants, 0);
  assert(result.width == 1919 && result.height == 1079); // No new padding/crop policy.
  wants.maximum = 2073600;
  result = scaled(1919, 1079, &wants, 0);
  assert(result.width == 1919 && result.height == 1079);
  wants.maximum = 0;
  result = scaled(1920, 1080, &wants, 0);
  assert(result.width == 1920 && result.height == 1080);
  wants.maximum = 921600;
  wants.has_target = 2;
  result = scaled(1920, 1080, &wants, 0);
  assert(result.width == 1920 && result.height == 1080);

  napi_value result_value;
  napi_get_undefined(env, &result_value);
  return result_value;
}
__asm__(".text\n.balign 4096\n"
        ".byte "
        "0x4c,0x8d,0x7c,0x24,0x28,0x4c,0x89,0xff,0x4c,0x89,0xf6,0xe8,0x10,0x9d,0xff,0xff,0x8b,0xbb,"
        "0xa0,0x0,0x0,0x0,0x8b,0xb3,0xa4,0x0,0x0,0x0,0x48,0x89,0xf8,0x48,0x9,0xf0,0x74,0x34,0x48,"
        "0x8b,0x54,0x24,0x30,0xeb,0x2f,0x8b,0xbb,0xa0,0x0,0x0,0x0,0x8b,0xb3,0xa4,0x0,0x0,0x0,0x48,"
        "0xba,0x0,0x0,0x90,0x0,0x1,0x0,0x0,0x0,0xe8,0x7a,0x9f,0xff,0xff,0x49,0x89,0xc4,0x49,0x89,"
        "0xd6,0x44,0x89,0x64,0x24,0x8,0x44,0x89,0x74,0x24,0xc,0xeb,0x32,0x31,0xd2\n"
        ".global fixture_scale_site\nfixture_scale_site:\ncall native_scale\nret\n"
        ".global fixture_scale_invoke\nfixture_scale_invoke:\npush %r15\nmov %rcx,%r15\nsub "
        "$8,%rsp\ncall fixture_scale_site\nadd $8,%rsp\npop %r15\nret\n.balign 4096\n");
napi_value napi_register_module_v1(napi_env env, napi_value exports) {
  Dl_info module;
  assert(dladdr((void *)fixture_scale_site, &module));
  napi_value value;
  napi_create_uint32(env, (uintptr_t)fixture_scale_site - (uintptr_t)module.dli_fbase, &value);
  napi_set_named_property(env, exports, "site", value);
  napi_create_uint32(env, (uintptr_t)native_scale - (uintptr_t)module.dli_fbase, &value);
  napi_set_named_property(env, exports, "target", value);
  napi_create_function(env, "run", NAPI_AUTO_LENGTH, run, NULL, &value);
  napi_set_named_property(env, exports, "run", value);
  return exports;
}
