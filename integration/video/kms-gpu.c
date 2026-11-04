// SPDX-License-Identifier: GPL-3.0-only
// One GPU snapshot into owned BGRA storage. No CPU mapping or encoder bridge.
#define _GNU_SOURCE
#include "kms-gpu.h"
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES3/gl3.h>
#include <drm_fourcc.h>
#include <fcntl.h>
#include <gbm.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

// GLES extension declarations require the core types above.
#include <GLES2/gl2ext.h>

struct dpe_kms_gpu {
  int fd;
  struct gbm_device *gbm;
  EGLDisplay display;
  EGLContext context;
  GLuint program;
  struct dpe_kms_buffer *preview;
  GLint source, destination, alpha;
  PFNEGLCREATEIMAGEKHRPROC create_image;
  PFNEGLDESTROYIMAGEKHRPROC destroy_image;
  PFNGLEGLIMAGETARGETTEXTURE2DOESPROC image_texture;
  PFNEGLCREATESYNCKHRPROC create_sync;
  PFNEGLDESTROYSYNCKHRPROC destroy_sync;
  PFNEGLDUPNATIVEFENCEFDANDROIDPROC dup_fence;
};
struct dpe_kms_buffer {
  struct dpe_kms_gpu *gpu;
  struct gbm_bo *bo;
  EGLImageKHR image;
  GLuint texture, framebuffer;
  int fd;
  uint32_t width, height;
};
static bool extension(const char *list, const char *name) {
  size_t size = strlen(name);
  if (!list)
    return false;
  for (const char *at = list; (at = strstr(at, name)); at += size)
    if ((at == list || at[-1] == ' ') && (!at[size] || at[size] == ' '))
      return true;
  return false;
}
static GLuint shader(GLenum type, const char *text) {
  GLuint shader = glCreateShader(type);
  glShaderSource(shader, 1, &text, NULL);
  glCompileShader(shader);
  GLint okay;
  glGetShaderiv(shader, GL_COMPILE_STATUS, &okay);
  if (!okay) {
    glDeleteShader(shader);
    return 0;
  }
  return shader;
}
struct dpe_kms_gpu *dpe_kms_gpu_create(const char *node) {
  struct dpe_kms_gpu *g = calloc(1, sizeof(*g));
  if (!g)
    return NULL;
  g->fd = open(node, O_RDWR | O_CLOEXEC | O_NOFOLLOW);
  if (g->fd < 0 || !(g->gbm = gbm_create_device(g->fd)))
    goto fail;
  g->display = eglGetPlatformDisplay(EGL_PLATFORM_GBM_KHR, g->gbm, NULL);
  if (g->display == EGL_NO_DISPLAY || !eglInitialize(g->display, NULL, NULL))
    goto fail;
  const char *extensions = eglQueryString(g->display, EGL_EXTENSIONS);
  const char *required[] = {"EGL_KHR_surfaceless_context", "EGL_KHR_no_config_context",
                            "EGL_EXT_image_dma_buf_import_modifiers",
                            "EGL_ANDROID_native_fence_sync"};
  for (unsigned i = 0; i < sizeof(required) / sizeof(required[0]); i++)
    if (!extension(extensions, required[i]))
      goto fail;
#define PROC(field, type, name)                                                                    \
  g->field = (type)eglGetProcAddress(name);                                                        \
  if (!g->field)                                                                                   \
  goto fail
  PROC(create_image, PFNEGLCREATEIMAGEKHRPROC, "eglCreateImageKHR");
  PROC(destroy_image, PFNEGLDESTROYIMAGEKHRPROC, "eglDestroyImageKHR");
  PROC(image_texture, PFNGLEGLIMAGETARGETTEXTURE2DOESPROC, "glEGLImageTargetTexture2DOES");
  PROC(create_sync, PFNEGLCREATESYNCKHRPROC, "eglCreateSyncKHR");
  PROC(destroy_sync, PFNEGLDESTROYSYNCKHRPROC, "eglDestroySyncKHR");
  PROC(dup_fence, PFNEGLDUPNATIVEFENCEFDANDROIDPROC, "eglDupNativeFenceFDANDROID");
#undef PROC
  const EGLint attributes[] = {EGL_CONTEXT_CLIENT_VERSION, 3, EGL_NONE};
  if (!eglBindAPI(EGL_OPENGL_ES_API))
    goto fail;
  g->context = eglCreateContext(g->display, EGL_NO_CONFIG_KHR, EGL_NO_CONTEXT, attributes);
  if (g->context == EGL_NO_CONTEXT ||
      !eglMakeCurrent(g->display, EGL_NO_SURFACE, EGL_NO_SURFACE, g->context))
    goto fail;
  const char *vertex =
      "#version 300 es\nuniform vec4 sourceRect,destinationRect;out vec2 uv;"
      "void main(){vec2 p=vec2(gl_VertexID&1,gl_VertexID>>1);uv=sourceRect.xy+p*sourceRect.zw;"
      "gl_Position=vec4((destinationRect.xy+p*destinationRect.zw)*2.0-1.0,0,1);}";
  const char *fragment =
      "#version 300 es\nprecision highp float;uniform sampler2D image;"
      "uniform bool useAlpha;in vec2 uv;out vec4 color;"
      "void main(){vec4 p=texture(image,uv);color=vec4(p.rgb,useAlpha?p.a:1.0);}";
  GLuint v = shader(GL_VERTEX_SHADER, vertex), f = shader(GL_FRAGMENT_SHADER, fragment);
  if (!v || !f) {
    glDeleteShader(v);
    glDeleteShader(f);
    goto fail;
  }
  g->program = glCreateProgram();
  glAttachShader(g->program, v);
  glAttachShader(g->program, f);
  glLinkProgram(g->program);
  glDeleteShader(v);
  glDeleteShader(f);
  GLint linked;
  glGetProgramiv(g->program, GL_LINK_STATUS, &linked);
  if (!linked || glGetError() != GL_NO_ERROR)
    goto fail;
  g->source = glGetUniformLocation(g->program, "sourceRect");
  g->destination = glGetUniformLocation(g->program, "destinationRect");
  g->alpha = glGetUniformLocation(g->program, "useAlpha");
  return g;
fail:
  dpe_kms_gpu_destroy(g);
  return NULL;
}
void dpe_kms_gpu_destroy(struct dpe_kms_gpu *g) {
  if (!g)
    return;
  dpe_kms_buffer_destroy(g->preview);
  if (g->display != EGL_NO_DISPLAY) {
    if (g->program)
      glDeleteProgram(g->program);
    eglMakeCurrent(g->display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
    if (g->context != EGL_NO_CONTEXT)
      eglDestroyContext(g->display, g->context);
    eglTerminate(g->display);
  }
  if (g->gbm)
    gbm_device_destroy(g->gbm);
  if (g->fd >= 0)
    close(g->fd);
  free(g);
}
int dpe_kms_gpu_fd(struct dpe_kms_gpu *g) { return g->fd; }
static EGLImageKHR import(struct dpe_kms_gpu *g, const gsr_kms_response_item *p) {
  EGLint attributes[64], n = 0;
#define ATTR(k, v)                                                                                 \
  do {                                                                                             \
    attributes[n++] = (k);                                                                         \
    attributes[n++] = (EGLint)(v);                                                                 \
  } while (0)
  ATTR(EGL_WIDTH, p->width);
  ATTR(EGL_HEIGHT, p->height);
  ATTR(EGL_LINUX_DRM_FOURCC_EXT, p->pixel_format);
  const EGLint fd_keys[] = {EGL_DMA_BUF_PLANE0_FD_EXT, EGL_DMA_BUF_PLANE1_FD_EXT,
                            EGL_DMA_BUF_PLANE2_FD_EXT, EGL_DMA_BUF_PLANE3_FD_EXT};
  const EGLint mods[] = {EGL_DMA_BUF_PLANE0_MODIFIER_LO_EXT, EGL_DMA_BUF_PLANE1_MODIFIER_LO_EXT,
                         EGL_DMA_BUF_PLANE2_MODIFIER_LO_EXT, EGL_DMA_BUF_PLANE3_MODIFIER_LO_EXT};
  for (int i = 0; i < p->num_dma_bufs; i++) {
    ATTR(fd_keys[i], p->dma_buf[i].fd);
    ATTR(fd_keys[i] + 1, p->dma_buf[i].offset);
    ATTR(fd_keys[i] + 2, p->dma_buf[i].pitch);
    if (p->modifier != DRM_FORMAT_MOD_INVALID) {
      ATTR(mods[i], p->modifier);
      ATTR(mods[i] + 1, p->modifier >> 32);
    }
  }
  attributes[n++] = EGL_NONE;
#undef ATTR
  return g->create_image(g->display, EGL_NO_CONTEXT, EGL_LINUX_DMA_BUF_EXT, NULL, attributes);
}
struct dpe_kms_buffer *dpe_kms_buffer_create(struct dpe_kms_gpu *g, uint32_t width, uint32_t height,
                                             struct dpe_vk_packet *packet, int fds[4]) {
  if (!width || !height || width > 8192 || height > 8192)
    return NULL;
  struct dpe_kms_buffer *b = calloc(1, sizeof(*b));
  if (!b)
    return NULL;
  b->gpu = g;
  b->fd = -1;
  b->width = width;
  b->height = height;
  const uint64_t modifier = DRM_FORMAT_MOD_LINEAR;
  b->bo = gbm_bo_create_with_modifiers2(g->gbm, width, height, GBM_FORMAT_ARGB8888, &modifier, 1,
                                        GBM_BO_USE_RENDERING);
  if (!b->bo || gbm_bo_get_modifier(b->bo) != modifier || gbm_bo_get_plane_count(b->bo) != 1 ||
      gbm_bo_get_offset(b->bo, 0))
    goto fail;
  b->fd = gbm_bo_get_fd_for_plane(b->bo, 0);
  uint32_t stride = gbm_bo_get_stride_for_plane(b->bo, 0);
  if (b->fd < 0 || stride < width * 4 || stride > UINT32_MAX / height)
    goto fail;
  gsr_kms_response_item item = {.width = width,
                                .height = height,
                                .pixel_format = DRM_FORMAT_ARGB8888,
                                .modifier = modifier,
                                .num_dma_bufs = 1,
                                .dma_buf = {{.fd = b->fd, .pitch = stride}}};
  b->image = import(g, &item);
  if (b->image == EGL_NO_IMAGE_KHR)
    goto fail;
  glGenTextures(1, &b->texture);
  glBindTexture(GL_TEXTURE_2D, b->texture);
  g->image_texture(GL_TEXTURE_2D, b->image);
  glGenFramebuffers(1, &b->framebuffer);
  glBindFramebuffer(GL_FRAMEBUFFER, b->framebuffer);
  glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, b->texture, 0);
  if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE ||
      glGetError() != GL_NO_ERROR)
    goto fail;
  *packet = dpe_vk_packet(DPE_VK_TEXTURE);
  packet->width = width;
  packet->height = height;
  packet->format = DRM_FORMAT_ARGB8888;
  packet->modifier = modifier;
  packet->nfd = 1;
  packet->strides[0] = stride;
  fds[0] = b->fd;
  fds[1] = fds[2] = fds[3] = -1;
  return b;
fail:
  dpe_kms_buffer_destroy(b);
  return NULL;
}
void dpe_kms_buffer_destroy(struct dpe_kms_buffer *b) {
  if (!b)
    return;
  glDeleteFramebuffers(1, &b->framebuffer);
  glDeleteTextures(1, &b->texture);
  if (b->image != EGL_NO_IMAGE_KHR)
    b->gpu->destroy_image(b->gpu->display, b->image);
  if (b->fd >= 0)
    close(b->fd);
  if (b->bo)
    gbm_bo_destroy(b->bo);
  free(b);
}
static bool draw(struct dpe_kms_buffer *b, const gsr_kms_response_item *p, bool cursor,
                 uint32_t logical_width, uint32_t logical_height) {
  struct dpe_kms_gpu *g = b->gpu;
  EGLImageKHR image = import(g, p);
  if (image == EGL_NO_IMAGE_KHR)
    return false;
  GLuint texture;
  glGenTextures(1, &texture);
  glBindTexture(GL_TEXTURE_2D, texture);
  g->image_texture(GL_TEXTURE_2D, image);
  glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
  glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
  glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
  glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
  glUniform4f(g->source, (float)p->src_x / p->width, (float)p->src_y / p->height,
              (float)p->src_w / p->width, (float)p->src_h / p->height);
  glUniform4f(g->destination, (float)p->dst_x / logical_width, (float)p->dst_y / logical_height,
              (float)p->dst_w / logical_width, (float)p->dst_h / logical_height);
  glUniform1i(g->alpha, cursor);
  if (cursor) {
    glEnable(GL_BLEND);
    glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA);
  } else
    glDisable(GL_BLEND);
  glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
  bool success = glGetError() == GL_NO_ERROR;
  // Submitted GL commands retain the imports; deletion does not revoke GPU
  // storage while a queued read is in flight. No CPU fence wait is introduced.
  glDeleteTextures(1, &texture);
  g->destroy_image(g->display, image);
  return success;
}
int dpe_kms_buffer_render(struct dpe_kms_buffer *b, const struct dpe_kms_scene *scene) {
  struct dpe_kms_gpu *g = b->gpu;
  glBindFramebuffer(GL_FRAMEBUFFER, b->framebuffer);
  glViewport(0, 0, b->width, b->height);
  glDisable(GL_DITHER);
  glUseProgram(g->program);
  glActiveTexture(GL_TEXTURE0);
  if (!draw(b, scene->primary, false, b->width, b->height) ||
      (scene->cursor && !draw(b, scene->cursor, true, b->width, b->height)))
    return -1;
  EGLSyncKHR sync = g->create_sync(g->display, EGL_SYNC_NATIVE_FENCE_ANDROID, NULL);
  if (sync == EGL_NO_SYNC_KHR)
    return -1;
  glFlush();
  int fd = g->dup_fence(g->display, sync);
  g->destroy_sync(g->display, sync);
  return fd;
}
bool dpe_kms_gpu_preview(struct dpe_kms_gpu *g, const struct dpe_kms_scene *scene,
                         uint32_t source_width, uint32_t source_height, uint32_t *width,
                         uint32_t *height, unsigned char *pixels) {
  if (!g || !scene || !scene->primary || !source_width || !source_height || !width || !height ||
      !*width || !*height || *width > 480 || *height > 270 || !pixels)
    return false;
  if ((uint64_t)*width * source_height > (uint64_t)*height * source_width)
    *width = (uint64_t)*height * source_width / source_height;
  else
    *height = (uint64_t)*width * source_height / source_width;
  if (!*width || !*height)
    return false;
  struct dpe_vk_packet packet;
  int fds[4];
  if (g->preview && (g->preview->width != *width || g->preview->height != *height)) {
    dpe_kms_buffer_destroy(g->preview);
    g->preview = NULL;
  }
  if (!g->preview)
    g->preview = dpe_kms_buffer_create(g, *width, *height, &packet, fds);
  struct dpe_kms_buffer *b = g->preview;
  if (!b)
    return false;
  glBindFramebuffer(GL_FRAMEBUFFER, b->framebuffer);
  glViewport(0, 0, *width, *height);
  glDisable(GL_DITHER);
  glUseProgram(g->program);
  glActiveTexture(GL_TEXTURE0);
  bool okay = draw(b, scene->primary, false, source_width, source_height) &&
              (!scene->cursor || draw(b, scene->cursor, true, source_width, source_height));
  if (okay) {
    glReadPixels(0, 0, *width, *height, GL_RGBA, GL_UNSIGNED_BYTE, pixels);
    okay = glGetError() == GL_NO_ERROR;
    for (size_t i = 0; i < (size_t)*width * *height * 4; i += 4) {
      unsigned char red = pixels[i];
      pixels[i] = pixels[i + 2];
      pixels[i + 2] = red;
      pixels[i + 3] = 255;
    }
  }
  return okay;
}
