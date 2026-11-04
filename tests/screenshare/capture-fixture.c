// A native ABI fixture, never a capture source. It exports the same small
// register-return/indirect-call shapes as the reviewed Rust PipeWire callback.
#define _GNU_SOURCE
#include <dlfcn.h>
#include <node_api.h>
#include <pipewire/stream.h>
#include <spa/buffer/meta.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

struct pair {
  struct pw_buffer *buffer;
  struct pw_stream *stream;
};
static struct pw_buffer frames[4];
static struct spa_buffer buffers[4];
static struct spa_data data[4];
static struct spa_chunk chunks[4];
static unsigned next, releases;
struct pair fixture_dequeue(struct pw_stream *stream) {
  return (struct pair){next < 4 ? &frames[next++] : NULL, stream};
}
void fixture_release(struct pair *buffer) {
  if (buffer->buffer)
    releases++;
}
extern struct pw_buffer *fixture_capture(struct pw_stream *stream);
extern char fixture_dequeue_call[], fixture_release_call[];
__asm__(".text\n"
        ".globl fixture_capture\n.type fixture_capture,@function\n"
        "fixture_capture:\n push %rbx\n sub $16,%rsp\n"
        ".globl fixture_dequeue_call\nfixture_dequeue_call:\n"
        "call *fixture_dequeue@GOTPCREL(%rip)\n"
        "mov %rax,%rbx\n mov %rax,(%rsp)\n mov %rdx,8(%rsp)\n mov %rsp,%rdi\n"
        ".globl fixture_release_call\nfixture_release_call:\n"
        "call *fixture_release@GOTPCREL(%rip)\n"
        "mov %rbx,%rax\n add $16,%rsp\n pop %rbx\n ret\n"
        ".size fixture_capture,.-fixture_capture\n");

static napi_value describe(napi_env env, napi_callback_info info) {
  (void)info;
  Dl_info module;
  dladdr(fixture_capture, &module);
  uintptr_t base = (uintptr_t)module.dli_fbase;
  char json[512];
  snprintf(
      json, sizeof(json),
      "{\"dequeue\":{\"call\":%llu,\"target\":%llu},\"release\":{\"call\":%llu,\"target\":%llu}}",
      (unsigned long long)((uintptr_t)fixture_dequeue_call - base),
      (unsigned long long)((uintptr_t)fixture_dequeue - base),
      (unsigned long long)((uintptr_t)fixture_release_call - base),
      (unsigned long long)((uintptr_t)fixture_release - base));
  napi_value result;
  napi_create_string_utf8(env, json, NAPI_AUTO_LENGTH, &result);
  return result;
}
static napi_value run(napi_env env, napi_callback_info info) {
  (void)info;
  for (unsigned i = 0; i < 4; i++) {
    chunks[i] = (struct spa_chunk){.size = 256, .stride = 16};
    data[i] = (struct spa_data){.type = SPA_DATA_DmaBuf, .chunk = &chunks[i]};
    buffers[i] = (struct spa_buffer){.n_datas = 1, .datas = &data[i]};
    frames[i] = (struct pw_buffer){.buffer = &buffers[i]};
  }
  next = releases = 0;
  struct pw_buffer *selected = fixture_capture((struct pw_stream *)1);
  char json[128];
  snprintf(json, sizeof(json), "{\"selected\":%td,\"released\":%u,\"dequeued\":%u}",
           selected - frames, releases, next);
  napi_value result;
  napi_create_string_utf8(env, json, NAPI_AUTO_LENGTH, &result);
  return result;
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
