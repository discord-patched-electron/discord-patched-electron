// SPDX-License-Identifier: GPL-3.0-only
// Selected KMS monitor -> owned GPU snapshot -> explicit-sync PipeWire source.
#define _GNU_SOURCE
#include "kms-client.h"
#include "kms-gpu.h"
#include "vkcapture-relay.h"
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <spa/param/video/format-utils.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <xf86drm.h>

struct source;
struct frame {
  struct source *owner;
  struct frame *next;
  struct pw_buffer *pw;
  struct dpe_kms_buffer *gpu;
  struct dpe_vk_relay *relay;
  bool busy, retired;
};
struct source {
  struct dpe_kms_output output;
  struct dpe_kms_client client;
  int metadata_fd;
  struct dpe_kms_inspector *inspector;
  struct dpe_kms_gpu *gpu;
  struct pw_main_loop *main;
  struct pw_stream *stream;
  struct spa_hook listener;
  struct spa_source *timer, *helper_io, *control;
  struct frame *frames, *pending;
  unsigned allocated, fps, negotiated_fps;
  bool streaming, negotiated, due, announced, closing;
  const char *error;
  uint64_t pending_deadline, sequence, delivered, released, skipped, first_layout_race;
  char command[128];
  unsigned command_size;
};
static uint64_t now_ns(void) {
  struct timespec now;
  clock_gettime(CLOCK_MONOTONIC, &now);
  return (uint64_t)now.tv_sec * 1000000000ull + now.tv_nsec;
}
static void fail(struct source *s, const char *reason) {
  if (!s->error) {
    s->error = reason;
    fprintf(stderr, "DPE KMS: stopped (%s), frames=%llu releases=%llu skipped=%llu\n", reason,
            (unsigned long long)s->delivered, (unsigned long long)s->released,
            (unsigned long long)s->skipped);
    printf("{\"event\":\"error\",\"backend\":\"kms\",\"reason\":\"%s\",\"errorCode\":\"%s\"}\n",
           reason, reason);
    fflush(stdout);
  }
  if (s->main)
    pw_main_loop_quit(s->main);
}
static void json_text(const char *text) {
  putchar('"');
  for (const unsigned char *p = (const unsigned char *)text; *p; p++) {
    if (*p == '"' || *p == '\\')
      printf("\\%c", *p);
    else if (*p < 32)
      printf("\\u%04x", *p);
    else
      putchar(*p);
  }
  putchar('"');
}
static int list_outputs(void) {
  struct dpe_kms_output outputs[DPE_KMS_OUTPUTS];
  unsigned count = dpe_kms_inventory(outputs), supported = 0;
  for (unsigned i = 0; i < count; i++)
    supported += outputs[i].supported;
  const char *reason = dpe_kms_helper_status();
  if (!reason && !supported)
    reason = count ? "unsupported" : "no-outputs";
  printf("{\"version\":1,\"backend\":\"kms\",\"available\":%s,\"reason\":",
         reason ? "false" : "true");
  if (reason)
    json_text(reason);
  else
    fputs("null", stdout);
  printf(",\"authorizationRequired\":%s,\"outputs\":[",
         dpe_kms_authorization_required() ? "true" : "false");
  for (unsigned i = 0; i < count; i++) {
    struct dpe_kms_output *o = &outputs[i];
    if (i)
      putchar(',');
    fputs("{\"id\":", stdout);
    json_text(o->id);
    fputs(",\"name\":", stdout);
    json_text(o->name);
    fputs(",\"description\":", stdout);
    json_text(o->name);
    printf(",\"width\":%u,\"height\":%u,\"supported\":%s,\"reason\":%s}", o->width, o->height,
           !reason && o->supported ? "true" : "false", o->supported ? "null" : "\"unsupported\"");
  }
  puts("]}");
  return 0;
}
static void timer_update(struct source *s) {
  unsigned fps = s->fps;
  if (s->negotiated_fps && fps > s->negotiated_fps)
    fps = s->negotiated_fps;
  uint64_t period = 1000000000ull / fps;
  struct timespec interval = {.tv_sec = period / 1000000000ull, .tv_nsec = period % 1000000000ull};
  if (pw_loop_update_timer(pw_main_loop_get_loop(s->main), s->timer, &interval, &interval, false) <
      0)
    fail(s, "capture-failed");
}
static void offer(struct source *s) {
  uint8_t memory[512];
  struct spa_pod_builder b = SPA_POD_BUILDER_INIT(memory, sizeof(memory));
  struct spa_pod_frame object;
  spa_pod_builder_push_object(&b, &object, SPA_TYPE_OBJECT_Format, SPA_PARAM_EnumFormat);
  spa_pod_builder_add(
      &b, SPA_FORMAT_mediaType, SPA_POD_Id(SPA_MEDIA_TYPE_video), SPA_FORMAT_mediaSubtype,
      SPA_POD_Id(SPA_MEDIA_SUBTYPE_raw), SPA_FORMAT_VIDEO_format, SPA_POD_Id(SPA_VIDEO_FORMAT_BGRA),
      SPA_FORMAT_VIDEO_size, SPA_POD_Rectangle(&SPA_RECTANGLE(s->output.width, s->output.height)),
      SPA_FORMAT_VIDEO_framerate, SPA_POD_Fraction(&SPA_FRACTION(0, 1)),
      SPA_FORMAT_VIDEO_maxFramerate,
      SPA_POD_CHOICE_RANGE_Fraction(&SPA_FRACTION(s->fps, 1), &SPA_FRACTION(1, 1),
                                    &SPA_FRACTION(s->fps, 1)),
      0);
  spa_pod_builder_prop(&b, SPA_FORMAT_VIDEO_modifier, SPA_POD_PROP_FLAG_MANDATORY);
  spa_pod_builder_long(&b, 0);
  const struct spa_pod *params[] = {spa_pod_builder_pop(&b, &object)};
  int result =
      pw_stream_get_state(s->stream, NULL) == PW_STREAM_STATE_UNCONNECTED
          ? pw_stream_connect(s->stream, PW_DIRECTION_OUTPUT, PW_ID_ANY,
                              PW_STREAM_FLAG_DRIVER | PW_STREAM_FLAG_ALLOC_BUFFERS, params, 1)
          : pw_stream_update_params(s->stream, params, 1);
  if (result < 0)
    fail(s, "pipewire-unavailable");
}
static void frame_destroy(struct frame *f) {
  struct source *s = f->owner;
  struct frame **at = &s->frames;
  while (*at && *at != f)
    at = &(*at)->next;
  if (*at)
    *at = f->next;
  if (s->allocated)
    s->allocated--;
  dpe_vk_relay_destroy(f->relay);
  dpe_kms_buffer_destroy(f->gpu);
  free(f);
}
static void released(void *opaque, bool okay) {
  struct frame *f = opaque;
  struct source *s = f->owner;
  f->busy = false;
  if (okay)
    s->released++;
  else
    fail(s, "capture-sync-failed");
  if (f->retired)
    frame_destroy(f);
}
static void add_buffer(void *opaque, struct pw_buffer *buffer) {
  struct source *s = opaque;
  if (!s->negotiated || s->allocated >= 16) {
    fail(s, "capture-buffer-limit");
    return;
  }
  struct frame *f = calloc(1, sizeof(*f));
  if (!f) {
    fail(s, "capture-failed");
    return;
  }
  f->owner = s;
  f->pw = buffer;
  f->next = s->frames;
  s->frames = f;
  s->allocated++;
  struct dpe_vk_packet packet;
  int fds[4];
  f->gpu = dpe_kms_buffer_create(s->gpu, s->output.width, s->output.height, &packet, fds);
  if (f->gpu)
    f->relay = dpe_vk_relay_create(dpe_kms_gpu_fd(s->gpu), pw_main_loop_get_loop(s->main),
                                   buffer->buffer, &packet, fds);
  if (!f->gpu || !f->relay) {
    frame_destroy(f);
    fail(s, "capture-sync-unavailable");
    return;
  }
  buffer->user_data = f;
}
static void remove_buffer(void *opaque, struct pw_buffer *buffer) {
  struct source *s = opaque;
  struct frame *f = buffer->user_data;
  buffer->user_data = NULL;
  if (!f)
    return;
  f->pw = NULL;
  f->retired = true;
  // A removed PipeWire buffer is not a GPU release. Keep storage until its
  // consumer timeline completes, or until the entire source is destroyed.
  if (!f->busy && s->pending != f)
    frame_destroy(f);
}
static void format(void *opaque, uint32_t id, const struct spa_pod *pod) {
  struct source *s = opaque;
  if (id != SPA_PARAM_Format)
    return;
  if (!pod) {
    s->negotiated = false;
    return;
  }
  struct spa_video_info_raw raw = {0};
  if (spa_format_video_raw_parse(pod, &raw) < 0 || raw.format != SPA_VIDEO_FORMAT_BGRA ||
      raw.size.width != s->output.width || raw.size.height != s->output.height ||
      !(raw.flags & SPA_VIDEO_FLAG_MODIFIER) || raw.modifier) {
    fprintf(stderr, "DPE KMS: unsupported format %u %ux%u flags=%u modifier=%llu\n", raw.format,
            raw.size.width, raw.size.height, raw.flags, (unsigned long long)raw.modifier);
    fail(s, "unsupported");
    return;
  }
  struct spa_fraction rate = raw.framerate.num ? raw.framerate : raw.max_framerate;
  if (!rate.denom || rate.num < rate.denom || (uint64_t)rate.num > 240ull * rate.denom) {
    fprintf(stderr, "DPE KMS: unsupported rate %u/%u max=%u/%u\n", raw.framerate.num,
            raw.framerate.denom, raw.max_framerate.num, raw.max_framerate.denom);
    fail(s, "unsupported");
    return;
  }
  s->negotiated_fps = rate.num / rate.denom;
  s->negotiated = true;
  timer_update(s);
  uint8_t memory[1024];
  struct spa_pod_builder b = SPA_POD_BUILDER_INIT(memory, sizeof(memory));
  struct spa_pod_frame buffers;
  const struct spa_pod *params[3];
  spa_pod_builder_push_object(&b, &buffers, SPA_TYPE_OBJECT_ParamBuffers, SPA_PARAM_Buffers);
  spa_pod_builder_add(&b, SPA_PARAM_BUFFERS_buffers, SPA_POD_CHOICE_RANGE_Int(4, 2, 8),
                      SPA_PARAM_BUFFERS_blocks, SPA_POD_Int(3), SPA_PARAM_BUFFERS_dataType,
                      SPA_POD_CHOICE_FLAGS_Int(1 << SPA_DATA_DmaBuf), 0);
  spa_pod_builder_prop(&b, DPE_RELAY_META_TYPE, SPA_POD_PROP_FLAG_MANDATORY);
  spa_pod_builder_int(&b, 1 << DPE_RELAY_META);
  params[0] = spa_pod_builder_pop(&b, &buffers);
  params[1] =
      spa_pod_builder_add_object(&b, SPA_TYPE_OBJECT_ParamMeta, SPA_PARAM_Meta, SPA_PARAM_META_type,
                                 SPA_POD_Id(DPE_RELAY_META), SPA_PARAM_META_size,
                                 SPA_POD_Int(sizeof(struct dpe_relay_timeline)));
  params[2] =
      spa_pod_builder_add_object(&b, SPA_TYPE_OBJECT_ParamMeta, SPA_PARAM_Meta, SPA_PARAM_META_type,
                                 SPA_POD_Id(SPA_META_Header), SPA_PARAM_META_size,
                                 SPA_POD_Int(sizeof(struct spa_meta_header)));
  if (pw_stream_update_params(s->stream, params, 3) < 0)
    fail(s, "pipewire-unavailable");
}
static void process(void *opaque) {
  struct source *s = opaque;
  if (!s->due)
    return;
  s->due = false;
  if (!s->streaming || !s->negotiated || s->pending || s->closing)
    return;
  struct pw_buffer *blocked[8];
  unsigned blocked_count = 0;
  struct frame *frame = NULL;
  for (unsigned i = 0; i < 8; i++) {
    struct pw_buffer *buffer = pw_stream_dequeue_buffer(s->stream);
    if (!buffer)
      break;
    struct frame *f = buffer->user_data;
    if (f && !f->busy && !f->retired) {
      frame = f;
      break;
    }
    blocked[blocked_count++] = buffer;
  }
  for (unsigned i = 0; i < blocked_count; i++)
    pw_stream_return_buffer(s->stream, blocked[i]);
  if (!frame) {
    s->skipped++;
    return;
  }
  s->pending = frame;
  s->pending_deadline = now_ns() + 500000000ull;
  if (!dpe_kms_request(&s->client))
    fail(s, "capture-helper-failed");
}
static void helper_input(void *opaque, int fd, uint32_t mask) {
  struct source *s = opaque;
  if (mask & (SPA_IO_ERR | SPA_IO_HUP)) {
    fail(s, "capture-helper-failed");
    return;
  }
  gsr_kms_response response = {0};
  int result = dpe_kms_receive(fd, &response);
  if (!result)
    return;
  if (result < 0 || !s->pending) {
    fprintf(stderr, "DPE KMS: invalid helper response (result=%d pending=%d)\n", result,
            s->pending != NULL);
    if (result > 0)
      dpe_kms_response_close(&response);
    fail(s, result == DPE_KMS_PERMISSION_DENIED ? "permission-required" : "unsupported");
    return;
  }
  struct frame *frame = s->pending;
  s->pending = NULL;
  struct dpe_kms_layout layout;
  struct dpe_kms_scene scene;
  bool metadata_valid = dpe_kms_metadata(s->inspector, &layout);
  enum dpe_kms_scene_result result_scene =
      metadata_valid ? dpe_kms_scene(&response, &s->output, &layout, &scene) : DPE_KMS_UNSUPPORTED;
  if (frame->retired) {
    dpe_kms_response_close(&response);
    frame_destroy(frame);
    return;
  }
  if (result_scene == DPE_KMS_CHANGED) {
    // Cursor enable/disable can race GET_KMS. Drop the snapshot and retry at
    // normal cadence, with no accumulated credits or unbounded retry interval.
    dpe_kms_response_close(&response);
    pw_stream_return_buffer(s->stream, frame->pw);
    s->skipped++;
    uint64_t now = now_ns();
    if (!s->first_layout_race)
      s->first_layout_race = now;
    else if (now - s->first_layout_race > 2000000000ull)
      fail(s, "capture-layout-changed");
    return;
  }
  s->first_layout_race = 0;
  bool valid = result_scene == DPE_KMS_VALID;
  if (!valid) {
    fprintf(stderr, "DPE KMS: unsupported scene metadata=%d planes=%u items=%d opaque=%d\n",
            metadata_valid, layout.planes, response.num_items, layout.primary_needs_opaque);
    for (int i = 0; i < response.num_items; i++) {
      const gsr_kms_response_item *p = &response.items[i];
      if (p->connector_id == s->output.connector)
        fprintf(stderr,
                "DPE KMS: plane type=%d format=%08x rotation=%d z=%d hdr=%d eotf=%u "
                "buffer=%ux%u source=%d,%d,%d,%d destination=%d,%d,%d,%d\n",
                p->plane_type, p->pixel_format, p->rotation, p->zpos, p->has_hdr_metadata,
                p->hdr_metadata.hdmi_metadata_type1.eotf, p->width, p->height, p->src_x, p->src_y,
                p->src_w, p->src_h, p->dst_x, p->dst_y, p->dst_w, p->dst_h);
    }
  }
  int acquire = valid ? dpe_kms_buffer_render(frame->gpu, &scene) : -1;
  dpe_kms_response_close(&response);
  if (acquire < 0) {
    pw_stream_return_buffer(s->stream, frame->pw);
    fail(s, valid ? "capture-gpu-failed" : "unsupported");
    return;
  }
  // Submit owns the sync_file even on failure. No output storage can be drawn
  // into again until released() verifies the consumer GPU's release timeline.
  if (dpe_vk_relay_submit(frame->relay, acquire, released, frame) < 0) {
    pw_stream_return_buffer(s->stream, frame->pw);
    fail(s, "capture-sync-failed");
    return;
  }
  frame->busy = true;
  struct spa_meta_header *header =
      spa_buffer_find_meta_data(frame->pw->buffer, SPA_META_Header, sizeof(*header));
  if (header)
    *header = (struct spa_meta_header){.pts = now_ns(), .seq = s->sequence++};
  if (pw_stream_queue_buffer(s->stream, frame->pw) < 0)
    fail(s, "pipewire-unavailable");
  else
    s->delivered++;
}
static void tick(void *opaque, uint64_t expirations) {
  (void)expirations;
  struct source *s = opaque;
  if (s->pending && now_ns() > s->pending_deadline) {
    fail(s, "capture-helper-timeout");
    return;
  }
  if (s->streaming && !s->pending && !s->due) {
    s->due = true;
    pw_stream_trigger_process(s->stream);
  }
}
static void state(void *opaque, enum pw_stream_state previous, enum pw_stream_state next,
                  const char *error) {
  fprintf(stderr, "DPE KMS: stream %s -> %s\n", pw_stream_state_as_string(previous),
          pw_stream_state_as_string(next));
  struct source *s = opaque;
  s->streaming = next == PW_STREAM_STATE_STREAMING;
  if (next == PW_STREAM_STATE_ERROR) {
    fprintf(stderr, "DPE KMS: PipeWire error: %s\n", error ? error : "unknown");
    fail(s, "pipewire-unavailable");
    return;
  }
  if (next == PW_STREAM_STATE_PAUSED && !s->announced &&
      pw_stream_get_node_id(s->stream) != PW_ID_ANY) {
    s->announced = true;
    printf("{\"event\":\"ready\",\"backend\":\"kms\",\"nodeId\":%u,\"width\":%u,\"height\":%u,"
           "\"fps\":%u,\"handoff\":\"copy\"}\n",
           pw_stream_get_node_id(s->stream), s->output.width, s->output.height, s->fps);
    fflush(stdout);
  }
}
static unsigned parse_rate(const char *line) {
  unsigned long rate = 0;
  int consumed = 0;
  if (sscanf(line, " { \"op\" : \"rate\" , \"fps\" : %lu } %n", &rate, &consumed) != 1 ||
      !consumed || line[consumed]) {
    consumed = 0;
    if (sscanf(line, "rate %lu %n", &rate, &consumed) != 1 || !consumed || line[consumed])
      return 0;
  }
  return rate >= 1 && rate <= 240 ? (unsigned)rate : 0;
}
static void commands(void *opaque, int fd, uint32_t mask) {
  struct source *s = opaque;
  char bytes[256];
  ssize_t size = mask & SPA_IO_IN ? read(fd, bytes, sizeof(bytes)) : 0;
  if (size < 0 && (errno == EAGAIN || errno == EINTR))
    return;
  if (size <= 0) {
    pw_main_loop_quit(s->main);
    return;
  }
  for (ssize_t i = 0; i < size; i++) {
    if (bytes[i] != '\n') {
      if (s->command_size >= sizeof(s->command) - 1) {
        fail(s, "capture-control-invalid");
        return;
      }
      s->command[s->command_size++] = bytes[i];
      continue;
    }
    s->command[s->command_size] = 0;
    if (!strcmp(s->command, "stop") || !strcmp(s->command, "{\"op\":\"stop\"}")) {
      pw_main_loop_quit(s->main);
      return;
    }
    unsigned rate = parse_rate(s->command);
    s->command_size = 0;
    if (!rate) {
      fail(s, "capture-control-invalid");
      return;
    }
    if (rate != s->fps) {
      fprintf(stderr, "DPE KMS: requested rate %u -> %u\n", s->fps, rate);
      s->fps = rate;
      s->due = false;
      timer_update(s);
      offer(s);
    }
  }
}
static void quit(void *opaque, int signal) {
  (void)signal;
  pw_main_loop_quit(((struct source *)opaque)->main);
}
int main(int argc, char **argv) {
  if (argc == 2 && !strcmp(argv[1], "--list"))
    return list_outputs();
  const char *selected = NULL, *render = "auto";
  bool authorize = false;
  unsigned fps = 0;
  for (int i = 1; i < argc; i += 2) {
    if (i + 1 >= argc)
      return 2;
    if (!strcmp(argv[i], "--output"))
      selected = argv[i + 1];
    else if (!strcmp(argv[i], "--render-node"))
      render = argv[i + 1];
    else if (!strcmp(argv[i], "--authorize") && !strcmp(argv[i + 1], "true"))
      authorize = true;
    else if (!strcmp(argv[i], "--fps")) {
      char *end;
      unsigned long value = strtoul(argv[i + 1], &end, 10);
      if (*end || value < 1 || value > 240)
        return 2;
      fps = value;
    } else
      return 2;
  }
  if (!selected || !fps)
    return 2;
  struct source s = {.client = {.fd = -1, .pid = -1}, .metadata_fd = -1, .fps = fps};
  const char *status = dpe_kms_helper_status();
  if (status) {
    fail(&s, status);
    return 1;
  }
  struct dpe_kms_output outputs[DPE_KMS_OUTPUTS];
  unsigned count = dpe_kms_inventory(outputs);
  bool found = false;
  for (unsigned i = 0; i < count; i++)
    if (!strcmp(outputs[i].id, selected) && outputs[i].supported) {
      s.output = outputs[i];
      found = true;
      break;
    }
  if (!found) {
    fail(&s, "unsupported");
    return 1;
  }
  if (strcmp(render, "auto") && strcmp(render, s.output.render)) {
    fail(&s, "unsupported");
    return 1;
  }
  if (!dpe_kms_client_open(&s.client, s.output.card, authorize)) {
    fail(&s, s.client.error ? s.client.error : "unsupported");
    return 1;
  }
  sigset_t signals;
  sigemptyset(&signals);
  sigaddset(&signals, SIGINT);
  sigaddset(&signals, SIGTERM);
  sigprocmask(SIG_BLOCK, &signals, NULL);
  pw_init(NULL, NULL);
  s.main = pw_main_loop_new(NULL);
  struct pw_context *context = NULL;
  struct pw_core *core = NULL;
  if (!s.main) {
    fail(&s, "pipewire-unavailable");
    goto done;
  }
  struct pw_loop *loop = pw_main_loop_get_loop(s.main);
  s.metadata_fd = open(s.output.card, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
  if (s.metadata_fd < 0 || drmSetClientCap(s.metadata_fd, DRM_CLIENT_CAP_UNIVERSAL_PLANES, 1) ||
      drmSetClientCap(s.metadata_fd, DRM_CLIENT_CAP_ATOMIC, 1)) {
    fail(&s, "unsupported");
    goto done;
  }
  s.inspector = dpe_kms_inspector_create(s.metadata_fd, &s.output);
  if (!s.inspector) {
    fail(&s, "unsupported");
    goto done;
  }
  s.gpu = dpe_kms_gpu_create(s.output.render);
  if (!s.gpu || !dpe_vk_relay_supported(dpe_kms_gpu_fd(s.gpu))) {
    fail(&s, "capture-sync-unavailable");
    goto done;
  }
  context = pw_context_new(loop, NULL, 0);
  core = context ? pw_context_connect(context, NULL, 0) : NULL;
  if (!core) {
    fail(&s, "pipewire-unavailable");
    goto done;
  }
  s.stream =
      pw_stream_new(core, "DPE KMS monitor capture",
                    pw_properties_new(PW_KEY_MEDIA_CLASS, "Video/Source", PW_KEY_NODE_NAME,
                                      "dpe-kms-capture", PW_KEY_NODE_DESCRIPTION,
                                      "DPE selected monitor", PW_KEY_NODE_VIRTUAL, "true", NULL));
  if (!s.stream) {
    fail(&s, "pipewire-unavailable");
    goto done;
  }
  const struct pw_stream_events events = {.version = PW_VERSION_STREAM_EVENTS,
                                          .state_changed = state,
                                          .param_changed = format,
                                          .add_buffer = add_buffer,
                                          .remove_buffer = remove_buffer,
                                          .process = process};
  pw_stream_add_listener(s.stream, &s.listener, &events, &s);
  s.timer = pw_loop_add_timer(loop, tick, &s);
  s.helper_io = pw_loop_add_io(loop, s.client.fd, SPA_IO_IN | SPA_IO_ERR | SPA_IO_HUP, false,
                               helper_input, &s);
  fcntl(STDIN_FILENO, F_SETFL, fcntl(STDIN_FILENO, F_GETFL) | O_NONBLOCK);
  s.control =
      pw_loop_add_io(loop, STDIN_FILENO, SPA_IO_IN | SPA_IO_ERR | SPA_IO_HUP, false, commands, &s);
  pw_loop_add_signal(loop, SIGINT, quit, &s);
  pw_loop_add_signal(loop, SIGTERM, quit, &s);
  if (!s.timer || !s.helper_io || !s.control) {
    fail(&s, "capture-failed");
    goto done;
  }
  timer_update(&s);
  offer(&s);
  if (!s.error)
    pw_main_loop_run(s.main);
done:
  s.closing = true;
  dpe_kms_client_close(&s.client);
  if (s.main) {
    struct pw_loop *loop = pw_main_loop_get_loop(s.main);
    if (s.timer)
      pw_loop_destroy_source(loop, s.timer);
    if (s.helper_io)
      pw_loop_destroy_source(loop, s.helper_io);
    if (s.control)
      pw_loop_destroy_source(loop, s.control);
  }
  if (s.stream)
    pw_stream_destroy(s.stream);
  // No more writes occur after source shutdown. Consumers keep their own
  // DMA-BUF references until their GPU work completes; storage is never reused.
  while (s.frames)
    frame_destroy(s.frames);
  dpe_kms_gpu_destroy(s.gpu);
  dpe_kms_inspector_destroy(s.inspector);
  if (s.metadata_fd >= 0)
    close(s.metadata_fd);
  if (core)
    pw_core_disconnect(core);
  if (context)
    pw_context_destroy(context);
  if (s.main)
    pw_main_loop_destroy(s.main);
  pw_deinit();
  printf("{\"event\":\"ended\",\"backend\":\"kms\",\"frames\":%llu,\"releases\":%llu,\"skipped\":%"
         "llu,\"failed\":%s,\"errorCode\":\"%s\"}\n",
         (unsigned long long)s.delivered, (unsigned long long)s.released,
         (unsigned long long)s.skipped, s.error ? "true" : "false", s.error ? s.error : "");
  return s.error ? 1 : 0;
}
