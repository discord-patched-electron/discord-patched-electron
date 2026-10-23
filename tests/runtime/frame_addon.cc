// Synthetic test frames only; compiled against Discord's pinned public header.
#include <node_api.h>
#include <dlfcn.h>
#include <atomic>
#include <string>
#include <vector>
#include "electron_video_shared.h"

using discord::media::electron::DiscordFrame;
using Deliver = void (*)(const char*, const DiscordFrame&, void (*)(void*), void*);
static Deliver deliver = nullptr;
static std::atomic<uint32_t> sent{0}, released{0};
struct Payload {
  std::vector<uint8_t> y = std::vector<uint8_t>(64 * 48, 82);
  std::vector<uint8_t> u = std::vector<uint8_t>(32 * 24, 90);
  std::vector<uint8_t> v = std::vector<uint8_t>(32 * 24, 240);
};
static void Release(void* pointer) {
  delete static_cast<Payload*>(pointer);
  released.fetch_add(1);
}
static napi_value Push(napi_env env, napi_callback_info info) {
  napi_value argument, result;
  size_t count = 1, length = 0;
  if (napi_get_cb_info(env, info, &count, &argument, nullptr, nullptr) != napi_ok || count != 1 ||
      napi_get_value_string_utf8(env, argument, nullptr, 0, &length) != napi_ok || length > 256) {
    napi_throw_type_error(env, nullptr, "Expected a stream identifier");
    return nullptr;
  }
  std::vector<char> id(length + 1);
  napi_get_value_string_utf8(env, argument, id.data(), id.size(), &length);
  auto* payload = new Payload();
  DiscordFrame frame{};
  frame.timestamp_us = static_cast<int64_t>(sent.fetch_add(1)) * 33333;
  frame.frame.yuv = {payload->y.data(), payload->u.data(), payload->v.data(), 64, 32, 32};
  frame.width = 64;
  frame.height = 48;
  frame.type = 1;  // Discord's public implementation uses 1 for I420 frames.
  frame.rotation = discord::media::electron::kRotation0;
  deliver(id.data(), frame, Release, payload);
  napi_get_undefined(env, &result);
  return result;
}
static napi_value Counts(napi_env env, napi_callback_info info) {
  napi_value result, value;
  napi_create_object(env, &result);
  napi_create_uint32(env, sent.load(), &value);
  napi_set_named_property(env, result, "sent", value);
  napi_create_uint32(env, released.load(), &value);
  napi_set_named_property(env, result, "released", value);
  return result;
}
static napi_value Initialize(napi_env env, napi_value exports) {
  deliver = reinterpret_cast<Deliver>(dlsym(RTLD_DEFAULT, "DeliverDiscordFrame"));
  if (!deliver) {
    napi_throw_error(env, nullptr, "Runtime does not export DeliverDiscordFrame");
    return nullptr;
  }
  napi_property_descriptor descriptors[] = {
      {"push", nullptr, Push, nullptr, nullptr, nullptr, napi_default, nullptr},
      {"counts", nullptr, Counts, nullptr, nullptr, nullptr, napi_default, nullptr}};
  napi_define_properties(env, exports, 2, descriptors);
  return exports;
}
NAPI_MODULE(NODE_GYP_MODULE_NAME, Initialize)
