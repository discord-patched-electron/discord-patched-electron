// Optional direct-output capture producer. No source is selected implicitly.
// GBM storage goes from compositor completion to PipeWire consumer ownership;
// there is no CPU pixel mapping, readback, or global portal replacement.
#define _GNU_SOURCE
#include "linux-dmabuf-v1-client.h"
#include "wayland-capture.h"
#include "xdg-output-unstable-v1-client.h"
#include <drm_fourcc.h>
#include <errno.h>
#include <fcntl.h>
#include <gbm.h>
#include <limits.h>
#include <pipewire/pipewire.h>
#include <signal.h>
#include <spa/param/video/format-utils.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#include <xf86drm.h>

#define OUTPUTS 32
#define PLANES 4
#define MODIFIERS 256
struct listed_output {
  struct wl_output *proxy;
  struct zxdg_output_v1 *logical;
  int32_t x, y, logical_x, logical_y;
  bool positioned, logical_positioned;
  uint32_t global, width, height, transform;
  char name[128], description[256];
  bool done;
};
struct inventory {
  struct wl_registry *registry;
  struct zwp_linux_dmabuf_v1 *dmabuf;
  struct zxdg_output_manager_v1 *output_manager;
  bool copy, source;
  struct listed_output outputs[OUTPUTS];
  unsigned count;
};
struct producer;
struct frame {
  struct producer *owner;
  struct pw_buffer *pw;
  struct gbm_bo *bo;
  struct dpe_wl_buffer capture;
  int fds[PLANES];
  bool retired;
};
struct producer {
  struct wl_display *display;
  struct inventory inventory;
  struct dpe_wl_capture *capture;
  struct dpe_wl_constraints constraints;
  struct pw_main_loop *main;
  struct pw_loop *loop;
  struct pw_context *context;
  struct pw_core *core;
  struct pw_stream *stream;
  struct spa_hook listener;
  struct spa_source *io, *timer, *control;
  struct gbm_device *gbm;
  drmDevicePtr device;
  int render_fd;
  struct frame *pending;
  struct gbm_bo *probe;
  uint32_t fourcc, spa_format, planes, fps, sequence, negotiated_width, negotiated_height;
  uint64_t modifier, negotiated_generation, period_ns, negotiated_period_ns;
  bool streaming, announced, closing, request_due;
  unsigned transient_failures;
  uint64_t retry_after_ns;
  int failed;
  char command[128];
  unsigned command_size;
};

static void text_json(const char *s) {
  putchar('"');
  for (const unsigned char *p = (const unsigned char *)s; *p; p++) {
    if (*p == '"' || *p == '\\')
      printf("\\%c", *p);
    else if (*p < 32)
      printf("\\u%04x", *p);
    else
      putchar(*p);
  }
  putchar('"');
}
static void geometry(void *data, struct wl_output *output, int32_t x, int32_t y, int32_t w,
                     int32_t h, int32_t subpixel, const char *make, const char *model,
                     int32_t transform) {
  (void)output;
  struct listed_output *entry = data;
  entry->x = x;
  entry->y = y;
  entry->positioned = true;
  (void)w;
  (void)h;
  (void)subpixel;
  (void)make;
  (void)model;
  ((struct listed_output *)data)->transform = transform;
}
static void mode(void *data, struct wl_output *output, uint32_t flags, int32_t w, int32_t h,
                 int32_t refresh) {
  (void)output;
  (void)refresh;
  if ((flags & WL_OUTPUT_MODE_CURRENT) && w > 0 && h > 0) {
    ((struct listed_output *)data)->width = w;
    ((struct listed_output *)data)->height = h;
  }
}
static void output_done(void *data, struct wl_output *output) {
  (void)output;
  ((struct listed_output *)data)->done = true;
}
static void scale(void *data, struct wl_output *output, int32_t factor) {
  (void)data;
  (void)output;
  (void)factor;
}
static void output_name(void *data, struct wl_output *output, const char *name) {
  (void)output;
  struct listed_output *entry = data;
  if (strlen(name) < sizeof(entry->name))
    strcpy(entry->name, name);
}
static void description(void *data, struct wl_output *output, const char *name) {
  (void)output;
  snprintf(((struct listed_output *)data)->description,
           sizeof(((struct listed_output *)data)->description), "%s", name);
}
static const struct wl_output_listener output_events = {geometry, mode,        output_done,
                                                        scale,    output_name, description};
static void logical_position(void *data, struct zxdg_output_v1 *output, int32_t x, int32_t y) {
  (void)output;
  struct listed_output *entry = data;
  entry->logical_x = x;
  entry->logical_y = y;
  entry->logical_positioned = true;
}
static void logical_size(void *data, struct zxdg_output_v1 *output, int32_t width, int32_t height) {
  (void)data;
  (void)output;
  (void)width;
  (void)height;
}
static void logical_done(void *data, struct zxdg_output_v1 *output) {
  (void)data;
  (void)output;
}
static void logical_name(void *data, struct zxdg_output_v1 *output, const char *name) {
  (void)data;
  (void)output;
  (void)name;
}
static const struct zxdg_output_v1_listener logical_events = {
    logical_position, logical_size, logical_done, logical_name, logical_name};
static void bind_logical_outputs(struct inventory *inventory) {
  if (!inventory->output_manager)
    return;
  for (unsigned n = 0; n < inventory->count; n++) {
    struct listed_output *entry = &inventory->outputs[n];
    if (!entry->logical && entry->proxy) {
      entry->logical =
          zxdg_output_manager_v1_get_xdg_output(inventory->output_manager, entry->proxy);
      zxdg_output_v1_add_listener(entry->logical, &logical_events, entry);
    }
  }
}
static void global(void *data, struct wl_registry *registry, uint32_t id, const char *interface,
                   uint32_t version) {
  struct inventory *inventory = data;
  if (!strcmp(interface, "ext_image_copy_capture_manager_v1"))
    inventory->copy = true;
  else if (!strcmp(interface, "ext_output_image_capture_source_manager_v1"))
    inventory->source = true;
  else if (!strcmp(interface, "zwp_linux_dmabuf_v1") && version >= 3 && !inventory->dmabuf)
    inventory->dmabuf = wl_registry_bind(registry, id, &zwp_linux_dmabuf_v1_interface, 3);
  else if (!strcmp(interface, "zxdg_output_manager_v1") && !inventory->output_manager) {
    inventory->output_manager = wl_registry_bind(registry, id, &zxdg_output_manager_v1_interface,
                                                 version < 3 ? version : 3);
    bind_logical_outputs(inventory);
  } else if (!strcmp(interface, "wl_output") && version >= 4 && inventory->count < OUTPUTS) {
    struct listed_output *entry = &inventory->outputs[inventory->count++];
    entry->global = id;
    entry->proxy = wl_registry_bind(registry, id, &wl_output_interface, 4);
    wl_output_add_listener(entry->proxy, &output_events, entry);
    bind_logical_outputs(inventory);
  }
}
static void removed(void *data, struct wl_registry *registry, uint32_t id) {
  (void)registry;
  struct inventory *inventory = data;
  for (unsigned i = 0; i < inventory->count; i++)
    if (inventory->outputs[i].global == id)
      inventory->outputs[i].done = false;
}
static const struct wl_registry_listener registry_events = {global, removed};
static bool available(const struct inventory *i) { return i->copy && i->source && i->dmabuf; }
static void inventory_destroy(struct inventory *i) {
  for (unsigned n = 0; n < i->count; n++) {
    if (i->outputs[n].logical)
      zxdg_output_v1_destroy(i->outputs[n].logical);
    if (i->outputs[n].proxy)
      wl_output_release(i->outputs[n].proxy);
  }
  if (i->output_manager)
    zxdg_output_manager_v1_destroy(i->output_manager);
  if (i->dmabuf)
    zwp_linux_dmabuf_v1_destroy(i->dmabuf);
  if (i->registry)
    wl_registry_destroy(i->registry);
}
static void list(const struct inventory *i) {
  printf(
      "{\"version\":1,\"backend\":\"wayland-direct\",\"available\":%s,\"reason\":%s,\"outputs\":[",
      available(i) ? "true" : "false", available(i) ? "null" : "\"protocol-unavailable\"");
  bool comma = false;
  for (unsigned n = 0; n < i->count; n++) {
    const struct listed_output *o = &i->outputs[n];
    if (!o->done || !*o->name)
      continue;
    printf("%s{\"id\":", comma ? "," : "");
    text_json(o->name);
    fputs(",\"name\":", stdout);
    text_json(o->name);
    fputs(",\"description\":", stdout);
    text_json(o->description);
    if (o->logical_positioned || o->positioned)
      printf(",\"position\":{\"x\":%d,\"y\":%d}", o->logical_positioned ? o->logical_x : o->x,
             o->logical_positioned ? o->logical_y : o->y);
    printf(",\"width\":%u,\"height\":%u,\"transform\":%u,\"supported\":%s,\"reason\":%s}", o->width,
           o->height, o->transform, available(i) && !o->transform ? "true" : "false",
           !available(i)  ? "\"protocol-unavailable\""
           : o->transform ? "\"transform-unsupported\""
                          : "null");
    comma = true;
  }
  puts("]}");
}
static void fail(struct producer *p, const char *reason) {
  if (!p->failed) {
    fputs("{\"event\":\"error\",\"backend\":\"wayland-direct\",\"reason\":", stdout);
    text_json(reason);
    puts("}");
    fflush(stdout);
  }
  p->failed = 1;
  pw_main_loop_quit(p->main);
}
static bool flush(struct producer *p) {
  int status = wl_display_flush(p->display);
  if (status < 0 && errno != EAGAIN) {
    fail(p, "wayland-disconnected");
    return false;
  }
  if (p->io)
    pw_loop_update_io(p->loop, p->io,
                      SPA_IO_IN | SPA_IO_ERR | SPA_IO_HUP | (status < 0 ? SPA_IO_OUT : 0));
  return true;
}
static void wayland_events(void *data, int fd, uint32_t mask) {
  (void)fd;
  struct producer *p = data;
  if ((mask & (SPA_IO_ERR | SPA_IO_HUP)) ||
      ((mask & SPA_IO_IN) && wl_display_dispatch(p->display) < 0)) {
    fail(p, "wayland-disconnected");
    return;
  }
  flush(p);
}
static uint32_t spa_for(uint32_t format) {
  switch (format) {
  case DRM_FORMAT_ARGB8888:
    return SPA_VIDEO_FORMAT_BGRA;
  case DRM_FORMAT_XRGB8888:
    return SPA_VIDEO_FORMAT_BGRx;
  case DRM_FORMAT_ABGR8888:
    return SPA_VIDEO_FORMAT_RGBA;
  case DRM_FORMAT_XBGR8888:
    return SPA_VIDEO_FORMAT_RGBx;
  default:
    return SPA_VIDEO_FORMAT_UNKNOWN;
  }
}
static uint32_t drm_for(uint32_t format) {
  const uint32_t formats[] = {DRM_FORMAT_ARGB8888, DRM_FORMAT_XRGB8888, DRM_FORMAT_ABGR8888,
                              DRM_FORMAT_XBGR8888};
  for (unsigned i = 0; i < 4; i++)
    if (spa_for(formats[i]) == format)
      return formats[i];
  return 0;
}
static bool accept_format(void *data, uint32_t format, bool dmabuf) {
  (void)data;
  return dmabuf && spa_for(format) != SPA_VIDEO_FORMAT_UNKNOWN;
}
static bool permitted(struct producer *p, uint32_t fourcc, uint64_t modifier) {
  for (unsigned i = 0; i < p->constraints.format_count; i++) {
    struct dpe_wl_format *f = &p->constraints.formats[i];
    if (f->dmabuf && f->format == fourcc && f->modifier == modifier &&
        modifier != DRM_FORMAT_MOD_INVALID)
      return true;
  }
  return false;
}
static bool current_layout(struct producer *p, uint32_t width, uint32_t height, uint32_t fourcc,
                           uint64_t modifier) {
  return width == p->constraints.width && height == p->constraints.height &&
         permitted(p, fourcc, modifier);
}
static bool probe_matches(struct producer *p, uint32_t width, uint32_t height, uint32_t fourcc,
                          uint64_t modifier) {
  return p->probe && gbm_bo_get_width(p->probe) == width && gbm_bo_get_height(p->probe) == height &&
         gbm_bo_get_format(p->probe) == fourcc && gbm_bo_get_modifier(p->probe) == modifier;
}
static void adopt_constraints(struct producer *p, const struct dpe_wl_constraints *value) {
  p->constraints = *value;
  bool compatible =
      current_layout(p, p->negotiated_width, p->negotiated_height, p->fourcc, p->modifier);
  if (compatible)
    p->negotiated_generation = value->generation;
  if (p->probe && !compatible) {
    gbm_bo_destroy(p->probe);
    p->probe = NULL;
  }
}
static const struct spa_pod *format_pod(struct producer *p, struct spa_pod_builder *b,
                                        uint32_t format, const uint64_t *modifiers, unsigned count,
                                        bool fixate) {
  struct spa_pod_frame object, choice;
  spa_pod_builder_push_object(b, &object, SPA_TYPE_OBJECT_Format, SPA_PARAM_EnumFormat);
  spa_pod_builder_add(
      b, SPA_FORMAT_mediaType, SPA_POD_Id(SPA_MEDIA_TYPE_video), SPA_FORMAT_mediaSubtype,
      SPA_POD_Id(SPA_MEDIA_SUBTYPE_raw), SPA_FORMAT_VIDEO_format, SPA_POD_Id(format),
      SPA_FORMAT_VIDEO_size,
      SPA_POD_Rectangle(&SPA_RECTANGLE(p->constraints.width, p->constraints.height)),
      SPA_FORMAT_VIDEO_framerate, SPA_POD_Fraction(&SPA_FRACTION(0, 1)),
      SPA_FORMAT_VIDEO_maxFramerate,
      SPA_POD_CHOICE_RANGE_Fraction(&SPA_FRACTION(p->fps, 1), &SPA_FRACTION(1, 1),
                                    &SPA_FRACTION(p->fps, 1)),
      0);
  spa_pod_builder_prop(b, SPA_FORMAT_VIDEO_modifier,
                       SPA_POD_PROP_FLAG_MANDATORY | (fixate ? SPA_POD_PROP_FLAG_DONT_FIXATE : 0));
  if (fixate) {
    spa_pod_builder_push_choice(b, &choice, SPA_CHOICE_Enum, 0);
    spa_pod_builder_long(b, modifiers[0]);
    for (unsigned i = 0; i < count; i++)
      spa_pod_builder_long(b, modifiers[i]);
    spa_pod_builder_pop(b, &choice);
  } else {
    spa_pod_builder_long(b, modifiers[0]);
  }
  return spa_pod_builder_pop(b, &object);
}
static unsigned formats(struct producer *p, struct spa_pod_builder *b,
                        const struct spa_pod **params) {
  const uint32_t fourccs[] = {DRM_FORMAT_ARGB8888, DRM_FORMAT_XRGB8888, DRM_FORMAT_ABGR8888,
                              DRM_FORMAT_XBGR8888};
  unsigned total = 0;
  for (unsigned f = 0; f < 4; f++) {
    uint64_t modifiers[MODIFIERS];
    unsigned count = 0;
    for (unsigned i = 0; i < p->constraints.format_count; i++) {
      struct dpe_wl_format *entry = &p->constraints.formats[i];
      if (entry->dmabuf && entry->format == fourccs[f] && entry->modifier != DRM_FORMAT_MOD_INVALID)
        modifiers[count++] = entry->modifier;
    }
    if (count)
      params[total++] = format_pod(p, b, spa_for(fourccs[f]), modifiers, count, true);
  }
  return total;
}
static unsigned stable_formats(struct producer *p, struct spa_pod_builder *b,
                               const struct spa_pod **params) {
  // Changing only the requested FPS must not reopen modifier negotiation for
  // a pool that the downstream GPU already imported successfully.
  if (p->negotiated_generation &&
      current_layout(p, p->negotiated_width, p->negotiated_height, p->fourcc, p->modifier)) {
    params[0] = format_pod(p, b, p->spa_format, &p->modifier, 1, false);
    return 1;
  }
  if (p->probe) {
    uint32_t fourcc = gbm_bo_get_format(p->probe);
    uint64_t modifier = gbm_bo_get_modifier(p->probe);
    if (current_layout(p, gbm_bo_get_width(p->probe), gbm_bo_get_height(p->probe), fourcc,
                       modifier)) {
      params[0] = format_pod(p, b, spa_for(fourcc), &modifier, 1, false);
      return 1;
    }
  }
  return formats(p, b, params);
}
static void frame_destroy(struct frame *f) {
  if (!f)
    return;
  if (f->capture.handle)
    wl_buffer_destroy(f->capture.handle);
  for (unsigned n = 0; n < PLANES; n++)
    if (f->fds[n] >= 0)
      close(f->fds[n]);
  if (f->bo)
    gbm_bo_destroy(f->bo);
  free(f);
}
static void buffer_removed(void *data, struct pw_buffer *buffer) {
  struct producer *p = data;
  struct frame *f = buffer->user_data;
  buffer->user_data = NULL;
  if (!f)
    return;
  f->pw = NULL;
  f->retired = true;
  // A format update can remove an in-flight compositor destination. Keep its
  // wl_buffer, BO and FDs until the frame callback or connection teardown.
  if (p->pending != f)
    frame_destroy(f);
}
static void buffer_added(void *data, struct pw_buffer *buffer) {
  struct producer *p = data;
  struct spa_buffer *spa = buffer->buffer;
  if (!p->negotiated_generation || spa->n_datas != p->planes) {
    fail(p, "buffer-plane-negotiation-failed");
    return;
  }
  struct frame *f = calloc(1, sizeof(*f));
  if (!f) {
    fail(p, "allocation-failed");
    return;
  }
  f->owner = p;
  f->pw = buffer;
  for (unsigned i = 0; i < PLANES; i++)
    f->fds[i] = -1;
  if (probe_matches(p, p->negotiated_width, p->negotiated_height, p->fourcc, p->modifier)) {
    f->bo = p->probe;
    p->probe = NULL;
  }
  if (!f->bo)
    f->bo = gbm_bo_create_with_modifiers2(p->gbm, p->negotiated_width, p->negotiated_height,
                                          p->fourcc, &p->modifier, 1, GBM_BO_USE_RENDERING);
  if (!f->bo || gbm_bo_get_modifier(f->bo) != p->modifier ||
      (unsigned)gbm_bo_get_plane_count(f->bo) != p->planes)
    goto failed;
  struct zwp_linux_buffer_params_v1 *params =
      zwp_linux_dmabuf_v1_create_params(p->inventory.dmabuf);
  if (!params)
    goto failed;
  bool valid = true;
  for (unsigned i = 0; i < p->planes; i++) {
    struct spa_data *d = &spa->datas[i];
    f->fds[i] = gbm_bo_get_fd_for_plane(f->bo, i);
    uint32_t stride = gbm_bo_get_stride_for_plane(f->bo, i), offset = gbm_bo_get_offset(f->bo, i);
    if (f->fds[i] < 0 || !stride || stride > INT32_MAX || !d->chunk ||
        !(d->type & (1u << SPA_DATA_DmaBuf))) {
      valid = false;
      break;
    }
    zwp_linux_buffer_params_v1_add(params, f->fds[i], i, offset, stride, p->modifier >> 32,
                                   p->modifier & UINT32_MAX);
    d->type = SPA_DATA_DmaBuf;
    d->flags = SPA_DATA_FLAG_READABLE;
    d->fd = f->fds[i];
    d->mapoffset = 0;
    d->maxsize = 0; // DMA-BUF size is not a pixel-mapping contract.
    d->data = NULL;
    // Some native consumers reject zero-size chunks even for DMA-BUF.
    *d->chunk = (struct spa_chunk){.offset = offset, .stride = stride, .size = 1};
  }
  if (valid)
    f->capture.handle = zwp_linux_buffer_params_v1_create_immed(params, p->negotiated_width,
                                                                p->negotiated_height, p->fourcc, 0);
  zwp_linux_buffer_params_v1_destroy(params);
  if (!valid || !f->capture.handle)
    goto failed;
  f->capture.generation = p->negotiated_generation;
  f->capture.width = p->negotiated_width;
  f->capture.height = p->negotiated_height;
  f->capture.format = (struct dpe_wl_format){p->fourcc, p->modifier, true};
  buffer->user_data = f;
  flush(p);
  return;
failed:
  frame_destroy(f);
  fail(p, "gpu-buffer-allocation-failed");
}
static void announce(struct producer *p) {
  uint32_t id = pw_stream_get_node_id(p->stream);
  if (id == PW_ID_ANY)
    return;
  printf("{\"event\":\"ready\",\"backend\":\"wayland-direct\",\"nodeId\":%u,"
         "\"width\":%u,\"height\":%u,\"generation\":%llu}\n",
         id, p->constraints.width, p->constraints.height,
         (unsigned long long)p->constraints.generation);
  fflush(stdout);
  p->announced = true;
}
static void update_period(struct producer *p) {
  uint64_t requested = UINT64_C(1000000000) / p->fps;
  p->period_ns = p->negotiated_period_ns > requested ? p->negotiated_period_ns : requested;
}
static void update_timer(struct producer *p) {
  if (p->streaming) {
    uint64_t period = p->period_ns ? p->period_ns : 1000000000 / p->fps;
    struct timespec first = {.tv_nsec = 1};
    struct timespec interval = {.tv_sec = period / 1000000000, .tv_nsec = period % 1000000000};
    pw_loop_update_timer(p->loop, p->timer, &first, &interval, false);
  } else {
    pw_loop_update_timer(p->loop, p->timer, NULL, NULL, false);
  }
}
static void state_changed(void *data, enum pw_stream_state previous, enum pw_stream_state next,
                          const char *error) {
  (void)previous;
  (void)error;
  struct producer *p = data;
  p->streaming = next == PW_STREAM_STATE_STREAMING;
  p->request_due = false;
  if (next == PW_STREAM_STATE_ERROR)
    fail(p, "pipewire-stream-failed");
  else if (next == PW_STREAM_STATE_PAUSED && !p->announced)
    announce(p);
  update_timer(p);
}
static void format_changed(void *data, uint32_t id, const struct spa_pod *value) {
  struct producer *p = data;
  if (id != SPA_PARAM_Format || !value)
    return;
  struct spa_video_info_raw raw = {0};
  if (spa_format_video_raw_parse(value, &raw) < 0 || !drm_for(raw.format) ||
      !(raw.flags & SPA_VIDEO_FLAG_MODIFIER)) {
    fail(p, "gpu-format-negotiation-failed");
    return;
  }
  if (raw.size.width != p->constraints.width || raw.size.height != p->constraints.height) {
    // The compositor's resize announcement can overtake an older PipeWire
    // format callback. The current offer already requests the latest size;
    // leave the old allocation state intact until that negotiation completes.
    if (p->negotiated_generation != p->constraints.generation)
      return;
    fail(p, "gpu-format-negotiation-failed");
    return;
  }
  uint32_t fourcc = drm_for(raw.format);
  uint8_t memory[16384];
  struct spa_pod_builder builder = SPA_POD_BUILDER_INIT(memory, sizeof(memory));
  const struct spa_pod *params[5];
  if (raw.flags & SPA_VIDEO_FLAG_MODIFIER_FIXATION_REQUIRED) {
    const struct spa_pod_prop *prop = spa_pod_find_prop(value, NULL, SPA_FORMAT_VIDEO_modifier);
    const struct spa_pod *v = &prop->value;
    if (!spa_pod_is_choice(v) || SPA_POD_CHOICE_VALUE_TYPE(v) != SPA_TYPE_Long ||
        SPA_POD_CHOICE_VALUE_SIZE(v) != sizeof(uint64_t)) {
      fail(p, "invalid-modifier-choice");
      return;
    }
    unsigned count = SPA_POD_CHOICE_N_VALUES(v);
    const uint64_t *values = SPA_POD_CHOICE_VALUES(v);
    uint64_t modifiers[MODIFIERS];
    unsigned accepted = 0;
    if (count > MODIFIERS + 1) {
      fail(p, "too-many-modifiers");
      return;
    }
    for (unsigned n = 1; n < count; n++)
      if (permitted(p, fourcc, values[n]))
        modifiers[accepted++] = values[n];
    if (p->probe)
      gbm_bo_destroy(p->probe);
    p->probe = accepted
                   ? gbm_bo_create_with_modifiers2(p->gbm, raw.size.width, raw.size.height, fourcc,
                                                   modifiers, accepted, GBM_BO_USE_RENDERING)
                   : NULL;
    if (!p->probe) {
      fail(p, "no-shared-gpu-modifier");
      return;
    }
    uint64_t selected = gbm_bo_get_modifier(p->probe);
    if (!permitted(p, fourcc, selected)) {
      fail(p, "allocator-returned-unadvertised-modifier");
      return;
    }
    params[0] = format_pod(p, &builder, raw.format, &selected, 1, false);
    unsigned total = formats(p, &builder, params + 1);
    pw_stream_update_params(p->stream, params, total + 1);
    return;
  }
  if (!permitted(p, fourcc, raw.modifier)) {
    fail(p, "unadvertised-format");
    return;
  }
  if (p->probe && !probe_matches(p, raw.size.width, raw.size.height, fourcc, raw.modifier)) {
    gbm_bo_destroy(p->probe);
    p->probe = NULL;
  }
  if (!p->probe)
    p->probe = gbm_bo_create_with_modifiers2(p->gbm, raw.size.width, raw.size.height, fourcc,
                                             &raw.modifier, 1, GBM_BO_USE_RENDERING);
  if (!p->probe || gbm_bo_get_plane_count(p->probe) < 1 ||
      gbm_bo_get_plane_count(p->probe) > PLANES) {
    fail(p, "gpu-allocation-unsupported");
    return;
  }
  p->fourcc = fourcc;
  p->spa_format = raw.format;
  p->modifier = raw.modifier;
  p->planes = gbm_bo_get_plane_count(p->probe);
  p->negotiated_width = raw.size.width;
  p->negotiated_height = raw.size.height;
  p->negotiated_generation = p->constraints.generation;
  struct spa_fraction rate = raw.framerate.num ? raw.framerate : raw.max_framerate;
  if (!rate.denom || rate.num < rate.denom || (uint64_t)rate.num > UINT64_C(240) * rate.denom) {
    fail(p, "invalid-negotiated-frame-rate");
    return;
  }
  // A local quality change and its PipeWire renegotiation are asynchronous.
  // Lower the cap immediately, but never exceed the currently negotiated rate
  // while waiting for a higher cap to be accepted.
  p->negotiated_period_ns = UINT64_C(1000000000) * rate.denom / rate.num;
  update_period(p);
  update_timer(p);
  params[0] = spa_pod_builder_add_object(
      &builder, SPA_TYPE_OBJECT_ParamBuffers, SPA_PARAM_Buffers, SPA_PARAM_BUFFERS_buffers,
      SPA_POD_CHOICE_RANGE_Int(4, 2, 8), SPA_PARAM_BUFFERS_blocks, SPA_POD_Int(p->planes),
      SPA_PARAM_BUFFERS_dataType, SPA_POD_CHOICE_FLAGS_Int(1 << SPA_DATA_DmaBuf));
  params[1] =
      spa_pod_builder_add_object(&builder, SPA_TYPE_OBJECT_ParamMeta, SPA_PARAM_Meta,
                                 SPA_PARAM_META_type, SPA_POD_Id(SPA_META_Header),
                                 SPA_PARAM_META_size, SPA_POD_Int(sizeof(struct spa_meta_header)));
  pw_stream_update_params(p->stream, params, 2);
}
static void process(void *data) {
  struct producer *p = data;
  if (!p->request_due)
    return;
  // Consume each grant even while the current pool is temporarily unusable.
  // PipeWire can publish a Format without a state_changed callback; retaining
  // this grant across that update would prevent the timer from ever firing it.
  p->request_due = false;
  if (p->pending || !p->streaming || p->closing || !p->negotiated_generation ||
      p->negotiated_generation != p->constraints.generation)
    return;
  // One timer grant permits one attempt; delayed graph scheduling never earns
  // capture credits. A timer gate avoids an accidental half-rate caused by
  // comparing nearly equal timestamps from independent periodic callbacks.
  struct pw_buffer *buffer = pw_stream_dequeue_buffer(p->stream);
  if (!buffer)
    return;
  struct frame *f = buffer->user_data;
  if (!f || !current_layout(p, f->capture.width, f->capture.height, f->capture.format.format,
                            f->capture.format.modifier)) {
    pw_stream_return_buffer(p->stream, buffer);
    return;
  }
  // A new constraint batch can keep the negotiated layout unchanged. PipeWire
  // need not send an identical Format or replace its buffers in that case.
  f->capture.generation = p->constraints.generation;
  p->pending = f;
  int status = dpe_wl_capture_submit(p->capture, &f->capture);
  if (status < 0) {
    p->pending = NULL;
    pw_stream_return_buffer(p->stream, buffer);
    if (status != -ESTALE && status != -EAGAIN && status != -EBUSY)
      fail(p, "capture-request-failed");
    return;
  }
  flush(p);
}
static void tick(void *data, uint64_t expirations) {
  (void)expirations;
  struct producer *p = data;
  if (p->retry_after_ns) {
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) == 0 &&
        (uint64_t)now.tv_sec * UINT64_C(1000000000) + now.tv_nsec < p->retry_after_ns)
      return;
    p->retry_after_ns = 0;
  }
  if (p->streaming && !p->pending && !p->request_due) {
    p->request_due = true;
    pw_stream_trigger_process(p->stream);
  }
}
static void frame_ready(void *data, const struct dpe_wl_buffer *buffer,
                        const struct dpe_wl_frame *frame, int status) {
  struct producer *p = data;
  struct frame *f = p->pending;
  p->pending = NULL;
  if (!f || buffer != &f->capture) {
    fail(p, "capture-ownership-mismatch");
    return;
  }
  if (f->retired) {
    frame_destroy(f);
    return;
  }
  if (status < 0 || frame->generation != p->constraints.generation) {
    pw_stream_return_buffer(p->stream, f->pw);
    if (status == -EAGAIN) {
      // Retry at the requested cadence, backing off repeated runtime errors to
      // at most four attempts per second. Never publish a failed destination.
      if (p->transient_failures < 5)
        p->transient_failures++;
      uint64_t delay = UINT64_C(20000000) << (p->transient_failures - 1);
      if (delay > UINT64_C(250000000))
        delay = UINT64_C(250000000);
      struct timespec now;
      if (clock_gettime(CLOCK_MONOTONIC, &now) == 0)
        p->retry_after_ns = (uint64_t)now.tv_sec * UINT64_C(1000000000) + now.tv_nsec + delay;
    } else if (status < 0 && status != -ESTALE)
      fail(p, "capture-frame-failed");
    return;
  }
  p->transient_failures = 0;
  p->retry_after_ns = 0;
  if (frame->transform != WL_OUTPUT_TRANSFORM_NORMAL) {
    pw_stream_return_buffer(p->stream, f->pw);
    fail(p, "transform-unsupported");
    return;
  }
  struct spa_meta_header *header =
      spa_buffer_find_meta_data(f->pw->buffer, SPA_META_Header, sizeof(struct spa_meta_header));
  if (header)
    *header = (struct spa_meta_header){.pts = frame->presentation_ns, .seq = p->sequence++};
  if (pw_stream_queue_buffer(p->stream, f->pw) < 0)
    fail(p, "pipewire-queue-failed");
}
static void capture_stopped(void *data, int reason) {
  fail(data, reason == -EOVERFLOW ? "capture-format-capacity-exceeded"
             : reason == -EPROTO  ? "capture-protocol-invalid"
             : reason == -ENODEV  ? "capture-output-removed"
                                  : "capture-source-stopped");
}
static void constraints(void *data, const struct dpe_wl_constraints *constraints) {
  struct producer *p = data;
  if (!constraints->has_device || constraints->width > 16384 || constraints->height > 16384) {
    fail(p, "gpu-capture-unavailable");
    return;
  }
  drmDevicePtr device = NULL;
  if (drmGetDeviceFromDevId(constraints->device, 0, &device) < 0 || !device ||
      !(device->available_nodes & (1 << DRM_NODE_RENDER))) {
    if (device)
      drmFreeDevice(&device);
    fail(p, "render-device-unavailable");
    return;
  }
  if (p->device) {
    bool equal = drmDevicesEqual(p->device, device);
    drmFreeDevice(&device);
    if (!equal) {
      fail(p, "capture-device-changed");
      return;
    }
  } else {
    p->device = device;
    p->render_fd = open(device->nodes[DRM_NODE_RENDER], O_RDWR | O_CLOEXEC | O_NOFOLLOW);
    if (p->render_fd < 0 || !(p->gbm = gbm_create_device(p->render_fd))) {
      fail(p, "render-device-open-failed");
      return;
    }
  }
  adopt_constraints(p, constraints);
  uint8_t memory[16384];
  struct spa_pod_builder builder = SPA_POD_BUILDER_INIT(memory, sizeof(memory));
  const struct spa_pod *params[4];
  unsigned total = stable_formats(p, &builder, params);
  if (!total) {
    fail(p, "no-supported-gpu-format");
    return;
  }
  if (p->stream) {
    pw_stream_update_params(p->stream, params, total);
    announce(p);
    return;
  }
  p->stream =
      pw_stream_new(p->core, "DPE direct output capture",
                    pw_properties_new(PW_KEY_MEDIA_CLASS, "Video/Source", PW_KEY_NODE_NAME,
                                      "dpe-wayland-direct", PW_KEY_NODE_DESCRIPTION,
                                      "DPE selected output", PW_KEY_NODE_VIRTUAL, "true", NULL));
  if (!p->stream) {
    fail(p, "pipewire-stream-create-failed");
    return;
  }
  static const struct pw_stream_events events = {.version = PW_VERSION_STREAM_EVENTS,
                                                 .state_changed = state_changed,
                                                 .param_changed = format_changed,
                                                 .add_buffer = buffer_added,
                                                 .remove_buffer = buffer_removed,
                                                 .process = process};
  pw_stream_add_listener(p->stream, &p->listener, &events, p);
  if (pw_stream_connect(p->stream, PW_DIRECTION_OUTPUT, PW_ID_ANY,
                        PW_STREAM_FLAG_DRIVER | PW_STREAM_FLAG_ALLOC_BUFFERS, params, total) < 0)
    fail(p, "pipewire-connect-failed");
}
static void quit(void *data, int signal) {
  (void)signal;
  pw_main_loop_quit(((struct producer *)data)->main);
}
static unsigned parse_rate(const char *line) {
  unsigned long rate = 0;
  int consumed = 0;
  // The main-process supervisor writes this small JSONL protocol. Accept the
  // equivalent plain command for a manually supervised standalone helper.
  if (sscanf(line, " { \"op\" : \"rate\" , \"fps\" : %lu } %n", &rate, &consumed) != 1 ||
      !consumed || line[consumed]) {
    consumed = 0;
    if (sscanf(line, "rate %lu %n", &rate, &consumed) != 1 || !consumed || line[consumed])
      return 0;
  }
  return rate >= 1 && rate <= 240 ? (unsigned)rate : 0;
}
static void change_rate(struct producer *p, unsigned rate) {
  if (p->fps == rate)
    return;
  p->fps = rate;
  update_period(p);
  p->request_due = false;
  if (p->stream) {
    uint8_t memory[16384];
    struct spa_pod_builder builder = SPA_POD_BUILDER_INIT(memory, sizeof(memory));
    const struct spa_pod *params[4];
    unsigned total = stable_formats(p, &builder, params);
    if (!total || pw_stream_update_params(p->stream, params, total) < 0) {
      fail(p, "capture-rate-update-failed");
      return;
    }
  }
  update_timer(p);
}
static void control_input(void *data, int fd, uint32_t mask) {
  struct producer *p = data;
  char bytes[256];
  ssize_t size = mask & SPA_IO_IN ? read(fd, bytes, sizeof(bytes)) : 0;
  if (size < 0 && (errno == EINTR || errno == EAGAIN))
    return;
  if (size <= 0) {
    pw_main_loop_quit(p->main);
    return;
  }
  for (ssize_t i = 0; i < size; i++) {
    if (bytes[i] == '\n') {
      p->command[p->command_size] = 0;
      unsigned rate = parse_rate(p->command);
      p->command_size = 0;
      if (!rate) {
        fail(p, "invalid-capture-rate-command");
        return;
      }
      change_rate(p, rate);
    } else if (p->command_size + 1 < sizeof(p->command) && bytes[i]) {
      p->command[p->command_size++] = bytes[i];
    } else {
      fail(p, "invalid-capture-rate-command");
      return;
    }
  }
}
static void core_error(void *data, uint32_t id, int seq, int res, const char *message) {
  (void)seq;
  (void)res;
  (void)message;
  if (id == PW_ID_CORE)
    fail(data, "pipewire-disconnected");
}
static int arguments(int argc, char **argv, const char **name, unsigned *fps) {
  if (argc == 2 && !strcmp(argv[1], "--list"))
    return 1;
  if (argc != 5 || strcmp(argv[1], "--output") || !*argv[2] || strlen(argv[2]) >= 128 ||
      strcmp(argv[3], "--fps"))
    return -1;
  char *end;
  unsigned long value = strtoul(argv[4], &end, 10);
  if (!*argv[4] || *end || value < 1 || value > 240)
    return -1;
  *name = argv[2];
  *fps = value;
  return 0;
}
int main(int argc, char **argv) {
  struct producer p = {.render_fd = -1};
  const char *name = NULL;
  int listing = arguments(argc, argv, &name, &p.fps);
  if (listing < 0)
    return 2;
  // Initialization must not strand a helper if a compositor is unresponsive.
  // Steady-state lifetime is owned by the parent and SIGTERM, not a fixed timer.
  pid_t parent = getppid();
  if (prctl(PR_SET_PDEATHSIG, SIGTERM) < 0 || getppid() != parent)
    return 3;
  alarm(8);
  p.display = wl_display_connect(NULL);
  if (!p.display) {
    if (listing) {
      puts("{\"version\":1,\"backend\":\"wayland-direct\",\"available\":false,"
           "\"reason\":\"wayland-unavailable\",\"outputs\":[]}");
      return 0;
    }
    return 3;
  }
  p.inventory.registry = wl_display_get_registry(p.display);
  wl_registry_add_listener(p.inventory.registry, &registry_events, &p.inventory);
  if (wl_display_roundtrip(p.display) < 0 || wl_display_roundtrip(p.display) < 0) {
    wl_display_disconnect(p.display);
    return 3;
  }
  if (listing) {
    list(&p.inventory);
    inventory_destroy(&p.inventory);
    wl_display_disconnect(p.display);
    return 0;
  }
  bool found = false;
  for (unsigned i = 0; i < p.inventory.count; i++) {
    struct listed_output *o = &p.inventory.outputs[i];
    if (o->done && !strcmp(o->name, name) && !o->transform)
      found = true;
  }
  if (!available(&p.inventory) || !found) {
    inventory_destroy(&p.inventory);
    wl_display_disconnect(p.display);
    return 3;
  }
  pw_init(NULL, NULL);
  p.main = pw_main_loop_new(NULL);
  if (!p.main)
    return 3;
  p.loop = pw_main_loop_get_loop(p.main);
  p.context = pw_context_new(p.loop, NULL, 0);
  p.core = p.context ? pw_context_connect(p.context, NULL, 0) : NULL;
  struct spa_hook core_listener = {0};
  static const struct pw_core_events core_events = {.version = PW_VERSION_CORE_EVENTS,
                                                    .error = core_error};
  if (!p.core) {
    fail(&p, "pipewire-unavailable");
    goto cleanup;
  }
  pw_core_add_listener(p.core, &core_listener, &core_events, &p);
  p.timer = pw_loop_add_timer(p.loop, tick, &p);
  struct stat input;
  if (!fstat(STDIN_FILENO, &input) && (S_ISFIFO(input.st_mode) || S_ISSOCK(input.st_mode)))
    p.control = pw_loop_add_io(p.loop, STDIN_FILENO, SPA_IO_IN | SPA_IO_ERR | SPA_IO_HUP, false,
                               control_input, &p);
  pw_loop_add_signal(p.loop, SIGTERM, quit, &p);
  pw_loop_add_signal(p.loop, SIGINT, quit, &p);
  const struct dpe_wl_callbacks callbacks = {constraints, frame_ready, capture_stopped};
  p.capture = dpe_wl_capture_new(p.display, name, &callbacks, &p);
  if (!p.capture || dpe_wl_capture_set_format_filter(p.capture, accept_format) < 0 ||
      wl_display_roundtrip(p.display) < 0 || wl_display_roundtrip(p.display) < 0 ||
      dpe_wl_capture_start(p.capture, true) < 0) {
    fail(&p, "capture-start-failed");
    goto cleanup;
  }
  p.io = pw_loop_add_io(p.loop, wl_display_get_fd(p.display), SPA_IO_IN | SPA_IO_ERR | SPA_IO_HUP,
                        false, wayland_events, &p);
  if (!p.io || !p.timer || !flush(&p)) {
    fail(&p, "capture-loop-failed");
    goto cleanup;
  }
  alarm(0);
  pw_main_loop_run(p.main);
cleanup:
  p.closing = true;
  if (p.io)
    pw_loop_destroy_source(p.loop, p.io);
  if (p.timer)
    pw_loop_destroy_source(p.loop, p.timer);
  if (p.control)
    pw_loop_destroy_source(p.loop, p.control);
  dpe_wl_capture_destroy(p.capture);
  // Removing the PipeWire pool retires an in-flight destination without
  // releasing its storage. Nonpending buffers can be destroyed normally.
  if (p.stream)
    pw_stream_destroy(p.stream);
  if (p.pending && p.pending->capture.handle) {
    wl_buffer_destroy(p.pending->capture.handle);
    p.pending->capture.handle = NULL;
  }
  inventory_destroy(&p.inventory);
  wl_display_disconnect(p.display);
  // Now the dedicated capture connection is gone, outstanding storage can be
  // retired too; no aborted image was published or reused.
  frame_destroy(p.pending);
  p.pending = NULL;
  if (p.probe)
    gbm_bo_destroy(p.probe);
  if (p.core)
    pw_core_disconnect(p.core);
  if (p.context)
    pw_context_destroy(p.context);
  if (p.gbm)
    gbm_device_destroy(p.gbm);
  if (p.render_fd >= 0)
    close(p.render_fd);
  if (p.device)
    drmFreeDevice(&p.device);
  pw_main_loop_destroy(p.main);
  pw_deinit();
  return p.failed;
}
