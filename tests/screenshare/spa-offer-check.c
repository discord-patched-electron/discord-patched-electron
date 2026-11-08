// SPDX-License-Identifier: MIT
// Actual SPA builder, intersection and fixation; no daemon, devices or pixels.
#include <assert.h>
#include <inttypes.h>
#include <spa/param/video/format-utils.h>
#include <spa/pod/builder.h>
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wunused-parameter"
#include <spa/pod/filter.h>
#pragma GCC diagnostic pop
#include <stdio.h>
#include <string.h>

static struct spa_pod *offer(void *memory, size_t size, const char *kind) {
  struct spa_pod_builder builder = SPA_POD_BUILDER_INIT(memory, size);
  struct spa_pod_frame frame;
  spa_pod_builder_push_object(&builder, &frame, SPA_TYPE_OBJECT_Format, SPA_PARAM_EnumFormat);
  spa_pod_builder_add(&builder, SPA_FORMAT_mediaType, SPA_POD_Id(SPA_MEDIA_TYPE_video),
                      SPA_FORMAT_mediaSubtype, SPA_POD_Id(SPA_MEDIA_SUBTYPE_raw),
                      SPA_FORMAT_VIDEO_format, SPA_POD_Id(SPA_VIDEO_FORMAT_BGRA),
                      SPA_FORMAT_VIDEO_size, SPA_POD_Rectangle(&SPA_RECTANGLE(1920, 1080)), 0);
  if (!strcmp(kind, "native")) {
    spa_pod_builder_add(&builder, SPA_FORMAT_VIDEO_framerate,
                        SPA_POD_CHOICE_RANGE_Fraction(&SPA_FRACTION(25, 1), &SPA_FRACTION(0, 1),
                                                      &SPA_FRACTION(1000, 1)),
                        0);
  } else {
    unsigned rate = !strcmp(kind, "fixed-rate") || !strcmp(kind, "fixed") ? 60 : 0;
    spa_pod_builder_add(&builder, SPA_FORMAT_VIDEO_framerate,
                        SPA_POD_Fraction(&SPA_FRACTION(rate, 1)), 0);
  }
  if (!strcmp(kind, "range240") || !strcmp(kind, "range30")) {
    unsigned maximum = !strcmp(kind, "range30") ? 30 : 240;
    spa_pod_builder_add(&builder, SPA_FORMAT_VIDEO_maxFramerate,
                        SPA_POD_CHOICE_RANGE_Fraction(&SPA_FRACTION(maximum, 1),
                                                      &SPA_FRACTION(1, 1),
                                                      &SPA_FRACTION(maximum, 1)),
                        0);
  } else if (!strcmp(kind, "fixed240") || !strcmp(kind, "maximum")) {
    unsigned maximum = !strcmp(kind, "maximum") ? 120 : 240;
    spa_pod_builder_add(&builder, SPA_FORMAT_VIDEO_maxFramerate,
                        SPA_POD_Fraction(&SPA_FRACTION(maximum, 1)), 0);
  }
  spa_pod_builder_prop(&builder, SPA_FORMAT_VIDEO_modifier, SPA_POD_PROP_FLAG_MANDATORY);
  spa_pod_builder_long(&builder, INT64_C(0x0200000028a01f04));
  return spa_pod_builder_pop(&builder, &frame);
}
int main(int argc, char **argv) {
  if (argc == 2 && !strcmp(argv[1], "supports-drop")) {
#ifdef SPA_POD_PROP_FLAG_DROP
    puts("1");
#else
    puts("0");
#endif
    return 0;
  }
  union {
    uint64_t align;
    unsigned char bytes[16384];
  } input, preferred, memory, producer_memory;
  if (argc == 2) {
    const struct spa_pod *pod = offer(input.bytes, sizeof(input.bytes), argv[1]);
    assert(pod && SPA_POD_SIZE(pod) <= sizeof(input.bytes));
    assert(fwrite(pod, SPA_POD_SIZE(pod), 1, stdout) == 1);
    return 0;
  }
  assert(argc == 3 && !strcmp(argv[1], "filter"));
  size_t size = fread(input.bytes, 1, sizeof(input.bytes), stdin);
  assert(size >= sizeof(struct spa_pod));
  const struct spa_pod *consumer = (const void *)input.bytes;
  assert(SPA_POD_SIZE(consumer) == size);
  const struct spa_pod *producer =
      offer(producer_memory.bytes, sizeof(producer_memory.bytes), argv[2]);
  memcpy(preferred.bytes, input.bytes, size);
  struct spa_pod *fixed = (void *)preferred.bytes, *result = NULL;
  struct spa_pod_builder builder = SPA_POD_BUILDER_INIT(memory.bytes, sizeof(memory.bytes));
  int modified = 0;
#ifdef SPA_POD_PROP_FLAG_DROP
  modified = spa_pod_filter_make(fixed);
#endif
  int status = spa_pod_filter(&builder, &result, producer, fixed);
  if (status < 0 && modified > 0) {
    spa_pod_builder_init(&builder, memory.bytes, sizeof(memory.bytes));
    status = spa_pod_filter(&builder, &result, producer, consumer);
  }
  assert(status >= 0 && result && spa_pod_fixate(result) >= 0);
  struct spa_video_info_raw raw = {0};
  assert(spa_format_video_raw_parse(result, &raw) >= 0);
  printf("{\"maximum\":%u,\"rate\":%u,\"width\":%u,\"height\":%u,\"modifier\":\"%016" PRIx64
         "\"}\n",
         raw.max_framerate.num, raw.framerate.num, raw.size.width, raw.size.height, raw.modifier);
}
