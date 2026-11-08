// Real SPA parser oracle for Rust producer offers, with callback reentry.
#include "../../integration/video/source-pods.h"
#include "../../integration/video/vkcapture-relay.h"
#include <assert.h>
#include <errno.h>
#include <spa/param/video/format-utils.h>
#include <stdio.h>
#include <string.h>
static unsigned calls;
static struct spa_video_info_raw parse_format(const struct spa_pod *pod) {
  uint64_t words[128];
  assert(SPA_POD_SIZE(pod) <= sizeof(words));
  memcpy(words, pod, SPA_POD_SIZE(pod));
  assert(spa_pod_fixate((struct spa_pod *)words) >= 0);
  struct spa_video_info_raw raw = {0};
  assert(spa_format_video_raw_parse((const struct spa_pod *)words, &raw) >= 0);
  return raw;
}
static int property(const struct spa_pod *pod, uint32_t key, uint32_t expected_choice,
                    uint32_t expected_values) {
  const struct spa_pod_prop *property = spa_pod_find_prop(pod, NULL, key);
  assert(property);
  uint32_t count, choice;
  const struct spa_pod *value = spa_pod_get_values(&property->value, &count, &choice);
  int32_t result;
  assert(choice == expected_choice && count == expected_values && !spa_pod_get_int(value, &result));
  return result;
}
static int nested(void *data, const struct spa_pod **params, uint32_t count) {
  (void)data;
  assert(count == 1);
  struct spa_video_info_raw raw = parse_format(params[0]);
  assert(raw.size.width == 1280 && raw.size.height == 720 && raw.max_framerate.num == 30);
  return -ECANCELED;
}
static int format(void *data, const struct spa_pod **params, uint32_t count) {
  (void)data;
  assert(count == 1 && !((uintptr_t)params[0] % 8));
  assert(SPA_POD_SIZE(params[0]) < 512);
  uint8_t saved[512];
  memcpy(saved, params[0], SPA_POD_SIZE(params[0]));
  struct spa_video_info_raw raw = parse_format(params[0]);
  assert(raw.format == SPA_VIDEO_FORMAT_BGRA && raw.size.width == 1920 && raw.size.height == 1080);
  assert(!raw.framerate.num && raw.framerate.denom == 1 && raw.max_framerate.num == 240 &&
         raw.max_framerate.denom == 1);
  assert((raw.flags & SPA_VIDEO_FLAG_MODIFIER) && raw.modifier == UINT64_C(0x0200000028a01f04));
  assert(dpe_source_format(1280, 720, SPA_VIDEO_FORMAT_BGRA, 0, 30, NULL, nested) == -ECANCELED);
  assert(!memcmp(saved, params[0], SPA_POD_SIZE(params[0])));
  calls++;
  return 17;
}
static int buffers(void *data, const struct spa_pod **params, uint32_t count) {
  unsigned planes = *(unsigned *)data;
  assert(count == (planes ? 3u : 2u));
  assert(spa_pod_is_object_type(params[0], SPA_TYPE_OBJECT_ParamBuffers));
  assert(property(params[0], SPA_PARAM_BUFFERS_buffers, SPA_CHOICE_Range, 3) == 4);
  assert(property(params[0], SPA_PARAM_BUFFERS_blocks, SPA_CHOICE_None, 1) ==
         (int)(planes ? planes + 2 : 1));
  assert(property(params[0], SPA_PARAM_BUFFERS_dataType, SPA_CHOICE_Flags, 1) ==
         1 << SPA_DATA_DmaBuf);
  if (planes) {
    assert(property(params[0], DPE_RELAY_META_TYPE, SPA_CHOICE_None, 1) == 1 << DPE_RELAY_META);
    assert(spa_pod_is_object_type(params[1], SPA_TYPE_OBJECT_ParamMeta));
    assert(property(params[1], SPA_PARAM_META_size, SPA_CHOICE_None, 1) ==
           sizeof(struct dpe_relay_timeline));
    const struct spa_pod_prop *type = spa_pod_find_prop(params[1], NULL, SPA_PARAM_META_type);
    uint32_t value;
    assert(type && !spa_pod_get_id(&type->value, &value) && value == DPE_RELAY_META);
  } else {
    assert(property(params[0], SPA_PARAM_BUFFERS_size, SPA_CHOICE_None, 1) == 1920 * 1080 * 4);
    assert(property(params[0], SPA_PARAM_BUFFERS_stride, SPA_CHOICE_None, 1) == 1920 * 4);
  }
  assert(property(params[count - 1], SPA_PARAM_META_size, SPA_CHOICE_None, 1) ==
         sizeof(struct spa_meta_header));
  calls++;
  return 0;
}
int main(void) {
  assert(dpe_source_format(1920, 1080, SPA_VIDEO_FORMAT_BGRA, UINT64_C(0x0200000028a01f04), 240,
                           NULL, format) == 17);
  for (unsigned planes = 0; planes <= 4; planes++)
    assert(!dpe_source_buffers(1920, 1080, planes, &planes, buffers));
  assert(calls == 6);
  assert(dpe_source_format(0, 1080, SPA_VIDEO_FORMAT_BGRA, 0, 60, NULL, format) == -EINVAL);
  assert(dpe_source_format(1920, 1080, SPA_VIDEO_FORMAT_BGRA, 0, 0, NULL, format) == -EINVAL);
  assert(dpe_source_format(1920, 1080, SPA_VIDEO_FORMAT_BGRA, 0, 241, NULL, format) == -EINVAL);
  assert(dpe_source_buffers(8193, 1080, 1, NULL, buffers) == -EINVAL);
  assert(dpe_source_buffers(1920, 1080, 5, NULL, buffers) == -EINVAL);
  assert(dpe_source_buffers(1920, 1080, 1, NULL, NULL) == -EINVAL);
  assert(calls == 6);
  puts("Rust producer format/buffer PODs pass real SPA parsing, bounds and reentry");
}
