// SPDX-License-Identifier: GPL-3.0-only
// Ephemeral monitor thumbnail. No portal, PipeWire stream, encoder or pixel file.
#define _GNU_SOURCE
#include "kms-gpu.h"
#include "linux-dmabuf-v1-client.h"
#include "wayland-capture.h"
#include <drm_fourcc.h>
#include <fcntl.h>
#include <gbm.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <unistd.h>
#include <xf86drm.h>

static uint32_t width, height;
static bool persistent;
static bool next_frame(void) {
  if (!persistent)
    return false;
  alarm(3);
  char demand;
  if (read(STDIN_FILENO, &demand, 1) != 1 || demand != '\n')
    return false;
  alarm(3);
  return true;
}
static unsigned char pixels[480 * 270 * 4];
static bool emit(struct dpe_kms_gpu *gpu, const struct dpe_kms_scene *scene, uint32_t w,
                 uint32_t h) {
  uint32_t frame_width = width, frame_height = height;
  if (!dpe_kms_gpu_preview(gpu, scene, w, h, &frame_width, &frame_height, pixels))
    return false;
  printf("DPE-PREVIEW %u %u\n", frame_width, frame_height);
  return fwrite(pixels, 4, frame_width * frame_height, stdout) == frame_width * frame_height &&
         !fflush(stdout);
}
static bool kms(const char *selected) {
  struct dpe_kms_output outputs[DPE_KMS_OUTPUTS], *output = NULL;
  unsigned count = dpe_kms_inventory(outputs);
  for (unsigned i = 0; i < count; i++)
    if (outputs[i].supported && !strcmp(outputs[i].id, selected))
      output = &outputs[i];
  if (!output || dpe_kms_helper_status() || dpe_kms_authorization_required())
    return false;
  struct dpe_kms_client client = {.fd = -1, .pid = -1};
  if (!dpe_kms_client_open(&client, output->card, false))
    return false;
  int metadata = open(output->card, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
  struct dpe_kms_inspector *inspector = NULL;
  struct dpe_kms_gpu *gpu = NULL;
  bool okay = false;
  if (metadata < 0 || drmSetClientCap(metadata, DRM_CLIENT_CAP_UNIVERSAL_PLANES, 1) ||
      drmSetClientCap(metadata, DRM_CLIENT_CAP_ATOMIC, 1))
    goto done;
  inspector = dpe_kms_inspector_create(metadata, output);
  gpu = dpe_kms_gpu_create(output->render);
  if (!inspector || !gpu)
    goto done;
  do {
    okay = false;
    for (unsigned attempt = 0; attempt < 3 && !okay; attempt++) {
      if (!dpe_kms_request(&client))
        break;
      struct pollfd fd = {.fd = client.fd, .events = POLLIN};
      if (poll(&fd, 1, 350) <= 0 || !(fd.revents & POLLIN))
        break;
      gsr_kms_response response = {0};
      if (dpe_kms_receive(client.fd, &response) <= 0)
        break;
      struct dpe_kms_layout layout;
      struct dpe_kms_scene scene;
      bool valid = dpe_kms_metadata(inspector, &layout) &&
                   dpe_kms_scene(&response, output, &layout, &scene) == DPE_KMS_VALID;
      if (valid)
        okay = emit(gpu, &scene, output->width, output->height);
      dpe_kms_response_close(&response);
    }
  } while (okay && next_frame());
done:
  dpe_kms_gpu_destroy(gpu);
  dpe_kms_inspector_destroy(inspector);
  if (metadata >= 0)
    close(metadata);
  dpe_kms_client_close(&client);
  return okay;
}
struct preview {
  struct wl_display *display;
  struct wl_registry *registry;
  struct zwp_linux_dmabuf_v1 *dmabuf;
  struct dpe_wl_capture *capture;
  struct dpe_wl_buffer buffer;
  struct dpe_kms_gpu *gpu;
  struct gbm_device *gbm;
  struct gbm_bo *bo;
  gsr_kms_response_item item;
  bool submitted, done, okay;
};
static void global(void *data, struct wl_registry *registry, uint32_t name, const char *interface,
                   uint32_t version) {
  struct preview *p = data;
  if (!strcmp(interface, zwp_linux_dmabuf_v1_interface.name) && version >= 3 && !p->dmabuf)
    p->dmabuf = wl_registry_bind(registry, name, &zwp_linux_dmabuf_v1_interface, 3);
}
static void removed(void *data, struct wl_registry *registry, uint32_t name) {
  (void)data;
  (void)registry;
  (void)name;
}
static bool format(void *data, uint32_t value, bool dmabuf) {
  (void)data;
  return dmabuf && (value == DRM_FORMAT_ARGB8888 || value == DRM_FORMAT_XRGB8888 ||
                    value == DRM_FORMAT_ABGR8888 || value == DRM_FORMAT_XBGR8888);
}
static void constraints(void *data, const struct dpe_wl_constraints *c) {
  struct preview *p = data;
  if (p->submitted || !p->dmabuf || !c->has_device || !c->width || !c->height || c->width > 16384 ||
      c->height > 16384) {
    p->done = true;
    return;
  }
  drmDevicePtr device = NULL;
  if (drmGetDeviceFromDevId(c->device, 0, &device) < 0 || !device ||
      !(device->available_nodes & (1 << DRM_NODE_RENDER))) {
    if (device)
      drmFreeDevice(&device);
    p->done = true;
    return;
  }
  p->gpu = dpe_kms_gpu_create(device->nodes[DRM_NODE_RENDER]);
  drmFreeDevice(&device);
  if (!p->gpu || !(p->gbm = gbm_create_device(dpe_kms_gpu_fd(p->gpu)))) {
    p->done = true;
    return;
  }
  for (unsigned i = 0; i < c->format_count && !p->bo; i++) {
    const struct dpe_wl_format *f = &c->formats[i];
    if (!format(NULL, f->format, f->dmabuf) || f->modifier == DRM_FORMAT_MOD_INVALID)
      continue;
    p->bo = gbm_bo_create_with_modifiers2(p->gbm, c->width, c->height, f->format, &f->modifier, 1,
                                          GBM_BO_USE_RENDERING);
    if (p->bo)
      p->buffer.format = *f;
  }
  if (!p->bo) {
    p->done = true;
    return;
  }
  int planes = gbm_bo_get_plane_count(p->bo);
  if (planes < 1 || planes > 4 || gbm_bo_get_modifier(p->bo) != p->buffer.format.modifier) {
    p->done = true;
    return;
  }
  p->item = (gsr_kms_response_item){.width = c->width,
                                    .height = c->height,
                                    .pixel_format = p->buffer.format.format,
                                    .modifier = p->buffer.format.modifier,
                                    .src_w = c->width,
                                    .src_h = c->height,
                                    .dst_w = c->width,
                                    .dst_h = c->height};
  struct zwp_linux_buffer_params_v1 *params = zwp_linux_dmabuf_v1_create_params(p->dmabuf);
  if (!params) {
    p->done = true;
    return;
  }
  for (int i = 0; i < planes; i++) {
    int fd = gbm_bo_get_fd_for_plane(p->bo, i);
    if (fd < 0) {
      p->done = true;
      break;
    }
    p->item.dma_buf[i].fd = fd;
    p->item.dma_buf[i].pitch = gbm_bo_get_stride_for_plane(p->bo, i);
    p->item.dma_buf[i].offset = gbm_bo_get_offset(p->bo, i);
    p->item.num_dma_bufs++;
    zwp_linux_buffer_params_v1_add(params, fd, i, p->item.dma_buf[i].offset,
                                   p->item.dma_buf[i].pitch, p->item.modifier >> 32,
                                   p->item.modifier);
  }
  if (!p->done)
    p->buffer.handle = zwp_linux_buffer_params_v1_create_immed(params, c->width, c->height,
                                                               p->item.pixel_format, 0);
  zwp_linux_buffer_params_v1_destroy(params);
  p->buffer.width = c->width;
  p->buffer.height = c->height;
  p->buffer.generation = c->generation;
  if (!p->buffer.handle || dpe_wl_capture_submit(p->capture, &p->buffer) < 0)
    p->done = true;
  else
    p->submitted = true;
}
static void frame(void *data, const struct dpe_wl_buffer *buffer, const struct dpe_wl_frame *frame,
                  int status) {
  (void)buffer;
  struct preview *p = data;
  if (!status && frame && !frame->transform) {
    const struct dpe_kms_scene scene = {.primary = &p->item};
    p->okay = emit(p->gpu, &scene, p->item.width, p->item.height);
  }
  if (p->okay && next_frame() && dpe_wl_capture_submit(p->capture, &p->buffer) == 0)
    return;
  p->done = true;
}
static void stopped(void *data, int reason) {
  (void)reason;
  ((struct preview *)data)->done = true;
}
static bool wayland(const char *selected) {
  struct preview p = {0};
  p.display = wl_display_connect(NULL);
  if (!p.display)
    return false;
  p.registry = wl_display_get_registry(p.display);
  const struct wl_registry_listener events = {global, removed};
  wl_registry_add_listener(p.registry, &events, &p);
  const struct dpe_wl_callbacks callbacks = {constraints, frame, stopped};
  p.capture = dpe_wl_capture_new(p.display, selected, &callbacks, &p);
  if (!p.capture || dpe_wl_capture_set_format_filter(p.capture, format) < 0 ||
      wl_display_roundtrip(p.display) < 0 || wl_display_roundtrip(p.display) < 0 ||
      dpe_wl_capture_start(p.capture, true) < 0)
    goto done;
  while (!p.done && wl_display_dispatch(p.display) >= 0) {
  }
done:
  dpe_wl_capture_destroy(p.capture);
  if (p.buffer.handle)
    wl_buffer_destroy(p.buffer.handle);
  if (p.dmabuf)
    zwp_linux_dmabuf_v1_destroy(p.dmabuf);
  wl_registry_destroy(p.registry);
  wl_display_disconnect(p.display);
  for (int i = 0; i < p.item.num_dma_bufs; i++)
    close(p.item.dma_buf[i].fd);
  if (p.bo)
    gbm_bo_destroy(p.bo);
  if (p.gbm)
    gbm_device_destroy(p.gbm);
  dpe_kms_gpu_destroy(p.gpu);
  return p.okay;
}
int main(int argc, char **argv) {
  persistent = argc == 11 && !strcmp(argv[9], "--persistent") && !strcmp(argv[10], "1");
  if ((argc != 9 && !persistent) || strcmp(argv[1], "--method") || strcmp(argv[3], "--output") ||
      strcmp(argv[5], "--width") || strcmp(argv[7], "--height") || !*argv[4] ||
      strlen(argv[4]) > 128)
    return 2;
  char *end;
  unsigned long w = strtoul(argv[6], &end, 10);
  if (!*argv[6] || *end || w < 16 || w > 480)
    return 2;
  unsigned long h = strtoul(argv[8], &end, 10);
  if (!*argv[8] || *end || h < 16 || h > 270)
    return 2;
  width = w;
  height = h;
  pid_t parent = getppid();
  if (prctl(PR_SET_PDEATHSIG, SIGTERM) < 0 || getppid() != parent)
    return 1;
  alarm(3);
  return !strcmp(argv[2], "kms")              ? !kms(argv[4])
         : !strcmp(argv[2], "wayland-direct") ? !wayland(argv[4])
                                              : 2;
}
