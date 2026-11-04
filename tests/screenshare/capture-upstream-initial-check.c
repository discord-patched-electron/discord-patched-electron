// Real SPA intersection/fixation with the same preferred-first, range-fallback
// order used by PipeWire impl-link. No compositor, desktop or GPU is opened.
#include "../../integration/video/capture-upstream.c"
#include <assert.h>
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wunused-parameter"
#include <spa/pod/filter.h>
#pragma GCC diagnostic pop

static unsigned updates, connects;
static struct upstream *current;
static const struct spa_pod *producer;
static bool fail_connect, destroy_connect;

static struct spa_pod *offer(uint8_t *memory, unsigned fixed, unsigned maximum, bool range) {
  struct spa_pod_builder builder = SPA_POD_BUILDER_INIT(memory, 512);
  struct spa_pod_frame frame;
  spa_pod_builder_push_object(&builder, &frame, SPA_TYPE_OBJECT_Format, SPA_PARAM_EnumFormat);
  spa_pod_builder_add(&builder, SPA_FORMAT_mediaType, SPA_POD_Id(SPA_MEDIA_TYPE_video),
                      SPA_FORMAT_mediaSubtype, SPA_POD_Id(SPA_MEDIA_SUBTYPE_raw),
                      SPA_FORMAT_VIDEO_format, SPA_POD_Id(SPA_VIDEO_FORMAT_BGRA),
                      SPA_FORMAT_VIDEO_size, SPA_POD_Rectangle(&SPA_RECTANGLE(1920, 1080)),
                      SPA_FORMAT_VIDEO_framerate, SPA_POD_Fraction(&SPA_FRACTION(fixed, 1)), 0);
  if (maximum) {
    spa_pod_builder_prop(&builder, SPA_FORMAT_VIDEO_maxFramerate, 0);
    if (range) {
      struct spa_pod_frame choice;
      spa_pod_builder_push_choice(&builder, &choice, SPA_CHOICE_Range, 0);
      spa_pod_builder_fraction(&builder, maximum, 1);
      spa_pod_builder_fraction(&builder, 1, 1);
      spa_pod_builder_fraction(&builder, maximum, 1);
      spa_pod_builder_pop(&builder, &choice);
    } else
      spa_pod_builder_fraction(&builder, maximum, 1);
  }
  spa_pod_builder_prop(&builder, SPA_FORMAT_VIDEO_modifier, SPA_POD_PROP_FLAG_MANDATORY);
  spa_pod_builder_long(&builder, INT64_C(0x0200000028a01f04));
  return spa_pod_builder_pop(&builder, &frame);
}

static struct spa_pod *native_offer(uint8_t *memory) {
  struct spa_pod_builder builder = SPA_POD_BUILDER_INIT(memory, 512);
  struct spa_pod_frame frame;
  spa_pod_builder_push_object(&builder, &frame, SPA_TYPE_OBJECT_Format, SPA_PARAM_EnumFormat);
  spa_pod_builder_add(&builder, SPA_FORMAT_mediaType, SPA_POD_Id(SPA_MEDIA_TYPE_video),
                      SPA_FORMAT_mediaSubtype, SPA_POD_Id(SPA_MEDIA_SUBTYPE_raw),
                      SPA_FORMAT_VIDEO_format, SPA_POD_Id(SPA_VIDEO_FORMAT_BGRA),
                      SPA_FORMAT_VIDEO_size, SPA_POD_Rectangle(&SPA_RECTANGLE(1920, 1080)),
                      SPA_FORMAT_VIDEO_framerate,
                      SPA_POD_CHOICE_RANGE_Fraction(&SPA_FRACTION(25, 1), &SPA_FRACTION(0, 1),
                                                    &SPA_FRACTION(1000, 1)),
                      0);
  spa_pod_builder_prop(&builder, SPA_FORMAT_VIDEO_modifier, SPA_POD_PROP_FLAG_MANDATORY);
  spa_pod_builder_long(&builder, INT64_C(0x0200000028a01f04));
  return spa_pod_builder_pop(&builder, &frame);
}

static void listen(struct pw_stream *stream, struct spa_hook *listener,
                   const struct pw_stream_events *callbacks, void *data) {
  (void)stream;
  (void)listener;
  assert(callbacks == &events);
  current = data;
}

static int update(struct pw_stream *stream, const struct spa_pod **params, uint32_t count) {
  (void)stream;
  (void)params;
  (void)count;
  updates++;
  return 0;
}

static struct spa_pod *intersect(const struct spa_pod *consumer, uint8_t *memory) {
  uint8_t preferred[1024];
  memcpy(preferred, consumer, SPA_POD_SIZE(consumer));
  struct spa_pod *filter = (void *)preferred, *result = NULL;
  struct spa_pod_builder builder = SPA_POD_BUILDER_INIT(memory, 1024);
  int fixed = spa_pod_filter_make(filter);
  int res = spa_pod_filter(&builder, &result, producer, filter);
  if (res < 0 && fixed > 0) {
    spa_pod_builder_init(&builder, memory, 1024);
    res = spa_pod_filter(&builder, &result, producer, consumer);
  }
  assert(res == 0 && result);
  spa_pod_fixate(result);
  SPA_POD_OBJECT_ID(result) = SPA_PARAM_Format;
  return result;
}

static int connect_stream(struct pw_stream *stream, enum pw_direction direction, uint32_t target,
                          enum pw_stream_flags flags, const struct spa_pod **params,
                          uint32_t count) {
  (void)stream;
  (void)direction;
  (void)target;
  (void)flags;
  assert(count == 1);
  connects++;
  if (fail_connect)
    return -EINVAL;
  assert(!current->timer && !current->loop);
  uint8_t memory[1024];
  negotiated(current, SPA_PARAM_Format, intersect(params[0], memory));
  if (destroy_connect)
    destroyed(current);
  return 0;
}

static void run(const struct spa_pod *consumer, unsigned expected, unsigned applied,
                unsigned preference) {
  assert(track_connect((void *)consumer, PW_DIRECTION_INPUT, 7, PW_STREAM_FLAG_AUTOCONNECT,
                       &consumer, 1) == 0);
  assert(atomic_load(&counts.active) == 1 && current->preferred == preference);
  assert(atomic_load(&counts.negotiated_num) == expected);
  assert(current->applied == applied && !current->pending);
  // A late rate change never triggers update_params or a timer in initial mode.
  atomic_store(&requested, 30);
  apply_pending(current);
  assert(updates == 0);
  destroyed(current);
  assert(!streams && atomic_load(&counts.active) == 0);
  assert(atomic_load(&counts.negotiated_num) == 0);
  atomic_store(&requested, 60);
}

int main(void) {
  uint8_t receiver_memory[512], producer_memory[512], fixed_memory[512];
  struct spa_pod *receiver = offer(receiver_memory, 0, 0, false);
  runtime_mode = false;
  atomic_store(&requested, 60);
  original_connect = connect_stream;
  original_update = update;
  add_listener = listen;
  // Loop/core symbols intentionally remain NULL: initial mode cannot use them.
  struct spa_pod *preferred = preferred_pod(receiver, 60);
  assert(preferred && !memcmp((uint8_t *)preferred + 8, receiver_memory + 8, receiver->size));
  const struct spa_pod_prop *prop =
      spa_pod_find_prop(preferred, NULL, SPA_FORMAT_VIDEO_maxFramerate);
  const struct spa_fraction *values = SPA_POD_CHOICE_VALUES(&prop->value);
  assert(prop->flags == SPA_POD_PROP_FLAG_DROP);
  assert(values[0].num == 60 && values[1].num == 0 && values[2].num == UINT32_MAX);
  free(preferred);

  producer = offer(producer_memory, 0, 240, true);
  run(receiver, 60, 60, 60);
  producer = offer(producer_memory, 0, 240, false);
  run(receiver, 240, 0, 60); // Fixed producer maximum survives fallback.
  producer = offer(producer_memory, 0, 30, true);
  run(receiver, 30, 0, 60); // A lower producer maximum remains compatible.
  producer = offer(producer_memory, 0, 0, false);
  run(receiver, 0, 0, 60); // DROP does not manufacture producer support.

  struct spa_pod *fixed = offer(fixed_memory, 60, 0, false);
  assert(!preferred_pod(fixed, 60));
  producer = offer(producer_memory, 60, 0, false);
  run(fixed, 0, 0, 0); // Original fixed-framerate compatibility is unchanged.
  fixed = offer(fixed_memory, 0, 120, false);
  assert(!preferred_pod(fixed, 60));
  producer = offer(producer_memory, 0, 240, true);
  run(fixed, 120, 0, 0); // Existing native maximum remains authoritative.

  // Reviewed Discord offer: its default25 is incompatible with producer0.
  // PipeWire must retry the complete original0..1000 range; max preference60
  // still wins. Fixed-rate peers also remain inside that unchanged range.
  struct spa_pod *native = native_offer(fixed_memory);
  run(native, 60, 60, 60);
  producer = offer(producer_memory, 60, 0, false);
  run(native, 0, 0, 60);
  producer = offer(producer_memory, 0, 0, false);
  run(native, 0, 0, 60);
  producer = offer(producer_memory, 0, 240, true);

  atomic_store(&requested, 0);
  run(receiver, 240, 0, 0); // A connect before JS reports FPS remains native.
  assert(atomic_load(&counts.initial_without_rate) == 1);
  // Later native modifier/format offers pass through; we never republish caps.
  assert(upstream_update((void *)receiver, (const struct spa_pod **)&receiver, 1) == 0);
  assert(updates == 1);
  fail_connect = true;
  assert(track_connect((void *)receiver, PW_DIRECTION_INPUT, 7, PW_STREAM_FLAG_AUTOCONNECT,
                       (const struct spa_pod **)&receiver, 1) == -EINVAL);
  assert(!streams && atomic_load(&counts.active) == 0);
  fail_connect = false;
  destroy_connect = true;
  assert(track_connect((void *)receiver, PW_DIRECTION_INPUT, 7, PW_STREAM_FLAG_AUTOCONNECT,
                       (const struct spa_pod **)&receiver, 1) == 0);
  assert(!streams && atomic_load(&counts.active) == 0 && connects == 12);
  puts("initial preference: SPA fallback, absent/fixed rates, no timer/update and lifetime passed");
  return 0;
}
