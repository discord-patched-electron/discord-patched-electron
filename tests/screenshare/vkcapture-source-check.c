// SPDX-License-Identifier: MIT
// Actual producer callbacks with owned Unix sockets and deferred synthetic GPU
// completion. No graphics device, compositor, or PipeWire daemon is opened.
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/sync_file.h>
#include <pipewire/pipewire.h>
#include <stdarg.h>
#include <stdlib.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <unistd.h>

static struct pw_buffer *available;
static unsigned queued, destroyed, imports, offers, allocated_width, allocated_height;
static uint64_t queued_sequence;
static uint32_t queued_size;
static void (*done)(void *, bool);
static void *done_data;
static int source_quit(struct pw_main_loop *p) {
  (void)p;
  return 0;
}
static uint32_t source_node(struct pw_stream *p) {
  (void)p;
  return 42;
}
static enum pw_stream_state source_state(struct pw_stream *p, const char **error) {
  (void)p;
  (void)error;
  return PW_STREAM_STATE_STREAMING;
}
static int source_params(struct pw_stream *p, const struct spa_pod **params, uint32_t count) {
  (void)p;
  (void)params;
  (void)count;
  offers++;
  return 0;
}
static int source_connect(struct pw_stream *p, enum pw_direction direction, uint32_t id,
                          enum pw_stream_flags flags, const struct spa_pod **params,
                          uint32_t count) {
  (void)direction;
  (void)id;
  (void)flags;
  return source_params(p, params, count);
}
static struct pw_buffer *source_dequeue(struct pw_stream *p) {
  (void)p;
  return available;
}
static int source_queue(struct pw_stream *p, struct pw_buffer *buffer) {
  (void)p;
  queued++;
  queued_size = buffer->buffer->datas[0].chunk->size;
  for (uint32_t i = 0; i < buffer->buffer->n_metas; i++)
    if (buffer->buffer->metas[i].type == SPA_META_Header)
      queued_sequence = ((struct spa_meta_header *)buffer->buffer->metas[i].data)->seq;
  return 0;
}
static int source_trigger(struct pw_stream *p) {
  (void)p;
  return 0;
}
static struct spa_source acquire_source;
static void (*acquire_callback)(void *, int, uint32_t);
static void *acquire_data;
static int acquire_status;
static struct pw_loop *source_loop(struct pw_main_loop *p) {
  (void)p;
  return (struct pw_loop *)1;
}
static struct spa_source *source_add_io(struct pw_loop *p, int fd, uint32_t mask, bool close_fd,
                                        void (*callback)(void *, int, uint32_t), void *data) {
  (void)p;
  (void)mask;
  assert(close_fd);
  assert(!acquire_callback);
  acquire_source.fd = fd;
  acquire_callback = callback;
  acquire_data = data;
  return &acquire_source;
}
static void source_destroy_io(struct pw_loop *p, struct spa_source *source) {
  (void)p;
  assert(source == &acquire_source);
  close(source->fd);
  acquire_callback = NULL;
  acquire_data = NULL;
}
static int source_ioctl(int fd, unsigned long request, ...) {
  assert(request == SYNC_IOC_FILE_INFO && fcntl(fd, F_GETFD) >= 0);
  va_list arguments;
  va_start(arguments, request);
  struct sync_file_info *info = va_arg(arguments, struct sync_file_info *);
  info->status = acquire_status;
  va_end(arguments);
  return 0;
}
#undef pw_loop_add_io
#undef pw_loop_destroy_source
#define pw_main_loop_get_loop source_loop
#define pw_loop_add_io source_add_io
#define pw_loop_destroy_source source_destroy_io
#define ioctl source_ioctl
#define pw_main_loop_quit source_quit
#define pw_stream_get_node_id source_node
#define pw_stream_get_state source_state
#define pw_stream_update_params source_params
#define pw_stream_connect source_connect
#define pw_stream_dequeue_buffer source_dequeue
#define pw_stream_queue_buffer source_queue
#define pw_stream_trigger_process source_trigger
static struct spa_source stopped_timer;
static bool stopped_timer_armed;
static unsigned stopped_timer_cancelled;
static int fixture_update_stop_timer(struct pw_loop *loop, struct spa_source *timer,
                                     const struct timespec *value, const struct timespec *interval,
                                     bool absolute) {
  (void)loop;
  assert(timer == &stopped_timer && !interval && !absolute);
  if (value) {
    assert(value->tv_sec == 5 && value->tv_nsec == 0);
    stopped_timer_armed = true;
  } else {
    stopped_timer_armed = false;
    stopped_timer_cancelled++;
  }
  return 0;
}
#undef pw_loop_update_timer
#define pw_loop_update_timer fixture_update_stop_timer
#define main unused_source_main
#include "../../integration/video/vkcapture-source.c"
#undef main

struct dpe_vk_gpu {
  uint8_t uuid[16];
  uint32_t width, height;
};
struct dpe_vk_buffer {
  unsigned identity;
};
static struct dpe_vk_buffer allocation;
const uint8_t *dpe_vk_gpu_uuid(struct dpe_vk_gpu *gpu) { return gpu->uuid; }
int dpe_vk_gpu_fd(struct dpe_vk_gpu *gpu) {
  (void)gpu;
  return -1;
}
int dpe_vk_gpu_import(struct dpe_vk_gpu *gpu, const struct dpe_vk_packet *p, const int fds[4]) {
  assert(fds[0] >= 0);
  imports++;
  gpu->width = p->width;
  gpu->height = p->height;
  return 0;
}
struct dpe_vk_buffer *dpe_vk_buffer_create(struct dpe_vk_gpu *gpu, struct spa_data *data,
                                           uint32_t width, uint32_t height) {
  (void)gpu;
  (void)data;
  allocated_width = width;
  allocated_height = height;
  return &allocation;
}
void dpe_vk_buffer_destroy(struct dpe_vk_buffer *p) {
  assert(p);
  destroyed++;
}
int dpe_vk_buffer_copy(struct dpe_vk_buffer *p, void (*complete)(void *, bool), void *opaque) {
  assert(p);
  assert(!done);
  done = complete;
  done_data = opaque;
  return 0;
}
static void complete_gpu(void) {
  void (*callback)(void *, bool) = done;
  void *data = done_data;
  done = NULL;
  done_data = NULL;
  assert(callback);
  callback(data, true);
}
static void packet(int fd, uint16_t type, uint64_t gen, uint64_t seq) {
  struct dpe_vk_packet p = dpe_vk_packet(type);
  p.generation = gen;
  p.sequence = seq;
  p.pts = seq * 1000000;
  assert(send(fd, &p, sizeof(p), 0) == sizeof(p));
}
static void texture(int fd, uint64_t gen, unsigned width, unsigned height) {
  int memory = memfd_create("owned-test-buffer", MFD_CLOEXEC);
  assert(memory >= 0);
  struct dpe_vk_packet p = dpe_vk_packet(DPE_VK_TEXTURE);
  p.generation = gen;
  p.width = width;
  p.height = height;
  p.nfd = 1;
  p.strides[0] = width * 4;
  char ancillary[CMSG_SPACE(sizeof(int))] = {0};
  struct iovec io = {&p, sizeof(p)};
  struct msghdr msg = {.msg_iov = &io,
                       .msg_iovlen = 1,
                       .msg_control = ancillary,
                       .msg_controllen = sizeof(ancillary)};
  struct cmsghdr *c = CMSG_FIRSTHDR(&msg);
  c->cmsg_level = SOL_SOCKET;
  c->cmsg_type = SCM_RIGHTS;
  c->cmsg_len = CMSG_LEN(sizeof(int));
  memcpy(CMSG_DATA(c), &memory, sizeof(memory));
  assert(sendmsg(fd, &msg, 0) == sizeof(p));
  close(memory);
}
static void fenced_packet(int fd, uint64_t generation, uint64_t sequence) {
  int fence = memfd_create("fake-fence", MFD_CLOEXEC);
  assert(fence >= 0);
  struct dpe_vk_packet p = dpe_vk_packet(DPE_VK_FRAME);
  p.generation = generation;
  p.sequence = sequence;
  p.flags = DPE_VK_SYNC_FD;
  p.nfd = 1;
  char ancillary[CMSG_SPACE(sizeof(int))] = {0};
  struct iovec io = {&p, sizeof(p)};
  struct msghdr msg = {.msg_iov = &io,
                       .msg_iovlen = 1,
                       .msg_control = ancillary,
                       .msg_controllen = sizeof(ancillary)};
  struct cmsghdr *c = CMSG_FIRSTHDR(&msg);
  c->cmsg_level = SOL_SOCKET;
  c->cmsg_type = SCM_RIGHTS;
  c->cmsg_len = CMSG_LEN(sizeof(int));
  memcpy(CMSG_DATA(c), &fence, sizeof(fence));
  assert(sendmsg(fd, &msg, 0) == sizeof(p));
  close(fence);
}
static struct dpe_vk_packet response(int fd, uint16_t type) {
  struct dpe_vk_packet p;
  assert(recv(fd, &p, sizeof(p), MSG_DONTWAIT) == sizeof(p));
  assert(p.type == type);
  return p;
}
static void set_format(struct source *s, unsigned width, unsigned height, unsigned fps) {
  uint8_t memory[512];
  struct spa_pod_builder b = SPA_POD_BUILDER_INIT(memory, sizeof(memory));
  const struct spa_pod *pod = spa_pod_builder_add_object(
      &b, SPA_TYPE_OBJECT_Format, SPA_PARAM_Format, SPA_FORMAT_mediaType,
      SPA_POD_Id(SPA_MEDIA_TYPE_video), SPA_FORMAT_mediaSubtype, SPA_POD_Id(SPA_MEDIA_SUBTYPE_raw),
      SPA_FORMAT_VIDEO_format, SPA_POD_Id(SPA_VIDEO_FORMAT_BGRA), SPA_FORMAT_VIDEO_size,
      SPA_POD_Rectangle(&SPA_RECTANGLE(width, height)), SPA_FORMAT_VIDEO_framerate,
      SPA_POD_Fraction(&SPA_FRACTION(0, 1)), SPA_FORMAT_VIDEO_maxFramerate,
      SPA_POD_Fraction(&SPA_FRACTION(fps, 1)), SPA_FORMAT_VIDEO_modifier, SPA_POD_Long(0));
  format(s, SPA_PARAM_Format, pod);
  assert(!s->failed);
}
int main(void) {
  int fds[2];
  assert(!socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, fds));
  struct dpe_vk_gpu gpu = {.width = 640, .height = 480};
  struct source s = {.fd = fds[0],
                     .stop_timer = &stopped_timer,
                     .gpu = &gpu,
                     .stream = (struct pw_stream *)1,
                     .unused_source_main = (struct pw_main_loop *)1,
                     .fps = 60,
                     .requested_fps = 60,
                     .hook_fps = 60,
                     .negotiated_fps = 60,
                     .format_width = 640,
                     .format_height = 480,
                     .negotiated = true,
                     .streaming = true,
                     .texture = {.generation = 1, .width = 640, .height = 480}};
  struct spa_chunk chunk = {.size = 4096, .stride = 256};
  struct spa_data data = {.type = SPA_DATA_DmaBuf, .maxsize = 4096, .chunk = &chunk};
  struct spa_meta_header header;
  struct spa_meta meta = {.type = SPA_META_Header, .size = sizeof(header), .data = &header};
  struct spa_buffer spa = {.n_metas = 1, .metas = &meta, .n_datas = 1, .datas = &data};
  struct pw_buffer buffer = {.buffer = &spa, .user_data = &allocation};
  available = &buffer;
  packet(fds[1], DPE_VK_FRAME, 1, 1);
  hook_input(&s, s.fd, SPA_IO_IN);
  process(&s);
  assert(done && s.pending_gpu);
  remove_buffer(&s, &buffer);
  assert(!s.failed && !s.pending && s.pending_gpu && !destroyed);
  // The pw_buffer may be freed by PipeWire immediately after remove_buffer.
  complete_gpu();
  assert(!s.failed && destroyed == 1 && queued == 0 && !s.pending_gpu);
  assert(response(fds[1], DPE_VK_RELEASE).sequence == 1);

  buffer.user_data = &allocation;
  packet(fds[1], DPE_VK_FRAME, 1, 2);
  hook_input(&s, s.fd, SPA_IO_IN);
  process(&s);
  assert(done);
  packet(fds[1], DPE_VK_STOP, 1, 0);
  texture(fds[1], 2, 800, 600);
  packet(fds[1], DPE_VK_FRAME, 2, 3);
  packet(fds[1], DPE_VK_STOP, 2, 0);
  hook_input(&s, s.fd, SPA_IO_IN);
  assert(!s.failed && s.deferred_texture && stopped_timer_armed);
  assert(response(fds[1], DPE_VK_RELEASE).generation == 2);
  texture(fds[1], 3, 1024, 768);
  packet(fds[1], DPE_VK_FRAME, 3, 4);
  hook_input(&s, s.fd, SPA_IO_IN);
  assert(!s.failed);
  assert(response(fds[1], DPE_VK_RELEASE).generation == 3);
  assert(!stopped_timer_armed && stopped_timer_cancelled >= 2);
  stopped_timeout(&s, 1); // A stale callback after replacement is harmless.
  assert(!s.failed);
  complete_gpu();
  assert(!s.failed && imports == 1 && s.texture.generation == 3 && queued_size == 0);
  assert(s.negotiated && s.format_width == 640); // current old Format survives EnumFormat update
  // Allocation follows the old negotiated size until the new Format arrives.
  struct spa_data alloc_data = {.type = 1u << SPA_DATA_DmaBuf, .chunk = &chunk};
  struct spa_buffer alloc_spa = {.n_datas = 1, .datas = &alloc_data};
  struct pw_buffer alloc_buffer = {.buffer = &alloc_spa};
  add_buffer(&s, &alloc_buffer);
  assert(!s.failed && allocated_width == 640 && allocated_height == 480);
  set_format(&s, 1024, 768, 60);
  packet(fds[1], DPE_VK_FRAME, 3, 5);
  hook_input(&s, s.fd, SPA_IO_IN);
  process(&s);
  complete_gpu();
  assert(!s.failed && queued_size == 4096 && queued_sequence == 5 && s.delivered == 1);
  assert(response(fds[1], DPE_VK_RELEASE).sequence == 5);
  // A rate decrease applies immediately; a raise waits for accepted maxFramerate.
  s.requested_fps = 30;
  update_rate(&s);
  assert(s.negotiated);
  assert(response(fds[1], DPE_VK_CONTROL).fps == 30);
  set_format(&s, 1024, 768, 30);
  s.requested_fps = 60;
  update_rate(&s);
  assert(s.hook_fps == 30 && s.negotiated);
  set_format(&s, 1024, 768, 60);
  assert(response(fds[1], DPE_VK_CONTROL).fps == 60);
  format(&s, SPA_PARAM_Format, NULL);
  assert(!s.negotiated && !s.failed);
  packet(fds[1], DPE_VK_FRAME, 3, 6);
  hook_input(&s, s.fd, SPA_IO_IN);
  assert(response(fds[1], DPE_VK_RELEASE).sequence == 6);
  set_format(&s, 1024, 768, 60);
  assert(s.negotiated && !s.failed);
  // Exported GPU readiness is asynchronous: a game present alone cannot start
  // sampling, and no second game present is needed once the fence signals.
  acquire_status = 0;
  fenced_packet(fds[1], 3, 7);
  hook_input(&s, s.fd, SPA_IO_IN);
  assert(s.acquire && acquire_callback);
  process(&s);
  assert(!done);
  acquire_status = 1;
  acquire_callback(acquire_data, acquire_source.fd, SPA_IO_IN);
  assert(!s.acquire && !acquire_callback);
  process(&s);
  assert(done);
  complete_gpu();
  assert(response(fds[1], DPE_VK_RELEASE).sequence == 7 && s.fenced_frames == 1);
  acquire_status = 0;
  fenced_packet(fds[1], 3, 8);
  hook_input(&s, s.fd, SPA_IO_IN);
  assert(s.acquire);
  packet(fds[1], DPE_VK_STOP, 3, 0);
  hook_input(&s, s.fd, SPA_IO_IN);
  assert(!s.acquire && !acquire_callback && !s.failed);
  int completion = memfd_create("errored-fence", MFD_CLOEXEC);
  assert(completion >= 0);
  acquire_status = -EIO;
  assert(!dpe_vk_fence_signaled(completion));
  acquire_status = 0;
  assert(!dpe_vk_fence_signaled(completion));
  acquire_status = 1;
  assert(dpe_vk_fence_signaled(completion));
  close(completion);
  assert(stopped_timer_armed && s.source_stopped);
  stopped_timeout(&s, 1);
  assert(s.failed); // Permanent surface loss ends capture after its grace period.
  close(fds[0]);
  close(fds[1]);
  puts("game producer: retired GPU allocation, repeated generations, negotiated dimensions, "
       "zero-size reuse and async rate updates passed");
  return 0;
}
