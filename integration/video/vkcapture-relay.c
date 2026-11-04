// SPDX-License-Identifier: MIT
// Relay immutable GPU storage. Never map pixels, render, or wait on the game.
#define _GNU_SOURCE
#include "vkcapture-relay.h"
#include "vkcapture-sync.h"
#include <drm_fourcc.h>
#include <errno.h>
#include <fcntl.h>
#include <spa/param/video/raw.h>
#include <stdlib.h>
#include <sys/eventfd.h>
#include <unistd.h>
#include <xf86drm.h>

struct dpe_vk_relay {
  int drm, timeline_fd, event_fd, acquire_fd, planes[4];
  uint32_t timeline, acquire;
  uint64_t generation, point;
  struct pw_loop *loop;
  struct spa_source *watch;
  struct dpe_relay_timeline *meta;
  void (*done)(void *, bool);
  void *opaque;
};
_Static_assert(sizeof(struct dpe_relay_timeline) == 24, "SyncTimeline ABI");

static int notify_point(int drm, uint32_t handle, uint64_t point, int fd) {
  struct {
    uint32_t handle, flags;
    uint64_t point;
    int32_t fd;
    uint32_t pad;
  } event = {.handle = handle, .point = point, .fd = fd};
  // Stable Linux 6.6 UAPI, without requiring a new libdrm runtime symbol.
  return drmIoctl(drm, DRM_IOWR(0xCF, __typeof__(event)), &event);
}
bool dpe_vk_relay_supported(int drm) {
  uint64_t capability = 0, point = 1, notification = 0;
  uint32_t timeline = 0, binary = 0;
  int sync_fd = -1, event_fd = -1;
  bool supported = false;
  if (drmGetCap(drm, DRM_CAP_SYNCOBJ_TIMELINE, &capability) || !capability ||
      drmSyncobjCreate(drm, 0, &timeline) ||
      drmSyncobjCreate(drm, DRM_SYNCOBJ_CREATE_SIGNALED, &binary) ||
      drmSyncobjExportSyncFile(drm, binary, &sync_fd) ||
      drmSyncobjImportSyncFile(drm, binary, sync_fd))
    goto done;
  event_fd = eventfd(0, EFD_NONBLOCK | EFD_CLOEXEC);
  // Exercise the same future-point registration and fence transfer as submit.
  // The fence is already signaled: no rendering or blocking wait is needed.
  if (event_fd < 0 || notify_point(drm, timeline, point, event_fd) ||
      drmSyncobjTransfer(drm, timeline, point, binary, 0, 0) ||
      drmSyncobjTimelineWait(drm, &timeline, &point, 1, 0, DRM_SYNCOBJ_WAIT_FLAGS_WAIT_ALL, NULL) ||
      read(event_fd, &notification, sizeof(notification)) != sizeof(notification) || !notification)
    goto done;
  supported = true;
done:
  if (event_fd >= 0)
    close(event_fd);
  if (sync_fd >= 0)
    close(sync_fd);
  if (binary)
    drmSyncobjDestroy(drm, binary);
  if (timeline)
    drmSyncobjDestroy(drm, timeline);
  return supported;
}

uint32_t dpe_vk_relay_format(uint32_t format) {
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
uint64_t dpe_vk_relay_generation(const struct dpe_vk_relay *r) { return r->generation; }
void dpe_vk_relay_destroy(struct dpe_vk_relay *r) {
  if (!r)
    return;
  if (r->watch)
    pw_loop_destroy_source(r->loop, r->watch);
  if (r->event_fd >= 0)
    close(r->event_fd);
  if (r->timeline_fd >= 0)
    close(r->timeline_fd);
  if (r->acquire_fd >= 0)
    close(r->acquire_fd);
  if (r->acquire)
    drmSyncobjDestroy(r->drm, r->acquire);
  if (r->timeline)
    drmSyncobjDestroy(r->drm, r->timeline);
  for (unsigned i = 0; i < 4; i++)
    if (r->planes[i] >= 0)
      close(r->planes[i]);
  free(r);
}
static void released(void *opaque, int fd, uint32_t mask) {
  struct dpe_vk_relay *r = opaque;
  uint64_t value;
  bool success = (mask & SPA_IO_IN) && !(mask & (SPA_IO_ERR | SPA_IO_HUP)) &&
                 read(fd, &value, sizeof(value)) == sizeof(value) &&
                 drmSyncobjTimelineWait(r->drm, &r->timeline, &r->point, 1, 0,
                                        DRM_SYNCOBJ_WAIT_FLAGS_WAIT_ALL, NULL) == 0 &&
                 (r->acquire_fd < 0 || dpe_vk_fence_signaled(r->acquire_fd));
  // An errored sync_file can wake a GPU semaphore too. Never ACK that lease.
  if (r->acquire_fd >= 0) {
    close(r->acquire_fd);
    r->acquire_fd = -1;
  }
  void (*done)(void *, bool) = r->done;
  void *data = r->opaque;
  r->done = NULL;
  // Completion can retire this allocation and its watch.
  if (done)
    done(data, success);
}
struct dpe_vk_relay *dpe_vk_relay_create(int drm, struct pw_loop *loop, struct spa_buffer *b,
                                         const struct dpe_vk_packet *p, const int fds[4]) {
  if (!p->nfd || p->nfd > 4 || p->flags || !dpe_vk_relay_format(p->format) ||
      b->n_datas != p->nfd + 2)
    return NULL;
  struct dpe_vk_relay *r = calloc(1, sizeof(*r));
  if (!r)
    return NULL;
  r->drm = drm;
  r->loop = loop;
  r->generation = p->generation;
  r->timeline_fd = r->event_fd = r->acquire_fd = -1;
  for (unsigned i = 0; i < 4; i++)
    r->planes[i] = -1;
  r->meta = spa_buffer_find_meta_data(b, DPE_RELAY_META, sizeof(*r->meta));
  if (!r->meta || drmSyncobjCreate(drm, 0, &r->timeline) || drmSyncobjCreate(drm, 0, &r->acquire) ||
      drmSyncobjHandleToFD(drm, r->timeline, &r->timeline_fd))
    goto fail;
  r->event_fd = eventfd(0, EFD_NONBLOCK | EFD_CLOEXEC);
  if (r->event_fd < 0)
    goto fail;
  r->watch =
      pw_loop_add_io(loop, r->event_fd, SPA_IO_IN | SPA_IO_ERR | SPA_IO_HUP, false, released, r);
  if (!r->watch)
    goto fail;
  for (unsigned i = 0; i < p->nfd; i++) {
    struct spa_data *d = &b->datas[i];
    if (!d->chunk || !(d->type & (1u << SPA_DATA_DmaBuf)) || fds[i] < 0)
      goto fail;
    r->planes[i] = fcntl(fds[i], F_DUPFD_CLOEXEC, 3);
    if (r->planes[i] < 0)
      goto fail;
    d->type = SPA_DATA_DmaBuf;
    d->flags = SPA_DATA_FLAG_READABLE;
    d->fd = r->planes[i];
    d->data = NULL;
    d->mapoffset = d->maxsize = 0;
    *d->chunk =
        (struct spa_chunk){.offset = p->offsets[i], .size = 1, .stride = (int32_t)p->strides[i]};
  }
  for (unsigned i = p->nfd; i < b->n_datas; i++) {
    struct spa_data *d = &b->datas[i];
    d->type = DPE_RELAY_DATA;
    d->flags = SPA_DATA_FLAG_READABLE;
    d->fd = r->timeline_fd;
    d->data = NULL;
    d->mapoffset = d->maxsize = 0;
  }
  *r->meta = (struct dpe_relay_timeline){0};
  return r;
fail:
  dpe_vk_relay_destroy(r);
  return NULL;
}
int dpe_vk_relay_submit(struct dpe_vk_relay *r, int acquire_fd, void (*done)(void *, bool),
                        void *opaque) {
  if (r->done || r->point > UINT64_MAX - 2) {
    if (acquire_fd >= 0)
      close(acquire_fd);
    return -EBUSY;
  }
  r->point += 2;
  uint64_t acquire = r->point - 1;
  r->meta->acquire_point = acquire;
  r->meta->release_point = r->point;
  // Register before queueing, including before the consumer creates the fence.
  // Kernel notification avoids a polling timer and another frame of latency.
  if (notify_point(r->drm, r->timeline, r->point, r->event_fd))
    goto fail;
  if (acquire_fd >= 0) {
    // Import replaces a binary syncobj's fence; importing onto the timeline
    // itself would discard its previous release points. Transfer a reference
    // into the new acquire point, then let the native GPU wait on that point.
    if (drmSyncobjImportSyncFile(r->drm, r->acquire, acquire_fd) ||
        drmSyncobjTransfer(r->drm, r->timeline, acquire, r->acquire, 0, 0))
      goto fail;
  } else if (drmSyncobjTimelineSignal(r->drm, &r->timeline, &acquire, 1)) {
    goto fail;
  }
  r->acquire_fd = acquire_fd;
  r->done = done;
  r->opaque = opaque;
  return 0;
fail:
  if (acquire_fd >= 0)
    close(acquire_fd);
  return -EIO;
}
