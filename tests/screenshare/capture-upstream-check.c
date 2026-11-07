#include "upstream-fixture.h"
#include <pthread.h>
#include <spa/param/video/format-utils.h>
struct timer {
  struct spa_source source;
  spa_source_timer_func_t callback;
  void *data;
  bool active;
};
static struct timer timers[64];
static unsigned created, removed, calls, connects, last_max, reject_next;
static bool fail_timer, fail_arm, destroy_update, nested_update, suppress_ack, destroy_arm;
static const struct spa_pod *nested_pod;
static struct fixture_stream *current;
static enum pw_stream_state stream_state = PW_STREAM_STATE_STREAMING;
static struct spa_source *add_timer(void *object, spa_source_timer_func_t func, void *data) {
  (void)object;
  if (fail_timer)
    return NULL;
  assert(created < 64);
  struct timer *timer = &timers[created++];
  *timer = (struct timer){.callback = func, .data = data, .active = true};
  return &timer->source;
}
static int arm_timer(void *object, struct spa_source *source, struct timespec *first,
                     struct timespec *interval, bool absolute) {
  (void)object;
  (void)source;
  assert(!absolute && first->tv_sec == 0 && first->tv_nsec == 100000000 && interval->tv_sec == 0 &&
         interval->tv_nsec == 100000000);
  if (destroy_arm) {
    destroy_arm = false;
    destroy(current);
  }
  return fail_arm ? -EINVAL : 0;
}
static void remove_timer(void *object, struct spa_source *source) {
  (void)object;
  struct timer *timer = (void *)source;
  assert(timer->active);
  timer->active = false;
  removed++;
}
static const struct spa_loop_utils_methods methods = {
    .version = SPA_VERSION_LOOP_UTILS_METHODS,
    .add_timer = add_timer,
    .update_timer = arm_timer,
    .destroy_source = remove_timer,
};
static struct spa_loop_utils utils = {
    .iface =
        SPA_INTERFACE_INIT(SPA_TYPE_INTERFACE_LoopUtils, SPA_VERSION_LOOP_UTILS, &methods, NULL),
};
static struct pw_loop loop = {.utils = &utils};
static struct pw_core *get_core(struct pw_stream *stream) { return (void *)stream; }
static struct pw_context *get_context(struct pw_core *core) { return (void *)core; }
static struct pw_loop *get_loop(struct pw_context *context) {
  (void)context;
  return &loop;
}
static enum pw_stream_state get_state(struct pw_stream *stream, const char **error) {
  (void)stream;
  (void)error;
  return stream_state;
}
static int connected(struct pw_stream *stream, enum pw_direction direction, uint32_t target,
                     enum pw_stream_flags flags, const struct spa_pod **params, uint32_t count) {
  (void)stream;
  (void)direction;
  (void)target;
  (void)flags;
  assert(count == 1 && params[0]);
  // Must be callable with a full registry: no registry lock spans pass-through.
  (void)metric("active");
  connects++;
  return 0;
}
static int updated(struct pw_stream *stream, const struct spa_pod **params, uint32_t count) {
  assert(count == 1);
  calls++;
  struct spa_video_info_raw raw = {0};
  assert(spa_format_video_raw_parse(params[0], &raw) >= 0 && raw.size.width == 1920 &&
         raw.size.height == 1080 && raw.modifier == UINT64_C(0x0200000028a01f04));
  last_max = maximum(params[0]);
  if (reject_next) {
    reject_next--;
    return -EINVAL;
  }
  if (nested_update) {
    nested_update = false;
    assert(fixture_update(stream, &nested_pod, 1) == 0);
  }
  if (destroy_update) {
    destroy_update = false;
    destroy((void *)stream);
  } else if (!suppress_ack) {
    uint8_t memory[512];
    observed((void *)stream, offer(memory, SPA_PARAM_Format, 0, last_max ? last_max : 240));
  }
  return 0;
}
static void pulse(unsigned index) {
  assert(timers[index].active);
  timers[index].callback(timers[index].data, 1);
}
static void observe(struct fixture_stream *stream, unsigned fixed, unsigned max) {
  uint8_t memory[512];
  observed(stream, offer(memory, SPA_PARAM_Format, fixed, max));
}
static void *foreign_observe(void *stream) {
  observe(stream, 0, 15);
  return NULL;
}
static void *foreign_destroy(void *stream) {
  destroy(stream);
  return NULL;
}
static void start(struct fixture_stream *stream, const struct spa_pod *pod) {
  current = stream;
  assert(fixture_connect((void *)stream, PW_DIRECTION_INPUT, 7, PW_STREAM_FLAG_AUTOCONNECT, &pod,
                         1) == 0);
}
int main(void) {
  struct fixture_native native = {connected,   updated,  listen_stream, get_core,
                                  get_context, get_loop, get_state};
  assert(fixture_initialize(&native, true));
  uint8_t memory[512], nested[512];
  const struct spa_pod *pod = offer(memory, SPA_PARAM_EnumFormat, 0, 0);
  struct fixture_stream stream = {0};
  start(&stream, pod);
  fixture_request(60);
  pulse(0);
  assert(calls == 0);
  observe(&stream, 60, 240);
  pulse(0);
  assert(calls == 0);
  assert(metric("width") == 1920 && metric("height") == 1080);
  observe(&stream, 0, 0);
  pulse(0);
  assert(calls == 0);
  observe(&stream, 0, 240);
  pulse(0);
  assert(calls == 1 && last_max == 60 && metric("applied") == 60);
  pulse(0);
  assert(calls == 1);
  fixture_request(30);
  pulse(0);
  assert(calls == 2 && metric("applied") == 30);
  fixture_request(60);
  pulse(0);
  assert(calls == 3 && metric("applied") == 60);
  fixture_request(0);
  pulse(0);
  assert(calls == 4 && metric("restored") == 1);
  fixture_request(15);
  reject_next = 1;
  pulse(0);
  assert(calls == 5 && metric("errors") == 1 && metric("applied") == 0);
  pulse(0);
  assert(calls == 6 && last_max == 0);
  pulse(0);
  assert(calls == 6);
  fixture_request(30);
  suppress_ack = true;
  pulse(0);
  assert(metric("pending") == 30 && metric("applied") == 0);
  observe(&stream, 0, 240);
  assert(metric("pending") == 30);
  observe(&stream, 0, 30);
  assert(metric("pending") == 0 && metric("applied") == 30);
  pthread_t thread;
  assert(pthread_create(&thread, NULL, foreign_observe, &stream) == 0);
  assert(pthread_join(thread, NULL) == 0 && metric("applied") == 30 && metric("maximum") == 30);
  suppress_ack = false;
  fixture_request(60);
  nested_update = true;
  nested_pod = offer(nested, SPA_PARAM_EnumFormat, 0, 120);
  pulse(0); // newest native caps win over the outer cap publication
  assert(metric("applied") == 0 && metric("pending") == 0);
  unsigned before = calls;
  pulse(0);
  assert(calls == before); // existing native maximum cannot be rewritten
  assert(fixture_update((void *)&stream, &pod, 1) == 0);
  fixture_request(30);
  destroy_update = true;
  pulse(0);
  assert(metric("active") == 0 && !timers[0].active);
  // Stale native tokens cannot touch a replacement stream owner.
  timers[0].callback(timers[0].data, 1);
  start(&stream, pod);
  observe(&stream, 0, 240);
  pulse(1);
  stream_state = PW_STREAM_STATE_ERROR;
  stream.events->state_changed(stream.data, PW_STREAM_STATE_STREAMING, stream_state, "fixture");
  before = calls;
  pulse(1);
  assert(calls == before && metric("asyncErrors") == 1);
  stream_state = PW_STREAM_STATE_STREAMING;
  assert(pthread_create(&thread, NULL, foreign_destroy, &stream) == 0);
  assert(pthread_join(thread, NULL) == 0 && !timers[1].active);
  fail_timer = true;
  start(&stream, pod);
  assert(metric("bypassed") >= 1);
  destroy(&stream);
  fail_timer = false;
  fail_arm = true;
  before = removed;
  start(&stream, pod);
  assert(removed == before + 1);
  destroy(&stream);
  fail_arm = false;
  destroy_arm = true;
  start(&stream, pod);
  assert(metric("active") == 0 && created == removed);
  // A reconnect retires both the old hook and timer, retaining only its new generation.
  start(&stream, pod);
  start(&stream, pod);
  assert(metric("active") == 1);
  destroy(&stream);
  struct fixture_stream many[17] = {0};
  for (unsigned i = 0; i < 17; i++)
    start(&many[i], pod);
  assert(metric("active") == 16 && !many[16].events);
  for (unsigned i = 0; i < 17; i++)
    destroy(&many[i]);
  assert(metric("active") == 0 && created == removed && connects > 17);
  puts("Rust C ABI: rates, nested callbacks, timers, thread guards, retirement and bounded "
       "admission passed");
}
