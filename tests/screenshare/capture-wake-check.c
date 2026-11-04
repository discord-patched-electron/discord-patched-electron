// The real publication/receive wrappers with native-owned opaque result storage.
// Exercise races and shutdown with pthreads, without a GPU or Rust allocations.
#include "../../integration/video/capture-picker.c"
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
static void *fake_read(void *value) { return value; }
static void fake_write(void *value, void *frame) {
  assert(value == &buffer || value == &other_buffer);
  assert(frame == value);
  ++writes;
}
static bool fake_overdue(void *tracker) {
  assert(tracker == &capture);
  ++overdue_calls;
  return false;
}
static void *fake_receive(void *result, void *receiver, uint64_t seconds, uint32_t nanos) {
  assert(receiver == &capture);
  pthread_mutex_lock(&fixture_lock);
  ++receive_calls;
  last_seconds = seconds;
  last_nanos = nanos;
  if (signal_publisher && receive_calls == 1) {
    publish_requested = true;
    pthread_cond_broadcast(&fixture_changed);
    // Force publication exactly after the first poll, before its caller waits.
    while (!publish_ready)
      pthread_cond_wait(&fixture_changed, &fixture_lock);
  }
  if (second_poll_message && receive_calls == 2)
    ++messages;
  if (seconds || nanos) {
    struct timespec deadline;
    assert(!clock_gettime(CLOCK_REALTIME, &deadline));
    ++deadline.tv_sec;
    while (!messages && !stopped)
      assert(!pthread_cond_timedwait(&fixture_changed, &fixture_lock, &deadline));
  }
  uint64_t tag = messages || stopped ? 7 : UINT64_MAX;
  if (messages)
    --messages;
  memcpy(result, &tag, sizeof(tag));
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
static void fake_release(void *frame) { assert(!frame); }
static struct delivery_probe *create(void) {
  struct capture_options options = {.frame_rate = 60, .frame_handler = recipient};
  struct delivery_probe *p = delivery_create(1, &options, true);
  assert(p && p->events);
  requests[0] = (struct request){.state = ACTIVE, .capture = &capture, .delivery = p};
  assert(delivery_read(&buffer) == &buffer);
  delivery_frame(NULL, 0, NULL, p);
  return p;
}
static void receive_frame(void) {
  uint8_t result[64] = {0};
  assert(delivery_receive(result, &capture, 0, 4166666) == result);
  assert(receive_timed_out(result));
  assert(delivery_overdue(&capture));
  assert(!delivery_overdue(&capture));
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
  struct delivery_probe *p = data;
  assert(delivery_read(&buffer) == &buffer);
  delivery_frame(NULL, 0, NULL, p);
  receive_frame(); // Initial bind rereads once to close read/bind publication race.
  pthread_mutex_lock(&fixture_lock);
  worker_ready = true;
  pthread_cond_broadcast(&fixture_changed);
  pthread_mutex_unlock(&fixture_lock);
  uint8_t result[64] = {0};
  delivery_receive(result, &capture, 0, 4166666);
  assert(!receive_timed_out(result));
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
  clone_i420 = fake_clone;
  release_i420 = release_frame = fake_release;

  // Before binding, even a native control result and its timeout arguments pass through.
  uint8_t result[64] = {0};
  messages = 1;
  delivery_receive(result, &capture, 2, 123);
  assert(!receive_timed_out(result) && last_seconds == 2 && last_nanos == 123);
  struct delivery_probe *p = create();
  receive_frame();
  uint64_t serial = p->wake_serial;
  delivery_write(&other_buffer, &other_buffer);
  assert(p->wake_serial == serial);
  for (unsigned i = 0; i < 20; ++i)
    delivery_write(&buffer, &buffer);
  receive_frame();
  assert(thread_frame_serial == p->frame_serial); // A burst is coalesced, never queued.

  delivery_write(&buffer, &buffer);
  serial = thread_frame_serial;
  messages = 1;
  delivery_receive(result, &capture, 0, 4166666);
  assert(!receive_timed_out(result) && thread_frame_serial == serial);
  receive_frame(); // The real native message did not consume an unread publication.

  receive_calls = 0;
  signal_publisher = true;
  assert(!pthread_create(&worker, NULL, publisher, NULL));
  receive_frame(); // Publication in the poll-to-wait window cannot be lost.
  assert(!pthread_join(worker, NULL));
  signal_publisher = false;

  delivery_write(&buffer, &buffer);
  receive_calls = 0;
  second_poll_message = true;
  serial = thread_frame_serial;
  delivery_receive(result, &capture, 0, 4166666);
  assert(!receive_timed_out(result) && thread_frame_serial == serial);
  second_poll_message = false;
  receive_frame(); // A message racing with wakeup also wins without losing that frame.

  thread_deadline = delivery_now() - 1;
  receive_frame(); // Static sources retain the heartbeat without idle polling.
  p->events = false;
  messages = 1;
  delivery_receive(result, &capture, 0, 987);
  assert(last_nanos == 987 && !receive_timed_out(result));
  p->events = true;

  // Destroy wakes a sleeping native capture thread before its stop-and-join.
  thread_delivery = NULL;
  thread_framebuffer = NULL;
  assert(!pthread_create(&worker, NULL, wait_for_stop, p));
  pthread_mutex_lock(&fixture_lock);
  while (!worker_ready)
    pthread_cond_wait(&fixture_changed, &fixture_lock);
  pthread_mutex_unlock(&fixture_lock);
  assert(!capture_destroy(&capture));
  assert(requests[0].state == EMPTY && writes == 24 && overdue_calls >= 6);
  puts("publication wake races, coalescing, control priority and stop/join passed");
  return 0;
}
