// Real local Unix FD ownership with mocked reviewed C ABI endpoints. No portal,
// display, PipeWire daemon, GPU, Discord account or capture is opened.
#include "capture-picker-fixture.h"
#include <assert.h>

static unsigned callbacks, originals, starts, stops;
static unsigned events, releases;
static struct picker_result result;
static pthread_mutex_t native_picked_mutex = PTHREAD_MUTEX_INITIALIZER;
static struct pipewire_config native_picked_config;
static bool native_picked_present;
static char last_event[192];
static napi_status enqueue(napi_threadsafe_function fn, void *data,
                           napi_threadsafe_function_call_mode mode) {
  (void)fn;
  (void)mode;
  snprintf(last_event, sizeof(last_event), "%s", (char *)data);
  events++;
  free(data);
  return napi_ok;
}
static napi_status release_dispatcher(napi_threadsafe_function fn,
                                      napi_threadsafe_function_release_mode mode) {
  (void)fn;
  assert(mode == napi_tsfn_abort);
  releases++;
  return napi_ok;
}
static void *cleanup_token;
static void (*registered_cleanup)(void *);
static napi_status make_resource(napi_env env, const char *value, size_t length, napi_value *out) {
  (void)env;
  (void)length;
  assert(!strcmp(value, "DPE external capture"));
  *out = (void *)3;
  return napi_ok;
}
static napi_status make_dispatcher(napi_env env, napi_value callback_value, napi_value resource,
                                   napi_value name, size_t queue, size_t threads, void *final_data,
                                   napi_finalize finalizer, void *context,
                                   napi_threadsafe_function_call_js call,
                                   napi_threadsafe_function *out) {
  (void)env;
  assert(callback_value == (void *)4 && !resource && name == (void *)3);
  assert(queue == 64 && threads == 1 && !final_data && !finalizer && !context && call == deliver);
  *out = (void *)2;
  return napi_ok;
}
static napi_status unref_dispatcher(napi_env env, napi_threadsafe_function value) {
  (void)env;
  assert(value == (void *)2);
  return napi_ok;
}
static napi_status on_cleanup(napi_env env, void (*hook)(void *), void *data) {
  (void)env;
  registered_cleanup = hook;
  cleanup_token = data;
  return napi_ok;
}
static void callback(const struct picker_result *value, const void *data) {
  assert(data == (void *)77);
  result = *value;
  if (value->success) {
    native_picked_config = value->config;
    native_picked_present = true;
  }
  callbacks++;
}
static int32_t native_show(const struct picker_options *options) {
  assert(options->callback == callback);
  originals++;
  return 0;
}
static int32_t native_new(const struct capture_options *options, void **capture) {
  starts++;
  // Cancellation cannot close the descriptor once the native constructor owns
  // it, even while that constructor has not returned its opaque capture yet.
  assert(!cancel(current_request));
  assert(fcntl(options->config->fd, F_GETFD) >= 0);
  close(options->config->fd);
  *capture = (void *)(uintptr_t)(1000 + starts);
  return 0;
}
static int32_t native_destroy(void *capture) {
  assert((uintptr_t)capture >= 1001 && (uintptr_t)capture <= 1033);
  stops++;
  return 0;
}
int main(void) {
  original_show = native_show;
  original_new = native_new;
  original_destroy = native_destroy;
  picked_mutex = &native_picked_mutex;
  picked_config = &native_picked_config;
  picked_present = &native_picked_present;
  api_napi_call_threadsafe_function = enqueue;
  api_napi_release_threadsafe_function = release_dispatcher;
  fixture_configure();
  struct picker_options options = {.source_type = 1, .callback = callback, .data = (void *)77};
  assert(show_picker(&options) == 0 && events == 1 && !originals);
  uint32_t first = current_request;
  assert(strstr(last_event, "request"));
  assert(show_picker(&options) == 0 && current_request != first && !callbacks);
  assert(!finish(first, "cancel", "", 0));
  assert(finish(current_request, "native", "", 0) && originals == 1 && !callbacks);
  assert(!finish(current_request, "native", "", 0));

  assert(show_picker(&options) == 0);
  assert(cancel(current_request) && callbacks == 1 && !result.success);
  assert(!cancel(current_request));
  assert(show_picker(&options) == 0);
  assert(finish(current_request, "error", "", 0));
  assert(callbacks == 2 && !result.success && result.error);

  char directory[] = "/tmp/dpe-picker-check-XXXXXX";
  assert(mkdtemp(directory));
  char pathname[108];
  snprintf(pathname, sizeof(pathname), "%s/pw", directory);
  int server = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
  assert(server >= 0);
  struct sockaddr_un address = {.sun_family = AF_UNIX};
  strcpy(address.sun_path, pathname);
  assert(!bind(server, (struct sockaddr *)&address, sizeof(address)) && !listen(server, 16));
  assert(show_picker(&options) == 0);
  assert(!finish(current_request, "selected", "relative", 42));
  assert(!finish(current_request, "selected", directory, 42));
  assert(!finish(current_request, "selected", "/not-a-socket", 42));
  assert(find(current_request)->state == WAITING);
  assert(finish(current_request, "selected", pathname, 42));
  assert(result.success && result.config.node == 42 && result.config.fd >= 0);
  int owned = result.config.fd;
  struct pipewire_config config = result.config;
  assert(cancel(current_request));
  assert(!native_picked_present);
  assert(fcntl(owned, F_GETFD) == -1 && errno == EBADF);
  struct capture_options capture_options = {.config = &config};
  void *capture = (void *)99;
  assert(capture_new(&capture_options, &capture) == -ECANCELED && !capture && !starts);

  // A foreign close followed by descriptor reuse cannot make cancellation
  // close an unrelated resource or permit a delayed native constructor.
  assert(!show_picker(&options));
  assert(finish(current_request, "selected", pathname, 42));
  owned = result.config.fd;
  config = result.config;
  close(owned);
  int unrelated[2];
  assert(!pipe(unrelated));
  if (unrelated[0] != owned)
    assert(dup2(unrelated[0], owned) == owned);
  assert(cancel(current_request));
  assert(fcntl(owned, F_GETFD) >= 0);
  assert(capture_new(&capture_options, &capture) == -ECANCELED && !capture && !starts);
  close(owned);
  if (unrelated[0] != owned)
    close(unrelated[0]);
  close(unrelated[1]);

  // Starting another picker retires a selection that never had a consumer,
  // without invoking the old callback through the replacement global handler.
  assert(show_picker(&options) == 0);
  assert(finish(current_request, "selected", pathname, 42));
  owned = result.config.fd;
  unsigned callback_count = callbacks;
  assert(show_picker(&options) == 0 && callbacks == callback_count);
  assert(!native_picked_present && fcntl(owned, F_GETFD) == -1 && errno == EBADF);
  assert(cancel(current_request));

  // A stale owner must never clear a newer global selected configuration.
  assert(show_picker(&options) == 0);
  assert(finish(current_request, "selected", pathname, 42));
  native_picked_config.node = 99;
  assert(cancel(current_request) && native_picked_present && native_picked_config.node == 99);

  assert(show_picker(&options) == 0);
  first = current_request;
  assert(finish(first, "selected", pathname, 43));
  config = result.config;
  assert(capture_new(&capture_options, &capture) == 0 && capture == (void *)1001);
  assert(find(first)->state == ACTIVE && find(first)->fd == -1);
  assert(strstr(last_event, "started") && !cancel(first));
  // A subsequent picker and cancellation must not release the first capture.
  assert(show_picker(&options) == 0);
  assert(finish(current_request, "selected", pathname, 44));
  assert(cancel(current_request));
  assert(find(first)->state == ACTIVE && !stops);
  assert(capture_destroy(capture) == 0 && stops == 1 && !find(first));
  assert(strstr(last_event, "stopped"));

  assert(show_picker(&options) == 0);
  assert(finish(current_request, "selected", pathname, 45));
  owned = result.config.fd;
  cleanup(NULL);
  assert(!native_picked_present);
  assert(releases == 1 && !dispatcher && closing);
  assert(fcntl(owned, F_GETFD) == -1 && errno == EBADF);
  assert(show_picker(&options) == 0 && originals == 2);
  cleanup(NULL);
  assert(releases == 1);
  // Replacing the document retires its Node environment but keeps the native
  // hook installed. The replacement callback must resume optimized capture.
  api_napi_create_string_utf8 = make_resource;
  api_napi_create_threadsafe_function = make_dispatcher;
  api_napi_unref_threadsafe_function = unref_dispatcher;
  api_napi_add_env_cleanup_hook = on_cleanup;
  assert(bind_dispatcher((void *)7, (void *)4) && dispatcher == (void *)2 && !closing);
  unsigned old_originals = originals;
  assert(show_picker(&options) == 0 && originals == old_originals);
  uint32_t replacement_request = current_request;
  assert(find(replacement_request)->state == WAITING && strstr(last_event, "request"));
  cleanup((void *)1); // A retired context's hook cannot close the new callback.
  assert(dispatcher == (void *)2 && !closing && releases == 1);
  assert(cleanup_token != (void *)1 && registered_cleanup);
  registered_cleanup(cleanup_token);
  assert(!dispatcher && closing && releases == 2 && !find(replacement_request));
  registered_cleanup(cleanup_token);
  assert(releases == 2);
  void *retired_generation = cleanup_token;
  assert(bind_dispatcher((void *)7, (void *)4));
  assert(dispatcher == (void *)2 && cleanup_token != retired_generation);
  cleanup(retired_generation); // The TSFN pointer is reused, the token is not.
  assert(dispatcher == (void *)2 && releases == 2);
  fixture_fail_enqueue = true;
  old_originals = originals;
  assert(!show_picker(&options) && originals == old_originals + 1);
  assert(!find(current_request));
  fixture_fail_enqueue = false;

  // The bounded registry falls back intact when all slots have native owners.
  assert(!fcntl(server, F_SETFL, O_NONBLOCK));
  void *active[32];
  for (unsigned i = 0; i < 32; i++) {
    int peer;
    while ((peer = accept(server, NULL, NULL)) >= 0)
      close(peer);
    assert(errno == EAGAIN || errno == EWOULDBLOCK);
    assert(!show_picker(&options));
    assert(finish(current_request, "selected", pathname, 100 + i));
    config = result.config;
    assert(!capture_new(&capture_options, &active[i]));
    assert(find(current_request)->state == ACTIVE);
  }
  old_originals = originals;
  unsigned old_events = events;
  assert(!show_picker(&options) && originals == old_originals + 1 && events == old_events);
  for (unsigned i = 0; i < 32; i++)
    assert(!capture_destroy(active[i]));
  assert(!find(0));
  cleanup(cleanup_token);
  assert(releases == 3);
  close(server);
  unlink(pathname);
  rmdir(directory);
  puts("picker supersession, exact request cancellation, FD transfer, capture lifecycle and "
       "cleanup passed");
  return 0;
}
