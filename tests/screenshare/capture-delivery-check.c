// Exercise the actual picker callback wrapper using owned opaque C frames and
// retained staging leases. No Rust layout, GPU, display or Discord is involved.
#include "../../integration/video/capture-picker.c"
#include <assert.h>

struct buffer {
  uint8_t bytes[8];
  unsigned leases;
};
struct frame {
  struct buffer *buffer;
};
static struct buffer a, b;
static unsigned delivered, destroyed, cloned, released, errors, empty;
static struct capture_options native_options;
static pthread_t worker;
static bool join_worker, fail_new, fail_update;
static unsigned events_sent;
static void *expected_data = (void *)123;

static void *new_frame(struct buffer *buffer) {
  struct frame *frame = malloc(sizeof(*frame));
  assert(frame);
  frame->buffer = buffer;
  return frame;
}
static void frame_destroy(void *opaque) {
  if (opaque) {
    destroyed++;
    free(opaque);
  }
}
static void *frame_clone(void *opaque, const uint8_t **data, size_t *size, size_t *stride_y,
                         size_t *stride_uv, size_t *u, size_t *v) {
  struct buffer *buffer = ((struct frame *)opaque)->buffer;
  if (!buffer)
    return NULL;
  struct buffer **lease = malloc(sizeof(*lease));
  assert(lease);
  *lease = buffer;
  buffer->leases++;
  cloned++;
  *data = buffer->bytes;
  *size = sizeof(buffer->bytes);
  *stride_y = 2;
  *stride_uv = 1;
  *u = 4;
  *v = 5;
  return lease;
}
static void lease_destroy(void *opaque) {
  struct buffer **lease = opaque;
  assert((*lease)->leases);
  (*lease)->leases--;
  released++;
  free(lease);
}
static void recipient(void *frame, uint32_t status, const char *error, void *data) {
  assert(data == expected_data);
  delivered++;
  if (status) {
    assert(status == 17 && error && !strcmp(error, "synthetic"));
    errors++;
  }
  empty += !frame;
  frame_destroy(frame);
}
static int32_t native_new(const struct capture_options *options, void **capture) {
  if (fail_new) {
    *capture = NULL;
    return -EIO;
  }
  native_options = *options;
  close(options->config->fd);
  *capture = &native_options;
  return 0;
}
static int32_t native_update(void *capture, const struct capture_options *options) {
  assert(capture == &native_options);
  if (fail_update)
    return -EIO;
  native_options = *options;
  return 0;
}
static int32_t native_destroy(void *capture) {
  assert(capture == &native_options);
  if (join_worker) {
    assert(!pthread_join(worker, NULL));
    join_worker = false;
  }
  return 0;
}
static void *deliver_worker(void *unused) {
  (void)unused;
  native_options.frame_handler(new_frame(&b), 0, NULL, native_options.data);
  return NULL;
}
static napi_status record_event(napi_threadsafe_function function, void *message,
                                napi_threadsafe_function_call_mode mode) {
  (void)function;
  (void)mode;
  events_sent++;
  free(message);
  return napi_ok;
}
static void remote_config(struct capture_options *options, struct pipewire_config *config) {
  int fds[2];
  assert(!socketpair(AF_UNIX, SOCK_STREAM, 0, fds));
  close(fds[1]);
  *config = (struct pipewire_config){fds[0], 42};
  options->config = config;
}
static struct delivery_probe *start(struct capture_options *options, void **capture,
                                    struct pipewire_config *config) {
  remote_config(options, config);
  struct stat stat;
  assert(!fstat(config->fd, &stat));
  requests[0] = (struct request){.state = SELECTED,
                                 .id = 1,
                                 .fd = config->fd,
                                 .node = 42,
                                 .dev = stat.st_dev,
                                 .ino = stat.st_ino};
  assert(!capture_new(options, capture));
  assert(requests[0].state == ACTIVE);
  return requests[0].delivery;
}
static void unknown_cadence(struct capture_options options, unsigned rate) {
  options.frame_rate = rate;
  struct delivery_probe *p = delivery_create(100, &options, false);
  assert(p && p->fast && p->requested_rate == rate);
  p->log = false;
  const uint64_t base = 10000000000ull, period = 1000000000ull / rate;
  uint64_t last_slot = UINT64_MAX;
  for (unsigned i = 0; i < 2400; i++) {
    // Alternating lateness crosses the target boundaries; elapsed-from-last
    // pacing loses frames here even though every nominal frame slot is polled.
    uint64_t now = base + (uint64_t)i * 1000000000ull / 240 + (i % 3) * 200000;
    uint64_t before = p->forwarded;
    delivery_frame_at(new_frame(NULL), 0, NULL, p, now);
    if (p->forwarded != before) {
      uint64_t slot = (now - base) / period;
      assert(last_slot == UINT64_MAX || slot > last_slot);
      last_slot = slot;
    }
    // At most one output per target slot; callback jitter creates no credit.
    assert(p->forwarded <= (now - base) / period + 1);
  }
  assert(p->forwarded == rate * 10 && p->unknown == 2400);
  // A long suspension drops missed slots. Repeated callbacks at the same time
  // cannot repay that backlog or exceed the requested rate after resumption.
  uint64_t resumed = base + 12000000000ull, before = p->forwarded;
  delivery_frame_at(new_frame(NULL), 0, NULL, p, resumed);
  for (unsigned i = 0; i < 32; i++)
    delivery_frame_at(new_frame(NULL), 0, NULL, p, resumed + i);
  assert(p->forwarded == before + 1 && p->next_unknown_ns > resumed + 31);
  delivery_destroy(p);
}
int main(void) {
  original_new = native_new;
  original_update = native_update;
  original_destroy = native_destroy;
  clone_i420 = frame_clone;
  release_i420 = lease_destroy;
  release_frame = frame_destroy;
  struct capture_options options = {
      .frame_rate = 60, .frame_handler = recipient, .data = expected_data};
  struct pipewire_config config;
  void *capture = NULL;

  unsetenv("DPE_CAPTURE_DELIVERY_PROBE");
  setenv("DPE_CAPTURE_DELIVERY_FAST", "0", 1);
  assert(!start(&options, &capture, &config));
  assert(native_options.frame_handler == recipient && native_options.frame_rate == 60);
  assert(!capture_destroy(capture));

  // Selected custom sources use FAST by default without periodic/destroy logs.
  // Capture stderr to verify normal operation emits no diagnostic records.
  unsetenv("DPE_CAPTURE_DELIVERY_FAST");
  int logs[2], saved_stderr = dup(STDERR_FILENO);
  assert(saved_stderr >= 0 && !pipe(logs));
  assert(dup2(logs[1], STDERR_FILENO) >= 0);
  close(logs[1]);
  struct delivery_probe *p = start(&options, &capture, &config);
  assert(p && p->auto_fast && p->fast && !p->log && native_options.frame_rate == 240);
  delivery_frame_at(new_frame(&a), 0, NULL, p, 1000000000);
  delivery_frame_at(new_frame(&a), 0, NULL, p, 1004000000);
  assert(p->forwarded == 1 && p->dropped == 1);
  options.frame_rate = 30;
  assert(!capture_update(capture, &options));
  assert(p->fast && !p->log && p->requested_rate == 30 && native_options.frame_rate == 240);
  options.frame_rate = 60;
  assert(!capture_update(capture, &options));
  assert(p->fast && !p->log && p->requested_rate == 60 && native_options.frame_rate == 240);
  assert(!capture_destroy(capture));
  assert(dup2(saved_stderr, STDERR_FILENO) >= 0);
  close(saved_stderr);
  char unexpected;
  assert(read(logs[0], &unexpected, 1) == 0);
  close(logs[0]);
  assert(!a.leases && cloned == released);

  setenv("DPE_CAPTURE_DELIVERY_PROBE", "1", 1);
  p = start(&options, &capture, &config);
  assert(p && !p->fast && p->log && native_options.frame_rate == 60);
  delivery_frame_at(new_frame(&a), 0, NULL, p, 1000000000);
  assert(a.leases == 1 && p->fresh == 1);
  delivery_frame_at(new_frame(&a), 0, NULL, p, 1016000000);
  assert(a.leases == 1 && p->duplicate == 1 && p->forwarded == 2);
  // The new pointer is acquired while A is still retained. A can only become
  // reusable after B replaces its lease, so reusing A next is fresh, not ABA.
  delivery_frame_at(new_frame(&b), 0, NULL, p, 1032000000);
  assert(!a.leases && b.leases == 1 && p->fresh == 2);
  delivery_frame_at(new_frame(&a), 0, NULL, p, 1048000000);
  assert(a.leases == 1 && !b.leases && p->fresh == 3);
  delivery_frame_at(new_frame(NULL), 0, NULL, p, 1064000000);
  assert(p->unknown == 1 && p->forwarded == 5 && !p->dropped);
  assert(!capture_destroy(capture));
  assert(!a.leases && !b.leases && cloned == released);

  setenv("DPE_CAPTURE_DELIVERY_FAST", "1", 1);
  p = start(&options, &capture, &config);
  assert(p && p->fast && native_options.frame_rate == 240 && options.frame_rate == 60);
  delivery_frame_at(new_frame(&a), 0, NULL, p, 2000000000);
  unsigned before = delivered, destroyed_before = destroyed;
  delivery_frame_at(new_frame(&a), 0, NULL, p, 2004000000);
  assert(delivered == before && destroyed == destroyed_before + 1 && p->dropped == 1);
  delivery_frame_at(new_frame(&b), 0, NULL, p, 2008000000);
  assert(delivered == before + 1 && p->fresh == 2);
  delivery_frame_at(new_frame(&b), 0, NULL, p, 2508000000);
  assert(p->heartbeats == 1 && p->forwarded == 3);
  delivery_frame_at(new_frame(NULL), 0, NULL, p, 2512000000);
  assert(p->unknown == 1 && p->dropped == 2);
  delivery_frame_at(new_frame(NULL), 0, NULL, p, 2525000000);
  assert(p->unknown == 2 && p->forwarded == 4);
  delivery_frame_at(NULL, 17, "synthetic", p, 2525000001);
  delivery_frame_at(new_frame(&a), 17, "synthetic", p, 2525000002);
  assert(errors == 2 && empty == 1);

  options.frame_rate = 30;
  options.width = 1280;
  options.height = 720;
  expected_data = options.data = (void *)456;
  assert(!capture_update(capture, &options));
  assert(native_options.frame_rate == 240 && p->requested_rate == 30 && p->fast);
  assert(native_options.width == 1280 && native_options.height == 720);
  assert(native_options.frame_handler == delivery_frame && native_options.data == p);
  delivery_frame_at(new_frame(&a), 0, NULL, p, 2530000000);
  before = delivered;
  delivery_frame_at(new_frame(NULL), 0, NULL, p, 2550000000);
  assert(delivered == before);
  delivery_frame_at(new_frame(NULL), 0, NULL, p, 2564000000);
  assert(delivered == before + 1);
  options.frame_rate = 60;
  assert(!capture_update(capture, &options));
  assert(native_options.frame_rate == 240 && p->requested_rate == 60 && p->fast);
  delivery_frame_at(new_frame(NULL), 0, NULL, p, 2581000000);
  assert(delivered == before + 2);
  fail_update = true;
  struct capture_options rejected = options;
  rejected.frame_rate = 30;
  rejected.data = (void *)789;
  assert(capture_update(capture, &rejected) == -EIO);
  assert(p->requested_rate == 60 && p->data == expected_data && p->fast);
  fail_update = false;
  options.frame_rate = 0;
  assert(!capture_update(capture, &options) && !p->fast && !native_options.frame_rate);
  delivery_frame_at(new_frame(NULL), 0, NULL, p, 2581000001);
  // Environment cleanup does not own a running native callback's lifetime.
  cleanup(NULL);
  assert(requests[0].delivery == p && p->lease);
  assert(!pthread_create(&worker, NULL, deliver_worker, NULL));
  join_worker = true;
  assert(!capture_destroy(capture));
  assert(!a.leases && !b.leases && cloned == released && requests[0].state == EMPTY);

  // An ordinary portal capture has no selected request. With no opt-in it stays
  // completely unwrapped; enabling the probe never takes its remote FD ownership.
  closing = false;
  options.frame_rate = 60;
  unsetenv("DPE_CAPTURE_DELIVERY_PROBE");
  unsetenv("DPE_CAPTURE_DELIVERY_FAST");
  remote_config(&options, &config);
  assert(!capture_new(&options, &capture));
  assert(native_options.frame_handler == recipient && requests[0].state == EMPTY);
  assert(!capture_destroy(capture));

  setenv("DPE_CAPTURE_DELIVERY_PROBE", "1", 1);
  dispatcher = (void *)1;
  api_napi_call_threadsafe_function = record_event;
  remote_config(&options, &config);
  fail_new = true;
  assert(capture_new(&options, &capture) == -EIO && !capture);
  assert(requests[0].state == EMPTY && !events_sent);
  assert(fcntl(config.fd, F_GETFD) >= 0); // Failed native owner still owns its FD.
  close(config.fd);
  fail_new = false;
  remote_config(&options, &config);
  assert(!capture_new(&options, &capture));
  p = requests[0].delivery;
  assert(p && !p->fast && requests[0].delivery_only && requests[0].fd == -1);
  assert(native_options.frame_rate == 60 && native_options.frame_handler == delivery_frame);
  delivery_frame_at(new_frame(&a), 0, NULL, p, 3000000000);
  delivery_frame_at(new_frame(&a), 0, NULL, p, 3004000000);
  assert(p->forwarded == 2 && p->duplicate == 1 && !p->dropped);
  assert(!capture_destroy(capture) && !events_sent && cloned == released);

  setenv("DPE_CAPTURE_DELIVERY_FAST", "1", 1);
  remote_config(&options, &config);
  assert(!capture_new(&options, &capture));
  p = requests[0].delivery;
  assert(p && p->fast && native_options.frame_rate == 240 && requests[0].delivery_only);
  delivery_frame_at(new_frame(&a), 0, NULL, p, 4000000000);
  delivery_frame_at(new_frame(&a), 0, NULL, p, 4004000000);
  assert(p->forwarded == 1 && p->dropped == 1);
  delivery_frame_at(new_frame(&a), 0, NULL, p, 4500000000);
  assert(p->forwarded == 2 && p->heartbeats == 1);
  options.frame_rate = 30;
  assert(!capture_update(capture, &options));
  assert(p->requested_rate == 30 && native_options.frame_rate == 240);
  options.frame_rate = 60;
  assert(!capture_update(capture, &options));
  assert(p->requested_rate == 60 && native_options.frame_rate == 240);
  assert(!events_sent);
  dispatcher = NULL;
  cleanup(NULL);
  assert(requests[0].delivery == p && a.leases == 1);
  assert(!pthread_create(&worker, NULL, deliver_worker, NULL));
  join_worker = true;
  assert(!capture_destroy(capture));
  assert(!a.leases && !b.leases && cloned == released && requests[0].state == EMPTY);
  unknown_cadence(options, 60);
  unknown_cadence(options, 30);
  puts("delivery identity leases, duplicate disposal, fallback cadence, heartbeat, update and "
       "joined shutdown for selected and ordinary portal captures passed");
  return 0;
}
