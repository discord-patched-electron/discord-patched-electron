// Synthetic raw video only. Uses the public PipeWire stream API; no display,
// camera or microphone is opened. Optional --dmabuf requires an explicit render
// node; the default uses MemFd and never opens a GPU. CLOCK_MONOTONIC ms markers.
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <pipewire/pipewire.h>
#include <signal.h>
#include <spa/param/video/format-utils.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
// Stable PipeWire 1.2 wire ABI. The ordinary producer still builds with the
// PipeWire 1.0 development headers shipped by Ubuntu 24.04; an older server
// cannot satisfy the explicit mode's mandatory metadata negotiation.
enum { DPE_SYNC_META = 9, DPE_SYNC_DATA = 5, DPE_BUFFERS_META_TYPE = 7 };
struct dpe_sync_timeline {
  uint32_t flags, padding;
  uint64_t acquire_point, release_point;
};
_Static_assert(sizeof(struct dpe_sync_timeline) == 24, "SyncTimeline ABI changed");
#ifdef DPE_SOURCE_DMABUF
#include "gpu-source.h"
#include <xf86drm.h>

struct source_buffer {
  struct gpu_frame *gpu;
  uint32_t timeline;
  int timeline_fd;
  uint64_t release;
};
#endif

struct source {
  struct pw_main_loop *loop;
  struct pw_stream *stream;
  struct spa_source *timer;
  struct spa_hook listener;
  uint32_t width, height, fps, sequence;
  uint32_t started_at, settle_after;
  bool started;
  bool dmabuf;
  bool sync_timeline;
  unsigned sync_delay_ms;
#ifdef DPE_SOURCE_DMABUF
  struct gpu_source *gpu;
  struct pw_buffer *pending, *release_pending;
  struct spa_source *sync_timer;
  int drm_fd;
  uint32_t pending_stamp, pending_sequence;
  uint64_t sync_queued, sync_acquired, sync_released, sync_waits;
#endif
  int failed;
};
static void fail(struct source *source, const char *message) {
  fprintf(stderr, "Synthetic source: %s\n", message);
  source->failed = 1;
  pw_main_loop_quit(source->loop);
}
#ifdef DPE_SOURCE_DMABUF
static void add_buffer(void *opaque, struct pw_buffer *pw) {
  struct source *source = opaque;
  if (!source->dmabuf)
    return;
  struct spa_buffer *buffer = pw->buffer;
  struct source_buffer *owned = calloc(1, sizeof(*owned));
  if (!owned) {
    fail(source, "Buffer ownership allocation failed");
    return;
  }
  owned->timeline_fd = -1;
  pw->user_data = owned;
  if (buffer->n_datas != (source->sync_timeline ? 3u : 1u) || !buffer->datas[0].chunk ||
      !(buffer->datas[0].type & (1u << SPA_DATA_DmaBuf)) ||
      !(owned->gpu = gpu_frame_create(source->gpu, &buffer->datas[0]))) {
    fail(source, "DMA-BUF allocation/negotiation failed; no CPU fallback");
    return;
  }
  if (!source->sync_timeline)
    return;
  struct dpe_sync_timeline *meta = spa_buffer_find_meta_data(buffer, DPE_SYNC_META, sizeof(*meta));
  if (!meta || drmSyncobjCreate(source->drm_fd, 0, &owned->timeline) ||
      drmSyncobjHandleToFD(source->drm_fd, owned->timeline, &owned->timeline_fd)) {
    fail(source, "Explicit-sync negotiation/allocation failed");
    return;
  }
  // Mutter exports one timeline twice: acquire and release are consecutive
  // points on that timeline, not eventfds or DMA-BUF descriptors.
  for (unsigned i = 1; i < 3; i++) {
    buffer->datas[i].type = DPE_SYNC_DATA;
    buffer->datas[i].flags = SPA_DATA_FLAG_READABLE;
    buffer->datas[i].fd = owned->timeline_fd;
    buffer->datas[i].data = NULL;
    buffer->datas[i].maxsize = 0;
  }
  *meta = (struct dpe_sync_timeline){0};
}
static void remove_buffer(void *opaque, struct pw_buffer *pw) {
  struct source *source = opaque;
  if (source->release_pending == pw)
    source->release_pending = NULL;
  if (source->pending == pw) {
    source->pending = NULL;
    if (source->sync_timer)
      pw_loop_update_timer(pw_main_loop_get_loop(source->loop), source->sync_timer, NULL, NULL,
                           false);
  }
  struct source_buffer *owned = pw->user_data;
  if (!owned)
    return;
  gpu_frame_destroy(owned->gpu);
  if (owned->timeline_fd >= 0)
    close(owned->timeline_fd);
  if (owned->timeline)
    drmSyncobjDestroy(source->drm_fd, owned->timeline);
  free(owned);
  pw->user_data = NULL;
}
static void completed(void *opaque, bool success) {
  struct source *source = opaque;
  struct pw_buffer *pw = source->pending;
  source->pending = NULL;
  if (!success || !pw) {
    fail(source, "GPU completion/queue failed");
    return;
  }
  if (source->sync_timeline) {
    struct source_buffer *owned = pw->user_data;
    uint64_t acquire = owned->release - 1;
    // Publish only after the actual GL fence completed. The consumer has
    // already received this buffer and must wait for this timeline point.
    if (drmSyncobjTimelineSignal(source->drm_fd, &owned->timeline, &acquire, 1))
      fail(source, "Acquire timeline signal failed");
    else
      source->sync_acquired++;
  } else if (pw_stream_queue_buffer(source->stream, pw) < 0) {
    fail(source, "GPU completion/queue failed");
  }
}
#endif
static uint32_t timestamp(void) {
  struct timespec now;
  clock_gettime(CLOCK_MONOTONIC, &now);
  return ((uint64_t)now.tv_sec * 1000 + now.tv_nsec / 1000000) & 0xffffff;
}
static uint8_t crc(uint32_t sequence) {
  const uint8_t bytes[] = {0xa7, sequence >> 16, sequence >> 8, sequence};
  uint8_t value = 0;
  for (unsigned i = 0; i < sizeof(bytes); i++) {
    value ^= bytes[i];
    for (int bit = 0; bit < 8; bit++)
      value = (value << 1) ^ (value & 128 ? 7 : 0);
  }
  return value;
}
#ifdef DPE_SOURCE_DMABUF
static void delayed_render(void *opaque, uint64_t expirations) {
  (void)expirations;
  struct source *source = opaque;
  struct source_buffer *owned = source->pending ? source->pending->user_data : NULL;
  if (!owned || gpu_frame_render(owned->gpu, source->pending_stamp, crc(source->pending_stamp),
                                 source->pending_sequence, completed, source) < 0)
    fail(source, "Delayed GPU rendering failed");
}
#endif
static void process(void *opaque) {
  struct source *source = opaque;
#ifdef DPE_SOURCE_DMABUF
  if (source->pending)
    return; // Bounded: no producer backlog while the previous GPU job completes.
#endif
  struct pw_buffer *pw;
#ifdef DPE_SOURCE_DMABUF
  pw = source->release_pending;
  source->release_pending = NULL;
  if (!pw)
#endif
    pw = pw_stream_dequeue_buffer(source->stream);
  if (!pw)
    return;
  struct spa_buffer *buffer = pw->buffer;
  uint32_t stamp = timestamp();
  struct spa_meta_header *header =
      spa_buffer_find_meta_data(buffer, SPA_META_Header, sizeof(*header));
  if (header)
    *header = (struct spa_meta_header){.pts = -1, .seq = source->sequence};
#ifdef DPE_SOURCE_DMABUF
  if (source->dmabuf) {
    struct source_buffer *owned = pw->user_data;
    if (!owned) {
      fail(source, "Missing DMA-BUF ownership");
      return;
    }
    if (source->sync_timeline && owned->release) {
      int result = drmSyncobjTimelineWait(source->drm_fd, &owned->timeline, &owned->release, 1, 0,
                                          DRM_SYNCOBJ_WAIT_FLAGS_WAIT_ALL, NULL);
      if (result) {
        if (result != -ETIME && errno != ETIME) {
          fail(source, "Release timeline check failed");
          return;
        }
        source->sync_waits++;
        // Keep ownership until release is signaled; do not depend on the newer
        // pw_stream_return_buffer API merely to build the ordinary fixture.
        source->release_pending = pw;
        return;
      }
      source->sync_released++;
    }
    if (!source->started) {
      source->started = true;
      source->started_at = stamp;
    }
    if (source->settle_after &&
        ((stamp - source->started_at) & 0xffffff) >= source->settle_after * 1000)
      gpu_source_set_pattern(source->gpu, 3);
    source->pending = pw;
    source->pending_stamp = stamp;
    source->pending_sequence = source->sequence++;
    if (getenv("DPE_SYNTHETIC_QUALITY"))
      fprintf(stderr, "quality-frame %u %u\n", stamp, source->pending_sequence);
    if (source->sync_timeline) {
      struct dpe_sync_timeline *meta =
          spa_buffer_find_meta_data(buffer, DPE_SYNC_META, sizeof(*meta));
      if (!meta) {
        fail(source, "Timeline metadata disappeared");
        return;
      }
      owned->release += 2;
      meta->acquire_point = owned->release - 1;
      meta->release_point = owned->release;
      if (pw_stream_queue_buffer(source->stream, pw) < 0) {
        fail(source, "Explicit-sync queue failed");
        return;
      }
      source->sync_queued++;
      struct timespec delay = {.tv_nsec = source->sync_delay_ms * 1000000u + 1};
      pw_loop_update_timer(pw_main_loop_get_loop(source->loop), source->sync_timer, &delay, NULL,
                           false);
    } else if (gpu_frame_render(owned->gpu, stamp, crc(stamp), source->pending_sequence, completed,
                                source) < 0) {
      fail(source, "GPU rendering failed");
    }
    return;
  }
#endif
  const size_t bytes = (size_t)source->width * source->height * 4;
  if (!buffer->n_datas || !buffer->datas[0].data || buffer->datas[0].maxsize < bytes) {
    fail(source, "CPU buffer allocation failed");
    return;
  }
  const uint8_t marker[] = {0xa7, stamp >> 16, stamp >> 8, stamp, crc(stamp)};
  uint8_t *pixels = buffer->datas[0].data;
  for (uint32_t y = 0; y < source->height; y++) {
    for (uint32_t x = 0; x < source->width; x++) {
      unsigned offset = (y * source->width + x) * 4;
      uint8_t color = (x / 20 * 13 + y / 20 * 7 + source->sequence * 11) & 255;
      if (y < source->height * 12 / 100) {
        unsigned bit = x * 40 / source->width;
        unsigned row = y >= source->height * 6 / 100;
        color = (((marker[bit / 8] >> (7 - bit % 8)) & 1) ^ row) ? 255 : 0;
        pixels[offset] = pixels[offset + 1] = pixels[offset + 2] = color;
      } else {
        pixels[offset] = color;
        pixels[offset + 1] = color + 70;
        pixels[offset + 2] = color + 130;
      }
      pixels[offset + 3] = 255;
    }
  }
  source->sequence++;
  *buffer->datas[0].chunk = (struct spa_chunk){.size = bytes, .stride = source->width * 4};
  pw_stream_queue_buffer(source->stream, pw);
}
static void tick(void *opaque, uint64_t expirations) {
  (void)expirations;
  struct source *source = opaque;
  pw_stream_trigger_process(source->stream);
}
static void state(void *opaque, enum pw_stream_state old, enum pw_stream_state next,
                  const char *error) {
  (void)old;
  struct source *source = opaque;
  if (next == PW_STREAM_STATE_ERROR) {
    fprintf(stderr, "Synthetic source: %s\n", error);
    source->failed = 1;
    pw_main_loop_quit(source->loop);
  } else if (next == PW_STREAM_STATE_PAUSED) {
    printf("%u\n", pw_stream_get_node_id(source->stream));
    fflush(stdout);
    pw_loop_update_timer(pw_main_loop_get_loop(source->loop), source->timer, NULL, NULL, false);
  } else if (next == PW_STREAM_STATE_STREAMING) {
    struct timespec first = {.tv_nsec = 1}, interval = {.tv_nsec = 1000000000 / source->fps};
    pw_loop_update_timer(pw_main_loop_get_loop(source->loop), source->timer, &first, &interval,
                         false);
  }
}
static void format(void *opaque, uint32_t id, const struct spa_pod *value) {
  struct source *source = opaque;
  if (id != SPA_PARAM_Format || !value)
    return;
  struct spa_video_info_raw raw;
  if (spa_format_video_raw_parse(value, &raw) < 0 || raw.format != SPA_VIDEO_FORMAT_BGRA ||
      raw.size.width != source->width || raw.size.height != source->height ||
      (source->dmabuf && (!(raw.flags & SPA_VIDEO_FLAG_MODIFIER) || raw.modifier != 0))) {
    source->failed = 1;
    pw_main_loop_quit(source->loop);
    return;
  }
  uint8_t memory[1024];
  struct spa_pod_builder builder = SPA_POD_BUILDER_INIT(memory, sizeof(memory));
  const struct spa_pod *params[3];
  unsigned count = 0;
  if (source->sync_timeline) {
    struct spa_pod_frame buffers;
    spa_pod_builder_push_object(&builder, &buffers, SPA_TYPE_OBJECT_ParamBuffers,
                                SPA_PARAM_Buffers);
    spa_pod_builder_add(&builder, SPA_PARAM_BUFFERS_buffers, SPA_POD_CHOICE_RANGE_Int(8, 2, 8),
                        SPA_PARAM_BUFFERS_blocks, SPA_POD_Int(3), SPA_PARAM_BUFFERS_dataType,
                        SPA_POD_CHOICE_FLAGS_Int(1 << SPA_DATA_DmaBuf), 0);
    spa_pod_builder_prop(&builder, DPE_BUFFERS_META_TYPE, SPA_POD_PROP_FLAG_MANDATORY);
    spa_pod_builder_int(&builder, 1 << DPE_SYNC_META);
    params[count++] = spa_pod_builder_pop(&builder, &buffers);
    params[count++] = spa_pod_builder_add_object(&builder, SPA_TYPE_OBJECT_ParamMeta,
                                                 SPA_PARAM_Meta, SPA_PARAM_META_type,
                                                 SPA_POD_Id(DPE_SYNC_META), SPA_PARAM_META_size,
                                                 SPA_POD_Int(sizeof(struct dpe_sync_timeline)));
  } else {
    params[count++] = spa_pod_builder_add_object(
        &builder, SPA_TYPE_OBJECT_ParamBuffers, SPA_PARAM_Buffers, SPA_PARAM_BUFFERS_buffers,
        SPA_POD_CHOICE_RANGE_Int(8, 2, 16), SPA_PARAM_BUFFERS_blocks, SPA_POD_Int(1),
        SPA_PARAM_BUFFERS_size, SPA_POD_Int(source->width * source->height * 4),
        SPA_PARAM_BUFFERS_stride, SPA_POD_Int(source->width * 4), SPA_PARAM_BUFFERS_dataType,
        SPA_POD_CHOICE_FLAGS_Int(1 << (source->dmabuf ? SPA_DATA_DmaBuf : SPA_DATA_MemFd)));
  }
  params[count++] =
      spa_pod_builder_add_object(&builder, SPA_TYPE_OBJECT_ParamMeta, SPA_PARAM_Meta,
                                 SPA_PARAM_META_type, SPA_POD_Id(SPA_META_Header),
                                 SPA_PARAM_META_size, SPA_POD_Int(sizeof(struct spa_meta_header)));
  pw_stream_update_params(source->stream, params, count);
}
static void quit(void *opaque, int signal) {
  (void)signal;
  pw_main_loop_quit(((struct source *)opaque)->loop);
}
int main(int argc, char **argv) {
  if (argc < 4)
    return 2;
  struct source source = {.width = atoi(argv[1]), .height = atoi(argv[2]), .fps = atoi(argv[3])};
  const char *node = NULL;
  unsigned pattern = 0;
  bool has_pattern = false;
  for (int i = 4; i < argc; i += 2) {
    if (i + 1 >= argc)
      return 2;
    if (!strcmp(argv[i], "--dmabuf") && !node) {
      node = argv[i + 1];
      source.dmabuf = true;
    } else if (!strcmp(argv[i], "--pattern") && !has_pattern) {
      has_pattern = true;
      if (!strcmp(argv[i + 1], "scroll"))
        pattern = 1;
      else if (!strcmp(argv[i + 1], "scroll16"))
        pattern = 4;
      else if (!strcmp(argv[i + 1], "framing"))
        pattern = 5;
      else if (!strcmp(argv[i + 1], "scenes"))
        pattern = 6;
      else if (!strcmp(argv[i + 1], "noise"))
        pattern = 2;
      else if (strcmp(argv[i + 1], "tiles"))
        return 2;
    } else if (!strcmp(argv[i], "--settle-after") && !source.settle_after) {
      char *end;
      unsigned long value = strtoul(argv[i + 1], &end, 10);
      if (*end || value < 1 || value > 15)
        return 2;
      source.settle_after = value;
    } else if (!strcmp(argv[i], "--sync-timeline") && !source.sync_timeline) {
      char *end;
      unsigned long value = strtoul(argv[i + 1], &end, 10);
      if (!*argv[i + 1] || *end || value > 100)
        return 2;
      source.sync_timeline = true;
      source.sync_delay_ms = value;
    } else {
      return 2;
    }
  }
  if (source.width < 80 || source.width > 4096 || source.height < 64 || source.height > 4096 ||
      source.fps < 1 || source.fps > 240)
    return 2;
  if ((pattern || source.settle_after || source.sync_timeline) && !source.dmabuf)
    return 2;
#ifndef DPE_SOURCE_DMABUF
  if (source.dmabuf)
    return 2;
#endif
  pw_init(NULL, NULL);
  source.loop = pw_main_loop_new(NULL);
  struct pw_loop *loop = pw_main_loop_get_loop(source.loop);
#ifdef DPE_SOURCE_DMABUF
  source.drm_fd = -1;
  if (source.dmabuf &&
      !(source.gpu = gpu_source_create(node, loop, source.width, source.height, pattern))) {
    pw_main_loop_destroy(source.loop);
    pw_deinit();
    return 3;
  }
  if (source.sync_timeline) {
    source.drm_fd = open(node, O_RDWR | O_CLOEXEC | O_NOFOLLOW);
    uint64_t capable = 0;
    if (source.drm_fd < 0 || drmGetCap(source.drm_fd, DRM_CAP_SYNCOBJ_TIMELINE, &capable) ||
        !capable)
      return 3;
    source.sync_timer = pw_loop_add_timer(loop, delayed_render, &source);
    if (!source.sync_timer)
      return 3;
  }
#endif
  struct pw_context *context = pw_context_new(loop, NULL, 0);
  struct pw_core *core = pw_context_connect(context, NULL, 0);
  if (!core)
    return 3;
  source.timer = pw_loop_add_timer(loop, tick, &source);
  pw_loop_add_signal(loop, SIGTERM, quit, &source);
  source.stream = pw_stream_new(core, "DPE synthetic capture",
                                pw_properties_new(PW_KEY_MEDIA_CLASS, "Video/Source",
                                                  PW_KEY_NODE_NAME, "dpe-synthetic-video", NULL));
  const struct pw_stream_events events = {.version = PW_VERSION_STREAM_EVENTS,
                                          .state_changed = state,
                                          .param_changed = format,
#ifdef DPE_SOURCE_DMABUF
                                          .add_buffer = add_buffer,
                                          .remove_buffer = remove_buffer,
#endif
                                          .process = process};
  pw_stream_add_listener(source.stream, &source.listener, &events, &source);
  uint8_t memory[512];
  struct spa_pod_builder builder = SPA_POD_BUILDER_INIT(memory, sizeof(memory));
  struct spa_pod_frame frame;
  spa_pod_builder_push_object(&builder, &frame, SPA_TYPE_OBJECT_Format, SPA_PARAM_EnumFormat);
  spa_pod_builder_add(
      &builder, SPA_FORMAT_mediaType, SPA_POD_Id(SPA_MEDIA_TYPE_video), SPA_FORMAT_mediaSubtype,
      SPA_POD_Id(SPA_MEDIA_SUBTYPE_raw), SPA_FORMAT_VIDEO_format, SPA_POD_Id(SPA_VIDEO_FORMAT_BGRA),
      SPA_FORMAT_VIDEO_size, SPA_POD_Rectangle(&SPA_RECTANGLE(source.width, source.height)),
      SPA_FORMAT_VIDEO_framerate, SPA_POD_Fraction(&SPA_FRACTION(source.fps, 1)), 0);
  if (source.dmabuf) {
    // This fixture offers exactly one fixed modifier, rather than a list that
    // requires allocator-driven fixation. Unsupported linear allocation fails.
    spa_pod_builder_prop(&builder, SPA_FORMAT_VIDEO_modifier, SPA_POD_PROP_FLAG_MANDATORY);
    spa_pod_builder_long(&builder, 0); // Explicit DRM_FORMAT_MOD_LINEAR, no implicit modifier.
  }
  const struct spa_pod *params[] = {spa_pod_builder_pop(&builder, &frame)};
  if (pw_stream_connect(source.stream, PW_DIRECTION_OUTPUT, PW_ID_ANY,
                        PW_STREAM_FLAG_DRIVER | (source.dmabuf ? PW_STREAM_FLAG_ALLOC_BUFFERS
                                                               : PW_STREAM_FLAG_MAP_BUFFERS),
                        params, 1) < 0)
    return 4;
  pw_main_loop_run(source.loop);
  pw_stream_destroy(source.stream);
#ifdef DPE_SOURCE_DMABUF
  gpu_source_destroy(source.gpu);
  if (source.drm_fd >= 0)
    close(source.drm_fd);
  if (source.sync_timeline)
    fprintf(stderr, "sync queued=%llu acquired=%llu released=%llu release_waits=%llu delay_ms=%u\n",
            (unsigned long long)source.sync_queued, (unsigned long long)source.sync_acquired,
            (unsigned long long)source.sync_released, (unsigned long long)source.sync_waits,
            source.sync_delay_ms);
#endif
  pw_core_disconnect(core);
  pw_context_destroy(context);
  pw_main_loop_destroy(source.loop);
  pw_deinit();
  return source.failed;
}
