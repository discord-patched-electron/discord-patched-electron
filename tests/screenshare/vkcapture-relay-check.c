// SPDX-License-Identifier: MIT
// Execute the Rust relay ABI with synthetic DRM timelines and owned fences.
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
static int fixture_drm;
static struct pw_loop fixture_pw_loop;
static uint32_t next_handle, timeline_handle, acquire_handle;
static bool handles[128];
static unsigned live_handles;
static uint64_t signaled_point, notification_point;
static unsigned destroyed;
static int notification_fd, fail_wait, fail_import, fail_transfer, imported_fd = -1;
static bool probing, fail_cap, fail_event;
static uint64_t transferred_point;
static struct spa_source *fixture_add_io(void *loop, int fd, uint32_t mask, bool close_fd,
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
static void fixture_destroy_io(void *loop, struct spa_source *source) {
  (void)loop;
  assert(watch && source == &watch->source);
  free(watch);
  watch = NULL;
}
int __wrap_drmSyncobjCreate(int fd, uint32_t flags, uint32_t *handle) {
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
int __wrap_drmSyncobjDestroy(int fd, uint32_t handle) {
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
int __wrap_drmSyncobjHandleToFD(int fd, uint32_t handle, int *result) {
  (void)fd;
  assert(handle < 128 && handles[handle] && handle == timeline_handle);
  *result = memfd_create("fake-relay-timeline", MFD_CLOEXEC);
  assert(*result >= 0);
  return 0;
}
int __wrap_drmSyncobjTimelineSignal(int fd, const uint32_t *handles, uint64_t *points,
                                    uint32_t count) {
  (void)fd;
  assert(count == 1 && handles[0] == timeline_handle && points[0] > signaled_point);
  signaled_point = points[0];
  return 0;
}
int __wrap_drmSyncobjTimelineWait(int fd, uint32_t *handles, uint64_t *points, unsigned count,
                                  int64_t timeout, unsigned flags, uint32_t *first) {
  (void)fd;
  (void)first;
  assert(count == 1 && handles[0] == timeline_handle && timeout == 0);
  assert(flags == DRM_SYNCOBJ_WAIT_FLAGS_WAIT_ALL);
  return fail_wait || signaled_point < points[0] ? -ETIME : 0;
}
int __wrap_drmIoctl(int fd, unsigned long request, void *opaque) {
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
int __wrap_drmSyncobjImportSyncFile(int fd, uint32_t handle, int sync_fd) {
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
int __wrap_drmSyncobjTransfer(int fd, uint32_t dst, uint64_t dst_point, uint32_t src,
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
int __wrap_drmGetCap(int fd, uint64_t capability, uint64_t *value) {
  (void)fd;
  assert(probing && capability == DRM_CAP_SYNCOBJ_TIMELINE);
  *value = !fail_cap;
  return 0;
}
int __wrap_drmSyncobjExportSyncFile(int fd, uint32_t handle, int *sync_fd) {
  (void)fd;
  assert(probing && handle < 128 && handles[handle] && handle != timeline_handle);
  *sync_fd = memfd_create("fake-probe-sync-file", MFD_CLOEXEC);
  assert(*sync_fd >= 0);
  int status = 1;
  assert(pwrite(*sync_fd, &status, sizeof(status), 0) == sizeof(status));
  return 0;
}
int __wrap_ioctl(int fd, unsigned long request, ...) {
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
#include "../../integration/video/vkcapture-relay.h"

struct fixture_buffer {
  struct spa_buffer spa;
  struct spa_data data[3];
  struct spa_chunk chunks[3];
  struct spa_meta meta;
  struct dpe_relay_timeline timeline;
};
static struct dpe_vk_relay *make_buffer(struct fixture_buffer **output, int texture) {
  struct fixture_buffer *b = calloc(1, sizeof(*b));
  assert(b);
  b->spa = (struct spa_buffer){.n_datas = 3, .datas = b->data, .n_metas = 1, .metas = &b->meta};
  for (unsigned i = 0; i < 3; i++)
    b->data[i] = (struct spa_data){.type = 1u << SPA_DATA_DmaBuf, .chunk = &b->chunks[i]};
  b->meta =
      (struct spa_meta){.type = DPE_RELAY_META, .size = sizeof(b->timeline), .data = &b->timeline};
  struct dpe_vk_packet packet = {.generation = 1,
                                 .width = 640,
                                 .height = 480,
                                 .nfd = 1,
                                 .format = DRM_FORMAT_ARGB8888,
                                 .strides = {2560}};
  int descriptors[4] = {texture, -1, -1, -1};
  struct dpe_vk_relay *relay =
      dpe_vk_relay_create(fixture_drm, &fixture_pw_loop, &b->spa, &packet, descriptors);
  assert(relay && dpe_vk_relay_generation(relay) == 1);
  assert(b->data[0].fd != texture && fcntl(b->data[0].fd, F_GETFD) & FD_CLOEXEC);
  assert(b->data[0].type == SPA_DATA_DmaBuf && b->chunks[0].stride == 2560);
  assert(b->data[1].type == DPE_RELAY_DATA && b->data[2].fd == b->data[1].fd);
  *output = b;
  return relay;
}
struct completion {
  unsigned calls;
  bool success;
};
static void completed(void *opaque, bool success) {
  struct completion *value = opaque;
  value->calls++;
  value->success = success;
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
static int fence(int status) {
  int fd = memfd_create("fake-sync-file", MFD_CLOEXEC);
  assert(fd >= 0 && pwrite(fd, &status, sizeof(status), 0) == sizeof(status));
  return fd;
}
static void closed_fd(int fd) {
  errno = 0;
  assert(fcntl(fd, F_GETFD) == -1 && errno == EBADF);
}
int main(void) {
  const struct spa_loop_utils_methods methods = {
      .version = 0, .add_io = fixture_add_io, .destroy_source = fixture_destroy_io};
  struct spa_loop_utils utils = {.iface = {.cb = {.funcs = &methods, .data = NULL}}};
  fixture_pw_loop.utils = &utils;
  fixture_drm = memfd_create("fake-drm-device", MFD_CLOEXEC);
  int texture = memfd_create("fake-relay-texture", MFD_CLOEXEC);
  assert(fixture_drm >= 0 && texture >= 0);
  probing = true;
  assert(dpe_vk_relay_supported(fixture_drm) && !live_handles && !watch);
  closed_fd(notification_fd);
  fail_cap = true;
  assert(!dpe_vk_relay_supported(fixture_drm) && !live_handles);
  fail_cap = false;
  fail_event = true;
  assert(!dpe_vk_relay_supported(fixture_drm) && !live_handles);
  fail_event = false;
  fail_import = true;
  assert(!dpe_vk_relay_supported(fixture_drm) && !live_handles);
  fail_import = false;
  fail_transfer = true;
  assert(!dpe_vk_relay_supported(fixture_drm) && !live_handles);
  fail_transfer = false;
  probing = false;
  destroyed = 0;

  struct fixture_buffer *buffer;
  struct dpe_vk_relay *relay = make_buffer(&buffer, texture);
  struct completion result = {0};
  assert(!dpe_vk_relay_submit(relay, -1, completed, &result));
  assert(buffer->timeline.acquire_point == 1 && buffer->timeline.release_point == 2);
  assert(!result.calls && signaled_point + 1 == notification_point);
  int duplicate = fence(1);
  assert(dpe_vk_relay_submit(relay, duplicate, completed, &result) < 0);
  closed_fd(duplicate);
  assert(!result.calls);
  complete_release(true);
  assert(result.calls == 1 && result.success);
  assert(!dpe_vk_relay_submit(relay, -1, completed, &result));
  free(buffer); // Completion must never access retired native metadata.
  complete_release(true);
  assert(result.calls == 2 && result.success);
  dpe_vk_relay_destroy(relay);
  assert(!watch && !live_handles && destroyed == 1);

  for (unsigned failure = 0; failure < 4; failure++) {
    relay = make_buffer(&buffer, texture);
    result = (struct completion){0};
    int fd = fence(0);
    assert(!dpe_vk_relay_submit(relay, fd, completed, &result));
    assert(transferred_point == 1 && notification_point == 2 && !result.calls);
    assert(fcntl(fd, F_GETFD) >= 0); // Relay owns the transferred acquire descriptor.
    if (failure == 0) {
      int status = 1;
      assert(pwrite(fd, &status, sizeof(status), 0) == sizeof(status));
      complete_release(true);
      assert(result.calls == 1 && result.success);
    } else if (failure == 1) {
      int status = -EIO;
      assert(pwrite(fd, &status, sizeof(status), 0) == sizeof(status));
      complete_release(true);
      assert(result.calls == 1 && !result.success);
    } else if (failure == 2) {
      complete_release(false);
      assert(result.calls == 1 && !result.success);
    } else {
      assert(!result.calls); // Shutdown cancels its watch without a completion.
    }
    free(buffer);
    dpe_vk_relay_destroy(relay);
    closed_fd(fd);
    assert(!watch && !live_handles);
  }
  for (unsigned failure = 0; failure < 3; failure++) {
    relay = make_buffer(&buffer, texture);
    result = (struct completion){0};
    fail_import = failure == 0;
    fail_transfer = failure == 1;
    fail_event = failure == 2;
    int fd = fence(0);
    assert(dpe_vk_relay_submit(relay, fd, completed, &result) < 0);
    closed_fd(fd);
    assert(!result.calls);
    fail_import = fail_transfer = fail_event = 0;
    free(buffer);
    dpe_vk_relay_destroy(relay);
    assert(!watch && !live_handles);
  }
  assert(fcntl(texture, F_GETFD) >= 0 && fcntl(fixture_drm, F_GETFD) >= 0);
  close(texture);
  close(fixture_drm);
  puts("GPU release gates completion; retired metadata, failed fences and cancellation passed");
  return 0;
}
