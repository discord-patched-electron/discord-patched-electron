#include "../../integration/video/capture-upstream.c"
#include <assert.h>
#include <errno.h>
#include <spa/pod/builder.h>

static struct upstream *current;
static unsigned calls, last_max, reject_next;
static bool destroy_during_update, suppress_ack, nested_update;
static const struct spa_pod *nested_pod;
static enum pw_stream_state fake_stream_state = PW_STREAM_STATE_STREAMING;
static enum pw_stream_state get_state(struct pw_stream *stream, const char **error) {
  (void)stream;
  (void)error;
  return fake_stream_state;
}
static unsigned maximum(const struct spa_pod *pod) {
  const struct spa_pod_prop *prop = spa_pod_find_prop(pod, NULL, SPA_FORMAT_VIDEO_maxFramerate);
  if (!prop)
    return 0;
  if (spa_pod_is_choice(&prop->value)) {
    const struct spa_fraction *range = SPA_POD_CHOICE_VALUES(&prop->value);
    return range[0].num;
  }
  struct spa_fraction value;
  assert(spa_pod_get_fraction(&prop->value, &value) == 0);
  return value.num;
}

static struct spa_pod *format(uint8_t *buffer, size_t size, uint32_t id, unsigned fixed,
                              unsigned maximum) {
  struct spa_pod_builder builder = SPA_POD_BUILDER_INIT(buffer, size);
  struct spa_pod_frame frame;
  spa_pod_builder_push_object(&builder, &frame, SPA_TYPE_OBJECT_Format, id);
  spa_pod_builder_add(&builder, SPA_FORMAT_mediaType, SPA_POD_Id(SPA_MEDIA_TYPE_video),
                      SPA_FORMAT_mediaSubtype, SPA_POD_Id(SPA_MEDIA_SUBTYPE_raw),
                      SPA_FORMAT_VIDEO_format, SPA_POD_Id(SPA_VIDEO_FORMAT_BGRA),
                      SPA_FORMAT_VIDEO_size, SPA_POD_Rectangle(&SPA_RECTANGLE(1920, 1080)),
                      SPA_FORMAT_VIDEO_framerate, SPA_POD_Fraction(&SPA_FRACTION(fixed, 1)), 0);
  if (maximum)
    spa_pod_builder_add(&builder, SPA_FORMAT_VIDEO_maxFramerate,
                        SPA_POD_Fraction(&SPA_FRACTION(maximum, 1)), 0);
  // Preserve a real modifier/property flag, rather than reconstructing only
  // enough of the format for the rate test to pass.
  spa_pod_builder_prop(&builder, SPA_FORMAT_VIDEO_modifier, SPA_POD_PROP_FLAG_MANDATORY);
  spa_pod_builder_long(&builder, INT64_C(0x0200000028a01f04));
  return spa_pod_builder_pop(&builder, &frame);
}

static int updated(struct pw_stream *stream, const struct spa_pod **params, uint32_t count) {
  (void)stream;
  assert(count == 1);
  calls++;
  struct spa_video_info_raw raw = {0};
  assert(spa_format_video_raw_parse(params[0], &raw) >= 0);
  last_max = maximum(params[0]);
  assert(raw.size.width == 1920 && raw.size.height == 1080);
  assert(raw.modifier == UINT64_C(0x0200000028a01f04));
  if (reject_next) {
    reject_next--;
    return -EINVAL;
  }
  if (nested_update) {
    nested_update = false;
    assert(track_update(current, &nested_pod, 1) >= 0);
  }
  if (destroy_during_update) {
    destroy_during_update = false;
    destroyed(current);
  } else if (!suppress_ack) {
    uint8_t memory[512];
    negotiated(current, SPA_PARAM_Format,
               format(memory, sizeof(memory), SPA_PARAM_Format, 0, last_max ? last_max : 240));
  }
  return 0;
}

static struct upstream *make_state(const struct spa_pod *pod) {
  struct upstream *state = calloc(1, sizeof(*state));
  assert(state && copy_caps(&state->original, &pod, 1));
  state->stream = (void *)state;
  state->owner = pthread_self();
  state->failed_generation = UINT64_MAX;
  atomic_init(&state->refs, 1);
  state->next = streams;
  streams = state;
  atomic_fetch_add(&counts.active, 1);
  return state;
}

static void request(unsigned fps) {
  atomic_store(&requested, ((atomic_load(&requested) + 256) & ~255ull) | fps);
}

int main(void) {
  runtime_mode = true;
  uint8_t memory[512], negotiated_memory[512], existing_memory[512];
  struct spa_pod *pod = format(memory, sizeof(memory), SPA_PARAM_EnumFormat, 0, 0);
  struct spa_pod *capped = capped_pod(pod, 60);
  assert(capped && capped->size == pod->size + 56);
  assert(!memcmp((uint8_t *)capped + 8, (uint8_t *)pod + 8, pod->size));
  struct spa_video_info_raw raw = {0};
  assert(spa_format_video_raw_parse(capped, &raw) >= 0 && raw.size.width == 1920 &&
         raw.modifier == UINT64_C(0x0200000028a01f04));
  const struct spa_pod_prop *maximum_prop =
      spa_pod_find_prop(capped, NULL, SPA_FORMAT_VIDEO_maxFramerate);
  assert(maximum_prop && SPA_POD_CHOICE_TYPE(&maximum_prop->value) == SPA_CHOICE_Range);
  const struct spa_fraction *range = SPA_POD_CHOICE_VALUES(&maximum_prop->value);
  assert(range[0].num == 60 && range[1].num == 1 && range[2].num == 60);
  free(capped);
  assert(!capped_pod(pod, 0) && !capped_pod(pod, 241));
  assert(!capped_pod(format(existing_memory, sizeof(existing_memory), SPA_PARAM_EnumFormat, 0, 120),
                     60));

  original_update = updated;
  stream_state = get_state;
  current = make_state(pod);
  request(60);
  apply_pending(current);
  assert(calls == 0); // No producer support observed yet.
  negotiated(current, SPA_PARAM_Format,
             format(negotiated_memory, sizeof(negotiated_memory), SPA_PARAM_Format, 60, 240));
  apply_pending(current);
  assert(atomic_load(&counts.width) == 1920 && atomic_load(&counts.height) == 1080);
  struct spa_pod_prop *size_prop = (struct spa_pod_prop *)spa_pod_find_prop(
      (struct spa_pod *)negotiated_memory, NULL, SPA_FORMAT_VIDEO_size);
  assert(size_prop);
  struct spa_rectangle *resize = SPA_POD_BODY(&size_prop->value);
  resize->width = 1280;
  resize->height = 720;
  negotiated(current, SPA_PARAM_Format, (struct spa_pod *)negotiated_memory);
  assert(atomic_load(&counts.width) == 1280 && atomic_load(&counts.height) == 720);

  assert(calls == 0); // Fixed framerate is unchanged even when max is present.
  negotiated(current, SPA_PARAM_Format,
             format(negotiated_memory, sizeof(negotiated_memory), SPA_PARAM_Format, 0, 0));
  apply_pending(current);
  assert(calls == 0); // Missing maximum is not treated as producer support.
  negotiated(current, SPA_PARAM_Format,
             format(negotiated_memory, sizeof(negotiated_memory), SPA_PARAM_Format, 0, 240));
  apply_pending(current);
  assert(calls == 1 && last_max == 60 && current->applied == 60);
  apply_pending(current);
  assert(calls == 1); // No renegotiation for every ordinary frame.
  request(30);
  apply_pending(current);
  assert(calls == 2 && last_max == 30 && current->applied == 30);
  request(60);
  apply_pending(current);
  assert(calls == 3 && last_max == 60 && current->applied == 60);
  request(0);
  apply_pending(current);
  assert(calls == 4 && last_max == 0 && current->applied == 0);
  assert(!spa_pod_find_prop(current->original.pods[0], NULL, SPA_FORMAT_VIDEO_maxFramerate));

  request(15);
  reject_next = 1;
  apply_pending(current);
  assert(calls == 5 && current->restore_needed && current->applied == 0);
  apply_pending(current);
  assert(calls == 6 && last_max == 0 && current->applied == 0);
  assert(atomic_load(&counts.failures) == 1);
  apply_pending(current);
  assert(calls == 6); // A rejecting producer is not retried each frame.
  request(30);
  apply_pending(current);
  assert(calls == 7 && last_max == 30 && current->applied == 30);

  // Publication is not acknowledgment. No applied-rate claim is made while
  // waiting, nor when a producer selects a nonmatching maximum.
  suppress_ack = true;
  request(60);
  apply_pending(current);
  assert(current->pending && current->applied == 0 && calls == 8);
  negotiated(current, SPA_PARAM_Format,
             format(negotiated_memory, sizeof(negotiated_memory), SPA_PARAM_Format, 0, 240));
  assert(current->pending && current->applied == 0);
  negotiated(current, SPA_PARAM_Format,
             format(negotiated_memory, sizeof(negotiated_memory), SPA_PARAM_Format, 0, 60));
  assert(!current->pending && current->applied == 60);

  // A stalled producer times out without any process callback. The original
  // offers are republished once; restoration is not claimed without an ack.
  request(30);
  apply_pending(current);
  current->deadline = 0;
  tick(current, 1);
  assert(current->disabled && current->pending && current->pending_fps == 0);
  negotiated(current, SPA_PARAM_Format,
             format(negotiated_memory, sizeof(negotiated_memory), SPA_PARAM_Format, 0, 30));
  assert(current->pending); // A late cap reply does not acknowledge restoration.
  unsigned after_restore = calls;
  current->deadline = 0;
  tick(current, 1);
  assert(!current->pending && current->applied == 0 && calls == after_restore);
  tick(current, 1);
  assert(calls == after_restore && atomic_load(&counts.timeouts) == 2);
  destroyed(current);

  // A true ERROR cannot be recovered merely by publishing offers. Disable the
  // experimental cap and leave the existing native error handling in control.
  current = make_state(pod);
  current->eligible = true;
  request(60);
  apply_pending(current);
  assert(current->pending);
  fake_stream_state = PW_STREAM_STATE_ERROR;
  state_changed(current, PW_STREAM_STATE_STREAMING, PW_STREAM_STATE_ERROR, "test");
  after_restore = calls;
  tick(current, 1);
  assert(current->disabled && !current->pending && calls == after_restore);
  destroyed(current);
  fake_stream_state = PW_STREAM_STATE_STREAMING;

  // A nested native update owns the newest original caps. The outer update
  // must not overwrite them, including when nested inside our own publication.
  current = make_state(pod);
  current->eligible = true;
  nested_pod = format(existing_memory, sizeof(existing_memory), SPA_PARAM_EnumFormat, 0, 120);
  nested_update = true;
  assert(track_update(current, (const struct spa_pod **)&pod, 1) >= 0);
  assert(maximum(current->original.pods[0]) == 120 && current->revision == 1);
  destroyed(current);
  current = make_state(pod);
  current->eligible = true;
  nested_update = true;
  request(60);
  apply_pending(current);
  assert(maximum(current->original.pods[0]) == 120 && current->revision == 1);
  assert(!current->pending && current->applied == 0);
  destroyed(current);

  // Destruction inside a synchronous callback must not free state/caps still
  // used by the caller. ASan/UBSan exercises this without any GPU session.
  current = make_state(pod);
  current->eligible = true;
  request(60);
  destroy_during_update = true;
  apply_pending(current);
  assert(!streams && atomic_load(&counts.active) == 0);
  assert(atomic_load(&counts.applied_fps) == 0);
  puts("upstream acknowledgment, preservation, timeout and reentrant cleanup passed");
  return 0;
}
