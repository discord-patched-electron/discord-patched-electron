// Real local Unix FD ownership with mocked reviewed C ABI endpoints. No portal,
// display, PipeWire daemon, GPU, Discord account or capture is opened.
#include "../../integration/video/capture-picker.c"
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
  assert((uintptr_t)capture >= 1001 && (uintptr_t)capture <= 1002);
  stops++;
  return 0;
}
int main(void) {
  uint8_t *image = calloc(1, 4096);
  assert(image);
  ElfW(Ehdr) *eh = (void *)image;
  eh->e_phoff = sizeof(*eh);
  eh->e_phnum = 2;
  ElfW(Phdr) *ph = (void *)(image + eh->e_phoff);
  ph[0] = (ElfW(Phdr)){.p_type = PT_LOAD, .p_flags = PF_R | PF_X, .p_memsz = 2048};
  ph[1] = (ElfW(Phdr)){.p_type = PT_LOAD, .p_flags = PF_R | PF_W, .p_vaddr = 2048, .p_memsz = 2048};
  uintptr_t base = (uintptr_t)image, slot = base + 3072, target = base + 1500;
  uint8_t *stub = image + 1024;
  stub[0] = 0xff;
  stub[1] = 0x25;
  stub[6] = 0x68;
  stub[11] = 0xe9;
  int32_t displacement = slot - (uintptr_t)(stub + 6);
  memcpy(stub + 2, &displacement, 4);
  *(uintptr_t *)slot = (uintptr_t)(stub + 6);
  assert(reviewed_slot(base, slot, target));
  displacement++;
  memcpy(stub + 2, &displacement, 4);
  assert(!reviewed_slot(base, slot, target));
  *(uintptr_t *)slot = base + 2500;
  assert(!reviewed_slot(base, slot, target));
  *(uintptr_t *)slot = target;
  assert(reviewed_slot(base, slot, target));
  free(image);
  original_show = native_show;
  original_new = native_new;
  original_destroy = native_destroy;
  picked_mutex = &native_picked_mutex;
  picked_config = &native_picked_config;
  picked_present = &native_picked_present;
  api_napi_call_threadsafe_function = enqueue;
  api_napi_release_threadsafe_function = release_dispatcher;
  dispatcher = (void *)1;
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
  assert(open_remote("relative") == -1 && open_remote(directory) == -1);
  assert(show_picker(&options) == 0);
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
  close(server);
  unlink(pathname);
  rmdir(directory);
  puts("picker supersession, exact request cancellation, FD transfer, capture lifecycle and "
       "cleanup passed");
  return 0;
}
