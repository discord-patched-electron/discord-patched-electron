// Rust wake callbacks over the actual C ABI, with native-owned result storage.
// Deterministic message/publication races and joined teardown; no GPU/display.
#include "capture-picker-fixture.h"
#include <assert.h>
static pthread_mutex_t fixture_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t fixture_changed = PTHREAD_COND_INITIALIZER;
static unsigned receive_calls, overdue_calls, messages, writes;
static bool publish_requested, publish_ready, worker_ready, stopped;
static bool signal_publisher, second_poll_message;
static pthread_t worker;
static int buffer, other_buffer, capture;
static uint64_t last_seconds;
static uint32_t last_nanos;
static struct dpe_delivery_snapshot snapshot(struct delivery_probe *p) {
  struct dpe_delivery_snapshot value = {0};
  assert(dpe_delivery_inspect(p, &value));
  return value;
}
static bool timeout(const void *value) {
  uint64_t tag;
  memcpy(&tag, value, 8);
  return tag == UINT64_MAX && !((const uint8_t *)value)[8];
}
static void *fake_read(void *value) { return value; }
static void fake_write(void *value, void *frame) {
  assert(value == &buffer || value == &other_buffer);
  assert(frame == value);
  writes++;
}
static bool fake_overdue(void *tracker) {
  assert(tracker == &capture);
  overdue_calls++;
  return false;
}
static void *fake_receive(void *result, void *receiver, uint64_t seconds, uint32_t nanos) {
  assert(receiver == &capture);
  pthread_mutex_lock(&fixture_lock);
  receive_calls++;
  last_seconds = seconds;
  last_nanos = nanos;
  if (signal_publisher && receive_calls == 1) {
    publish_requested = true;
    pthread_cond_broadcast(&fixture_changed);
    while (!publish_ready)
      pthread_cond_wait(&fixture_changed, &fixture_lock);
  }
  if (second_poll_message && receive_calls == 2)
    messages++;
  if (seconds || nanos) {
    struct timespec deadline;
    assert(!clock_gettime(CLOCK_REALTIME, &deadline));
    deadline.tv_sec++;
    while (!messages && !stopped)
      assert(!pthread_cond_timedwait(&fixture_changed, &fixture_lock, &deadline));
  }
  uint64_t tag = messages || stopped ? 7 : UINT64_MAX;
  if (messages)
    messages--;
  memcpy(result, &tag, 8);
  ((uint8_t *)result)[8] = 0;
  pthread_mutex_unlock(&fixture_lock);
  return result;
}
static void recipient(void *frame, uint32_t status, const char *error, void *data) {
  assert(!frame && !status && !error && !data);
}
static void *fake_clone(void *frame, const uint8_t **bytes, size_t *size, size_t *y, size_t *uv,
                        size_t *u, size_t *v) {
  (void)frame;
  (void)bytes;
  (void)size;
  (void)y;
  (void)uv;
  (void)u;
  (void)v;
  abort();
}
static void fake_release(void *value) { assert(!value); }
static int32_t fake_update(void *value, const struct capture_options *options) {
  assert(value == &capture && options->frame_handler == dpe_delivery_frame);
  return 0;
}
static int32_t fake_new(const struct capture_options *options, void **output) {
  close(options->config->fd);
  *output = &capture;
  assert(options->frame_handler == delivery_frame);
  return 0;
}
static bool fixture_event_sent;
static napi_status fixture_enqueue(napi_threadsafe_function fn, void *message,
                                   napi_threadsafe_function_call_mode mode) {
  (void)fn;
  (void)mode;
  fixture_event_sent = true;
  free(message);
  return napi_ok;
}
static struct delivery_probe *create(void) {
  struct pipewire_config config;
  fixture_select(&config);
  struct capture_options options = {
      .frame_rate = 60, .frame_handler = recipient, .config = &config};
  void *output = NULL;
  assert(!capture_new(&options, &output) && output == &capture);
  struct delivery_probe *p = find(0)->delivery;
  assert(p && snapshot(p).events && fixture_event_sent);
  assert(delivery_read(&buffer) == &buffer);
  delivery_frame(NULL, 0, NULL, p);
  return p;
}
static void receive_frame(void) {
  uint8_t result[64] = {0};
  assert(delivery_receive(result, &capture, 0, 4166666) == result && timeout(result));
  assert(delivery_overdue(&capture) && !delivery_overdue(&capture));
}
static void *publisher(void *unused) {
  (void)unused;
  pthread_mutex_lock(&fixture_lock);
  while (!publish_requested)
    pthread_cond_wait(&fixture_changed, &fixture_lock);
  pthread_mutex_unlock(&fixture_lock);
  delivery_write(&buffer, &buffer);
  pthread_mutex_lock(&fixture_lock);
  publish_ready = true;
  pthread_cond_broadcast(&fixture_changed);
  pthread_mutex_unlock(&fixture_lock);
  return NULL;
}
static void *wait_for_stop(void *data) {
  assert(delivery_read(&buffer) == &buffer);
  delivery_frame(NULL, 0, NULL, data);
  receive_frame();
  pthread_mutex_lock(&fixture_lock);
  worker_ready = true;
  pthread_cond_broadcast(&fixture_changed);
  pthread_mutex_unlock(&fixture_lock);
  uint8_t result[64] = {0};
  delivery_receive(result, &capture, 0, 4166666);
  assert(!timeout(result));
  return NULL;
}
static int32_t fake_destroy(void *value) {
  assert(value == &capture);
  pthread_mutex_lock(&fixture_lock);
  stopped = true;
  pthread_cond_broadcast(&fixture_changed);
  pthread_mutex_unlock(&fixture_lock);
  assert(!pthread_join(worker, NULL));
  return 0;
}
int main(void) {
  unsetenv("DPE_CAPTURE_DELIVERY_FAST");
  unsetenv("DPE_CAPTURE_DELIVERY_PROBE");
  unsetenv("DPE_CAPTURE_DELIVERY_EVENT");
  installed_wake = true;
  original_read = fake_read;
  original_write = fake_write;
  original_receive = fake_receive;
  original_overdue = fake_overdue;
  original_destroy = fake_destroy;
  original_new = fake_new;
  api_napi_call_threadsafe_function = fixture_enqueue;
  clone_i420 = fake_clone;
  release_i420 = release_frame = fake_release;
  assert(dpe_delivery_configure_wake(original_read, original_write, original_receive,
                                     original_overdue));
  // Preparing originals must not activate an uncommitted/failed redirect group.
  struct capture_options uncommitted = {.frame_rate = 60, .frame_handler = recipient};
  struct delivery_probe *inactive =
      dpe_delivery_create(99, &uncommitted, true,
                          (struct dpe_delivery_functions){clone_i420, release_i420, release_frame});
  assert(inactive && !snapshot(inactive).events);
  delivery_destroy(inactive);
  fixture_configure();
  assert(dpe_delivery_enable_wake());
  uint8_t result[64] = {0};
  messages = 1;
  delivery_receive(result, &capture, 2, 123);
  assert(!timeout(result) && last_seconds == 2 && last_nanos == 123);
  struct delivery_probe *p = create();
  receive_frame();
  uint64_t serial = snapshot(p).wake_serial;
  delivery_write(&other_buffer, &other_buffer);
  assert(snapshot(p).wake_serial == serial);
  for (unsigned i = 0; i < 20; i++)
    delivery_write(&buffer, &buffer);
  receive_frame(); // A burst advances one receive/overdue, with no queued credits.
  delivery_write(&buffer, &buffer);
  messages = 1;
  delivery_receive(result, &capture, 0, 4166666);
  assert(!timeout(result) && !delivery_overdue(&capture));
  receive_frame(); // Native control did not consume the concurrent frame wake.
  receive_calls = 0;
  signal_publisher = true;
  assert(!pthread_create(&worker, NULL, publisher, NULL));
  receive_frame();
  assert(!pthread_join(worker, NULL));
  signal_publisher = false;
  delivery_write(&buffer, &buffer);
  receive_calls = 0;
  second_poll_message = true;
  delivery_receive(result, &capture, 0, 4166666);
  assert(!timeout(result) && !delivery_overdue(&capture));
  second_poll_message = false;
  receive_frame(); // A message in the second native poll also leaves the frame pending.
  receive_frame(); // Deadline-only heartbeat, without a new publication.
  struct capture_options options = {.frame_rate = 60, .frame_handler = recipient};
  setenv("DPE_CAPTURE_DELIVERY_EVENT", "0", 1);
  assert(!dpe_delivery_update(p, &capture, &options, fake_update));
  messages = 1;
  delivery_receive(result, &capture, 2, 321);
  assert(!timeout(result) && last_seconds == 2 && last_nanos == 321);
  unsetenv("DPE_CAPTURE_DELIVERY_EVENT");
  assert(!dpe_delivery_update(p, &capture, &options, fake_update));
  assert(!pthread_create(&worker, NULL, wait_for_stop, p));
  pthread_mutex_lock(&fixture_lock);
  while (!worker_ready)
    pthread_cond_wait(&fixture_changed, &fixture_lock);
  pthread_mutex_unlock(&fixture_lock);
  assert(!capture_destroy(&capture));
  struct dpe_delivery_snapshot retired = {0};
  assert(!dpe_delivery_inspect(p, &retired));
  assert(writes >= 23 && overdue_calls);
  puts("Rust publication wake races, native-message priority, heartbeat and shutdown passed");
}
