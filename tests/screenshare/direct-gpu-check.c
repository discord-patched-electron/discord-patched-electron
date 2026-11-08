#define _GNU_SOURCE
#include "../../integration/video/direct-capture-gpu.h"
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>
#include <GLES2/gl2ext.h>
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <gbm.h>
#include <stdio.h>
#include <sys/mman.h>
#include <unistd.h>
#include <va/va.h>
#include <va/va_drm.h>
#include <va/va_drmcommon.h>
static void check_neutral_padding(int render, const struct encoder_nv12 *view) {
  VADisplay display = vaGetDisplayDRM(render);
  int major, minor;
  assert(display && vaInitialize(display, &major, &minor) == VA_STATUS_SUCCESS);
  unsigned height = (view->height + 15u) & ~15u;
  VADRMPRIMESurfaceDescriptor descriptor = {.fourcc = VA_FOURCC_NV12,
                                            .width = view->width,
                                            .height = height,
                                            .num_objects = 1,
                                            .num_layers = 1};
  descriptor.objects[0].fd = view->fd;
  descriptor.objects[0].size = view->size;
  descriptor.objects[0].drm_format_modifier = view->modifier;
  descriptor.layers[0].drm_format = GBM_FORMAT_NV12;
  descriptor.layers[0].num_planes = 2;
  for (unsigned i = 0; i < 2; ++i) {
    descriptor.layers[0].offset[i] = view->offset[i];
    descriptor.layers[0].pitch[i] = view->stride[i];
  }
  VASurfaceAttrib attrs[2] = {
      {.type = VASurfaceAttribMemoryType,
       .flags = VA_SURFACE_ATTRIB_SETTABLE,
       .value = {.type = VAGenericValueTypeInteger,
                 .value.i = VA_SURFACE_ATTRIB_MEM_TYPE_DRM_PRIME_2}},
      {.type = VASurfaceAttribExternalBufferDescriptor,
       .flags = VA_SURFACE_ATTRIB_SETTABLE,
       .value = {.type = VAGenericValueTypePointer, .value.p = &descriptor}}};
  VASurfaceID surface;
  assert(vaCreateSurfaces(display, VA_RT_FORMAT_YUV420, view->width, height, &surface, 1, attrs,
                          2) == VA_STATUS_SUCCESS);
  VAImageFormat format = {
      .fourcc = VA_FOURCC_NV12, .byte_order = VA_LSB_FIRST, .bits_per_pixel = 12};
  VAImage image;
  assert(vaCreateImage(display, &format, view->width, height, &image) == VA_STATUS_SUCCESS);
  assert(vaGetImage(display, surface, 0, 0, view->width, height, image.image_id) ==
         VA_STATUS_SUCCESS);
  void *pixels = NULL;
  assert(vaMapBuffer(display, image.buf, &pixels) == VA_STATUS_SUCCESS && pixels);
  for (unsigned plane = 0; plane < 2; ++plane)
    for (unsigned row = plane ? view->height / 2 : view->height;
         row < (plane ? height / 2 : height); ++row)
      for (unsigned x = 0; x < view->width; ++x)
        assert(((uint8_t *)pixels)[image.offsets[plane] + (size_t)row * image.pitches[plane] + x] ==
               (plane ? 128 : 16));
  assert(vaUnmapBuffer(display, image.buf) == VA_STATUS_SUCCESS);
  assert(vaDestroyImage(display, image.image_id) == VA_STATUS_SUCCESS);
  assert(vaDestroySurfaces(display, &surface, 1) == VA_STATUS_SUCCESS);
  assert(vaTerminate(display) == VA_STATUS_SUCCESS);
  puts("Pooled NV12 allocation has neutral black bottom padding");
}
int main(void) {
  int fd = open("/dev/dri/renderD128", O_RDWR | O_CLOEXEC);
  assert(fd >= 0);
  struct gbm_device *gbm = gbm_create_device(fd);
  assert(gbm);
  struct gbm_bo *bo =
      gbm_bo_create(gbm, 640, 480, GBM_FORMAT_XRGB8888, GBM_BO_USE_RENDERING | GBM_BO_USE_LINEAR);
  assert(bo);
  EGLDisplay display = eglGetPlatformDisplay(EGL_PLATFORM_GBM_KHR, gbm, NULL);
  assert(display != EGL_NO_DISPLAY);
  assert(eglInitialize(display, NULL, NULL));
  assert(eglBindAPI(EGL_OPENGL_ES_API));
  EGLint context_attrs[] = {EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE};
  EGLContext context = eglCreateContext(display, EGL_NO_CONFIG_KHR, EGL_NO_CONTEXT, context_attrs);
  assert(context != EGL_NO_CONTEXT);
  assert(eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, context));
  int image_fd = gbm_bo_get_fd(bo);
  EGLint attrs[] = {EGL_WIDTH,
                    640,
                    EGL_HEIGHT,
                    480,
                    EGL_LINUX_DRM_FOURCC_EXT,
                    GBM_FORMAT_XRGB8888,
                    EGL_DMA_BUF_PLANE0_FD_EXT,
                    image_fd,
                    EGL_DMA_BUF_PLANE0_OFFSET_EXT,
                    0,
                    EGL_DMA_BUF_PLANE0_PITCH_EXT,
                    gbm_bo_get_stride(bo),
                    EGL_NONE};
  PFNEGLCREATEIMAGEKHRPROC create_image = (void *)eglGetProcAddress("eglCreateImageKHR");
  PFNEGLDESTROYIMAGEKHRPROC destroy_image = (void *)eglGetProcAddress("eglDestroyImageKHR");
  PFNGLEGLIMAGETARGETTEXTURE2DOESPROC target =
      (void *)eglGetProcAddress("glEGLImageTargetTexture2DOES");
  assert(create_image && target && destroy_image);
  EGLImageKHR image = create_image(display, EGL_NO_CONTEXT, EGL_LINUX_DMA_BUF_EXT, NULL, attrs);
  assert(image != EGL_NO_IMAGE_KHR);
  GLuint texture, fbo;
  glGenTextures(1, &texture);
  glBindTexture(GL_TEXTURE_2D, texture);
  target(GL_TEXTURE_2D, image);
  glGenFramebuffers(1, &fbo);
  glBindFramebuffer(GL_FRAMEBUFFER, fbo);
  glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, texture, 0);
  assert(glCheckFramebufferStatus(GL_FRAMEBUFFER) == GL_FRAMEBUFFER_COMPLETE);
  glViewport(0, 0, 640, 480);
  glClearColor(1, 0, 0, 1);
  glClear(GL_COLOR_BUFFER_BIT);
  glFinish();
  assert(glGetError() == GL_NO_ERROR);
  struct dpe_direct_packet p = dpe_direct_packet(DPE_DIRECT_FRAME);
  p.width = 640;
  p.height = 480;
  p.descriptors = 1;
  p.timestamp_ns = 1234567;
  p.image = (struct dpe_direct_image){.width = 640,
                                      .height = 480,
                                      .format = GBM_FORMAT_XRGB8888,
                                      .planes = 1,
                                      .modifier = gbm_bo_get_modifier(bo),
                                      .stride = {gbm_bo_get_stride(bo)},
                                      .source = {0, 0, 640, 480},
                                      .destination = {0, 0, 640, 480}};
  int fds[DPE_DIRECT_FDS] = {gbm_bo_get_fd(bo)};
  struct dpe_direct_gpu *gpu = dpe_direct_gpu_create("/dev/dri/renderD128");
  assert(gpu);
  void *frame[4];
  for (unsigned i = 0; i < 4; i++) {
    frame[i] = dpe_direct_gpu_convert(gpu, &p, fds, 640, 480);
    assert(frame[i]);
  }
  assert(!dpe_direct_gpu_convert(gpu, &p, fds, 640, 480));
  assert(errno == EAGAIN);
  size_t w, h;
  uint64_t time;
  assert(dpe_direct_frame_data(frame[0], &w, &h, &time));
  assert(w == 640 && h == 480 && time == 1234567);
  struct encoder_nv12 view;
  void *lease;
  const char *node;
  assert(dpe_direct_frame_view(frame[0], &view, &lease, &node));
  assert(view.width == 640 && view.height == 480 && view.fd >= 0);
  const uint8_t *data;
  size_t size, sy, suv, u, v;
  void *cpu = dpe_direct_frame_i420(frame[0], &data, &size, &sy, &suv, &u, &v);
  assert(cpu);
  printf("RGB red -> I420 Y=%u U=%u V=%u, stride=%zu/%zu bytes=%zu\n", data[200 * sy + 200],
         data[u + 100 * suv + 100], data[v + 100 * suv + 100], sy, suv, size);
  assert(data[200 * sy + 200] > 55 && data[200 * sy + 200] < 90);
  assert(data[u + 100 * suv + 100] < 115);
  assert(data[v + 100 * suv + 100] > 200);
  for (unsigned i = 0; i < 4; i++)
    assert(dpe_direct_frame_destroy(frame[i]));

  // Firmware may read two rows beyond the visible picture. These must contain
  // neutral NV12 black, including after import through an independent display.
  void *padded_frame = dpe_direct_gpu_convert(gpu, &p, fds, 640, 360);
  struct encoder_nv12 padded;
  void *padded_lease;
  assert(padded_frame && dpe_direct_frame_view(padded_frame, &padded, &padded_lease, NULL));
  assert(padded.width == 640 && padded.height == 360);
  check_neutral_padding(fd, &padded);
  dpe_direct_frame_release(padded_lease);
  assert(dpe_direct_frame_destroy(padded_frame));

  // A sealed cursor bitmap is the only source-pixel CPU upload. Verify VPP keeps
  // the red background and clips a green cursor at the top-left edge.
  int cursor_fd = memfd_create("synthetic-cursor", MFD_CLOEXEC | MFD_ALLOW_SEALING);
  assert(cursor_fd >= 0);
  uint32_t pixels[16 * 16];
  for (unsigned i = 0; i < 16 * 16; ++i)
    pixels[i] = 0xff00ff00;
  assert(write(cursor_fd, pixels, sizeof(pixels)) == sizeof(pixels));
  assert(!fcntl(cursor_fd, F_ADD_SEALS, F_SEAL_WRITE | F_SEAL_GROW | F_SEAL_SHRINK | F_SEAL_SEAL));
  p.cursor = (struct dpe_direct_image){.width = 16,
                                       .height = 16,
                                       .format = GBM_FORMAT_ARGB8888,
                                       .planes = 1,
                                       .storage = DPE_DIRECT_CURSOR_BITMAP,
                                       .fd = {1},
                                       .stride = {64},
                                       .source = {0, 0, 16, 16},
                                       .destination = {-4, -4, 16, 16}};
  p.descriptors = 2;
  fds[1] = cursor_fd;
  void *cursor_frame = dpe_direct_gpu_convert(gpu, &p, fds, 640, 480);
  assert(cursor_frame);
  const uint8_t *cursor_data;
  size_t cursor_size, cursor_sy, cursor_suv, cursor_u, cursor_v;
  void *cursor_cpu = dpe_direct_frame_i420(cursor_frame, &cursor_data, &cursor_size, &cursor_sy,
                                           &cursor_suv, &cursor_u, &cursor_v);
  assert(cursor_cpu);
  printf("cursor green Y=%u background red Y=%u\n", cursor_data[4 * cursor_sy + 4],
         cursor_data[200 * cursor_sy + 200]);
  assert(cursor_data[4 * cursor_sy + 4] > 140 && cursor_data[4 * cursor_sy + 4] < 190);
  assert(cursor_data[200 * cursor_sy + 200] > 55 && cursor_data[200 * cursor_sy + 200] < 90);
  assert(dpe_direct_i420_destroy(cursor_cpu));
  assert(dpe_direct_frame_destroy(cursor_frame));
  close(cursor_fd);
  // Encoder and CPU leases retain the allocation after capture teardown.
  dpe_direct_gpu_destroy(gpu);
  assert(fcntl(view.fd, F_GETFD) >= 0);
  assert(dpe_direct_i420_destroy(cpu));
  dpe_direct_frame_release(lease);
  glDeleteFramebuffers(1, &fbo);
  glDeleteTextures(1, &texture);
  destroy_image(display, image);
  eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
  eglDestroyContext(display, context);
  eglTerminate(display);
  close(image_fd);
  close(fds[0]);
  gbm_bo_destroy(bo);
  gbm_device_destroy(gbm);
  close(fd);
  puts("Bounded pool, color, frame ABI and post-teardown leases pass");
  return 0;
}
