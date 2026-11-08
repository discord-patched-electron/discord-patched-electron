// Actual reviewed x86 call/stub/GOT shapes, isolated from any PipeWire daemon.
#define _GNU_SOURCE
#include <dlfcn.h>
#include <node_api.h>
#include <pipewire/stream.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
static unsigned connects, updates;
static int connect_stream(struct pw_stream *s, enum pw_direction d, uint32_t t,
                          enum pw_stream_flags f, const struct spa_pod **p, uint32_t n) {
  (void)s;
  (void)d;
  (void)t;
  (void)f;
  (void)p;
  (void)n;
  return ++connects;
}
static int update_stream(struct pw_stream *s, const struct spa_pod **p, uint32_t n) {
  (void)s;
  (void)p;
  (void)n;
  return ++updates;
}
// Writable inner function pointers are part of the reviewed native ABI.
__attribute__((used)) static void *connect_inner = connect_stream;
__attribute__((used)) static void *update_inner = update_stream;
extern int fixture_reviewed_connect(struct pw_stream *, enum pw_direction, uint32_t,
                                    enum pw_stream_flags, const struct spa_pod **, uint32_t);
extern int fixture_unrelated_connect(struct pw_stream *, enum pw_direction, uint32_t,
                                     enum pw_stream_flags, const struct spa_pod **, uint32_t);
extern int fixture_reviewed_update(struct pw_stream *, const struct spa_pod **, uint32_t);
extern int fixture_unrelated_update(struct pw_stream *, const struct spa_pod **, uint32_t);
extern char fixture_connect_call[], fixture_update_call[], fixture_connect_stub[],
    fixture_update_stub[];
extern char fixture_other_connect_call[], fixture_other_update_call[];
#define STUB(name)                                                                                 \
  ".globl fixture_" #name "_stub\n.type fixture_" #name "_stub,@function\n"                        \
  "fixture_" #name "_stub:\npush %rbp\nmov %rsp,%rbp\nmov " #name                                  \
  "_inner(%rip),%rax\npop %rbp\njmp *%rax\n"
#define CALL(name, kind, label)                                                                    \
  ".globl fixture_" #kind "_" #name "\n.type fixture_" #kind "_" #name ",@function\n"              \
  "fixture_" #kind "_" #name ":\nsub $8,%rsp\n.globl " #label "\n" #label ":\n"                    \
  "call *fixture_" #name "_stub@GOTPCREL(%rip)\nadd $8,%rsp\nret\n"
__asm__(".text\n" STUB(connect) STUB(update) CALL(connect, reviewed, fixture_connect_call)
            CALL(update, reviewed, fixture_update_call)
                CALL(connect, unrelated, fixture_other_connect_call)
                    CALL(update, unrelated, fixture_other_update_call));
static uintptr_t slot(const char *call) {
  int32_t displacement;
  memcpy(&displacement, call + 2, 4);
  return (uintptr_t)call + 6 + displacement;
}
static napi_value describe(napi_env env, napi_callback_info info) {
  (void)info;
  Dl_info module;
  dladdr(fixture_connect_stub, &module);
  uintptr_t base = (uintptr_t)module.dli_fbase;
  char json[1024];
  snprintf(json, sizeof(json),
           "{\"connect\":{\"slot\":%llu,\"target\":%llu,\"call\":%llu},"
           "\"update\":{\"slot\":%llu,\"target\":%llu,\"call\":%llu},"
           "\"calls\":[%u,%u,%u,%u],\"slotsIntact\":%s}",
           (unsigned long long)(slot(fixture_other_connect_call) - base),
           (unsigned long long)((uintptr_t)fixture_connect_stub - base),
           (unsigned long long)((uintptr_t)fixture_connect_call - base),
           (unsigned long long)(slot(fixture_other_update_call) - base),
           (unsigned long long)((uintptr_t)fixture_update_stub - base),
           (unsigned long long)((uintptr_t)fixture_update_call - base),
           (unsigned char)fixture_connect_call[0], (unsigned char)fixture_update_call[0],
           (unsigned char)fixture_other_connect_call[0],
           (unsigned char)fixture_other_update_call[0],
           *(uintptr_t *)slot(fixture_other_connect_call) == (uintptr_t)fixture_connect_stub &&
                   *(uintptr_t *)slot(fixture_other_update_call) == (uintptr_t)fixture_update_stub
               ? "true"
               : "false");
  napi_value result;
  napi_create_string_utf8(env, json, NAPI_AUTO_LENGTH, &result);
  return result;
}
static napi_value run(napi_env env, napi_callback_info info) {
  (void)info;
  // OUTPUT is outside video capture scope; original native arguments pass intact.
  fixture_reviewed_connect((void *)1, PW_DIRECTION_OUTPUT, 7, PW_STREAM_FLAG_AUTOCONNECT, NULL, 0);
  fixture_unrelated_connect((void *)1, PW_DIRECTION_OUTPUT, 7, PW_STREAM_FLAG_AUTOCONNECT, NULL, 0);
  fixture_reviewed_update((void *)1, NULL, 0);
  fixture_unrelated_update((void *)1, NULL, 0);
  char json[100];
  snprintf(json, sizeof(json), "{\"connects\":%u,\"updates\":%u}", connects, updates);
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
