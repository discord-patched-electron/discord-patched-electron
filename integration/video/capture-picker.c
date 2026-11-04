// Reviewed native picker C ABI bridge for explicitly selected external nodes.
// Opt-in delivery diagnostics also wrap ordinary native PipeWire captures.
// No global portal replacement and no capture source is selected implicitly.
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <link.h>
#include <node_api.h>
#include <poll.h>
#include <pthread.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

struct pipewire_config {
  int32_t fd;
  uint32_t node;
};
struct picker_result {
  bool success;
  uint8_t padding[3];
  struct pipewire_config config;
  uint32_t padding2;
  const char *error;
};
typedef void (*picker_callback)(const struct picker_result *, const void *);
struct picker_options {
  uint32_t source_type, padding;
  picker_callback callback;
  const void *data;
};
typedef void (*frame_handler_fn)(void *, uint32_t, const char *, void *);
struct capture_options {
  uint32_t source_id, frame_rate, width, height;
  frame_handler_fn frame_handler;
  void *data;
  const struct pipewire_config *config;
};
_Static_assert(sizeof(struct picker_result) == 24 && offsetof(struct picker_result, config) == 4 &&
                   offsetof(struct picker_result, error) == 16,
               "reviewed picker result ABI");
_Static_assert(sizeof(struct picker_options) == 24 &&
                   offsetof(struct picker_options, callback) == 8,
               "reviewed picker options ABI");
_Static_assert(sizeof(struct capture_options) == 40 &&
                   offsetof(struct capture_options, config) == 32,
               "reviewed capture options ABI");
typedef int32_t (*show_fn)(const struct picker_options *);
typedef int32_t (*new_fn)(const struct capture_options *, void **);
typedef int32_t (*destroy_fn)(void *);
typedef int32_t (*update_fn)(void *, const struct capture_options *);
// The reviewed clone export supplies the data pointer and layout itself. The
// returned opaque lease retains the staging allocation; it does not copy pixels.
typedef void *(*clone_i420_fn)(void *, const uint8_t **, size_t *, size_t *, size_t *, size_t *,
                               size_t *);
typedef void (*release_frame_fn)(void *);
static show_fn original_show;
static new_fn original_new;
static destroy_fn original_destroy;
static update_fn original_update;
static clone_i420_fn clone_i420;
static release_frame_fn release_i420, release_frame;
static uintptr_t installed_slot;
static pthread_mutex_t mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t *picked_mutex;
static struct pipewire_config *picked_config;
static bool *picked_present;
_Static_assert(sizeof(pthread_mutex_t) == 40, "reviewed native selected-config mutex ABI");
static napi_threadsafe_function dispatcher;
static bool closing;
static uint32_t next_id, current_request;
enum request_state { EMPTY, WAITING, SELECTED, STARTING, ACTIVE, CANCELLED };
struct delivery_probe {
  pthread_mutex_t lock;
  pthread_mutex_t wake_lock;
  pthread_cond_t wake;
  void *framebuffer;
  uint64_t wake_serial, frame_serial;
  bool events, stopping;
  frame_handler_fn handler;
  void *data, *lease;
  const uint8_t *identity;
  uint32_t id, requested_rate;
  bool fast, auto_fast, log;
  uint64_t callbacks, fresh, duplicate, unknown, forwarded, dropped, heartbeats;
  uint64_t last_forward_ns, last_report_ns, next_unknown_ns;
};
struct request {
  enum request_state state;
  bool delivery_only;
  uint32_t id;
  struct picker_options options;
  int fd;
  uint32_t node;
  dev_t dev;
  ino_t ino;
  void *capture;
  struct delivery_probe *delivery;
};
#define REQUESTS 32
static struct request requests[REQUESTS];
// Exact scoped Rust functions below use only opaque pointers/scalars. The
// receiver preserves its own Result storage; only its reviewed timeout tag is
// inspected. No Rust object is constructed, copied, or mutated by this bridge.
typedef void *(*buffer_read_fn)(void *);
typedef void (*buffer_write_fn)(void *, void *);
typedef void *(*receive_fn)(void *, void *, uint64_t, uint32_t);
typedef bool (*overdue_fn)(void *);
static buffer_read_fn original_read;
static buffer_write_fn original_write;
static receive_fn original_receive;
static overdue_fn original_overdue;
static bool installed_wake;
static _Thread_local struct delivery_probe *thread_delivery;
static _Thread_local void *thread_framebuffer;
static _Thread_local uint64_t thread_serial, thread_frame_serial, thread_deadline;
static _Thread_local bool thread_force_frame;

static uint64_t delivery_now(void) {
  struct timespec time;
  return !clock_gettime(CLOCK_MONOTONIC, &time)
             ? (uint64_t)time.tv_sec * 1000000000ull + time.tv_nsec
             : 0;
}
static bool delivery_enabled(const char *name) {
  const char *value = getenv(name);
  return value && !strcmp(value, "1");
}
static bool delivery_fast_enabled(bool auto_fast) {
  const char *value = getenv("DPE_CAPTURE_DELIVERY_FAST");
  // An explicit probe is transparent unless FAST=1 is also supplied. Any
  // explicit FAST value other than 1 disables automatic selected-source mode.
  return value ? !strcmp(value, "1") : auto_fast && !delivery_enabled("DPE_CAPTURE_DELIVERY_PROBE");
}
static bool delivery_events_enabled(bool fast) {
  const char *value = getenv("DPE_CAPTURE_DELIVERY_EVENT");
  return installed_wake && fast && (!value || strcmp(value, "0"));
}
static void delivery_wake(struct delivery_probe *p, bool stop) {
  pthread_mutex_lock(&p->wake_lock);
  p->stopping |= stop;
  ++p->wake_serial;
  pthread_cond_signal(&p->wake);
  pthread_mutex_unlock(&p->wake_lock);
}
static void delivery_bind(struct delivery_probe *p) {
  if (!installed_wake || !thread_framebuffer)
    return;
  pthread_mutex_lock(&p->wake_lock);
  if (thread_delivery != p || p->framebuffer != thread_framebuffer) {
    p->framebuffer = thread_framebuffer;
    thread_delivery = p;
    thread_serial = p->wake_serial++;
    thread_frame_serial = p->frame_serial++;
    thread_deadline = delivery_now() + 500000000ull;
  }
  pthread_mutex_unlock(&p->wake_lock);
}
static void *delivery_read(void *buffer) {
  thread_framebuffer = buffer;
  return original_read(buffer);
}
static void delivery_write(void *buffer, void *frame) {
  original_write(buffer, frame); // Publish fully before making its reader runnable.
  pthread_mutex_lock(&mutex);
  for (unsigned i = 0; i < REQUESTS; ++i) {
    struct delivery_probe *p = requests[i].delivery;
    if (!p)
      continue;
    pthread_mutex_lock(&p->wake_lock);
    if (p->events && !p->stopping && p->framebuffer == buffer) {
      ++p->frame_serial;
      ++p->wake_serial;
      pthread_cond_signal(&p->wake);
    }
    pthread_mutex_unlock(&p->wake_lock);
  }
  pthread_mutex_unlock(&mutex);
}
static bool receive_timed_out(const void *result) {
  uint64_t tag;
  memcpy(&tag, result, sizeof(tag));
  return tag == UINT64_MAX && ((const uint8_t *)result)[8] == 0;
}
static void *delivery_receive(void *result, void *receiver, uint64_t seconds, uint32_t nanos) {
  struct delivery_probe *p = thread_delivery;
  thread_force_frame = false;
  if (!p)
    return original_receive(result, receiver, seconds, nanos);
  pthread_mutex_lock(&p->wake_lock);
  bool events = p->events && !p->stopping && p->framebuffer == thread_framebuffer;
  pthread_mutex_unlock(&p->wake_lock);
  if (!events)
    return original_receive(result, receiver, seconds, nanos);

  // Native stop/config messages always win. Timeout carries no owned payload,
  // so reusing its output storage for a second zero-time receive is safe.
  original_receive(result, receiver, 0, 0);
  if (!receive_timed_out(result))
    return result;
  pthread_mutex_lock(&p->wake_lock);
  int wait_status = 0;
  while (p->events && !p->stopping && p->wake_serial == thread_serial && !wait_status) {
    struct timespec deadline = {.tv_sec = thread_deadline / 1000000000ull,
                                .tv_nsec = thread_deadline % 1000000000ull};
    wait_status = pthread_cond_timedwait(&p->wake, &p->wake_lock, &deadline);
  }
  uint64_t serial = p->wake_serial, frame_serial = p->frame_serial;
  bool stopping = p->stopping, enabled = p->events;
  pthread_mutex_unlock(&p->wake_lock);
  if (stopping || !enabled || (wait_status && wait_status != ETIMEDOUT))
    return original_receive(result, receiver, seconds, nanos);
  original_receive(result, receiver, 0, 0);
  if (receive_timed_out(result)) {
    thread_force_frame = frame_serial != thread_frame_serial || wait_status == ETIMEDOUT;
    thread_serial = serial;
    thread_frame_serial = frame_serial;
    if (thread_force_frame)
      thread_deadline = delivery_now() + 500000000ull;
  }
  // A concurrently queued native message leaves publication counters unread;
  // the next receive still sees that frame after native processes the message.
  return result;
}
static bool delivery_overdue(void *tracker) {
  if (thread_force_frame) {
    thread_force_frame = false;
    return true;
  }
  return original_overdue(tracker);
}
static void delivery_report(const struct delivery_probe *p) {
  if (!p->log)
    return;
  fprintf(
      stderr,
      "[DPE capture delivery] {\"requestId\":%u,\"fast\":%s,\"eventDriven\":%s,\"requestedFps\":%u,"
      "\"callbacks\":%llu,\"fresh\":%llu,\"duplicate\":%llu,\"unknown\":%llu,"
      "\"forwarded\":%llu,\"dropped\":%llu,\"heartbeats\":%llu}\n",
      p->id, p->fast ? "true" : "false", p->events ? "true" : "false", p->requested_rate,
      (unsigned long long)p->callbacks, (unsigned long long)p->fresh,
      (unsigned long long)p->duplicate, (unsigned long long)p->unknown,
      (unsigned long long)p->forwarded, (unsigned long long)p->dropped,
      (unsigned long long)p->heartbeats);
}
static bool delivery_unknown_due(struct delivery_probe *p, uint64_t now) {
  uint64_t period = 1000000000ull / p->requested_rate;
  if (!p->next_unknown_ns || now < p->last_forward_ns)
    p->next_unknown_ns =
        p->last_forward_ns && now >= p->last_forward_ns ? p->last_forward_ns + period : now;
  if (now < p->next_unknown_ns)
    return false;
  // Keep the original phase when polling jitters. Resetting from each actual
  // callback would repeatedly miss a 240-Hz tick and turn requested60 into48.
  // Missed slots expire; no stored credit is replayed after a scheduler stall.
  p->next_unknown_ns += ((now - p->next_unknown_ns) / period + 1) * period;
  return true;
}
static void delivery_frame_at(void *frame, uint32_t status, const char *error, void *opaque,
                              uint64_t now) {
  struct delivery_probe *p = opaque;
  delivery_bind(p);
  pthread_mutex_lock(&p->lock);
  frame_handler_fn handler = p->handler;
  void *data = p->data;
  bool forward = true;
  if (frame && !status) {
    const uint8_t *identity = NULL;
    size_t size = 0, stride_y = 0, stride_uv = 0, u = 0, v = 0;
    void *lease = clone_i420(frame, &identity, &size, &stride_y, &stride_uv, &u, &v);
    bool known = lease && identity && size;
    bool duplicate = known && identity == p->identity;
    p->callbacks++;
    if (known) {
      p->duplicate += duplicate;
      p->fresh += !duplicate;
      // Acquire the new lease before releasing the old one: the pool cannot
      // recycle its address and make a fresh frame look identical (ABA).
      if (p->lease)
        release_i420(p->lease);
      p->lease = lease;
      p->identity = identity;
    } else {
      p->unknown++;
      if (lease)
        release_i420(lease);
    }
    if (p->fast && now) {
      if (duplicate && p->last_forward_ns && now >= p->last_forward_ns) {
        forward = now - p->last_forward_ns >= 500000000ull;
        p->heartbeats += forward;
      } else if (!known) {
        // CPU fallback cannot be identified by this GPU staging API. Keep its
        // original cadence instead of flooding downstream with 240 FPS.
        forward = delivery_unknown_due(p, now);
      }
    }
    if (forward) {
      p->forwarded++;
      p->last_forward_ns = now;
      if (known && p->fast && now)
        p->next_unknown_ns = now + 1000000000ull / p->requested_rate;
    } else {
      p->dropped++;
    }
    if (p->log && now && (!p->last_report_ns || now - p->last_report_ns >= 2000000000ull)) {
      delivery_report(p);
      p->last_report_ns = now;
    }
  }
  pthread_mutex_unlock(&p->lock);
  // The reviewed callback transfers ownership of frame to the recipient.
  // Errors and empty notifications retain their original timing and payload.
  if (forward)
    handler(frame, status, error, data);
  else
    release_frame(frame);
}
static void delivery_frame(void *frame, uint32_t status, const char *error, void *opaque) {
  delivery_frame_at(frame, status, error, opaque, delivery_now());
}
static struct delivery_probe *delivery_create(uint32_t id, const struct capture_options *options,
                                              bool auto_fast) {
  bool fast = delivery_fast_enabled(auto_fast);
  if ((!fast && !delivery_enabled("DPE_CAPTURE_DELIVERY_PROBE")) || !options->frame_handler ||
      !clone_i420 || !release_i420 || !release_frame)
    return NULL;
  struct delivery_probe *p = calloc(1, sizeof(*p));
  if (!p)
    return NULL;
  if (pthread_mutex_init(&p->lock, NULL)) {
    free(p);
    return NULL;
  }
  if (pthread_mutex_init(&p->wake_lock, NULL)) {
    pthread_mutex_destroy(&p->lock);
    free(p);
    return NULL;
  }
  pthread_condattr_t attributes;
  bool attributes_ready = pthread_condattr_init(&attributes) == 0;
  bool ready = attributes_ready && !pthread_condattr_setclock(&attributes, CLOCK_MONOTONIC) &&
               !pthread_cond_init(&p->wake, &attributes);
  if (attributes_ready)
    pthread_condattr_destroy(&attributes);
  if (!ready) {
    pthread_mutex_destroy(&p->wake_lock);
    pthread_mutex_destroy(&p->lock);
    free(p);
    return NULL;
  }
  p->handler = options->frame_handler;
  p->data = options->data;
  p->id = id;
  p->auto_fast = auto_fast;
  p->log = delivery_enabled("DPE_CAPTURE_DELIVERY_FAST") ||
           delivery_enabled("DPE_CAPTURE_DELIVERY_PROBE");
  p->requested_rate = options->frame_rate;
  p->fast = fast && options->frame_rate && options->frame_rate < 240;
  p->events = delivery_events_enabled(p->fast);
  return p;
}
static void delivery_destroy(struct delivery_probe *p) {
  if (!p)
    return;
  // cc_linux_capture_destroy joins the capture thread before this runs. JS
  // environment cleanup must not free a probe belonging to an active capture.
  delivery_report(p);
  if (p->lease)
    release_i420(p->lease);
  if (thread_delivery == p) {
    thread_delivery = NULL;
    thread_framebuffer = NULL;
  }
  pthread_cond_destroy(&p->wake);
  pthread_mutex_destroy(&p->wake_lock);
  pthread_mutex_destroy(&p->lock);
  free(p);
}

#define API(name) static __typeof__(name) *api_##name
API(napi_get_cb_info);
API(napi_get_value_string_utf8);
API(napi_get_value_uint32);
API(napi_get_undefined);
API(napi_get_boolean);
API(napi_create_string_utf8);
API(napi_create_function);
API(napi_set_named_property);
API(napi_throw_error);
API(napi_create_threadsafe_function);
API(napi_call_threadsafe_function);
API(napi_release_threadsafe_function);
API(napi_unref_threadsafe_function);
API(napi_add_env_cleanup_hook);
API(napi_get_global);
API(napi_call_function);

static napi_value fail(napi_env env, const char *message) {
  api_napi_throw_error(env, NULL, message);
  return NULL;
}
static napi_value undefined(napi_env env) {
  napi_value value;
  api_napi_get_undefined(env, &value);
  return value;
}
static napi_value boolean(napi_env env, bool value) {
  napi_value result;
  api_napi_get_boolean(env, value, &result);
  return result;
}
static struct request *find(uint32_t id) {
  for (unsigned i = 0; i < REQUESTS; i++)
    if (requests[i].state != EMPTY && requests[i].id == id)
      return &requests[i];
  return NULL;
}
// Must be called under mutex. The TSFN is nonblocking and never calls JS inline.
static bool notify(const char *event, const struct request *request) {
  if (!dispatcher || closing || request->delivery_only)
    return false;
  char *message = malloc(192);
  if (!message)
    return false;
  snprintf(message, 192, "{\"event\":\"%s\",\"requestId\":%u,\"sourceType\":%u}", event,
           request->id, request->options.source_type);
  if (api_napi_call_threadsafe_function(dispatcher, message, napi_tsfn_nonblocking) != napi_ok) {
    free(message);
    return false;
  }
  return true;
}
static void deliver(napi_env env, napi_value callback, void *context, void *data) {
  (void)context;
  char *message = data;
  if (env && callback) {
    napi_value value, global, ignored;
    if (api_napi_create_string_utf8(env, message, NAPI_AUTO_LENGTH, &value) == napi_ok &&
        api_napi_get_global(env, &global) == napi_ok)
      api_napi_call_function(env, global, callback, 1, &value, &ignored);
  }
  free(message);
}
static bool same_fd(const struct request *r) {
  struct stat st;
  return r->fd >= 0 && !fstat(r->fd, &st) && st.st_dev == r->dev && st.st_ino == r->ino;
}
static void cancel_locked(struct request *r) {
  if (r->state == SELECTED) {
    // Upstream ReleasePickedStream only closes its portal session; it does not
    // clear the selected config. Match and clear our config under the same
    // pthread mutex used by Get/ClearPickedPipewireConfig before closing its FD.
    pthread_mutex_lock(picked_mutex);
    if (*picked_present && picked_config->fd == r->fd && picked_config->node == r->node)
      *picked_present = false;
    pthread_mutex_unlock(picked_mutex);
    if (same_fd(r))
      close(r->fd);
  }
  // Keep the cancelled fd/node tombstone until the next picker request. A
  // delayed native start must not consume a reused descriptor from this pick.
  r->state = CANCELLED;
  notify("cancelled", r);
}
static int32_t show_picker(const struct picker_options *options) {
  if (!options || !options->callback)
    return original_show(options);
  pthread_mutex_lock(&mutex);
  if (closing || !dispatcher) {
    pthread_mutex_unlock(&mutex);
    return original_show(options);
  }
  struct request *slot = NULL;
  for (unsigned i = 0; i < REQUESTS; i++) {
    struct request *r = &requests[i];
    if (r->state == CANCELLED)
      memset(r, 0, sizeof(*r));
    if (r->state == WAITING || r->state == SELECTED) {
      cancel_locked(r);
      // The upstream global picker handler was replaced already. Do not call
      // an old callback into the new handler during supersession.
      memset(r, 0, sizeof(*r));
    }
    if (!slot && r->state == EMPTY)
      slot = r;
  }
  if (!slot) {
    pthread_mutex_unlock(&mutex);
    return original_show(options);
  }
  do {
    next_id++;
  } while (!next_id || find(next_id));
  *slot = (struct request){.state = WAITING, .id = next_id, .options = *options, .fd = -1};
  current_request = slot->id;
  bool sent = notify("request", slot);
  if (!sent)
    memset(slot, 0, sizeof(*slot));
  pthread_mutex_unlock(&mutex);
  return sent ? 0 : original_show(options);
}
static int32_t capture_new(const struct capture_options *options, void **capture) {
  uint32_t id = 0;
  struct delivery_probe *delivery = NULL;
  pthread_mutex_lock(&mutex);
  if (options && options->config) {
    for (unsigned i = 0; i < REQUESTS; i++) {
      struct request *r = &requests[i];
      if ((r->state == SELECTED || r->state == CANCELLED) && options->config->fd == r->fd &&
          options->config->node == r->node) {
        if (r->state == CANCELLED || !same_fd(r)) {
          pthread_mutex_unlock(&mutex);
          if (capture)
            *capture = NULL;
          return -ECANCELED;
        }
        // Native new starts the thread which takes ownership later. Transfer
        // before calling it, so cancellation cannot close that live FD.
        r->state = STARTING;
        r->fd = -1;
        id = r->id;
        delivery = r->delivery = delivery_create(id, options, true);
        break;
      }
    }
    // Ordinary portal captures use the same reviewed Linux C ABI. Track only
    // their callback lifetime: the native owner keeps sole responsibility for
    // its portal session and remote FD, and no custom picker events are emitted.
    if (!id && !closing && options->config->fd >= 0 && options->config->node != UINT32_MAX) {
      for (unsigned i = 0; i < REQUESTS; i++) {
        struct request *r = &requests[i];
        if (r->state != EMPTY)
          continue;
        do {
          next_id++;
        } while (!next_id || find(next_id));
        delivery = delivery_create(next_id, options, false);
        if (delivery) {
          id = next_id;
          *r = (struct request){
              .state = STARTING, .delivery_only = true, .id = id, .fd = -1, .delivery = delivery};
        }
        break;
      }
    }
  }
  pthread_mutex_unlock(&mutex);
  struct capture_options adapted;
  if (delivery) {
    adapted = *options;
    adapted.frame_handler = delivery_frame;
    adapted.data = delivery;
    // In both reviewed binaries LinuxCaptureSource drops config.frame_rate
    // before creating/updating its PipeWire stream. This changes only the
    // capture thread's FpsTracker; producer pacing remains independently set.
    if (delivery->fast)
      adapted.frame_rate = 240;
    options = &adapted;
  }
  int32_t result = original_new(options, capture);
  if (id) {
    pthread_mutex_lock(&mutex);
    struct request *r = find(id);
    if (r && r->state == STARTING) {
      if (!result && capture && *capture) {
        r->capture = *capture;
        r->state = ACTIVE;
        notify("started", r);
      } else {
        notify("stopped", r);
        delivery_destroy(r->delivery);
        memset(r, 0, sizeof(*r));
      }
    }
    pthread_mutex_unlock(&mutex);
  }
  return result;
}
static int32_t capture_update(void *capture, const struct capture_options *options) {
  struct capture_options adapted;
  struct delivery_probe *delivery = NULL;
  bool fast = false;
  pthread_mutex_lock(&mutex);
  if (options) {
    for (unsigned i = 0; i < REQUESTS; i++) {
      struct request *r = &requests[i];
      struct delivery_probe *p = r->delivery;
      if (r->state != ACTIVE || r->capture != capture || !p)
        continue;
      adapted = *options;
      pthread_mutex_lock(&p->lock);
      delivery = p;
      fast =
          delivery_fast_enabled(p->auto_fast) && options->frame_rate && options->frame_rate < 240;
      if (fast)
        adapted.frame_rate = 240;
      adapted.frame_handler = delivery_frame;
      adapted.data = p;
      break;
    }
  }
  // The reviewed update only sends a message; it cannot call the frame handler
  // inline. Retain the registry lock until that message has copied its options.
  int32_t result = original_update(capture, delivery ? &adapted : options);
  if (delivery) {
    // Failed queue submission must preserve the previous callback and cadence.
    // Holding its lock also prevents a concurrent callback seeing half an update.
    if (!result) {
      if (delivery->requested_rate != options->frame_rate || delivery->fast != fast)
        delivery->next_unknown_ns = 0;
      delivery->requested_rate = options->frame_rate;
      delivery->fast = fast;
      pthread_mutex_lock(&delivery->wake_lock);
      delivery->events = delivery_events_enabled(fast);
      pthread_mutex_unlock(&delivery->wake_lock);
      delivery_wake(delivery, false);
      delivery->log = delivery_enabled("DPE_CAPTURE_DELIVERY_FAST") ||
                      delivery_enabled("DPE_CAPTURE_DELIVERY_PROBE");
      if (options->frame_handler) {
        delivery->handler = options->frame_handler;
        delivery->data = options->data;
      }
    }
    pthread_mutex_unlock(&delivery->lock);
  }
  pthread_mutex_unlock(&mutex);
  return result;
}
static int32_t capture_destroy(void *capture) {
  // Wake before the native destroy posts its stop and joins. During that tiny
  // interval the receiver uses its original interruptible native timeout.
  pthread_mutex_lock(&mutex);
  for (unsigned i = 0; i < REQUESTS; ++i)
    if (requests[i].state == ACTIVE && requests[i].capture == capture && requests[i].delivery)
      delivery_wake(requests[i].delivery, true);
  pthread_mutex_unlock(&mutex);
  int32_t result = original_destroy(capture);
  pthread_mutex_lock(&mutex);
  for (unsigned i = 0; i < REQUESTS; i++) {
    struct request *r = &requests[i];
    if (r->state == ACTIVE && r->capture == capture) {
      notify("stopped", r);
      delivery_destroy(r->delivery);
      memset(r, 0, sizeof(*r));
      break;
    }
  }
  pthread_mutex_unlock(&mutex);
  return result;
}
static int open_remote(const char *path) {
  struct stat st;
  struct sockaddr_un address = {.sun_family = AF_UNIX};
  if (!path || path[0] != '/' || strlen(path) >= sizeof(address.sun_path) || lstat(path, &st) ||
      !S_ISSOCK(st.st_mode) || st.st_uid != geteuid())
    return -1;
  int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
  if (fd < 0)
    return -1;
  strcpy(address.sun_path, path);
  if (connect(fd, (struct sockaddr *)&address, sizeof(address)) < 0) {
    if (errno != EINPROGRESS && errno != EAGAIN)
      goto failed;
    struct pollfd pollfd = {.fd = fd, .events = POLLOUT};
    int error = 0;
    socklen_t size = sizeof(error);
    if (poll(&pollfd, 1, 250) <= 0 || getsockopt(fd, SOL_SOCKET, SO_ERROR, &error, &size) || error)
      goto failed;
  }
  struct ucred peer;
  socklen_t size = sizeof(peer);
  if (getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &peer, &size) || peer.uid != geteuid() ||
      fcntl(fd, F_SETFL, 0) < 0)
    goto failed;
  return fd;
failed:
  close(fd);
  return -1;
}
// Returns false for stale/repeated completion. Native callback calls are always
// outside the registry lock because they can synchronously cause other hooks.
static bool finish(uint32_t id, const char *action, const char *path, uint32_t node) {
  int fd = !strcmp(action, "selected") ? open_remote(path) : -1;
  struct stat st = {0};
  if (!strcmp(action, "selected") && (fd < 0 || fstat(fd, &st))) {
    if (fd >= 0)
      close(fd);
    return false;
  }
  pthread_mutex_lock(&mutex);
  struct request *r = find(id);
  if (!r || r->state != WAITING || current_request != id || closing) {
    pthread_mutex_unlock(&mutex);
    if (fd >= 0)
      close(fd);
    return false;
  }
  struct picker_options options = r->options;
  struct picker_result result = {.config = {.fd = -1}};
  bool native = !strcmp(action, "native");
  if (fd >= 0) {
    for (unsigned i = 0; i < REQUESTS; i++)
      if (&requests[i] != r && requests[i].state == SELECTED)
        cancel_locked(&requests[i]);
    r->fd = fd;
    r->node = node;
    r->dev = st.st_dev;
    r->ino = st.st_ino;
    r->state = SELECTED;
    result.success = true;
    result.config = (struct pipewire_config){fd, node};
  } else {
    if (!native)
      notify("cancelled", r);
    memset(r, 0, sizeof(*r));
    if (!strcmp(action, "error"))
      result.error = "The selected capture source could not be started.";
  }
  pthread_mutex_unlock(&mutex);
  if (native)
    original_show(&options);
  else
    options.callback(&result, options.data);
  return true;
}
static bool cancel(uint32_t id) {
  pthread_mutex_lock(&mutex);
  struct request *r = find(id);
  if (!r || (r->state != WAITING && r->state != SELECTED)) {
    pthread_mutex_unlock(&mutex);
    return false;
  }
  bool callback = r->state == WAITING && current_request == id;
  struct picker_options options = r->options;
  cancel_locked(r);
  pthread_mutex_unlock(&mutex);
  if (callback) {
    struct picker_result result = {.config = {.fd = -1}};
    options.callback(&result, options.data);
  }
  return true;
}
static void cleanup(void *unused) {
  (void)unused;
  pthread_mutex_lock(&mutex);
  closing = true;
  for (unsigned i = 0; i < REQUESTS; i++)
    if (requests[i].state == SELECTED)
      cancel_locked(&requests[i]);
  napi_threadsafe_function previous = dispatcher;
  dispatcher = NULL;
  pthread_mutex_unlock(&mutex);
  if (previous)
    api_napi_release_threadsafe_function(previous, napi_tsfn_abort);
}
static int protection(uintptr_t base, uintptr_t address, size_t size) {
  const ElfW(Ehdr) *header = (void *)base;
  const ElfW(Phdr) *segments = (void *)(base + header->e_phoff);
  int result = 0;
  for (unsigned i = 0; i < header->e_phnum; i++) {
    const ElfW(Phdr) *p = &segments[i];
    if (p->p_type == PT_LOAD && address >= base + p->p_vaddr && size <= p->p_memsz &&
        address - base - p->p_vaddr <= p->p_memsz - size)
      result = (p->p_flags & PF_R ? PROT_READ : 0) | (p->p_flags & PF_W ? PROT_WRITE : 0) |
               (p->p_flags & PF_X ? PROT_EXEC : 0);
  }
  for (unsigned i = 0; i < header->e_phnum; i++) {
    const ElfW(Phdr) *p = &segments[i];
    if (p->p_type == PT_GNU_RELRO && address >= base + p->p_vaddr && size <= p->p_memsz &&
        address - base - p->p_vaddr <= p->p_memsz - size)
      result &= ~PROT_WRITE;
  }
  return result;
}
static bool reviewed_slot(uintptr_t base, uintptr_t slot, uintptr_t target) {
  uintptr_t current = *(uintptr_t *)slot;
  if (current == target)
    return true;
  // A fresh voice module may not have called these PLT entries yet. Its GOT
  // then points six bytes into its own reviewed lazy-resolution stub. Accept
  // only the canonical jump back to this exact slot followed by push/jump;
  // an arbitrary pre-existing hook is never treated as the original target.
  if (current < base + 6 || !(protection(base, current - 6, 16) & PROT_EXEC))
    return false;
  const uint8_t *stub = (void *)(current - 6);
  int32_t displacement;
  memcpy(&displacement, stub + 2, 4);
  return stub[0] == 0xff && stub[1] == 0x25 && current + displacement == slot && stub[6] == 0x68 &&
         stub[11] == 0xe9;
}
static napi_value install(napi_env env, napi_callback_info info) {
  napi_value args[16];
  size_t count = 16, length;
  char filename[PATH_MAX];
  uint32_t offsets[14];
  if (api_napi_get_cb_info(env, info, &count, args, NULL, NULL) != napi_ok || count != 16 ||
      api_napi_get_value_string_utf8(env, args[0], filename, sizeof(filename), &length) !=
          napi_ok ||
      !length || length >= sizeof(filename) - 1)
    return fail(env, "Invalid capture picker arguments.");
  for (unsigned i = 0; i < 14; i++)
    if (api_napi_get_value_uint32(env, args[i + 1], &offsets[i]) != napi_ok || !offsets[i])
      return fail(env, "Invalid capture picker offset.");
  void *handle = dlopen(filename, RTLD_NOW | RTLD_NOLOAD);
  struct link_map *map;
  if (!handle || dlinfo(handle, RTLD_DI_LINKMAP, &map)) {
    if (handle)
      dlclose(handle);
    return fail(env, "The native capture module is not loaded.");
  }
  uintptr_t base = map->l_addr, slots[4], targets[4], pages[4];
  int permissions[4];
  size_t page_size = sysconf(_SC_PAGESIZE);
  if (installed_slot) {
    dlclose(handle);
    return installed_slot == base + offsets[0] ? undefined(env)
                                               : fail(env, "Capture picker module changed.");
  }
  const char *names[] = {"cc_linux_show_picker", "cc_linux_capture_new", "cc_linux_capture_destroy",
                         "cc_linux_capture_update"};
  for (unsigned i = 0; i < 4; i++) {
    unsigned index = i == 3 ? 9 : i * 2;
    slots[i] = base + offsets[index];
    targets[i] = base + offsets[index + 1];
    permissions[i] = protection(base, slots[i], sizeof(uintptr_t));
    if (slots[i] % sizeof(uintptr_t) || !(permissions[i] & PROT_READ) ||
        permissions[i] & PROT_EXEC || !(protection(base, targets[i], 16) & PROT_EXEC) ||
        (uintptr_t)dlsym(handle, names[i]) != targets[i] ||
        !reviewed_slot(base, slots[i], targets[i])) {
      dlclose(handle);
      return fail(env, "Capture picker linkage differs from reviewed native code.");
    }
    pages[i] = slots[i] & ~(page_size - 1);
  }
  // Each reviewed export address pins its revision's matching private storage.
  uint32_t selection_base = offsets[1] == 0xe5fe50   ? 0x22ecb40
                            : offsets[1] == 0xe2c3b0 ? 0x22f1b40
                                                     : 0;
  const uint32_t selection_offsets[] = {selection_base, selection_base + 0x50,
                                        selection_base + 0x58};
  const size_t selection_sizes[] = {sizeof(pthread_mutex_t), sizeof(struct pipewire_config),
                                    sizeof(bool)};
  for (unsigned i = 0; i < 3; i++) {
    int flags = protection(base, base + offsets[i + 6], selection_sizes[i]);
    if (!selection_base || offsets[i + 6] != selection_offsets[i] ||
        flags != (PROT_READ | PROT_WRITE)) {
      dlclose(handle);
      return fail(env, "Selected capture configuration differs from reviewed native code.");
    }
  }
  const char *delivery_names[] = {"cc_video_frame_clone_i420", "cc_i420_buffer_destroy",
                                  "cc_video_frame_destroy"};
  for (unsigned i = 0; i < 3; i++) {
    uintptr_t address = base + offsets[11 + i];
    if (!(protection(base, address, 16) & PROT_EXEC) ||
        (uintptr_t)dlsym(handle, delivery_names[i]) != address) {
      dlclose(handle);
      return fail(env, "Capture delivery linkage differs from reviewed native code.");
    }
  }
  napi_value resource;
  napi_threadsafe_function next;
  if (api_napi_create_string_utf8(env, "DPE external capture", NAPI_AUTO_LENGTH, &resource) !=
          napi_ok ||
      api_napi_create_threadsafe_function(env, args[15], NULL, resource, 64, 1, NULL, NULL, NULL,
                                          deliver, &next) != napi_ok) {
    dlclose(handle);
    return fail(env, "Capture picker callback is unavailable.");
  }
  api_napi_unref_threadsafe_function(env, next);
  if (api_napi_add_env_cleanup_hook(env, cleanup, NULL) != napi_ok) {
    api_napi_release_threadsafe_function(next, napi_tsfn_abort);
    dlclose(handle);
    return fail(env, "Capture picker cleanup is unavailable.");
  }
  for (unsigned i = 0; i < 4; i++) {
    if (mprotect((void *)pages[i], page_size, permissions[i] | PROT_WRITE)) {
      for (unsigned j = 0; j < i; j++)
        mprotect((void *)pages[j], page_size, permissions[j]);
      api_napi_release_threadsafe_function(next, napi_tsfn_abort);
      dlclose(handle);
      return fail(env, "Could not prepare capture picker linkage.");
    }
  }
  original_show = (show_fn)targets[0];
  original_new = (new_fn)targets[1];
  original_destroy = (destroy_fn)targets[2];
  original_update = (update_fn)targets[3];
  clone_i420 = (clone_i420_fn)(base + offsets[11]);
  release_i420 = (release_frame_fn)(base + offsets[12]);
  release_frame = (release_frame_fn)(base + offsets[13]);
  picked_mutex = (void *)(base + offsets[6]);
  picked_config = (void *)(base + offsets[7]);
  picked_present = (void *)(base + offsets[8]);
  pthread_mutex_lock(&mutex);
  dispatcher = next;
  closing = false;
  pthread_mutex_unlock(&mutex);
  uintptr_t replacements[] = {(uintptr_t)show_picker, (uintptr_t)capture_new,
                              (uintptr_t)capture_destroy, (uintptr_t)capture_update};
  for (unsigned i = 0; i < 4; i++)
    __atomic_store_n((uintptr_t *)slots[i], replacements[i], __ATOMIC_RELEASE);
  bool restored = true;
  for (unsigned i = 0; i < 4; i++)
    restored &= mprotect((void *)pages[i], page_size, permissions[i]) == 0;
  if (!restored) {
    bool rolled_back = true;
    for (unsigned i = 0; i < 4; i++) {
      if (!mprotect((void *)pages[i], page_size, permissions[i] | PROT_WRITE)) {
        __atomic_store_n((uintptr_t *)slots[i], targets[i], __ATOMIC_RELEASE);
        mprotect((void *)pages[i], page_size, permissions[i]);
      } else {
        rolled_back = false;
      }
    }
    cleanup(NULL);
    if (rolled_back)
      dlclose(handle);
    return fail(env, "Could not restore capture picker linkage protection.");
  }
  installed_slot = slots[0];
  // The hooks retain native callback pointers; keep the native module loaded.
  return undefined(env);
}
// Optional, full-hash-scoped publication wakeups. Install before any capture
// starts; all five sites are verified before code is touched. The native picker
// ABI stays usable when this additional scope/helper is unavailable.
static uint8_t *wake_near_page(uintptr_t site, size_t size) {
  uintptr_t center = site & ~(size - 1);
  for (uintptr_t distance = 0x10000; distance < 0x70000000; distance += 0x10000) {
    for (int sign = -1; sign <= 1; sign += 2) {
      if ((sign < 0 && center < distance) || (sign > 0 && center > UINTPTR_MAX - distance))
        continue;
      uintptr_t address = sign < 0 ? center - distance : center + distance;
      void *page = mmap((void *)address, size, PROT_READ | PROT_WRITE,
                        MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0);
      if (page == (void *)address)
        return page;
      if (page != MAP_FAILED)
        munmap(page, size);
    }
  }
  return MAP_FAILED;
}
static const uint32_t wake_offsets[2][10] = {{0xe799cd, 0xe639b0, 0xe84f16, 0xe63ac0, 0xe83765,
                                              0xe63ac0, 0xe2880e, 0x14da210, 0xe28836, 0xe50d70},
                                             {0xe8ee2d, 0xe78e10, 0xe9a376, 0xe78f20, 0xe98bc5,
                                              0xe78f20, 0xe5c83e, 0x14e76b0, 0xe5c866, 0xe661d0}};
static bool wake_call_matches(uintptr_t base, unsigned index, uintptr_t site, uintptr_t target) {
  static const uint8_t before[][11] = {
      {0x48, 0x8b, 0x7f, 0x18, 0x48, 0x83, 0xc7, 0x10},
      {0x48, 0x89, 0xdf, 0x48, 0x89, 0xc6},
      {0x4c, 0x89, 0xff, 0x48, 0x89, 0xc6},
      {0x89, 0xd1, 0x4c, 0x89, 0xe7, 0x4c, 0x89, 0xee, 0x48, 0x89, 0xc2},
      {0x48, 0x89, 0xef}};
  static const uint8_t after[][8] = {{0x48, 0x89, 0xc2, 0x31, 0xc0, 0x59, 0xc3},
                                     {0x31, 0xed},
                                     {0x48, 0x8d, 0xbc, 0x24, 0x00, 0x01, 0x00, 0x00},
                                     {0x48, 0x8b, 0x84, 0x24, 0x08, 0x01, 0x00, 0x00},
                                     {0x84, 0xc0, 0x74, 0xa0}};
  static const unsigned before_size[] = {8, 6, 6, 11, 3}, after_size[] = {7, 2, 8, 8, 4};
  size_t size = index == 4 ? 6 : 5;
  if (!(protection(base, site - 11, 11 + size + 30) & PROT_EXEC) ||
      !(protection(base, target, 16) & PROT_EXEC) ||
      memcmp((void *)(site - before_size[index]), before[index], before_size[index]) ||
      memcmp((void *)(site + size), after[index], after_size[index]))
    return false;
  const uint8_t *code = (void *)site;
  int32_t displacement;
  memcpy(&displacement, code + (index == 4 ? 2 : 1), 4);
  if (index != 4)
    return code[0] == 0xe8 && site + 5 + displacement == target;
  uintptr_t slot = site + 6 + displacement;
  return code[0] == 0xff && code[1] == 0x15 &&
         (protection(base, slot, sizeof(uintptr_t)) & PROT_READ) && *(uintptr_t *)slot == target;
}
static napi_value install_wake(napi_env env, napi_callback_info info) {
  napi_value args[11];
  size_t count = 11, length;
  char filename[PATH_MAX];
  uint32_t offsets[10];
  if (api_napi_get_cb_info(env, info, &count, args, NULL, NULL) != napi_ok || count != 11 ||
      api_napi_get_value_string_utf8(env, args[0], filename, sizeof(filename), &length) !=
          napi_ok ||
      !length || length >= sizeof(filename) - 1)
    return boolean(env, false);
  for (unsigned i = 0; i < 10; ++i)
    if (api_napi_get_value_uint32(env, args[i + 1], &offsets[i]) != napi_ok || !offsets[i])
      return boolean(env, false);
  if (!installed_slot)
    return boolean(env, false);
  if (installed_wake)
    return boolean(env, true);
  pthread_mutex_lock(&mutex);
  bool active = false;
  for (unsigned i = 0; i < REQUESTS; ++i)
    active |= requests[i].state != EMPTY;
  pthread_mutex_unlock(&mutex);
  if (active)
    return boolean(env, false);
  void *handle = dlopen(filename, RTLD_NOW | RTLD_NOLOAD);
  struct link_map *map;
  if (!handle || dlinfo(handle, RTLD_DI_LINKMAP, &map)) {
    if (handle)
      dlclose(handle);
    return boolean(env, false);
  }
  uintptr_t base = map->l_addr;
  uintptr_t show = (uintptr_t)original_show - base;
  const uint32_t *expected = show == 0xe2c3b0   ? wake_offsets[0]
                             : show == 0xe5fe50 ? wake_offsets[1]
                                                : NULL;
  if (!expected || memcmp(offsets, expected, sizeof(offsets))) {
    dlclose(handle);
    return boolean(env, false);
  }
  uintptr_t sites[5], pages[5];
  int permissions[5];
  uint8_t saved[5][6];
  size_t page_size = sysconf(_SC_PAGESIZE);
  for (unsigned i = 0; i < 5; ++i) {
    sites[i] = base + offsets[i * 2];
    pages[i] = sites[i] & ~(page_size - 1);
    permissions[i] = protection(base, sites[i], 6);
    if (!wake_call_matches(base, i, sites[i], base + offsets[i * 2 + 1]) ||
        sites[i] + 6 > pages[i] + page_size) {
      dlclose(handle);
      return boolean(env, false);
    }
    memcpy(saved[i], (void *)sites[i], 6);
  }
  uint8_t *trampoline = wake_near_page(sites[0], page_size);
  if (trampoline == MAP_FAILED) {
    dlclose(handle);
    return boolean(env, false);
  }
  uintptr_t replacements[] = {(uintptr_t)delivery_read, (uintptr_t)delivery_write,
                              (uintptr_t)delivery_write, (uintptr_t)delivery_receive,
                              (uintptr_t)delivery_overdue};
  for (unsigned i = 0; i < 5; ++i) {
    intptr_t displacement = (intptr_t)(trampoline + i * 16) - (intptr_t)(sites[i] + 5);
    if (displacement < INT32_MIN || displacement > INT32_MAX) {
      munmap(trampoline, page_size);
      dlclose(handle);
      return boolean(env, false);
    }
    const uint8_t jump[] = {0xff, 0x25, 0, 0, 0, 0};
    memcpy(trampoline + i * 16, jump, sizeof(jump));
    memcpy(trampoline + i * 16 + sizeof(jump), &replacements[i], sizeof(uintptr_t));
  }
  if (mprotect(trampoline, page_size, PROT_READ | PROT_EXEC)) {
    munmap(trampoline, page_size);
    dlclose(handle);
    return boolean(env, false);
  }
  for (unsigned i = 0; i < 5; ++i) {
    if (mprotect((void *)pages[i], page_size, permissions[i] | PROT_WRITE)) {
      for (unsigned j = 0; j < i; ++j)
        mprotect((void *)pages[j], page_size, permissions[j]);
      munmap(trampoline, page_size);
      dlclose(handle);
      return boolean(env, false);
    }
  }
  original_read = (buffer_read_fn)(base + offsets[1]);
  original_write = (buffer_write_fn)(base + offsets[3]);
  original_receive = (receive_fn)(base + offsets[7]);
  original_overdue = (overdue_fn)(base + offsets[9]);
  for (unsigned i = 0; i < 5; ++i) {
    uint8_t replacement[6] = {0xe8, 0, 0, 0, 0, 0x90};
    int32_t displacement = (intptr_t)(trampoline + i * 16) - (intptr_t)(sites[i] + 5);
    memcpy(replacement + 1, &displacement, 4);
    memcpy((void *)sites[i], replacement, i == 4 ? 6 : 5);
  }
  bool restored = true;
  for (unsigned i = 0; i < 5; ++i)
    restored &= !mprotect((void *)pages[i], page_size, permissions[i]);
  if (!restored) {
    bool rolled_back = true;
    for (unsigned i = 0; i < 5; ++i) {
      if (!mprotect((void *)pages[i], page_size, permissions[i] | PROT_WRITE)) {
        memcpy((void *)sites[i], saved[i], i == 4 ? 6 : 5);
        mprotect((void *)pages[i], page_size, permissions[i]);
      } else {
        rolled_back = false;
      }
    }
    if (rolled_back) {
      munmap(trampoline, page_size);
      dlclose(handle);
    }
    return boolean(env, false);
  }
  installed_wake = true;
  // Native targets and nearby executable trampolines live until process exit.
  return boolean(env, true);
}
static napi_value finish_js(napi_env env, napi_callback_info info) {
  napi_value args[4];
  size_t count = 4, length;
  uint32_t id, node;
  char action[16], path[sizeof(((struct sockaddr_un *)0)->sun_path)];
  if (api_napi_get_cb_info(env, info, &count, args, NULL, NULL) != napi_ok || count != 4 ||
      api_napi_get_value_uint32(env, args[0], &id) != napi_ok || !id ||
      api_napi_get_value_string_utf8(env, args[1], action, sizeof(action), &length) != napi_ok ||
      !length || length >= sizeof(action) - 1 ||
      api_napi_get_value_string_utf8(env, args[2], path, sizeof(path), &length) != napi_ok ||
      length >= sizeof(path) - 1 || api_napi_get_value_uint32(env, args[3], &node) != napi_ok ||
      node == UINT32_MAX ||
      (strcmp(action, "native") && strcmp(action, "selected") && strcmp(action, "cancel") &&
       strcmp(action, "error")))
    return fail(env, "Invalid capture picker completion.");
  return boolean(env, finish(id, action, path, node));
}
static napi_value cancel_js(napi_env env, napi_callback_info info) {
  napi_value arg;
  size_t count = 1;
  uint32_t id;
  if (api_napi_get_cb_info(env, info, &count, &arg, NULL, NULL) != napi_ok || count != 1 ||
      api_napi_get_value_uint32(env, arg, &id) != napi_ok || !id)
    return fail(env, "Invalid capture picker cancellation.");
  return boolean(env, cancel(id));
}
napi_value dpe_capture_picker_register(napi_env env, napi_value exports) {
#define LOAD(name)                                                                                 \
  api_##name = dlsym(RTLD_DEFAULT, #name);                                                         \
  if (!api_##name)                                                                                 \
  return exports
  LOAD(napi_get_cb_info);
  LOAD(napi_get_value_string_utf8);
  LOAD(napi_get_value_uint32);
  LOAD(napi_get_undefined);
  LOAD(napi_get_boolean);
  LOAD(napi_create_string_utf8);
  LOAD(napi_create_function);
  LOAD(napi_set_named_property);
  LOAD(napi_throw_error);
  LOAD(napi_create_threadsafe_function);
  LOAD(napi_call_threadsafe_function);
  LOAD(napi_release_threadsafe_function);
  LOAD(napi_unref_threadsafe_function);
  LOAD(napi_add_env_cleanup_hook);
  LOAD(napi_get_global);
  LOAD(napi_call_function);
#undef LOAD
  const struct {
    const char *name;
    napi_callback callback;
  } methods[] = {{"installCapturePicker", install},
                 {"installCaptureWake", install_wake},
                 {"finishCapturePicker", finish_js},
                 {"cancelCapturePicker", cancel_js}};
  for (unsigned i = 0; i < sizeof(methods) / sizeof(methods[0]); i++) {
    napi_value function;
    if (api_napi_create_function(env, methods[i].name, NAPI_AUTO_LENGTH, methods[i].callback, NULL,
                                 &function) != napi_ok ||
        api_napi_set_named_property(env, exports, methods[i].name, function) != napi_ok)
      return exports;
  }
  return exports;
}
