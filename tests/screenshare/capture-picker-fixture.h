// Actual Rust registry and TSFN dispatcher over deterministic mock Node APIs.
#define _GNU_SOURCE
#include "../../integration/video/capture-picker.h"
#include "../../integration/video/direct-capture-wire.h"
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <node_api.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>
struct fixture_dispatch_api {
  __typeof__(napi_create_string_utf8) *string;
  __typeof__(napi_create_threadsafe_function) *create;
  __typeof__(napi_call_threadsafe_function) *call;
  __typeof__(napi_release_threadsafe_function) *release;
  __typeof__(napi_unref_threadsafe_function) *unref;
  __typeof__(napi_add_env_cleanup_hook) *cleanup;
  __typeof__(napi_get_global) *global;
  __typeof__(napi_call_function) *function;
};
bool dpe_picker_dispatch_fixture_configure(struct fixture_dispatch_api);
bool dpe_picker_dispatch_fixture_bind(napi_env, napi_value);
uintptr_t dpe_picker_dispatch_fixture_value(void);
void dpe_picker_dispatch_fixture_cleanup(void *);
bool dpe_picker_dispatch_fixture_notify(const char *, uint32_t, uint32_t);
#define dispatcher ((napi_threadsafe_function)dpe_picker_dispatch_fixture_value())
#define closing (!dispatcher)
#define cleanup dpe_picker_dispatch_fixture_cleanup
#define bind_dispatcher dpe_picker_dispatch_fixture_bind
#define show_picker dpe_picker_show
#define capture_new dpe_picker_new
#define capture_update dpe_picker_update
#define capture_destroy dpe_picker_destroy
#define finish dpe_picker_finish
#define cancel dpe_picker_cancel
#define delivery_read dpe_delivery_read
#define delivery_write dpe_delivery_write
#define delivery_receive dpe_delivery_receive
#define delivery_overdue dpe_delivery_overdue
#define delivery_frame dpe_delivery_frame
#define delivery_frame_at dpe_delivery_frame_at
#define delivery_wake dpe_delivery_wake
#define delivery_destroy dpe_delivery_destroy
static show_fn original_show;
static new_fn original_new;
static destroy_fn original_destroy;
static update_fn original_update;
static clone_i420_fn clone_i420;
static release_frame_fn release_i420, release_frame;
static pthread_mutex_t *picked_mutex;
static struct pipewire_config *picked_config;
static bool *picked_present;
static buffer_read_fn original_read;
static buffer_write_fn original_write;
static receive_fn original_receive;
static overdue_fn original_overdue;
static bool installed_wake;
static napi_threadsafe_function_call_js deliver;
static bool fixture_fail_enqueue;
static __typeof__(napi_call_threadsafe_function) *api_napi_call_threadsafe_function;
static __typeof__(napi_release_threadsafe_function) *api_napi_release_threadsafe_function;
static __typeof__(napi_create_string_utf8) *api_napi_create_string_utf8;
static __typeof__(napi_create_threadsafe_function) *api_napi_create_threadsafe_function;
static __typeof__(napi_unref_threadsafe_function) *api_napi_unref_threadsafe_function;
static __typeof__(napi_add_env_cleanup_hook) *api_napi_add_env_cleanup_hook;
static napi_status fixture_string(napi_env env, const char *text, size_t length,
                                  napi_value *output) {
  if (!strcmp(text, "DPE external capture") && api_napi_create_string_utf8)
    return api_napi_create_string_utf8(env, text, length, output);
  *output = (void *)3;
  if (text[0] == '{' && api_napi_call_threadsafe_function) {
    char *copy = strndup(text, length);
    assert(copy);
    return api_napi_call_threadsafe_function((void *)1, copy, napi_tsfn_nonblocking);
  }
  return napi_ok;
}
static napi_status fixture_create(napi_env env, napi_value callback, napi_value resource,
                                  napi_value name, size_t queue, size_t threads, void *final_data,
                                  napi_finalize finalizer, void *context,
                                  napi_threadsafe_function_call_js call,
                                  napi_threadsafe_function *output) {
  deliver = call;
  if (api_napi_create_threadsafe_function)
    return api_napi_create_threadsafe_function(env, callback, resource, name, queue, threads,
                                               final_data, finalizer, context, call, output);
  *output = (void *)1;
  return napi_ok;
}
static napi_status fixture_call(napi_threadsafe_function fn, void *data,
                                napi_threadsafe_function_call_mode mode) {
  (void)fn;
  assert(mode == napi_tsfn_nonblocking && deliver);
  if (fixture_fail_enqueue)
    return napi_queue_full;
  // Synchronous collection only: the fake global/function APIs cannot execute
  // user code or reenter the picker. The Rust Event allocation is still retired
  // by the real Rust delivery callback, including its JSON serialization.
  deliver((void *)7, (void *)4, NULL, data);
  return napi_ok;
}
static napi_status fixture_release(napi_threadsafe_function fn,
                                   napi_threadsafe_function_release_mode mode) {
  return api_napi_release_threadsafe_function ? api_napi_release_threadsafe_function(fn, mode)
                                              : napi_ok;
}
static napi_status fixture_unref(napi_env env, napi_threadsafe_function fn) {
  return api_napi_unref_threadsafe_function ? api_napi_unref_threadsafe_function(env, fn) : napi_ok;
}
static napi_status fixture_cleanup_hook(napi_env env, void (*hook)(void *), void *data) {
  return api_napi_add_env_cleanup_hook ? api_napi_add_env_cleanup_hook(env, hook, data) : napi_ok;
}
static napi_status fixture_global(napi_env env, napi_value *output) {
  (void)env;
  *output = (void *)5;
  return napi_ok;
}
static napi_status fixture_function(napi_env env, napi_value global, napi_value callback,
                                    size_t count, const napi_value *values, napi_value *output) {
  (void)env;
  (void)global;
  (void)callback;
  (void)values;
  assert(count == 1);
  *output = NULL;
  return napi_ok;
}
static inline struct delivery_probe *
delivery_create(uint32_t id, const struct capture_options *options, bool auto_fast) {
  if (installed_wake) {
    assert(dpe_delivery_configure_wake(original_read, original_write, original_receive,
                                       original_overdue));
    assert(dpe_delivery_enable_wake());
  }
  return dpe_delivery_create(
      id, options, auto_fast,
      (struct dpe_delivery_functions){clone_i420, release_i420, release_frame});
}
#define EMPTY 0
#define WAITING 1
#define SELECTED 2
#define STARTING 3
#define ACTIVE 4
#define CANCELLED 5
#define current_request dpe_picker_current()
static struct dpe_picker_snapshot *find(uint32_t id) {
  static struct dpe_picker_snapshot value;
  return dpe_picker_inspect(id, &value) ? &value : NULL;
}
static int32_t fixture_unused_show(const struct picker_options *options) {
  (void)options;
  return -EINVAL;
}
static int32_t fixture_unused_new(const struct capture_options *options, void **out) {
  (void)options;
  (void)out;
  return -EINVAL;
}
static int32_t fixture_unused_destroy(void *value) {
  (void)value;
  return -EINVAL;
}
static int32_t fixture_unused_update(void *value, const struct capture_options *options) {
  (void)value;
  (void)options;
  return -EINVAL;
}
static void fixture_configure(void) {
  static pthread_mutex_t selected_mutex = PTHREAD_MUTEX_INITIALIZER;
  static struct pipewire_config selected_config;
  static bool selected_present;
  if (!original_show)
    original_show = fixture_unused_show;
  if (!original_new)
    original_new = fixture_unused_new;
  if (!original_destroy)
    original_destroy = fixture_unused_destroy;
  if (!original_update)
    original_update = fixture_unused_update;
  if (!picked_mutex)
    picked_mutex = &selected_mutex;
  if (!picked_config)
    picked_config = &selected_config;
  if (!picked_present)
    picked_present = &selected_present;
  assert(dpe_picker_configure((struct dpe_picker_native){original_show,
                                                         original_new,
                                                         original_destroy,
                                                         original_update,
                                                         {clone_i420, release_i420, release_frame},
                                                         (uintptr_t)picked_mutex,
                                                         (uintptr_t)picked_config,
                                                         (uintptr_t)picked_present,
                                                         dpe_picker_dispatch_fixture_notify}));
  assert(dpe_picker_dispatch_fixture_configure((struct fixture_dispatch_api){
      fixture_string, fixture_create, fixture_call, fixture_release, fixture_unref,
      fixture_cleanup_hook, fixture_global, fixture_function}));
  assert(bind_dispatcher((void *)7, (void *)4));
}
static void fixture_selected(const struct picker_result *result, const void *data) {
  assert(result->success);
  *(struct pipewire_config *)data = result->config;
}
static inline void fixture_select(struct pipewire_config *config) {
  // Connect an actual same-UID socket through the same completion entry point
  // used by the renderer. Only the synthetic source path lives in this fixture.
  char directory[] = "/tmp/dpe-selected-check-XXXXXX", path[108];
  assert(mkdtemp(directory));
  snprintf(path, sizeof(path), "%s/source", directory);
  int server = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
  assert(server >= 0);
  struct sockaddr_un address = {.sun_family = AF_UNIX};
  strcpy(address.sun_path, path);
  assert(!bind(server, (struct sockaddr *)&address, sizeof(address)) && !listen(server, 1));
  struct picker_options options = {.source_type = 1, .callback = fixture_selected, .data = config};
  assert(!show_picker(&options));
  assert(finish(current_request, "selected", path, 42));
  close(server);
  unlink(path);
  rmdir(directory);
}
