#define _GNU_SOURCE
#include "../../integration/video/direct-capture-gpu.h"
#include "av1-ivf.h"
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>
#include <GLES2/gl2ext.h>
#include <assert.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <gbm.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

static unsigned emitted, active_codec;
static FILE *recording;
static void encoded(void *unused, const struct encoder_frame_meta *meta, uint64_t info,
                    const uint8_t *bytes, size_t size, const void *recon) {
  (void)unused;
  (void)info;
  (void)recon;
  assert(meta->width == 640 && meta->height == 480);
  if (active_codec == 2) {
    if (!emitted)
      ivf_header(recording, 640, 480);
    ivf_packet(recording, bytes, size, emitted);
  } else
    assert(fwrite(bytes, 1, size, recording) == size);
  ++emitted;
}
static void dropped(void *unused, const struct encoder_frame_meta *meta) {
  (void)unused;
  (void)meta;
  assert(0);
}
static uint64_t clock_ns(clockid_t clock) {
  struct timespec t;
  assert(!clock_gettime(clock, &t));
  return (uint64_t)t.tv_sec * 1000000000ull + t.tv_nsec;
}
int main(int argc, char **argv) {
  assert(argc == 6);
  void *library = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
  assert(library);
  const struct dpe_hevc_api *(*get)(void) = (void *)dlsym(library, "dpe_hevc_api");
  assert(get);
  const struct dpe_hevc_api *api = get();
  assert(api->abi == DPE_HEVC_ABI && api->size == sizeof(*api));
  void (*stats)(uint64_t[5]) = (void *)dlsym(library, "dpe_encoder_import_stats");
  unsigned frames = (unsigned)strtoul(argv[4], NULL, 10);
  assert(frames >= 60 && frames <= 10000);
  active_codec = (unsigned)strtoul(argv[5], NULL, 10);
  assert(active_codec <= 2);
  recording = fopen(argv[3], "wb");
  assert(recording);
  int fd = open(argv[2], O_RDWR | O_CLOEXEC);
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
  struct dpe_direct_gpu *gpu = dpe_direct_gpu_create(argv[2]);
  assert(gpu);

  struct encoder_session session = {.fps = 60, .maximum = {640, 480}, .codec = active_codec};
  struct encoder_options options = {.fps = 60,
                                    .dimensions = {640, 480},
                                    .bitrate_target = 6000000,
                                    .bitrate_maximum = 6000000,
                                    .keyframe_interval = 60};
  void *encoder = api->create(argv[2], session, options, NULL, encoded, dropped);
  assert(encoder);
  uint64_t encode_cpu = 0, encode_wall = 0, start = clock_ns(CLOCK_MONOTONIC);
  for (unsigned i = 0; i < frames; ++i) {
    bool green = (i / 30) % 2;
    glClearColor(green ? 0 : 1, green ? 1 : 0, 0, 1);
    glClear(GL_COLOR_BUFFER_BIT);
    glFinish();
    p.timestamp_ns = (uint64_t)(i + 1) * 16666667;
    void *frame = dpe_direct_gpu_convert(gpu, &p, fds, 640, 480);
    assert(frame);
    struct encoder_nv12 view;
    void *lease;
    assert(dpe_direct_frame_view(frame, &view, &lease, NULL));
    struct encoder_frame_meta meta = {
        .width = 640, .height = 480, .timestamp = (uint64_t)(i + 1) * 16667};
    struct encoder_frame_info info = {.idr = i % 60 == 0, .timestamp_ms = (uint64_t)(i + 1) * 17};
    uint64_t cpu = clock_ns(CLOCK_THREAD_CPUTIME_ID), wall = clock_ns(CLOCK_MONOTONIC);
    assert(api->nv12(encoder, &meta, info, &view, lease, dpe_direct_frame_release));
    encode_cpu += clock_ns(CLOCK_THREAD_CPUTIME_ID) - cpu;
    encode_wall += clock_ns(CLOCK_MONOTONIC) - wall;
    assert(dpe_direct_frame_destroy(frame));
  }
  dpe_direct_gpu_destroy(gpu);
  api->destroy(encoder);
  assert(emitted == frames && dpe_direct_gpu_readbacks() == 0);
  uint64_t imports[5] = {0};
  if (stats)
    stats(imports);
  assert(!imports[3]);
  printf("{\"frames\":%u,\"encodeCpuNsPerFrame\":%.1f,\"encodeWallNsPerFrame\":%.1f,"
         "\"wallSeconds\":%.3f,\"imports\":%llu,\"hits\":%llu,\"evictions\":%llu,\"live\":%llu,"
         "\"bypasses\":%llu}\n",
         frames, (double)encode_cpu / frames, (double)encode_wall / frames,
         (clock_ns(CLOCK_MONOTONIC) - start) / 1e9, (unsigned long long)imports[0],
         (unsigned long long)imports[1], (unsigned long long)imports[2],
         (unsigned long long)imports[3], (unsigned long long)imports[4]);
  fclose(recording);
  dlclose(library);
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
  return 0;
}
