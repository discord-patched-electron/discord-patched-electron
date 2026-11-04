#include "wayland-capture.h"
#include "ext-image-capture-source-v1-client.h"
#include "ext-image-copy-capture-v1-client.h"
#include <errno.h>
#include <limits.h>
#include <stdlib.h>
#include <string.h>

#define OUTPUT_LIMIT 32
#define OUTPUT_NAME_LIMIT 128
struct output {
  struct dpe_wl_capture *capture;
  struct wl_output *proxy;
  uint32_t global;
  char name[OUTPUT_NAME_LIMIT];
  bool done;
};
struct dpe_wl_capture {
  struct wl_display *display;
  struct wl_registry *registry;
  struct ext_output_image_capture_source_manager_v1 *sources;
  struct ext_image_copy_capture_manager_v1 *manager;
  struct ext_image_capture_source_v1 *source;
  struct ext_image_copy_capture_session_v1 *session;
  struct ext_image_copy_capture_frame_v1 *pending;
  struct output outputs[OUTPUT_LIMIT], *selected;
  struct dpe_wl_constraints current, batch;
  struct dpe_wl_frame frame;
  const struct dpe_wl_buffer *buffer;
  struct dpe_wl_callbacks callbacks;
  void *data;
  char name[OUTPUT_NAME_LIMIT];
  bool batching, has_size, has_transform, has_time, has_damage;
  int error;
  uint64_t rejected_generation;
  bool (*accept_format)(void *, uint32_t, bool);
};
static void stop(struct dpe_wl_capture *capture, int reason) {
  if (capture->error)
    return;
  capture->error = reason;
  if (capture->callbacks.stopped)
    capture->callbacks.stopped(capture->data, reason);
}
static void batch(struct dpe_wl_capture *capture) {
  if (capture->batching)
    return;
  capture->batching = true;
  capture->has_size = false;
  memset(&capture->batch, 0, sizeof(capture->batch));
}
static void size_event(void *opaque, struct ext_image_copy_capture_session_v1 *session,
                       uint32_t width, uint32_t height) {
  (void)session;
  struct dpe_wl_capture *capture = opaque;
  batch(capture);
  if (capture->has_size || !width || !height || width > INT32_MAX || height > INT32_MAX) {
    stop(capture, -EPROTO);
    return;
  }
  capture->has_size = true;
  capture->batch.width = width;
  capture->batch.height = height;
}
static void add_format(struct dpe_wl_capture *capture, uint32_t format, uint64_t modifier,
                       bool dmabuf) {
  batch(capture);
  if (capture->accept_format && !capture->accept_format(capture->data, format, dmabuf))
    return;
  for (unsigned i = 0; i < capture->batch.format_count; i++) {
    struct dpe_wl_format *entry = &capture->batch.formats[i];
    if (entry->format == format && entry->modifier == modifier && entry->dmabuf == dmabuf)
      return;
  }
  if (capture->batch.format_count == DPE_WL_FORMAT_LIMIT) {
    stop(capture, -EOVERFLOW);
    return;
  }
  capture->batch.formats[capture->batch.format_count++] =
      (struct dpe_wl_format){format, modifier, dmabuf};
}
static void shm_event(void *opaque, struct ext_image_copy_capture_session_v1 *session,
                      uint32_t format) {
  (void)session;
  add_format(opaque, format, 0, false);
}
static void device_event(void *opaque, struct ext_image_copy_capture_session_v1 *session,
                         struct wl_array *device) {
  (void)session;
  struct dpe_wl_capture *capture = opaque;
  batch(capture);
  if (capture->batch.has_device || device->size != sizeof(dev_t)) {
    stop(capture, -EPROTO);
    return;
  }
  capture->batch.has_device = true;
  memcpy(&capture->batch.device, device->data, sizeof(dev_t));
}
static void dmabuf_event(void *opaque, struct ext_image_copy_capture_session_v1 *session,
                         uint32_t format, struct wl_array *modifiers) {
  (void)session;
  struct dpe_wl_capture *capture = opaque;
  batch(capture);
  if (modifiers->size % sizeof(uint64_t)) {
    stop(capture, -EPROTO);
    return;
  }
  if (capture->accept_format && !capture->accept_format(capture->data, format, true))
    return;
  if (modifiers->size / sizeof(uint64_t) > DPE_WL_FORMAT_LIMIT) {
    stop(capture, -EOVERFLOW);
    return;
  }
  for (size_t offset = 0; offset < modifiers->size; offset += sizeof(uint64_t)) {
    uint64_t modifier;
    memcpy(&modifier, (char *)modifiers->data + offset, sizeof(modifier));
    add_format(capture, format, modifier, true);
  }
}
static void done_event(void *opaque, struct ext_image_copy_capture_session_v1 *session) {
  (void)session;
  struct dpe_wl_capture *capture = opaque;
  if (capture->error)
    return;
  if (!capture->batching || !capture->has_size || !capture->batch.format_count) {
    stop(capture, -EPROTO);
    return;
  }
  capture->batch.generation = capture->current.generation + 1;
  capture->current = capture->batch;
  capture->batching = false;
  if (capture->callbacks.constraints)
    capture->callbacks.constraints(capture->data, &capture->current);
}
static void stopped_event(void *opaque, struct ext_image_copy_capture_session_v1 *session) {
  (void)session;
  stop(opaque, -ECANCELED);
}
static const struct ext_image_copy_capture_session_v1_listener session_listener = {
    size_event, shm_event, device_event, dmabuf_event, done_event, stopped_event};

static void transform_event(void *opaque, struct ext_image_copy_capture_frame_v1 *frame,
                            uint32_t transform) {
  (void)frame;
  struct dpe_wl_capture *capture = opaque;
  if (capture->has_transform || transform > WL_OUTPUT_TRANSFORM_FLIPPED_270) {
    stop(capture, -EPROTO);
    return;
  }
  capture->has_transform = true;
  capture->frame.transform = transform;
}
static void damage_event(void *opaque, struct ext_image_copy_capture_frame_v1 *frame, int32_t x,
                         int32_t y, int32_t width, int32_t height) {
  (void)frame;
  struct dpe_wl_capture *capture = opaque;
  if (x < 0 || y < 0 || width < 0 || height < 0 || (uint64_t)x + width > capture->buffer->width ||
      (uint64_t)y + height > capture->buffer->height) {
    stop(capture, -EPROTO);
    return;
  }
  if (!width || !height)
    return;
  struct dpe_wl_frame *result = &capture->frame;
  if (!capture->has_damage) {
    result->x = x;
    result->y = y;
    result->width = width;
    result->height = height;
  } else {
    int32_t right = result->x + result->width, bottom = result->y + result->height;
    if (x + width > right)
      right = x + width;
    if (y + height > bottom)
      bottom = y + height;
    if (x < result->x)
      result->x = x;
    if (y < result->y)
      result->y = y;
    result->width = right - result->x;
    result->height = bottom - result->y;
  }
  capture->has_damage = true;
}
static void time_event(void *opaque, struct ext_image_copy_capture_frame_v1 *frame, uint32_t high,
                       uint32_t low, uint32_t ns) {
  (void)frame;
  struct dpe_wl_capture *capture = opaque;
  uint64_t seconds = ((uint64_t)high << 32) | low;
  if (capture->has_time || ns >= 1000000000 || seconds > (UINT64_MAX - ns) / 1000000000) {
    stop(capture, -EPROTO);
    return;
  }
  capture->has_time = true;
  capture->frame.presentation_ns = seconds * 1000000000 + ns;
}
static void finish(struct dpe_wl_capture *capture, int status) {
  struct dpe_wl_frame result = capture->frame;
  const struct dpe_wl_buffer *buffer = capture->buffer;
  ext_image_copy_capture_frame_v1_destroy(capture->pending);
  capture->pending = NULL;
  capture->buffer = NULL;
  if (capture->callbacks.frame)
    capture->callbacks.frame(capture->data, buffer, &result,
                             capture->error ? capture->error : status);
}
static void ready_event(void *opaque, struct ext_image_copy_capture_frame_v1 *frame) {
  (void)frame;
  struct dpe_wl_capture *capture = opaque;
  if (!capture->has_time || !capture->has_transform)
    stop(capture, -EPROTO);
  finish(capture, 0);
}
static void failed_event(void *opaque, struct ext_image_copy_capture_frame_v1 *frame,
                         uint32_t reason) {
  (void)frame;
  struct dpe_wl_capture *capture = opaque;
  // Unknown is explicitly retryable in ext-image-copy-capture. In particular,
  // niri uses it when a render or asynchronous sync completion fails.
  int status = -EAGAIN;
  if (reason == EXT_IMAGE_COPY_CAPTURE_FRAME_V1_FAILURE_REASON_BUFFER_CONSTRAINTS) {
    status = -ESTALE;
    capture->rejected_generation = capture->frame.generation;
  } else if (reason == EXT_IMAGE_COPY_CAPTURE_FRAME_V1_FAILURE_REASON_STOPPED) {
    status = -ECANCELED;
    stop(capture, status);
  }
  finish(capture, status);
}
static const struct ext_image_copy_capture_frame_v1_listener frame_listener = {
    transform_event, damage_event, time_event, ready_event, failed_event};

static void output_geometry(void *opaque, struct wl_output *output, int32_t x, int32_t y,
                            int32_t width, int32_t height, int32_t subpixel, const char *make,
                            const char *model, int32_t transform) {
  (void)opaque;
  (void)output;
  (void)x;
  (void)y;
  (void)width;
  (void)height;
  (void)subpixel;
  (void)make;
  (void)model;
  (void)transform;
}
static void output_mode(void *opaque, struct wl_output *output, uint32_t flags, int32_t width,
                        int32_t height, int32_t refresh) {
  (void)opaque;
  (void)output;
  (void)flags;
  (void)width;
  (void)height;
  (void)refresh;
}
static void output_done(void *opaque, struct wl_output *output) {
  (void)output;
  ((struct output *)opaque)->done = true;
}
static void output_scale(void *opaque, struct wl_output *output, int32_t scale) {
  (void)opaque;
  (void)output;
  (void)scale;
}
static void output_name(void *opaque, struct wl_output *output, const char *name) {
  (void)output;
  struct output *entry = opaque;
  if (!name[0] || strlen(name) >= sizeof(entry->name) || entry->name[0]) {
    stop(entry->capture, -EPROTO);
    return;
  }
  strcpy(entry->name, name);
}
static void output_description(void *opaque, struct wl_output *output, const char *description) {
  (void)opaque;
  (void)output;
  (void)description;
}
static const struct wl_output_listener output_listener = {
    output_geometry, output_mode, output_done, output_scale, output_name, output_description};
static void global(void *opaque, struct wl_registry *registry, uint32_t name, const char *interface,
                   uint32_t version) {
  struct dpe_wl_capture *capture = opaque;
  if (!strcmp(interface, ext_image_copy_capture_manager_v1_interface.name) && !capture->manager)
    capture->manager =
        wl_registry_bind(registry, name, &ext_image_copy_capture_manager_v1_interface, 1);
  else if (!strcmp(interface, ext_output_image_capture_source_manager_v1_interface.name) &&
           !capture->sources)
    capture->sources =
        wl_registry_bind(registry, name, &ext_output_image_capture_source_manager_v1_interface, 1);
  else if (!strcmp(interface, wl_output_interface.name) && version >= 4) {
    for (unsigned i = 0; i < OUTPUT_LIMIT; i++) {
      struct output *entry = &capture->outputs[i];
      if (entry->proxy)
        continue;
      *entry = (struct output){.capture = capture, .global = name};
      entry->proxy = wl_registry_bind(registry, name, &wl_output_interface, 4);
      if (!entry->proxy) {
        stop(capture, -ENOMEM);
        return;
      }
      wl_output_add_listener(entry->proxy, &output_listener, entry);
      return;
    }
    stop(capture, -EOVERFLOW);
  }
}
static void removed(void *opaque, struct wl_registry *registry, uint32_t name) {
  (void)registry;
  struct dpe_wl_capture *capture = opaque;
  for (unsigned i = 0; i < OUTPUT_LIMIT; i++) {
    struct output *entry = &capture->outputs[i];
    if (!entry->proxy || entry->global != name)
      continue;
    if (capture->selected == entry)
      stop(capture, -ENODEV);
    wl_output_release(entry->proxy);
    memset(entry, 0, sizeof(*entry));
  }
}
static const struct wl_registry_listener registry_listener = {global, removed};

struct dpe_wl_capture *dpe_wl_capture_new(struct wl_display *display, const char *output_name,
                                          const struct dpe_wl_callbacks *callbacks, void *data) {
  if (!display || !output_name || !output_name[0] || strlen(output_name) >= OUTPUT_NAME_LIMIT ||
      !callbacks) {
    errno = EINVAL;
    return NULL;
  }
  struct dpe_wl_capture *capture = calloc(1, sizeof(*capture));
  if (!capture)
    return NULL;
  strcpy(capture->name, output_name);
  capture->callbacks = *callbacks;
  capture->display = display;
  capture->data = data;
  capture->registry = wl_display_get_registry(display);
  if (!capture->registry) {
    free(capture);
    return NULL;
  }
  wl_registry_add_listener(capture->registry, &registry_listener, capture);
  return capture;
}
int dpe_wl_capture_set_format_filter(struct dpe_wl_capture *capture,
                                     bool (*accept)(void *, uint32_t, bool)) {
  if (!capture || capture->session)
    return -EINVAL;
  capture->accept_format = accept;
  return 0;
}
int dpe_wl_capture_start(struct dpe_wl_capture *capture, bool paint_cursor) {
  if (capture->error)
    return capture->error;
  int error = wl_display_get_error(capture->display);
  if (error) {
    stop(capture, -error);
    return -error;
  }
  if (capture->session)
    return -EALREADY;
  if (!capture->manager || !capture->sources)
    return -ENOTSUP;
  struct output *selected = NULL;
  for (unsigned i = 0; i < OUTPUT_LIMIT; i++) {
    struct output *entry = &capture->outputs[i];
    if (!entry->proxy || !entry->done || strcmp(entry->name, capture->name))
      continue;
    if (selected)
      return -EEXIST;
    selected = entry;
  }
  if (!selected)
    return -ENOENT;
  capture->selected = selected;
  capture->source =
      ext_output_image_capture_source_manager_v1_create_source(capture->sources, selected->proxy);
  if (!capture->source)
    return -ENOMEM;
  capture->session = ext_image_copy_capture_manager_v1_create_session(
      capture->manager, capture->source,
      paint_cursor ? EXT_IMAGE_COPY_CAPTURE_MANAGER_V1_OPTIONS_PAINT_CURSORS : 0);
  if (!capture->session) {
    ext_image_capture_source_v1_destroy(capture->source);
    capture->source = NULL;
    return -ENOMEM;
  }
  ext_image_copy_capture_session_v1_add_listener(capture->session, &session_listener, capture);
  return 0;
}
int dpe_wl_capture_submit(struct dpe_wl_capture *capture, const struct dpe_wl_buffer *buffer) {
  if (capture->error)
    return capture->error;
  int error = wl_display_get_error(capture->display);
  if (error) {
    stop(capture, -error);
    return -error;
  }
  if (capture->pending)
    return -EBUSY;
  if (!capture->session || !capture->current.generation || capture->batching)
    return -EAGAIN;
  const struct dpe_wl_constraints *constraints = &capture->current;
  if (!buffer || !buffer->handle)
    return -EINVAL;
  if (buffer->generation != constraints->generation ||
      buffer->generation <= capture->rejected_generation || buffer->width != constraints->width ||
      buffer->height != constraints->height)
    return -ESTALE;
  bool supported = false;
  for (unsigned i = 0; i < constraints->format_count; i++) {
    const struct dpe_wl_format *format = &constraints->formats[i];
    if (format->dmabuf == buffer->format.dmabuf && format->format == buffer->format.format &&
        format->modifier == buffer->format.modifier)
      supported = true;
  }
  if (!supported)
    return -ENOTSUP;
  capture->frame = (struct dpe_wl_frame){.generation = buffer->generation};
  capture->has_time = capture->has_transform = capture->has_damage = false;
  capture->buffer = buffer;
  capture->pending = ext_image_copy_capture_session_v1_create_frame(capture->session);
  if (!capture->pending) {
    capture->buffer = NULL;
    return -ENOMEM;
  }
  ext_image_copy_capture_frame_v1_add_listener(capture->pending, &frame_listener, capture);
  ext_image_copy_capture_frame_v1_attach_buffer(capture->pending, buffer->handle);
  ext_image_copy_capture_frame_v1_damage_buffer(capture->pending, 0, 0, buffer->width,
                                                buffer->height);
  ext_image_copy_capture_frame_v1_capture(capture->pending);
  return 0;
}
int dpe_wl_capture_error(const struct dpe_wl_capture *capture) {
  return capture->error ? capture->error : -wl_display_get_error(capture->display);
}
void dpe_wl_capture_destroy(struct dpe_wl_capture *capture) {
  if (!capture)
    return;
  if (capture->pending)
    ext_image_copy_capture_frame_v1_destroy(capture->pending);
  if (capture->session)
    ext_image_copy_capture_session_v1_destroy(capture->session);
  if (capture->source)
    ext_image_capture_source_v1_destroy(capture->source);
  if (capture->sources)
    ext_output_image_capture_source_manager_v1_destroy(capture->sources);
  if (capture->manager)
    ext_image_copy_capture_manager_v1_destroy(capture->manager);
  for (unsigned i = 0; i < OUTPUT_LIMIT; i++)
    if (capture->outputs[i].proxy)
      wl_output_release(capture->outputs[i].proxy);
  wl_registry_destroy(capture->registry);
  free(capture);
}
