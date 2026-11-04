// SPDX-License-Identifier: MIT
// Execute the real relay and producer callbacks with synthetic DRM timelines.
// No GPU, compositor, or PipeWire server; pixel storage is never accessed.
#define _GNU_SOURCE
#include <assert.h>
#include <drm_fourcc.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/sync_file.h>
#include <pipewire/pipewire.h>
#include <stdarg.h>
#include <stdlib.h>
#include <sys/eventfd.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <unistd.h>
#include <xf86drm.h>

struct fixture_watch {
  struct spa_source source;
  void (*callback)(void *, int, uint32_t);
  void *opaque;
};
static struct fixture_watch *watch;
static uint32_t next_handle, timeline_handle, acquire_handle;
static bool handles[128];
static unsigned live_handles;
static uint64_t signaled_point, notification_point;
static unsigned destroyed, queued, quit_count;
static int notification_fd, fail_wait, fail_import, fail_transfer, imported_fd = -1;
static bool probing, fail_cap, fail_event;
static uint64_t transferred_point;
static struct pw_buffer *available;
static void *stream_source;
static bool defer_trigger;
static void process(void *);

static struct spa_source *fixture_add_io(struct pw_loop *loop, int fd, uint32_t mask, bool close_fd,
                                         void (*callback)(void *, int, uint32_t), void *opaque) {
  (void)loop;
  (void)mask;
  assert(!close_fd && !watch);
  watch = calloc(1, sizeof(*watch));
  assert(watch);
  watch->source.fd = fd;
  watch->callback = callback;
  watch->opaque = opaque;
  return &watch->source;
}
static void fixture_destroy_io(struct pw_loop *loop, struct spa_source *source) {
  (void)loop;
  assert(watch && source == &watch->source);
  free(watch);
  watch = NULL;
}
static int fixture_create(int fd, uint32_t flags, uint32_t *handle) {
  (void)fd;
  assert((flags == 0 || (probing && flags == DRM_SYNCOBJ_CREATE_SIGNALED)) &&
         next_handle + 1 < 128);
  *handle = ++next_handle;
  if (!live_handles) {
    timeline_handle = *handle;
    signaled_point = notification_point = transferred_point = 0;
  }
  handles[*handle] = true;
  live_handles++;
  return 0;
}
static int fixture_destroy(int fd, uint32_t handle) {
  (void)fd;
  assert(handle < 128 && handles[handle]);
  handles[handle] = false;
  live_handles--;
  if (handle == timeline_handle) {
    timeline_handle = 0;
    destroyed++;
  }
  if (handle == acquire_handle) {
    acquire_handle = 0;
    imported_fd = -1;
  }
  return 0;
}
static int fixture_export(int fd, uint32_t handle, int *result) {
  (void)fd;
  assert(handle < 128 && handles[handle] && handle == timeline_handle);
  *result = memfd_create("fake-relay-timeline", MFD_CLOEXEC);
  assert(*result >= 0);
  return 0;
}
static int fixture_signal(int fd, const uint32_t *handles, uint64_t *points, uint32_t count) {
  (void)fd;
  assert(count == 1 && handles[0] == timeline_handle && points[0] > signaled_point);
  signaled_point = points[0];
  return 0;
}
static int fixture_wait(int fd, uint32_t *handles, uint64_t *points, unsigned count,
                        int64_t timeout, unsigned flags, uint32_t *first) {
  (void)fd;
  (void)first;
  assert(count == 1 && handles[0] == timeline_handle && timeout == 0);
  assert(flags == DRM_SYNCOBJ_WAIT_FLAGS_WAIT_ALL);
  return fail_wait || signaled_point < points[0] ? -ETIME : 0;
}
static int fixture_ioctl(int fd, unsigned long request, void *opaque) {
  (void)fd;
  struct fixture_event {
    uint32_t handle, flags;
    uint64_t point;
    int32_t fd;
    uint32_t pad;
  } *event = opaque;
  assert(request == DRM_IOWR(0xCF, struct fixture_event));
  assert(event->handle == timeline_handle && event->flags == 0);
  assert(event->point > signaled_point);
  if (fail_event)
    return -ENOTTY;
  assert(probing || (watch && event->fd == watch->source.fd));
  notification_fd = event->fd;
  notification_point = event->point;
  return 0;
}
static int fixture_import(int fd, uint32_t handle, int sync_fd) {
  (void)fd;
  assert(handle < 128 && handles[handle] && handle != timeline_handle);
  assert(!acquire_handle || handle == acquire_handle); // Reuse one binary object.
  assert(fcntl(sync_fd, F_GETFD) >= 0);
  acquire_handle = handle;
  imported_fd = sync_fd;
  if (fail_import)
    return -EIO;
  return 0;
}
static int fixture_transfer(int fd, uint32_t dst, uint64_t dst_point, uint32_t src,
                            uint64_t src_point, uint32_t flags) {
  (void)fd;
  assert(dst == timeline_handle && src == acquire_handle && !src_point && !flags);
  assert(dst_point + (probing ? 0 : 1) == notification_point && imported_fd >= 0);
  if (fail_transfer)
    return -EIO;
  transferred_point = dst_point;
  if (probing) {
    signaled_point = dst_point;
    uint64_t one = 1;
    assert(write(notification_fd, &one, sizeof(one)) == sizeof(one));
  }
  return 0;
}
static int fixture_cap(int fd, uint64_t capability, uint64_t *value) {
  (void)fd;
  assert(probing && capability == DRM_CAP_SYNCOBJ_TIMELINE);
  *value = !fail_cap;
  return 0;
}
static int fixture_export_sync_file(int fd, uint32_t handle, int *sync_fd) {
  (void)fd;
  assert(probing && handle < 128 && handles[handle] && handle != timeline_handle);
  *sync_fd = memfd_create("fake-probe-sync-file", MFD_CLOEXEC);
  assert(*sync_fd >= 0);
  int status = 1;
  assert(pwrite(*sync_fd, &status, sizeof(status), 0) == sizeof(status));
  return 0;
}
static int fixture_sync_ioctl(int fd, unsigned long request, ...) {
  assert(request == SYNC_IOC_FILE_INFO);
  va_list args;
  va_start(args, request);
  struct sync_file_info *info = va_arg(args, struct sync_file_info *);
  va_end(args);
  int status;
  if (pread(fd, &status, sizeof(status), 0) != sizeof(status))
    return -1;
  info->status = status;
  return 0;
}
static struct pw_loop *fixture_loop(struct pw_main_loop *loop) {
  (void)loop;
  return (struct pw_loop *)1;
}
static int fixture_quit(struct pw_main_loop *loop) {
  (void)loop;
  quit_count++;
  return 0;
}
static struct pw_buffer *fixture_dequeue(struct pw_stream *stream) {
  (void)stream;
  return available;
}
static int fixture_queue(struct pw_stream *stream, struct pw_buffer *buffer) {
  (void)stream;
  assert(buffer == available);
  queued++;
  return 0;
}
static int fixture_trigger(struct pw_stream *stream) {
  (void)stream;
  assert(stream_source);
  if (!defer_trigger)
    process(stream_source);
  return 0;
}

#undef pw_loop_add_io
#undef pw_loop_destroy_source
#define pw_loop_add_io fixture_add_io
#define pw_loop_destroy_source fixture_destroy_io
#define drmSyncobjCreate fixture_create
#define drmSyncobjDestroy fixture_destroy
#define drmSyncobjHandleToFD fixture_export
#define drmSyncobjTimelineSignal fixture_signal
#define drmSyncobjTimelineWait fixture_wait
#define drmSyncobjImportSyncFile fixture_import
#define drmSyncobjTransfer fixture_transfer
#define drmSyncobjExportSyncFile fixture_export_sync_file
#define drmGetCap fixture_cap
#define drmIoctl fixture_ioctl
#define ioctl fixture_sync_ioctl
#include "../../integration/video/vkcapture-relay.c"
#define pw_main_loop_get_loop fixture_loop
#define pw_main_loop_quit fixture_quit
#define pw_stream_dequeue_buffer fixture_dequeue
#define pw_stream_queue_buffer fixture_queue
#define pw_stream_trigger_process fixture_trigger
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

const uint8_t *dpe_vk_gpu_uuid(struct dpe_vk_gpu *gpu) {
  (void)gpu;
  static const uint8_t uuid[16] = {0};
  return uuid;
}
int dpe_vk_gpu_fd(struct dpe_vk_gpu *gpu) {
  (void)gpu;
  return 100;
}
int dpe_vk_gpu_import(struct dpe_vk_gpu *gpu, const struct dpe_vk_packet *packet,
                      const int fds[4]) {
  (void)gpu;
  (void)packet;
  (void)fds;
  abort();
}
struct dpe_vk_buffer *dpe_vk_buffer_create(struct dpe_vk_gpu *gpu, struct spa_data *data,
                                           uint32_t width, uint32_t height) {
  (void)gpu;
  (void)data;
  (void)width;
  (void)height;
  abort();
}
void dpe_vk_buffer_destroy(struct dpe_vk_buffer *buffer) {
  (void)buffer;
  abort();
}
int dpe_vk_buffer_copy(struct dpe_vk_buffer *buffer, void (*done)(void *, bool), void *opaque) {
  (void)buffer;
  (void)done;
  (void)opaque;
  abort();
}

struct fixture_buffer {
  struct pw_buffer pw;
  struct spa_buffer spa;
  struct spa_data data[3];
  struct spa_chunk chunks[3];
  struct spa_meta meta[2];
  struct spa_meta_header header;
  struct dpe_relay_timeline timeline;
};
static struct fixture_buffer *make_buffer(struct source *s) {
  struct fixture_buffer *b = calloc(1, sizeof(*b));
  assert(b);
  b->pw.buffer = &b->spa;
  b->spa = (struct spa_buffer){.n_datas = 3, .datas = b->data, .n_metas = 2, .metas = b->meta};
  for (unsigned i = 0; i < 3; i++)
    b->data[i] = (struct spa_data){.type = 1u << SPA_DATA_DmaBuf, .chunk = &b->chunks[i]};
  b->meta[0] =
      (struct spa_meta){.type = DPE_RELAY_META, .size = sizeof(b->timeline), .data = &b->timeline};
  b->meta[1] =
      (struct spa_meta){.type = SPA_META_Header, .size = sizeof(b->header), .data = &b->header};
  add_buffer(s, &b->pw);
  assert(!s->failed && b->pw.user_data);
  assert(b->data[0].fd != s->texture_fds[0]);
  assert(fcntl(b->data[0].fd, F_GETFD) & FD_CLOEXEC);
  assert(b->data[0].type == SPA_DATA_DmaBuf && b->chunks[0].stride == 2560);
  assert(b->data[1].type == DPE_RELAY_DATA && b->data[2].fd == b->data[1].fd);
  available = &b->pw;
  return b;
}
static void no_ack(int fd) {
  struct dpe_vk_packet packet;
  errno = 0;
  assert(recv(fd, &packet, sizeof(packet), MSG_DONTWAIT) < 0 && errno == EAGAIN);
}
static void expect_ack(int fd, uint64_t sequence) {
  struct dpe_vk_packet packet;
  assert(recv(fd, &packet, sizeof(packet), MSG_DONTWAIT) == sizeof(packet));
  assert(packet.type == DPE_VK_RELEASE && packet.generation == 1 && packet.sequence == sequence);
  no_ack(fd);
}
static void submit(struct source *s, uint64_t sequence) {
  s->have_frame = true;
  s->frame = (struct dpe_vk_packet){.generation = 1, .sequence = sequence, .pts = sequence * 1000};
  process(s);
  assert(!s->failed && s->pending_relay && watch);
  assert(signaled_point + 1 == notification_point);
}
static void complete_release(bool success) {
  assert(watch);
  struct fixture_watch current = *watch;
  signaled_point = notification_point;
  fail_wait = !success;
  uint64_t one = 1;
  assert(write(notification_fd, &one, sizeof(one)) == sizeof(one));
  current.callback(current.opaque, current.source.fd, SPA_IO_IN);
  fail_wait = 0;
}
static void set_fence(int fd, int status) {
  assert(pwrite(fd, &status, sizeof(status), 0) == sizeof(status));
}
static int send_fenced(struct source *s, int peer, uint64_t sequence, int status) {
  int fd = memfd_create("fake-sync-file", MFD_CLOEXEC);
  assert(fd >= 0);
  set_fence(fd, status);
  struct dpe_vk_packet p = dpe_vk_packet(DPE_VK_FRAME);
  p.generation = 1;
  p.sequence = sequence;
  p.flags = DPE_VK_SYNC_FD;
  p.nfd = 1;
  struct iovec iov = {.iov_base = &p, .iov_len = sizeof(p)};
  char control[CMSG_SPACE(sizeof(fd))] = {0};
  struct msghdr message = {
      .msg_iov = &iov, .msg_iovlen = 1, .msg_control = control, .msg_controllen = sizeof(control)};
  struct cmsghdr *cmsg = CMSG_FIRSTHDR(&message);
  cmsg->cmsg_level = SOL_SOCKET;
  cmsg->cmsg_type = SCM_RIGHTS;
  cmsg->cmsg_len = CMSG_LEN(sizeof(fd));
  memcpy(CMSG_DATA(cmsg), &fd, sizeof(fd));
  assert(sendmsg(peer, &message, 0) == sizeof(p));
  hook_input(s, s->fd, SPA_IO_IN);
  return fd;
}
static void closed_fd(int fd) {
  errno = 0;
  assert(fcntl(fd, F_GETFD) == -1 && errno == EBADF);
}

int main(void) {
  int sockets[2];
  assert(!socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, sockets));
  int texture = memfd_create("fake-relay-texture", MFD_CLOEXEC);
  assert(texture >= 0);
  struct source s = {.fd = sockets[0],
                     .stop_timer = &stopped_timer,
                     .relay = true,
                     .unused_source_main = (struct pw_main_loop *)1,
                     .stream = (struct pw_stream *)1,
                     .gpu = (struct dpe_vk_gpu *)1,
                     .fps = 60,
                     .requested_fps = 60,
                     .negotiated_fps = 60,
                     .hook_fps = 60,
                     .negotiated = true,
                     .streaming = true,
                     .format_width = 640,
                     .format_height = 480,
                     .texture_fds = {texture, -1, -1, -1},
                     .texture = {.generation = 1,
                                 .width = 640,
                                 .height = 480,
                                 .nfd = 1,
                                 .format = DRM_FORMAT_ARGB8888,
                                 .strides = {2560}}};
  stream_source = &s;
  // A signaled fence probes timeline/import/transfer/Eventfd support without
  // opening GPU work. Older kernels and partial driver support fail closed.
  probing = true;
  assert(dpe_vk_relay_supported(100));
  assert(!live_handles && !watch);
  closed_fd(notification_fd);
  fail_cap = true;
  assert(!dpe_vk_relay_supported(100) && !live_handles);
  s.auto_relay = true;
  preflight_relay(&s);
  assert(!s.relay && !s.failed && s.relay_fallback == RELAY_SYNC_UNAVAILABLE);
  assert(!strcmp(handoff_fallback(&s), "synchronization-unavailable"));
  s.relay = true;
  s.auto_relay = false;
  preflight_relay(&s);
  assert(s.failed && !strcmp(s.error_code, "unsupported-buffer"));
  s.failed = 0;
  s.error_code = NULL;
  s.relay_fallback = RELAY_NO_FALLBACK;
  fail_cap = false;
  fail_event = true;
  assert(!dpe_vk_relay_supported(100) && !live_handles);
  fail_event = false;
  fail_import = 1;
  assert(!dpe_vk_relay_supported(100) && !live_handles);
  fail_import = 0;
  fail_transfer = 1;
  assert(!dpe_vk_relay_supported(100) && !live_handles);
  fail_transfer = 0;
  probing = false;
  destroyed = quit_count = 0;
  struct fixture_buffer *buffer = make_buffer(&s);
  submit(&s, 1);
  assert(queued == 1 && buffer->header.seq == 1);
  no_ack(sockets[1]);
  // PipeWire can make the same buffer dequeuable before GPU completion.
  // A second process callback must not reuse the texture or signal an ACK.
  process(&s);
  assert(queued == 1);
  no_ack(sockets[1]);
  complete_release(true);
  assert(!s.pending_relay && s.relay_releases == 1 && !s.have_frame);
  expect_ack(sockets[1], 1);
  submit(&s, 2);
  no_ack(sockets[1]);
  remove_buffer(&s, &buffer->pw);
  free(buffer); // PipeWire storage and metadata cease existing immediately.
  available = NULL;
  assert(s.pending_relay && !s.pending && destroyed == 0 && watch);
  complete_release(true); // Must not touch any freed PipeWire memory.
  assert(!s.pending_relay && destroyed == 1 && !watch);
  expect_ack(sockets[1], 2);

  buffer = make_buffer(&s);
  submit(&s, 3);
  s.source_stopped = true;
  complete_release(true);
  no_ack(sockets[1]); // Retired hook generations must never receive a late ACK.
  remove_buffer(&s, &buffer->pw);
  free(buffer);
  assert(destroyed == 2 && !watch);
  s.source_stopped = false;

  buffer = make_buffer(&s);
  submit(&s, 4);
  complete_release(false);
  assert(s.failed && quit_count == 1);
  no_ack(sockets[1]); // Eventfd readiness is insufficient if DRM wait fails.
  remove_buffer(&s, &buffer->pw);
  free(buffer);
  assert(destroyed == 3 && !watch);
  s.failed = 0;
  s.have_frame = false;

  buffer = make_buffer(&s);
  // A valid but unsignaled hook fence is queued immediately. Only the native
  // consumer waits for it; no per-frame helper IO watch or CPU wait is added.
  unsigned before = queued;
  int fence = send_fenced(&s, sockets[1], 5, 0);
  assert(!s.failed && s.pending_relay && !s.acquire && !s.have_relay_acquire);
  assert(queued == before + 1 && signaled_point == 0 && transferred_point == 1);
  int received_fence = imported_fd;
  assert(received_fence != fence && fcntl(received_fence, F_GETFD) & FD_CLOEXEC);
  no_ack(sockets[1]);
  process(&s);
  assert(queued == before + 1);
  set_fence(fence, 1);
  no_ack(sockets[1]); // Acquire alone does not release the lease.
  complete_release(true);
  expect_ack(sockets[1], 5);
  closed_fd(received_fence);
  close(fence);

  // The same binary object can import a new fence without replacing or
  // prematurely signaling the previously used timeline release point.
  fence = send_fenced(&s, sockets[1], 6, 0);
  assert(transferred_point == 3 && signaled_point == 2 && notification_point == 4);
  received_fence = imported_fd;
  set_fence(fence, -EIO);
  complete_release(true);
  assert(s.failed && quit_count == 2);
  no_ack(sockets[1]); // GPU error may wake a semaphore, but must not permit reuse.
  closed_fd(received_fence);
  close(fence);
  remove_buffer(&s, &buffer->pw);
  free(buffer);
  assert(destroyed == 4 && !watch);
  s.failed = 0;
  s.have_frame = false;

  // Reject a fence that failed before arrival without queueing it.
  before = queued;
  fence = send_fenced(&s, sockets[1], 7, -EIO);
  assert(s.failed && !s.pending_relay && queued == before && quit_count == 3);
  no_ack(sockets[1]);
  close(fence);
  s.failed = 0;

  // Unsubmitted fence ownership survives a deferred process callback, then
  // STOP cancels it without an ACK or leaking the received descriptor.
  defer_trigger = true;
  fence = send_fenced(&s, sockets[1], 8, 0);
  assert(s.have_relay_acquire && !s.pending_relay && !s.acquire);
  received_fence = s.relay_acquire_fd;
  struct dpe_vk_packet stop = dpe_vk_packet(DPE_VK_STOP);
  stop.generation = 1;
  assert(send(sockets[1], &stop, sizeof(stop), 0) == sizeof(stop));
  hook_input(&s, s.fd, SPA_IO_IN);
  assert(!s.have_relay_acquire && !s.have_frame && !s.failed && stopped_timer_armed);
  closed_fd(received_fence);
  no_ack(sockets[1]);
  close(fence);
  defer_trigger = false;
  s.source_stopped = false;

  // Import/transfer failure retains the lease until teardown and closes the
  // received fence. No incomplete buffer is queued or acknowledged.
  for (unsigned failure = 0; failure < 2; failure++) {
    buffer = make_buffer(&s);
    fail_import = failure == 0;
    fail_transfer = failure == 1;
    before = queued;
    fence = send_fenced(&s, sockets[1], 9 + failure, 0);
    assert(s.failed && s.pending_relay && !s.have_relay_acquire && queued == before);
    closed_fd(imported_fd);
    no_ack(sockets[1]);
    close(fence);
    remove_buffer(&s, &buffer->pw);
    free(buffer);
    dpe_vk_relay_destroy(s.pending_relay);
    s.pending_relay = NULL;
    s.failed = 0;
    s.have_frame = false;
    fail_import = fail_transfer = 0;
    assert(!watch && !live_handles);
  }

  buffer = make_buffer(&s);
  fence = send_fenced(&s, sockets[1], 11, 0);
  received_fence = imported_fd;
  close(s.fd); // Production shutdown retires the hook texture first.
  struct dpe_vk_packet packet;
  assert(recv(sockets[1], &packet, sizeof(packet), MSG_DONTWAIT) == 0);
  remove_buffer(&s, &buffer->pw);
  free(buffer);
  assert(s.pending_relay && destroyed == 6);
  dpe_vk_relay_destroy(s.pending_relay);
  s.pending_relay = NULL;
  assert(destroyed == 7 && !watch && !live_handles);
  closed_fd(received_fence);
  close(fence);
  close(sockets[1]);
  close(texture);
  puts("game relay: GPU release gates ACK, early PW return, retired metadata, stale generation, "
       "forwarded unsignaled acquire, failed fence, import/transfer failure and shutdown "
       "cancellation passed");
  return 0;
}
