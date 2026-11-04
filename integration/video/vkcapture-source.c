// SPDX-License-Identifier: MIT
// Selected same-user hook socket -> explicitly owned GPU buffers -> PipeWire.
// Game pixels stay on GPU; bounded cursor-only X11 metadata is optional.
#define _GNU_SOURCE
#include "vkcapture-compat.h"
#include "vkcapture-gpu.h"
#include "vkcapture-relay.h"
#include "vkcapture-sync.h"
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <glob.h>
#include <linux/sync_file.h>
#include <signal.h>
#include <spa/param/video/format-utils.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

enum relay_fallback {
  RELAY_NO_FALLBACK,
  RELAY_EXPORT_UNAVAILABLE,
  RELAY_SYNC_UNAVAILABLE,
  RELAY_CURSOR_COMPOSITION
};
struct source {
  struct pw_main_loop *main;
  struct pw_stream *stream;
  struct spa_hook listener;
  struct dpe_vk_gpu *gpu;
  struct pw_buffer *pending;
  struct dpe_vk_buffer *pending_gpu;
  struct dpe_vk_relay *pending_relay;
  bool relay, auto_relay, compat;
  struct spa_source *compat_timer, *stop_timer;
  struct dpe_obs_texture compat_texture;
  size_t compat_size;
  int compat_fds[4];
  unsigned compat_nfds;
  enum relay_fallback relay_fallback;
  int texture_fds[4];
  uint64_t relay_frames, relay_releases;
  struct spa_source *acquire;
  int relay_acquire_fd;
  bool have_relay_acquire;
  struct dpe_vk_packet copying;
  struct dpe_vk_packet texture, frame, deferred;
  int deferred_fds[4], fd, failed;
  const char *error_code;
  unsigned fps, requested_fps, negotiated_fps, hook_fps;
  uint32_t format_width, format_height;
  char command[64];
  size_t command_size;
  bool have_frame, deferred_texture, negotiated, streaming, source_stopped;
  uint64_t delivered, last_sequence, dropped, texture_changes, retired_buffers, fenced_frames;
};
static void fail(struct source *s, const char *reason) {
  if (s->failed)
    return;
  s->failed = 1;
  fprintf(stderr, "DPE game capture: %s\n", reason);
  pw_main_loop_quit(s->main);
}
static bool send_packet(struct source *s, struct dpe_vk_packet *p) {
  if (send(s->fd, p, sizeof(*p), MSG_NOSIGNAL | MSG_DONTWAIT) == sizeof(*p))
    return true;
  fail(s, "hook control disconnected or backpressured");
  return false;
}
static void release_packet(struct source *s, const struct dpe_vk_packet *frame) {
  if (s->compat)
    return; // Vanilla OBS hooks have no release message or ownership handshake.
  struct dpe_vk_packet p = dpe_vk_packet(DPE_VK_RELEASE);
  p.generation = frame->generation;
  p.sequence = frame->sequence;
  send_packet(s, &p);
}
static void close_relay_acquire(struct source *s) {
  if (s->have_relay_acquire) {
    close(s->relay_acquire_fd);
    s->have_relay_acquire = false;
  }
}
static void release_frame(struct source *s) {
  close_relay_acquire(s);
  s->have_frame = false;
  release_packet(s, &s->frame);
}
static const char *handoff_fallback(const struct source *s) {
  if (s->compat)
    return "upstream-implicit-sync";
  switch (s->relay_fallback) {
  case RELAY_EXPORT_UNAVAILABLE:
    return "export-unavailable";
  case RELAY_CURSOR_COMPOSITION:
    return "cursor-composition";
  case RELAY_SYNC_UNAVAILABLE:
    return "synchronization-unavailable";
  default:
    return "";
  }
}
static void preflight_relay(struct source *s) {
  if (!s->relay || dpe_vk_relay_supported(dpe_vk_gpu_fd(s->gpu)))
    return;
  if (s->auto_relay) {
    s->relay = false;
    s->relay_fallback = RELAY_SYNC_UNAVAILABLE;
  } else {
    s->error_code = "unsupported-buffer";
    fail(s, "direct relay synchronization unavailable on this device/kernel");
  }
}
static void announce(struct source *s) {
  uint32_t node = pw_stream_get_node_id(s->stream);
  if (node == PW_ID_ANY || !s->negotiated)
    return;
  printf("{\"event\":\"ready\",\"backend\":\"vkcapture\",\"nodeId\":%u,\"width\":%u,"
         "\"height\":%u,\"generation\":%llu,\"fps\":%u,\"handoff\":\"%s\","
         "\"handoffFallback\":\"%s\"}\n",
         node, s->texture.width, s->texture.height, (unsigned long long)s->texture.generation,
         s->fps, s->relay ? "relay" : "copy", handoff_fallback(s));
  fflush(stdout);
}
static void offer(struct source *s) {
  uint8_t memory[512];
  struct spa_pod_builder b = SPA_POD_BUILDER_INIT(memory, sizeof(memory));
  struct spa_pod_frame f;
  spa_pod_builder_push_object(&b, &f, SPA_TYPE_OBJECT_Format, SPA_PARAM_EnumFormat);
  spa_pod_builder_add(
      &b, SPA_FORMAT_mediaType, SPA_POD_Id(SPA_MEDIA_TYPE_video), SPA_FORMAT_mediaSubtype,
      SPA_POD_Id(SPA_MEDIA_SUBTYPE_raw), SPA_FORMAT_VIDEO_format,
      SPA_POD_Id(s->relay ? dpe_vk_relay_format(s->texture.format) : SPA_VIDEO_FORMAT_BGRA),
      SPA_FORMAT_VIDEO_size, SPA_POD_Rectangle(&SPA_RECTANGLE(s->texture.width, s->texture.height)),
      SPA_FORMAT_VIDEO_framerate, SPA_POD_Fraction(&SPA_FRACTION(0, 1)),
      SPA_FORMAT_VIDEO_maxFramerate,
      SPA_POD_CHOICE_RANGE_Fraction(&SPA_FRACTION(s->fps, 1), &SPA_FRACTION(1, 1),
                                    &SPA_FRACTION(s->fps, 1)),
      0);
  spa_pod_builder_prop(&b, SPA_FORMAT_VIDEO_modifier, SPA_POD_PROP_FLAG_MANDATORY);
  spa_pod_builder_long(&b, s->relay ? s->texture.modifier : 0);
  const struct spa_pod *params[] = {spa_pod_builder_pop(&b, &f)};
  // Updating EnumFormat does not retire the current Format synchronously.
  // Keep it valid until Format NULL/replacement; geometry checks prevent using
  // the previous pool for a resized game image.
  enum pw_stream_state state = pw_stream_get_state(s->stream, NULL);
  int result =
      state == PW_STREAM_STATE_UNCONNECTED
          ? pw_stream_connect(s->stream, PW_DIRECTION_OUTPUT, PW_ID_ANY,
                              PW_STREAM_FLAG_DRIVER | PW_STREAM_FLAG_ALLOC_BUFFERS, params, 1)
          : pw_stream_update_params(s->stream, params, 1);
  if (result < 0)
    fail(s, "PipeWire format offer rejected");
}
static void apply_texture(struct source *s, const struct dpe_vk_packet *p, int fds[4]) {
  if (s->relay && !s->texture.generation && dpe_vk_window(p) && getenv("DISPLAY")) {
    s->relay = false;
    s->relay_fallback = RELAY_CURSOR_COMPOSITION;
  }
  int result = dpe_vk_gpu_import(s->gpu, p, fds);
  if (s->relay) {
    if (p->flags || !dpe_vk_relay_format(p->format))
      result = -1;
    for (unsigned i = 0; i < 4; i++) {
      if (s->texture_fds[i] >= 0)
        close(s->texture_fds[i]);
      s->texture_fds[i] = i < p->nfd ? fcntl(fds[i], F_DUPFD_CLOEXEC, 3) : -1;
      if (i < p->nfd && s->texture_fds[i] < 0)
        result = -1;
    }
  }
  for (unsigned i = 0; i < 4; i++)
    if (fds[i] >= 0) {
      close(fds[i]);
      fds[i] = -1;
    }
  if (result < 0) {
    s->error_code = "unsupported-buffer";
    fail(s, "unsupported game texture format/device/modifier");
    return;
  }
  bool resized = p->width != s->texture.width || p->height != s->texture.height;
  s->texture = *p;
  if (s->relay || resized || pw_stream_get_state(s->stream, NULL) == PW_STREAM_STATE_UNCONNECTED)
    offer(s);
  else
    announce(s);
}
static void control_rate(struct source *s) {
  unsigned fps = s->requested_fps;
  if (s->negotiated_fps && fps > s->negotiated_fps)
    fps = s->negotiated_fps;
  if (s->failed || s->hook_fps == fps)
    return;
  if (s->compat) {
    // The upstream protocol cannot pace its producer. Bound our GPU copies to
    // the negotiated rate, without claiming this changes the game's hook rate.
    uint64_t period = 1000000000u / fps;
    struct timespec interval = {.tv_sec = period / 1000000000u, .tv_nsec = period % 1000000000u};
    struct timespec first = {.tv_nsec = 1};
    if (s->compat_timer)
      pw_loop_update_timer(pw_main_loop_get_loop(s->main), s->compat_timer, &first, &interval,
                           false);
    s->hook_fps = fps;
    return;
  }
  struct dpe_vk_packet p = dpe_vk_packet(DPE_VK_CONTROL);
  p.flags = DPE_VK_CAPTURE | (s->relay ? DPE_VK_DIRECT : 0);
  p.fps = fps;
  memcpy(p.uuid, dpe_vk_gpu_uuid(s->gpu), 16);
  if (!send_packet(s, &p))
    return;
  s->hook_fps = fps;
}
static void update_rate(struct source *s) {
  control_rate(s);
  if (s->pending_gpu || s->pending_relay || s->fps == s->requested_fps || s->failed)
    return;
  s->fps = s->requested_fps;
  if (s->texture.generation)
    offer(s);
}
static void command_input(void *opaque, int fd, uint32_t mask) {
  struct source *s = opaque;
  if (mask & (SPA_IO_HUP | SPA_IO_ERR)) {
    pw_main_loop_quit(s->main);
    return;
  }
  char data[256];
  ssize_t count = read(fd, data, sizeof(data));
  if (count < 0 && (errno == EAGAIN || errno == EINTR))
    return;
  if (count <= 0) {
    pw_main_loop_quit(s->main);
    return;
  }
  for (ssize_t i = 0; i < count; i++) {
    if (data[i] != '\n') {
      if (s->command_size >= sizeof(s->command) - 1) {
        fail(s, "oversized control command");
        return;
      }
      s->command[s->command_size++] = data[i];
      continue;
    }
    s->command[s->command_size] = 0;
    if (s->command_size < 6 || strncmp(s->command, "rate ", 5)) {
      fail(s, "invalid control command");
      return;
    }
    unsigned fps = 0;
    for (size_t j = 5; j < s->command_size; j++) {
      if (!isdigit((unsigned char)s->command[j]) || fps > 240) {
        fail(s, "invalid control rate");
        return;
      }
      fps = fps * 10 + (unsigned)(s->command[j] - '0');
    }
    if (!fps || fps > 240) {
      fail(s, "invalid control rate");
      return;
    }
    s->requested_fps = fps;
    s->command_size = 0;
  }
  update_rate(s);
}
static void completed(void *opaque, bool success) {
  struct source *s = opaque;
  struct pw_buffer *p = s->pending;
  struct dpe_vk_buffer *gpu_buffer = s->pending_gpu;
  s->pending = NULL;
  s->pending_gpu = NULL;
  bool current =
      !s->source_stopped && !s->deferred_texture && s->copying.generation == s->texture.generation;
  if (!success) {
    if (!p)
      dpe_vk_buffer_destroy(gpu_buffer);
    fail(s, "GPU copy completion failed");
    return;
  }
  // pw_buffer storage stops existing when remove_buffer returns. Its independently
  // owned GPU allocation remains alive until this completion callback.
  if (p) {
    struct spa_data *data = &p->buffer->datas[0];
    bool deliver = current && s->negotiated && s->streaming &&
                   s->format_width == s->texture.width && s->format_height == s->texture.height;
    data->chunk->size = deliver ? data->maxsize : 0;
    if (deliver) {
      struct spa_meta_header *header =
          spa_buffer_find_meta_data(p->buffer, SPA_META_Header, sizeof(*header));
      if (header)
        *header =
            (struct spa_meta_header){.pts = (int64_t)s->copying.pts, .seq = s->copying.sequence};
      s->delivered++;
    } else
      s->dropped++;
    if (pw_stream_queue_buffer(s->stream, p) < 0)
      fail(s, "PipeWire queue failed");
  } else {
    s->dropped++;
    dpe_vk_buffer_destroy(gpu_buffer);
  }
  s->have_frame = false;
  // Retired game generations no longer hold a lease. Never send a late ACK that
  // could be confused with a newer retirement after repeated swapchain changes.
  if (current)
    release_packet(s, &s->copying);
  if (s->deferred_texture) {
    s->deferred_texture = false;
    apply_texture(s, &s->deferred, s->deferred_fds);
  }
  update_rate(s);
}
static void relay_completed(void *opaque, bool success) {
  struct source *s = opaque;
  struct dpe_vk_relay *owned = s->pending_relay;
  s->pending_relay = NULL;
  bool retired = s->pending == NULL;
  s->pending = NULL;
  bool current =
      !s->source_stopped && !s->deferred_texture && s->copying.generation == s->texture.generation;
  if (retired)
    dpe_vk_relay_destroy(owned);
  if (!success) {
    fail(s, "consumer GPU release failed");
    return;
  }
  s->relay_releases++;
  s->have_frame = false;
  if (current)
    release_packet(s, &s->copying);
  if (s->deferred_texture) {
    s->deferred_texture = false;
    apply_texture(s, &s->deferred, s->deferred_fds);
  }
  update_rate(s);
}
static void process(void *opaque) {
  struct source *s = opaque;
  if (s->pending_gpu || s->pending_relay || s->acquire || !s->have_frame || !s->streaming ||
      !s->negotiated || s->failed)
    return;
  if (s->source_stopped || s->format_width != s->texture.width ||
      s->format_height != s->texture.height) {
    s->dropped++;
    release_frame(s);
    return;
  }
  struct pw_buffer *p = pw_stream_dequeue_buffer(s->stream);
  if (!p) {
    s->dropped++;
    release_frame(s);
    return;
  }
  s->pending = p;
  if (s->relay) {
    struct dpe_vk_relay *relay = p->user_data;
    if (!relay || dpe_vk_relay_generation(relay) != s->texture.generation) {
      s->pending = NULL;
      p->buffer->datas[0].chunk->size = 0;
      pw_stream_queue_buffer(s->stream, p);
      s->dropped++;
      release_frame(s);
      return;
    }
    p->buffer->datas[0].chunk->size = 1;
    s->pending_relay = relay;
    s->copying = s->frame;
    struct spa_meta_header *header =
        spa_buffer_find_meta_data(p->buffer, SPA_META_Header, sizeof(*header));
    if (header)
      *header = (struct spa_meta_header){.pts = (int64_t)s->frame.pts, .seq = s->frame.sequence};
    int acquire_fd = s->have_relay_acquire ? s->relay_acquire_fd : -1;
    s->have_relay_acquire = false; // submit owns the descriptor, including on failure.
    if (dpe_vk_relay_submit(relay, acquire_fd, relay_completed, s) < 0 ||
        pw_stream_queue_buffer(s->stream, p) < 0) {
      fail(s, "relay synchronization or queue failed");
      return;
    }
    s->delivered++;
    s->relay_frames++;
    return;
  }
  s->pending_gpu = p->user_data;
  s->copying = s->frame;
  int result = p->user_data ? dpe_vk_buffer_copy(p->user_data, completed, s) : -EINVAL;
  if (result == -EAGAIN) {
    // An old pool buffer can be returned while a replacement pool is negotiated.
    s->pending = NULL;
    s->pending_gpu = NULL;
    p->buffer->datas[0].chunk->size = 0;
    pw_stream_queue_buffer(s->stream, p);
    s->dropped++;
    release_frame(s);
  } else if (result < 0)
    fail(s, "GPU copy submission failed");
}
static void add_buffer(void *opaque, struct pw_buffer *p) {
  struct source *s = opaque;
  struct spa_buffer *b = p->buffer;
  if (s->relay) {
    p->user_data = dpe_vk_relay_create(dpe_vk_gpu_fd(s->gpu), pw_main_loop_get_loop(s->main), b,
                                       &s->texture, s->texture_fds);
    if (!p->user_data)
      fail(s, "relay buffer or explicit synchronization unavailable");
    return;
  }
  if (b->n_datas != 1 || !b->datas[0].chunk || !(b->datas[0].type & (1u << SPA_DATA_DmaBuf)) ||
      !(p->user_data =
            dpe_vk_buffer_create(s->gpu, &b->datas[0], s->format_width, s->format_height)))
    fail(s, "GPU output allocation failed");
}
static void remove_buffer(void *opaque, struct pw_buffer *p) {
  struct source *s = opaque;
  if (s->relay) {
    if (s->pending == p && s->pending_relay) {
      s->pending = NULL;
      s->retired_buffers++;
    } else
      dpe_vk_relay_destroy(p->user_data);
    p->user_data = NULL;
    return;
  }
  if (s->pending == p && s->pending_gpu) {
    s->pending = NULL;
    s->retired_buffers++;
  } else
    dpe_vk_buffer_destroy(p->user_data);
  p->user_data = NULL;
}
static void format(void *opaque, uint32_t id, const struct spa_pod *value) {
  struct source *s = opaque;
  if (id != SPA_PARAM_Format)
    return;
  if (!value) {
    s->negotiated = false;
    return;
  }
  struct spa_video_info_raw raw;
  if (spa_format_video_raw_parse(value, &raw) < 0 ||
      raw.format != (s->relay ? dpe_vk_relay_format(s->texture.format) : SPA_VIDEO_FORMAT_BGRA) ||
      !raw.size.width || !raw.size.height || raw.size.width > 8192 || raw.size.height > 8192 ||
      !(raw.flags & SPA_VIDEO_FLAG_MODIFIER) ||
      raw.modifier != (s->relay ? s->texture.modifier : 0)) {
    fail(s, "PipeWire negotiated an unsupported output format");
    return;
  }
  struct spa_fraction rate = raw.framerate.num ? raw.framerate : raw.max_framerate;
  if (!rate.denom || rate.num < rate.denom || (uint64_t)rate.num > UINT64_C(240) * rate.denom) {
    fail(s, "PipeWire negotiated an unsupported frame rate");
    return;
  }
  s->format_width = raw.size.width;
  s->format_height = raw.size.height;
  s->negotiated_fps = rate.num / rate.denom;
  s->negotiated = true;
  control_rate(s);
  uint8_t memory[1024];
  struct spa_pod_builder b = SPA_POD_BUILDER_INIT(memory, sizeof(memory));
  const struct spa_pod *params[4];
  unsigned count = 0;
  if (s->relay) {
    struct spa_pod_frame buffers;
    spa_pod_builder_push_object(&b, &buffers, SPA_TYPE_OBJECT_ParamBuffers, SPA_PARAM_Buffers);
    spa_pod_builder_add(&b, SPA_PARAM_BUFFERS_buffers, SPA_POD_CHOICE_RANGE_Int(4, 2, 8),
                        SPA_PARAM_BUFFERS_blocks, SPA_POD_Int(s->texture.nfd + 2),
                        SPA_PARAM_BUFFERS_dataType, SPA_POD_CHOICE_FLAGS_Int(1 << SPA_DATA_DmaBuf),
                        0);
    spa_pod_builder_prop(&b, DPE_RELAY_META_TYPE, SPA_POD_PROP_FLAG_MANDATORY);
    spa_pod_builder_int(&b, 1 << DPE_RELAY_META);
    params[count++] = spa_pod_builder_pop(&b, &buffers);
    params[count++] = spa_pod_builder_add_object(&b, SPA_TYPE_OBJECT_ParamMeta, SPA_PARAM_Meta,
                                                 SPA_PARAM_META_type, SPA_POD_Id(DPE_RELAY_META),
                                                 SPA_PARAM_META_size,
                                                 SPA_POD_Int(sizeof(struct dpe_relay_timeline)));
  } else {
    params[count++] = spa_pod_builder_add_object(
        &b, SPA_TYPE_OBJECT_ParamBuffers, SPA_PARAM_Buffers, SPA_PARAM_BUFFERS_buffers,
        SPA_POD_CHOICE_RANGE_Int(4, 2, 8), SPA_PARAM_BUFFERS_blocks, SPA_POD_Int(1),
        SPA_PARAM_BUFFERS_size, SPA_POD_Int(raw.size.width * raw.size.height * 4),
        SPA_PARAM_BUFFERS_stride, SPA_POD_Int(raw.size.width * 4), SPA_PARAM_BUFFERS_dataType,
        SPA_POD_CHOICE_FLAGS_Int(1 << SPA_DATA_DmaBuf));
  }
  params[count++] =
      spa_pod_builder_add_object(&b, SPA_TYPE_OBJECT_ParamMeta, SPA_PARAM_Meta, SPA_PARAM_META_type,
                                 SPA_POD_Id(SPA_META_Header), SPA_PARAM_META_size,
                                 SPA_POD_Int(sizeof(struct spa_meta_header)));
  if (pw_stream_update_params(s->stream, params, count) < 0) {
    fail(s, "PipeWire buffer negotiation failed");
    return;
  }
  announce(s);
}
static void state(void *opaque, enum pw_stream_state old, enum pw_stream_state next,
                  const char *error) {
  (void)old;
  (void)error;
  struct source *s = opaque;
  s->streaming = next == PW_STREAM_STATE_STREAMING;
  if (next == PW_STREAM_STATE_ERROR)
    fail(s, "PipeWire stream failed");
  if (next == PW_STREAM_STATE_PAUSED) {
    // Node readiness precedes consumer format negotiation. The native picker
    // needs its ID to connect; requested geometry is explicit, not inferred.
    uint32_t node = pw_stream_get_node_id(s->stream);
    if (node != PW_ID_ANY) {
      printf("{\"event\":\"ready\",\"backend\":\"vkcapture\",\"nodeId\":%u,\"width\":%u,\"height\":"
             "%u,\"generation\":%llu,\"fps\":%u,\"handoff\":\"%s\","
             "\"handoffFallback\":\"%s\"}\n",
             node, s->texture.width, s->texture.height, (unsigned long long)s->texture.generation,
             s->fps, s->relay ? "relay" : "copy", handoff_fallback(s));
      fflush(stdout);
    }
  }
}
static void cancel_acquire(struct source *s) {
  close_relay_acquire(s);
  if (s->acquire) {
    pw_loop_destroy_source(pw_main_loop_get_loop(s->main), s->acquire);
    s->acquire = NULL;
  }
}
static void acquired(void *opaque, int fd, uint32_t mask) {
  struct source *s = opaque;
  bool valid =
      (mask & SPA_IO_IN) && !(mask & (SPA_IO_ERR | SPA_IO_HUP)) && dpe_vk_fence_signaled(fd);
  cancel_acquire(s);
  if (!valid) {
    fail(s, "game GPU completion fence failed");
    return;
  }
  if (s->have_frame && !s->source_stopped && s->frame.generation == s->texture.generation) {
    if (s->streaming && s->negotiated)
      pw_stream_trigger_process(s->stream);
    else
      release_frame(s);
  }
}
// A surface may be replaced during resize. Give its successor a short grace
// period, but do not leave a permanently destroyed game window sharing forever.
static void stopped_timeout(void *opaque, uint64_t expirations) {
  (void)expirations;
  struct source *s = opaque;
  if (s->source_stopped)
    fail(s, "selected game surface closed");
}
static void hook_input(void *opaque, int fd, uint32_t mask) {
  struct source *s = opaque;
  if (mask & (SPA_IO_HUP | SPA_IO_ERR)) {
    fail(s, "selected game disconnected");
    return;
  }
  for (unsigned iteration = 0; iteration < 4; iteration++) {
    struct dpe_vk_packet p;
    int fds[4] = {-1, -1, -1, -1};
    unsigned count = 0;
    union {
      struct cmsghdr align;
      char data[CMSG_SPACE(16 * sizeof(int))];
    } control;
    struct iovec iov = {.iov_base = &p, .iov_len = sizeof(p)};
    struct msghdr msg = {.msg_iov = &iov,
                         .msg_iovlen = 1,
                         .msg_control = control.data,
                         .msg_controllen = sizeof(control)};
    ssize_t n = recvmsg(fd, &msg, MSG_DONTWAIT | MSG_CMSG_CLOEXEC | MSG_TRUNC);
    if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK))
      return;
    bool valid =
        n > 0 && dpe_vk_valid(&p, (size_t)n) && !(msg.msg_flags & (MSG_TRUNC | MSG_CTRUNC));
    for (struct cmsghdr *c = CMSG_FIRSTHDR(&msg); n >= 0 && c; c = CMSG_NXTHDR(&msg, c)) {
      if (c->cmsg_level != SOL_SOCKET || c->cmsg_type != SCM_RIGHTS || c->cmsg_len < CMSG_LEN(0)) {
        valid = false;
        continue;
      }
      size_t num = (c->cmsg_len - CMSG_LEN(0)) / sizeof(int);
      int *received = (int *)CMSG_DATA(c);
      for (size_t i = 0; i < num; i++) {
        if (count < 4)
          fds[count++] = received[i];
        else {
          close(received[i]);
          valid = false;
        }
      }
    }
    if (!valid) {
      for (unsigned i = 0; i < count; i++)
        close(fds[i]);
      fail(s, "invalid hook packet");
      return;
    }
    if (p.type == DPE_VK_UNSUPPORTED && !count && !p.nfd && !p.flags && !p.generation &&
        !s->texture.generation && s->relay) {
      if (!s->auto_relay) {
        s->error_code = "unsupported-buffer";
        fail(s, "game cannot allocate a directly importable GPU buffer");
        return;
      }
      // No texture was exported and no native stream exists yet. The hook
      // stopped admission; retry its ordinary GPU-copy allocation once.
      s->relay = false;
      s->relay_fallback = RELAY_EXPORT_UNAVAILABLE;
      s->hook_fps = 0;
      control_rate(s);
      continue;
    }
    if (p.type == DPE_VK_TEXTURE && count == p.nfd && count &&
        p.generation > (s->deferred_texture ? s->deferred.generation : s->texture.generation)) {
      cancel_acquire(s);
      if (!s->pending_gpu && !s->pending_relay)
        s->have_frame = false;
      s->texture_changes++;
      s->source_stopped = false;
      if (s->stop_timer)
        pw_loop_update_timer(pw_main_loop_get_loop(s->main), s->stop_timer, NULL, NULL, false);
      if (s->pending_gpu || s->pending_relay) {
        if (s->deferred_texture)
          for (unsigned i = 0; i < 4; i++)
            if (s->deferred_fds[i] >= 0)
              close(s->deferred_fds[i]);
        s->deferred = p;
        memcpy(s->deferred_fds, fds, sizeof(fds));
        s->deferred_texture = true;
      } else
        apply_texture(s, &p, fds);
    } else {
      uint64_t generation = s->deferred_texture ? s->deferred.generation : s->texture.generation;
      bool fenced = p.type == DPE_VK_FRAME && p.flags == DPE_VK_SYNC_FD && p.nfd == 1 && count == 1;
      bool plain = p.flags == 0 && p.nfd == 0 && count == 0;
      if (p.type == DPE_VK_FRAME && (fenced || plain) && p.generation == generation &&
          !s->source_stopped && p.sequence > s->last_sequence &&
          (!s->have_frame ||
           ((s->pending_gpu || s->pending_relay) && p.generation != s->copying.generation))) {
        if (fenced) {
          struct sync_file_info info = {0};
          if (ioctl(fds[0], SYNC_IOC_FILE_INFO, &info) < 0 || info.status < 0) {
            close(fds[0]);
            fail(s, "invalid game completion fence");
            return;
          }
          s->fenced_frames++;
        }
        s->last_sequence = p.sequence;
        if (s->pending_gpu || s->pending_relay || !s->streaming || !s->negotiated) {
          if (fenced)
            close(fds[0]);
          s->dropped++;
          release_packet(s, &p);
          continue;
        }
        s->frame = p;
        s->have_frame = true;
        if (fenced && s->relay) {
          s->relay_acquire_fd = fds[0];
          s->have_relay_acquire = true;
          pw_stream_trigger_process(s->stream);
        } else if (fenced) {
          s->acquire = pw_loop_add_io(pw_main_loop_get_loop(s->main), fds[0],
                                      SPA_IO_IN | SPA_IO_ERR | SPA_IO_HUP, true, acquired, s);
          if (!s->acquire) {
            close(fds[0]);
            fail(s, "game completion fence watch failed");
            return;
          }
        } else
          pw_stream_trigger_process(s->stream);
      } else {
        for (unsigned i = 0; i < count; i++)
          close(fds[i]);
        if (p.type == DPE_VK_STOP && plain && p.generation == generation) {
          cancel_acquire(s);
          s->have_frame = false;
          s->source_stopped = true;
          struct timespec grace = {.tv_sec = 5};
          if (s->stop_timer)
            pw_loop_update_timer(pw_main_loop_get_loop(s->main), s->stop_timer, &grace, NULL,
                                 false);
          else
            fail(s, "selected game surface closed");
        } else {
          fail(s, "unexpected hook generation/sequence or descriptors");
          return;
        }
      }
    }
    if (s->failed)
      return;
  }
}
static void compat_tick(void *opaque, uint64_t expirations) {
  (void)expirations;
  struct source *s = opaque;
  if (!s->texture.generation || !s->streaming || !s->negotiated || s->pending_gpu ||
      s->deferred_texture || s->have_frame)
    return;
  struct timespec now;
  clock_gettime(CLOCK_MONOTONIC, &now);
  s->frame = dpe_vk_packet(DPE_VK_FRAME);
  s->frame.generation = s->texture.generation;
  s->frame.sequence = ++s->last_sequence;
  s->frame.pts = (uint64_t)now.tv_sec * 1000000000u + now.tv_nsec;
  s->have_frame = true;
  pw_stream_trigger_process(s->stream);
}
static void compat_input(void *opaque, int fd, uint32_t mask) {
  struct source *s = opaque;
  if (mask & (SPA_IO_HUP | SPA_IO_ERR)) {
    fail(s, "upstream game disconnected");
    return;
  }
  for (unsigned iteration = 0; iteration < 4; iteration++) {
    union {
      struct cmsghdr align;
      char bytes[CMSG_SPACE(16 * sizeof(int))];
    } ancillary;
    struct iovec iov = {.iov_base = (uint8_t *)&s->compat_texture + s->compat_size,
                        .iov_len = sizeof(s->compat_texture) - s->compat_size};
    struct msghdr msg = {.msg_iov = &iov,
                         .msg_iovlen = 1,
                         .msg_control = ancillary.bytes,
                         .msg_controllen = sizeof(ancillary)};
    ssize_t count = recvmsg(fd, &msg, MSG_DONTWAIT | MSG_CMSG_CLOEXEC);
    if (count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR))
      return;
    bool valid = count > 0 && !(msg.msg_flags & (MSG_TRUNC | MSG_CTRUNC));
    for (struct cmsghdr *c = CMSG_FIRSTHDR(&msg); count >= 0 && c; c = CMSG_NXTHDR(&msg, c)) {
      if (c->cmsg_level != SOL_SOCKET || c->cmsg_type != SCM_RIGHTS || c->cmsg_len < CMSG_LEN(0)) {
        valid = false;
        continue;
      }
      size_t length = (c->cmsg_len - CMSG_LEN(0)) / sizeof(int);
      int *fds = (int *)CMSG_DATA(c);
      for (size_t i = 0; i < length; i++) {
        if (s->compat_nfds < 4)
          s->compat_fds[s->compat_nfds++] = fds[i];
        else {
          close(fds[i]);
          valid = false;
        }
      }
    }
    if (!valid) {
      fail(s, "invalid upstream game packet");
      return;
    }
    s->compat_size += (size_t)count;
    if (s->compat_size != sizeof(s->compat_texture))
      continue;
    struct dpe_vk_packet texture;
    uint64_t generation =
        (s->deferred_texture ? s->deferred.generation : s->texture.generation) + 1;
    if (!dpe_obs_texture_packet(&s->compat_texture, s->compat_nfds, generation, &texture)) {
      fail(s, "unsupported upstream game texture");
      return;
    }
    s->compat_size = s->compat_nfds = 0;
    s->texture_changes++;
    if (s->pending_gpu) {
      if (s->deferred_texture)
        for (unsigned i = 0; i < 4; i++)
          if (s->deferred_fds[i] >= 0)
            close(s->deferred_fds[i]);
      s->deferred = texture;
      memcpy(s->deferred_fds, s->compat_fds, sizeof(s->deferred_fds));
      s->deferred_texture = true;
    } else
      apply_texture(s, &texture, s->compat_fds);
    for (unsigned i = 0; i < 4; i++)
      s->compat_fds[i] = -1;
    if (s->failed)
      return;
  }
}
static void quit(void *opaque, int signal) {
  (void)signal;
  pw_main_loop_quit(((struct source *)opaque)->main);
}
int main(int argc, char **argv) {
  struct source s = {.fd = -1, .texture_fds = {-1, -1, -1, -1}, .compat_fds = {-1, -1, -1, -1}};
  const char *handoff = getenv("DPE_GAME_CAPTURE_HANDOFF");
  if (handoff && strcmp(handoff, "copy") && strcmp(handoff, "relay") && strcmp(handoff, "auto"))
    return 2;
  s.auto_relay = handoff && !strcmp(handoff, "auto");
  s.relay = s.auto_relay || (handoff && !strcmp(handoff, "relay"));
  const char *node = NULL;
  uint8_t wanted_uuid[16] = {0};
  bool have_uuid = false;
  for (int i = 1; i < argc; i += 2) {
    if (i + 1 >= argc)
      return 2;
    char *end = NULL;
    unsigned long value = strtoul(argv[i + 1], &end, 10);
    if (!strcmp(argv[i], "--hook-fd") && !*end && value <= INT32_MAX)
      s.fd = (int)value;
    else if (!strcmp(argv[i], "--fps") && !*end && value >= 1 && value <= 240)
      s.fps = value;
    else if (!strcmp(argv[i], "--render-node"))
      node = argv[i + 1];
    else if (!strcmp(argv[i], "--hook-protocol") && !strcmp(argv[i + 1], "obs"))
      s.compat = true;
    else if (!strcmp(argv[i], "--device-uuid") && strlen(argv[i + 1]) == 32) {
      have_uuid = true;
      for (unsigned j = 0; j < 16; j++) {
        char byte[] = {argv[i + 1][j * 2], argv[i + 1][j * 2 + 1], 0};
        wanted_uuid[j] = strtoul(byte, &end, 16);
        if (*end || !isxdigit((unsigned char)byte[0]) || !isxdigit((unsigned char)byte[1]))
          return 2;
      }
    } else
      return 2;
  }
  struct ucred cred;
  socklen_t size = sizeof(cred);
  int kind = 0;
  socklen_t kind_size = sizeof(kind);
  if (s.fd < 0 || !node || (strcmp(node, "auto") && strncmp(node, "/dev/dri/renderD", 16)) ||
      !s.fps || (!have_uuid && !s.compat) ||
      getsockopt(s.fd, SOL_SOCKET, SO_TYPE, &kind, &kind_size) ||
      kind != (s.compat ? SOCK_STREAM : SOCK_SEQPACKET) ||
      getsockopt(s.fd, SOL_SOCKET, SO_PEERCRED, &cred, &size) || cred.uid != geteuid())
    return 2;
  if (s.compat)
    s.relay = s.auto_relay = false;
  fcntl(s.fd, F_SETFD, FD_CLOEXEC);
  fcntl(s.fd, F_SETFL, fcntl(s.fd, F_GETFL) | O_NONBLOCK);
  s.requested_fps = s.fps;
  // PipeWire uses signalfd. Block before EGL/PipeWire create worker threads so
  // SIGTERM cannot take its default action on a thread with an inherited mask.
  sigset_t signals;
  sigemptyset(&signals);
  sigaddset(&signals, SIGTERM);
  sigaddset(&signals, SIGINT);
  if (sigprocmask(SIG_BLOCK, &signals, NULL) < 0)
    return 3;
  pw_init(NULL, NULL);
  s.main = pw_main_loop_new(NULL);
  if (!s.main)
    return 3;
  struct pw_loop *loop = pw_main_loop_get_loop(s.main);
  if (!strcmp(node, "auto")) {
    glob_t nodes = {0};
    if (!glob("/dev/dri/renderD*", 0, NULL, &nodes)) {
      for (size_t i = 0; i < nodes.gl_pathc && i < 16 && !s.gpu; i++) {
        struct dpe_vk_gpu *gpu = dpe_vk_gpu_create(nodes.gl_pathv[i], loop, 1, 1);
        if (gpu && (s.compat || !memcmp(dpe_vk_gpu_uuid(gpu), wanted_uuid, 16)))
          s.gpu = gpu;
        else
          dpe_vk_gpu_destroy(gpu);
      }
    }
    globfree(&nodes);
  } else {
    s.gpu = dpe_vk_gpu_create(node, loop, 1, 1);
    if (s.gpu && !s.compat && memcmp(dpe_vk_gpu_uuid(s.gpu), wanted_uuid, 16)) {
      dpe_vk_gpu_destroy(s.gpu);
      s.gpu = NULL;
    }
  }
  if (!s.gpu)
    return 3;
  struct pw_context *context = pw_context_new(loop, NULL, 0);
  struct pw_core *core = context ? pw_context_connect(context, NULL, 0) : NULL;
  if (!core)
    return 3;
  s.stream = pw_stream_new(core, "DPE game capture",
                           pw_properties_new(PW_KEY_MEDIA_CLASS, "Video/Source", PW_KEY_NODE_NAME,
                                             "dpe-game-capture", PW_KEY_NODE_DESCRIPTION,
                                             "DPE game capture", NULL));
  if (!s.stream)
    return 3;
  const struct pw_stream_events events = {.version = PW_VERSION_STREAM_EVENTS,
                                          .state_changed = state,
                                          .param_changed = format,
                                          .add_buffer = add_buffer,
                                          .remove_buffer = remove_buffer,
                                          .process = process};
  pw_stream_add_listener(s.stream, &s.listener, &events, &s);
  struct spa_source *input = pw_loop_add_io(loop, s.fd, SPA_IO_IN | SPA_IO_HUP | SPA_IO_ERR, false,
                                            s.compat ? compat_input : hook_input, &s);
  fcntl(STDIN_FILENO, F_SETFL, fcntl(STDIN_FILENO, F_GETFL) | O_NONBLOCK);
  struct spa_source *commands = pw_loop_add_io(
      loop, STDIN_FILENO, SPA_IO_IN | SPA_IO_HUP | SPA_IO_ERR, false, command_input, &s);
  pw_loop_add_signal(loop, SIGTERM, quit, &s);
  pw_loop_add_signal(loop, SIGINT, quit, &s);
  s.stop_timer = pw_loop_add_timer(loop, stopped_timeout, &s);
  preflight_relay(&s);
  struct dpe_vk_packet control = dpe_vk_packet(DPE_VK_CONTROL);
  control.flags = DPE_VK_CAPTURE | (s.relay ? DPE_VK_DIRECT : 0);
  control.fps = s.fps;
  s.hook_fps = s.fps;
  memcpy(control.uuid, dpe_vk_gpu_uuid(s.gpu), 16);
  bool control_sent;
  if (s.compat) {
    struct dpe_obs_control obs = {.capturing = 1};
    memcpy(obs.uuid, dpe_vk_gpu_uuid(s.gpu), 16);
    s.compat_timer = pw_loop_add_timer(loop, compat_tick, &s);
    s.hook_fps = 0;
    control_rate(&s);
    control_sent =
        s.compat_timer && send(s.fd, &obs, sizeof(obs), MSG_NOSIGNAL | MSG_DONTWAIT) == sizeof(obs);
  } else
    control_sent = send_packet(&s, &control);
  if (s.failed || !input || !commands || !control_sent)
    s.failed = 1;
  else
    pw_main_loop_run(s.main);
  // A disconnected consumer never releases a texture still being read by its GPU.
  // Closing the socket tells the hook to retire that allocation; imported EGL
  // storage remains referenced until queued GPU work is retired on teardown.
  close(s.fd);
  cancel_acquire(&s);
  if (input)
    pw_loop_destroy_source(loop, input);
  if (commands)
    pw_loop_destroy_source(loop, commands);
  if (s.compat_timer)
    pw_loop_destroy_source(loop, s.compat_timer);
  if (s.stop_timer)
    pw_loop_destroy_source(loop, s.stop_timer);
  for (unsigned i = 0; i < s.compat_nfds; i++)
    close(s.compat_fds[i]);
  if (s.deferred_texture)
    for (unsigned i = 0; i < 4; i++)
      if (s.deferred_fds[i] >= 0)
        close(s.deferred_fds[i]);
  pw_stream_destroy(s.stream);
  if (s.pending_relay)
    dpe_vk_relay_destroy(s.pending_relay);
  for (unsigned i = 0; i < 4; i++)
    if (s.texture_fds[i] >= 0)
      close(s.texture_fds[i]);
  if (s.pending_gpu)
    dpe_vk_buffer_destroy(s.pending_gpu);
  dpe_vk_gpu_destroy(s.gpu);
  pw_core_disconnect(core);
  pw_context_destroy(context);
  pw_main_loop_destroy(s.main);
  pw_deinit();
  printf("{\"event\":\"ended\",\"backend\":\"vkcapture\",\"frames\":%llu,\"failed\":%s,"
         "\"errorCode\":\"%s\",\"dropped\":%llu,\"textureChanges\":%llu,\"retiredBuffers\":%llu,"
         "\"fencedFrames\":%llu,\"handoff\":\"%s\",\"handoffFallback\":\"%s\","
         "\"relayFrames\":%llu,\"relayReleases\":%llu}\n",
         (unsigned long long)s.delivered, s.failed ? "true" : "false",
         s.failed ? (s.error_code ? s.error_code : "capture-failed") : "",
         (unsigned long long)s.dropped, (unsigned long long)s.texture_changes,
         (unsigned long long)s.retired_buffers, (unsigned long long)s.fenced_frames,
         s.relay ? "relay" : "copy", handoff_fallback(&s), (unsigned long long)s.relay_frames,
         (unsigned long long)s.relay_releases);
  return s.failed;
}
