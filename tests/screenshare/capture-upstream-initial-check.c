// Real callbacks into Rust; SPA intersection has a separate native oracle.
#include "upstream-fixture.h"
#include <pthread.h>
static unsigned connects, updates, expected;
static bool fail_connect, destroy_connect;
static int connect_stream(struct pw_stream *raw, enum pw_direction direction, uint32_t target,
                          enum pw_stream_flags flags, const struct spa_pod **params,
                          uint32_t count) {
  (void)direction;
  (void)target;
  (void)flags;
  connects++;
  assert(count == 1 && maximum(params[0]) == expected);
  if (fail_connect)
    return -EINVAL;
  struct fixture_stream *stream = (void *)raw;
  if (stream->events) {
    uint8_t memory[512];
    observed(stream, offer(memory, SPA_PARAM_Format, 0, expected ? expected : 240));
    if (destroy_connect)
      destroy(stream);
  }
  return 0;
}
static int update_stream(struct pw_stream *stream, const struct spa_pod **params, uint32_t count) {
  (void)stream;
  (void)params;
  (void)count;
  updates++;
  return 0;
}
static void *foreign_destroy(void *stream) {
  destroy(stream);
  return NULL;
}
static void run(struct fixture_stream *stream, const struct spa_pod *pod, unsigned preference) {
  expected = preference;
  assert(fixture_connect((void *)stream, PW_DIRECTION_INPUT, 7, PW_STREAM_FLAG_AUTOCONNECT, &pod,
                         1) == 0);
  assert(metric("active") == 1 && metric("preferred") == preference &&
         metric("applied") == preference);
  fixture_request(30);
  assert(updates == 0);
  destroy(stream);
  assert(metric("active") == 0 && metric("maximum") == 0);
  fixture_request(60);
}
int main(void) {
  struct fixture_native native = {
      .connect = connect_stream, .update = update_stream, .listen = listen_stream};
  assert(fixture_initialize(&native, false)); // no core/loop functions at all
  fixture_request(60);
  uint8_t memory[512];
#ifdef SPA_POD_PROP_FLAG_DROP
  const unsigned preference = 60;
#else
  const unsigned preference = 0;
#endif
  const struct spa_pod *pod = offer(memory, SPA_PARAM_EnumFormat, 0, 0);
  struct fixture_stream stream = {0};
  run(&stream, pod, preference);
  assert(metric("initial") == (preference != 0) && metric("late") == 1);
  fixture_request(0);
  run(&stream, pod, 0);
  assert(metric("withoutRate") == 1);
  expected = 120;
  pod = offer(memory, SPA_PARAM_EnumFormat, 0, 120);
  assert(fixture_connect((void *)&stream, PW_DIRECTION_INPUT, 7, PW_STREAM_FLAG_AUTOCONNECT, &pod,
                         1) == 0);
  assert(metric("preferred") == 0);
  destroy(&stream);
  pod = offer(memory, SPA_PARAM_EnumFormat, 60, 0);
  run(&stream, pod, 0);
  pod = offer(memory, SPA_PARAM_EnumFormat, 0, 0);
  assert(fixture_update((void *)&stream, &pod, 1) == 0 && updates == 1);
  expected = preference;
  fail_connect = true;
  assert(fixture_connect((void *)&stream, PW_DIRECTION_INPUT, 7, PW_STREAM_FLAG_AUTOCONNECT, &pod,
                         1) == -EINVAL);
  assert(spa_list_is_empty(&stream.listeners));
  stream.events = NULL;
  stream.hook = NULL;
  assert(metric("active") == 0);
  fail_connect = false;
  destroy_connect = true;
  assert(fixture_connect((void *)&stream, PW_DIRECTION_INPUT, 7, PW_STREAM_FLAG_AUTOCONNECT, &pod,
                         1) == 0);
  assert(metric("active") == 0);
  destroy_connect = false;
  destroy_listen = true;
  unsigned before = connects;
  assert(fixture_connect((void *)&stream, PW_DIRECTION_INPUT, 7, PW_STREAM_FLAG_AUTOCONNECT, &pod,
                         1) == -EPIPE);
  assert(connects == before && metric("active") == 0);
  destroy_listen = false;
  assert(fixture_connect((void *)&stream, PW_DIRECTION_INPUT, 7, PW_STREAM_FLAG_AUTOCONNECT, &pod,
                         1) == 0);
  pthread_t thread;
  assert(pthread_create(&thread, NULL, foreign_destroy, &stream) == 0);
  assert(pthread_join(thread, NULL) == 0 && metric("active") == 0);
  puts("initial Rust owner: preference, no timer, reentry and foreign destroy passed");
}
