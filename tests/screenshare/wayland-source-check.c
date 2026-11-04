// Exercise producer ownership and negotiation without a compositor/GPU/device.
#define main dpe_wayland_source_entrypoint
#include "../../integration/video/wayland-source.c"
#undef main
#include <assert.h>

static unsigned returned, queued, triggers, dequeues;
static struct pw_buffer *next_buffer;
static int submit_status;
int __wrap_pw_stream_return_buffer(struct pw_stream *stream, struct pw_buffer *buffer) {
  (void)stream;
  assert(buffer);
  returned++;
  return 0;
}
int __wrap_pw_stream_queue_buffer(struct pw_stream *stream, struct pw_buffer *buffer) {
  (void)stream;
  assert(buffer);
  queued++;
  return 0;
}
int __wrap_pw_stream_trigger_process(struct pw_stream *stream) {
  (void)stream;
  triggers++;
  return 0;
}
struct pw_buffer *__wrap_pw_stream_dequeue_buffer(struct pw_stream *stream) {
  (void)stream;
  dequeues++;
  struct pw_buffer *result = next_buffer;
  next_buffer = NULL;
  return result;
}
int __wrap_dpe_wl_capture_submit(struct dpe_wl_capture *capture,
                                 const struct dpe_wl_buffer *buffer) {
  (void)capture;
  assert(buffer);
  return submit_status;
}
static struct frame *frame_for(struct producer *p, struct pw_buffer *pw, unsigned generation) {
  struct frame *f = calloc(1, sizeof(*f));
  assert(f);
  f->owner = p;
  f->pw = pw;
  f->capture.generation = generation;
  for (unsigned i = 0; i < PLANES; i++)
    f->fds[i] = -1;
  pw->user_data = f;
  return f;
}
int main(void) {
  struct listed_output located = {0};
  geometry(&located, NULL, -2560, 360, 600, 340, 0, "Synthetic", "Monitor", 0);
  assert(located.positioned && located.x == -2560 && located.y == 360);
  logical_position(&located, NULL, -1280, 180);
  assert(located.logical_positioned && located.logical_x == -1280 && located.logical_y == 180);
  assert(parse_rate("{\"op\":\"rate\",\"fps\":60}") == 60);
  assert(parse_rate(" { \"op\" : \"rate\" , \"fps\" : 30 } ") == 30);
  assert(parse_rate("rate 15") == 15);
  assert(!parse_rate("rate 0") && !parse_rate("rate 241") && !parse_rate("rate -1"));
  assert(!parse_rate("{\"op\":\"rate\",\"fps\":60.1}"));
  assert(!parse_rate("{\"op\":\"rate\",\"fps\":60} trailing"));
  struct producer p = {.constraints = {.generation = 2, .width = 1920, .height = 1080},
                       .fps = 60,
                       .streaming = true,
                       .negotiated_generation = 2};
  p.negotiated_period_ns = UINT64_C(1000000000) / 30;
  update_period(&p);
  assert(p.period_ns == UINT64_C(1000000000) / 30);
  p.fps = 15;
  update_period(&p);
  assert(p.period_ns == UINT64_C(1000000000) / 15);
  p.fps = 60;
  update_period(&p);
  assert(p.period_ns == UINT64_C(1000000000) / 30);
  p.negotiated_period_ns = UINT64_C(1000000000) / 60;
  update_period(&p);
  assert(p.period_ns == UINT64_C(1000000000) / 60);
  struct pw_buffer pw = {0};
  struct frame *f = frame_for(&p, &pw, 1);
  p.pending = f;
  // Removing old format buffers cannot free a destination still owned by the
  // compositor. It is retained until the real frame callback retires it.
  buffer_removed(&p, &pw);
  assert(p.pending == f && f->retired && !f->pw && !pw.user_data);
  struct dpe_wl_frame result = {.generation = 1};
  frame_ready(&p, &f->capture, &result, 0);
  assert(!p.pending && !queued && !returned);

  f = frame_for(&p, &pw, 1);
  p.pending = f;
  frame_ready(&p, &f->capture, &result, 0);
  assert(!p.pending && returned == 1 && !queued);
  frame_destroy(f);

  f = frame_for(&p, &pw, 2);
  p.pending = f;
  result.generation = 2;
  frame_ready(&p, &f->capture, &result, -ESTALE);
  assert(!p.pending && returned == 2 && !queued && !p.failed);
  frame_destroy(f);

  struct spa_meta_header header = {0};
  struct spa_meta meta = {.type = SPA_META_Header, .size = sizeof(header), .data = &header};
  struct spa_buffer spa = {.n_metas = 1, .metas = &meta};
  pw.buffer = &spa;
  f = frame_for(&p, &pw, 2);
  p.pending = f;
  result.presentation_ns = 1234567890;
  frame_ready(&p, &f->capture, &result, 0);
  assert(!p.pending && queued == 1 && returned == 2 && header.pts == 1234567890);
  assert(p.sequence == 1);
  frame_destroy(f);

  // Graph callbacks cannot cause extra captures between timer grants. Missed
  // ticks cannot accumulate work, nor can a paused stream begin capture.
  process(&p);
  assert(!dequeues);
  tick(&p, 100);
  tick(&p, 1);
  assert(triggers == 1 && p.request_due);
  process(&p);
  process(&p);
  assert(dequeues == 1 && !p.request_due);
  p.pending = (void *)1;
  tick(&p, 1);
  assert(triggers == 1);
  p.pending = NULL;
  p.streaming = false;
  tick(&p, 1);
  assert(triggers == 1);

  p.constraints.format_count = 3;
  p.constraints.formats[0] = (struct dpe_wl_format){DRM_FORMAT_ARGB8888, 17, true};
  p.constraints.formats[1] = (struct dpe_wl_format){DRM_FORMAT_XRGB8888, 0, true};
  p.constraints.formats[2] = (struct dpe_wl_format){DRM_FORMAT_ARGB8888, 0, false};
  assert(permitted(&p, DRM_FORMAT_ARGB8888, 17));
  assert(!permitted(&p, DRM_FORMAT_ARGB8888, 0));
  assert(!permitted(&p, DRM_FORMAT_ARGB8888, DRM_FORMAT_MOD_INVALID));
  uint8_t bytes[4096];
  struct spa_pod_builder builder = SPA_POD_BUILDER_INIT(bytes, sizeof(bytes));
  const struct spa_pod *offers[4];
  assert(formats(&p, &builder, offers) == 2);
  struct spa_video_info_raw raw;
  assert(spa_format_video_raw_parse(offers[0], &raw) >= 0);
  assert(raw.format == SPA_VIDEO_FORMAT_BGRA && raw.size.width == 1920);
  assert(raw.flags & SPA_VIDEO_FLAG_MODIFIER_FIXATION_REQUIRED);

  // An unchanged layout can reuse its allocated pool after another constraint
  // batch even if PipeWire does not resend an identical Format callback.
  p.negotiated_width = 1920;
  p.negotiated_height = 1080;
  p.fourcc = DRM_FORMAT_ARGB8888;
  p.spa_format = SPA_VIDEO_FORMAT_BGRA;
  p.modifier = 17;
  struct dpe_wl_constraints newer = p.constraints;
  newer.generation = 3;
  adopt_constraints(&p, &newer);
  assert(p.negotiated_generation == 3);
  builder = SPA_POD_BUILDER_INIT(bytes, sizeof(bytes));
  assert(stable_formats(&p, &builder, offers) == 1);
  assert(spa_format_video_raw_parse(offers[0], &raw) >= 0);
  assert(raw.modifier == 17 && raw.format == SPA_VIDEO_FORMAT_BGRA);
  assert(!(raw.flags & SPA_VIDEO_FLAG_MODIFIER_FIXATION_REQUIRED));
  f = frame_for(&p, &pw, 2);
  f->capture.width = 1920;
  f->capture.height = 1080;
  f->capture.format = p.constraints.formats[0];
  p.streaming = p.request_due = true;
  next_buffer = &pw;
  submit_status = -EAGAIN; // Incomplete compositor constraint batch.
  unsigned previous_returned = returned;
  process(&p);
  assert(!p.failed && !p.pending && returned == previous_returned + 1);
  assert(f->capture.generation == 3);

  // Runtime failure returns, but never publishes, the failed destination.
  // Repeated failures back off; a later successful frame resets the backoff.
  unsigned previous_queued = queued;
  p.pending = f;
  result.generation = 3;
  frame_ready(&p, &f->capture, &result, -EAGAIN);
  assert(!p.failed && !p.pending && queued == previous_queued && p.transient_failures == 1);
  assert(p.retry_after_ns);
  p.retry_after_ns = UINT64_MAX;
  unsigned previous_triggers = triggers;
  tick(&p, 1);
  assert(triggers == previous_triggers);
  p.pending = f;
  frame_ready(&p, &f->capture, &result, 0);
  assert(queued == previous_queued + 1 && !p.transient_failures && !p.retry_after_ns);
  frame_destroy(f);

  // A resize offer can overtake the old Format callback. Old allocation
  // dimensions stay valid for retiring the old pool, while capture is paused
  // until a matching new negotiated layout arrives.
  newer.width = 1280;
  newer.generation = 4;
  adopt_constraints(&p, &newer);
  assert(p.negotiated_generation == 3 && p.negotiated_width == 1920);
  format_changed(&p, SPA_PARAM_Format, offers[0]);
  format_changed(&p, SPA_PARAM_Format, NULL);
  assert(!p.failed && p.negotiated_generation == 3 && p.negotiated_width == 1920);
  p.request_due = true;
  unsigned previous_dequeues = dequeues;
  process(&p);
  assert(!p.request_due && dequeues == previous_dequeues);
  // Matching negotiation need not cause a state_changed callback. The next
  // tick must still be able to grant work after that temporary mismatch.
  p.negotiated_generation = 4;
  previous_triggers = triggers;
  tick(&p, 1);
  assert(p.request_due && triggers == previous_triggers + 1);
  process(&p);
  assert(!p.request_due && dequeues == previous_dequeues + 1);
  puts(
      "direct source ownership, stale-frame exclusion, timer admission and modifier offers passed");
  return 0;
}
