// Metadata-only callbacks exercising the real Rust adapter over the C ABI.
#include <assert.h>
#include <errno.h>
#include <pipewire/pipewire.h>
#include <spa/param/video/raw.h>
#include <spa/pod/builder.h>
#include <spa/pod/iter.h>
#include <stdio.h>
#include <string.h>

struct fixture_native {
  int (*connect)(struct pw_stream *, enum pw_direction, uint32_t, enum pw_stream_flags,
                 const struct spa_pod **, uint32_t);
  int (*update)(struct pw_stream *, const struct spa_pod **, uint32_t);
  void (*listen)(struct pw_stream *, struct spa_hook *, const struct pw_stream_events *, void *);
  struct pw_core *(*core)(struct pw_stream *);
  struct pw_context *(*context)(struct pw_core *);
  struct pw_loop *(*loop)(struct pw_context *);
  enum pw_stream_state (*state)(struct pw_stream *, const char **);
};
extern bool fixture_initialize(const struct fixture_native *, bool);
extern int fixture_connect(struct pw_stream *, enum pw_direction, uint32_t, enum pw_stream_flags,
                           const struct spa_pod **, uint32_t);
extern int fixture_update(struct pw_stream *, const struct spa_pod **, uint32_t);
extern void fixture_request(unsigned);
extern size_t fixture_snapshot(char *, size_t);
static unsigned metric(const char *name) {
  char json[1024], key[80];
  assert(fixture_snapshot(json, sizeof(json)));
  snprintf(key, sizeof(key), "\"%s\":", name);
  const char *value = strstr(json, key);
  assert(value);
  unsigned result;
  assert(sscanf(value + strlen(key), "%u", &result) == 1);
  return result;
}
struct fixture_stream {
  const struct pw_stream_events *events;
  void *data;
  struct spa_list listeners;
  struct spa_hook *hook;
};
static void destroy(struct fixture_stream *stream) {
  if (stream->events) {
    const struct pw_stream_events *events = stream->events;
    void *data = stream->data;
    stream->events = NULL;
    events->destroy(data);
    assert(spa_list_is_empty(&stream->listeners));
    stream->hook = NULL;
  }
}
static bool destroy_listen;
static void unlisten(struct spa_hook *hook) {
  struct fixture_stream *stream = hook->priv;
  assert(spa_list_is_empty(&stream->listeners));
  stream->hook = NULL;
  stream->events = NULL;
}
static void listen_stream(struct pw_stream *raw, struct spa_hook *hook,
                          const struct pw_stream_events *events, void *data) {
  struct fixture_stream *stream = (void *)raw;
  assert(!stream->hook);
  spa_list_init(&stream->listeners);
  spa_list_append(&stream->listeners, &hook->link);
  stream->events = events;
  stream->data = data;
  stream->hook = hook;
  hook->priv = stream;
  hook->removed = unlisten;
  if (destroy_listen)
    destroy(stream);
}
static void observed(struct fixture_stream *stream, const struct spa_pod *pod) {
  assert(stream->events);
  stream->events->param_changed(stream->data, SPA_PARAM_Format, pod);
}
static struct spa_pod *offer(uint8_t *memory, uint32_t id, unsigned fixed, unsigned maximum) {
  struct spa_pod_builder builder = SPA_POD_BUILDER_INIT(memory, 512);
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
  spa_pod_builder_prop(&builder, SPA_FORMAT_VIDEO_modifier, SPA_POD_PROP_FLAG_MANDATORY);
  spa_pod_builder_long(&builder, INT64_C(0x0200000028a01f04));
  return spa_pod_builder_pop(&builder, &frame);
}
static unsigned maximum(const struct spa_pod *pod) {
  const struct spa_pod_prop *prop = spa_pod_find_prop(pod, NULL, SPA_FORMAT_VIDEO_maxFramerate);
  if (!prop)
    return 0;
  if (spa_pod_is_choice(&prop->value))
    return ((const struct spa_fraction *)SPA_POD_CHOICE_VALUES(&prop->value))[0].num;
  struct spa_fraction value;
  assert(spa_pod_get_fraction(&prop->value, &value) == 0);
  return value.num;
}
