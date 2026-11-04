// SPDX-License-Identifier: MIT
// Opted-in game capture: GPU import/copy only; owned linear BGRA output pool.
#define _GNU_SOURCE
#include "vkcapture-gpu.h"
#include "vkcapture-cursor.h"
#include "vkcapture-sync.h"
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES3/gl3.h>
#include <errno.h>
#include <fcntl.h>
#include <gbm.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

// Extension declarations depend on the core GLES types above.
#include <GLES2/gl2ext.h>

struct dpe_vk_gpu {
  int fd;
  struct gbm_device *gbm;
  EGLDisplay display;
  EGLContext context;
  struct pw_loop *loop;
  uint32_t width, height;
  EGLImageKHR input_image;
  GLuint input_texture;
  GLuint preview_texture, preview_framebuffer;
  uint32_t preview_width, preview_height;
  bool flip;
  uint8_t uuid[16];
  GLuint program;
  GLint flip_uniform, cursor_uniform;
  struct dpe_game_cursor cursor;
  GLuint cursor_texture;
  uint32_t window, cursor_serial;
  bool pending;
  PFNEGLCREATEIMAGEKHRPROC create_image;
  PFNEGLDESTROYIMAGEKHRPROC destroy_image;
  PFNGLEGLIMAGETARGETTEXTURE2DOESPROC image_texture;
  PFNEGLCREATESYNCKHRPROC create_sync;
  PFNEGLDESTROYSYNCKHRPROC destroy_sync;
  PFNEGLDUPNATIVEFENCEFDANDROIDPROC dup_fence;
};
struct dpe_vk_buffer {
  struct dpe_vk_gpu *gpu;
  uint32_t width, height;
  struct gbm_bo *bo;
  int fd;
  EGLImageKHR image;
  GLuint texture, framebuffer;
  struct spa_source *completion;
  void (*complete)(void *, bool);
  void *opaque;
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
static GLuint shader(GLenum kind, const char *source) {
  GLuint value = glCreateShader(kind);
  glShaderSource(value, 1, &source, NULL);
  glCompileShader(value);
  GLint success = 0;
  glGetShaderiv(value, GL_COMPILE_STATUS, &success);
  if (!success) {
    char log[1024];
    glGetShaderInfoLog(value, sizeof(log), NULL, log);
    fprintf(stderr, "DPE game capture shader: %s\n", log);
    glDeleteShader(value);
    return 0;
  }
  return value;
}
static bool program(struct dpe_vk_gpu *gpu) {
  const char *vertex = "#version 300 es\n"
                       "out vec2 uv; void main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);"
                       "uv=p; gl_Position=vec4(p*2.0-1.0,0.0,1.0);}";
  const char *fragment =
      "#version 300 es\nprecision highp float;"
      "uniform sampler2D image; uniform bool flip; uniform bool cursor; in vec2 uv; out vec4 color;"
      "void main(){vec4 "
      "t=texture(image,vec2(uv.x,flip?1.0-uv.y:uv.y));color=cursor?t.bgra:vec4(t.rgb,1.0);}";
  GLuint vert = shader(GL_VERTEX_SHADER, vertex), frag = shader(GL_FRAGMENT_SHADER, fragment);
  if (!vert || !frag) {
    glDeleteShader(vert);
    glDeleteShader(frag);
    return false;
  }
  gpu->program = glCreateProgram();
  glAttachShader(gpu->program, vert);
  glAttachShader(gpu->program, frag);
  glLinkProgram(gpu->program);
  glDeleteShader(vert);
  glDeleteShader(frag);
  GLint success = 0;
  glGetProgramiv(gpu->program, GL_LINK_STATUS, &success);
  gpu->flip_uniform = glGetUniformLocation(gpu->program, "flip");
  gpu->cursor_uniform = glGetUniformLocation(gpu->program, "cursor");
  return success && glGetError() == GL_NO_ERROR;
}

struct dpe_vk_gpu *dpe_vk_gpu_create(const char *node, struct pw_loop *loop, uint32_t width,
                                     uint32_t height) {
  struct dpe_vk_gpu *gpu = calloc(1, sizeof(*gpu));
  if (!gpu)
    return NULL;
  gpu->fd = -1;
  gpu->loop = loop;
  gpu->width = width;
  gpu->height = height;

  gpu->fd = open(node, O_RDWR | O_CLOEXEC | O_NOFOLLOW);
  if (gpu->fd < 0 || !(gpu->gbm = gbm_create_device(gpu->fd)))
    goto fail;
  gpu->display = eglGetPlatformDisplay(EGL_PLATFORM_GBM_KHR, gpu->gbm, NULL);
  if (gpu->display == EGL_NO_DISPLAY || !eglInitialize(gpu->display, NULL, NULL))
    goto fail;
  const char *extensions = eglQueryString(gpu->display, EGL_EXTENSIONS);
  if (!extension(extensions, "EGL_KHR_surfaceless_context") ||
      !extension(extensions, "EGL_KHR_no_config_context") ||
      !extension(extensions, "EGL_EXT_image_dma_buf_import_modifiers") ||
      !extension(extensions, "EGL_ANDROID_native_fence_sync"))
    goto fail;
#define PROC(field, type, name)                                                                    \
  gpu->field = (type)eglGetProcAddress(name);                                                      \
  if (!gpu->field)                                                                                 \
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
  gpu->context = eglCreateContext(gpu->display, EGL_NO_CONFIG_KHR, EGL_NO_CONTEXT, attributes);
  if (gpu->context == EGL_NO_CONTEXT ||
      !eglMakeCurrent(gpu->display, EGL_NO_SURFACE, EGL_NO_SURFACE, gpu->context) || !program(gpu))
    goto fail;
  typedef void (*get_uuid_fn)(GLenum, GLuint, GLubyte *);
  get_uuid_fn get_uuid = (get_uuid_fn)eglGetProcAddress("glGetUnsignedBytei_vEXT");
  if (!extension((const char *)glGetString(GL_EXTENSIONS), "GL_EXT_memory_object") || !get_uuid)
    goto fail;
  get_uuid(0x9597 /* GL_DEVICE_UUID_EXT */, 0, gpu->uuid);
  if (glGetError() != GL_NO_ERROR)
    goto fail;
  return gpu;
fail:
  fprintf(stderr, "DPE game capture GPU setup unsupported/failed (EGL 0x%x)\n", eglGetError());
  dpe_vk_gpu_destroy(gpu);
  return NULL;
}

void dpe_vk_gpu_destroy(struct dpe_vk_gpu *gpu) {
  if (!gpu)
    return;
  dpe_cursor_destroy(&gpu->cursor);
  if (gpu->display != EGL_NO_DISPLAY) {
    glDeleteTextures(1, &gpu->cursor_texture);
    glDeleteTextures(1, &gpu->preview_texture);
    glDeleteFramebuffers(1, &gpu->preview_framebuffer);
    glDeleteTextures(1, &gpu->input_texture);
    if (gpu->input_image != EGL_NO_IMAGE_KHR)
      gpu->destroy_image(gpu->display, gpu->input_image);
    if (gpu->program)
      glDeleteProgram(gpu->program);
    eglMakeCurrent(gpu->display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
    if (gpu->context != EGL_NO_CONTEXT)
      eglDestroyContext(gpu->display, gpu->context);
    eglTerminate(gpu->display);
  }
  if (gpu->gbm)
    gbm_device_destroy(gpu->gbm);
  if (gpu->fd >= 0)
    close(gpu->fd);
  free(gpu);
}

struct dpe_vk_buffer *dpe_vk_buffer_create(struct dpe_vk_gpu *gpu, struct spa_data *data,
                                           uint32_t width, uint32_t height) {
  if (!width || !height || width > 8192 || height > 8192)
    return NULL;
  struct dpe_vk_buffer *frame = calloc(1, sizeof(*frame));
  if (!frame)
    return NULL;
  frame->gpu = gpu;
  frame->width = width;
  frame->height = height;
  frame->fd = -1;
  const uint64_t linear = 0;
  frame->bo = gbm_bo_create_with_modifiers2(gpu->gbm, width, height, GBM_FORMAT_ARGB8888, &linear,
                                            1, GBM_BO_USE_RENDERING);
  if (!frame->bo || gbm_bo_get_modifier(frame->bo) != linear ||
      gbm_bo_get_plane_count(frame->bo) != 1 || gbm_bo_get_offset(frame->bo, 0) != 0)
    goto fail;
  frame->fd = gbm_bo_get_fd_for_plane(frame->bo, 0);
  uint32_t stride = gbm_bo_get_stride_for_plane(frame->bo, 0);
  if (frame->fd < 0 || stride < width * 4 || stride > UINT32_MAX / height)
    goto fail;
  const EGLint attributes[] = {EGL_WIDTH,
                               width,
                               EGL_HEIGHT,
                               height,
                               EGL_LINUX_DRM_FOURCC_EXT,
                               GBM_FORMAT_ARGB8888,
                               EGL_DMA_BUF_PLANE0_FD_EXT,
                               frame->fd,
                               EGL_DMA_BUF_PLANE0_OFFSET_EXT,
                               0,
                               EGL_DMA_BUF_PLANE0_PITCH_EXT,
                               stride,
                               EGL_DMA_BUF_PLANE0_MODIFIER_LO_EXT,
                               0,
                               EGL_DMA_BUF_PLANE0_MODIFIER_HI_EXT,
                               0,
                               EGL_NONE};
  frame->image =
      gpu->create_image(gpu->display, EGL_NO_CONTEXT, EGL_LINUX_DMA_BUF_EXT, NULL, attributes);
  if (frame->image == EGL_NO_IMAGE_KHR)
    goto fail;
  glGenTextures(1, &frame->texture);
  glBindTexture(GL_TEXTURE_2D, frame->texture);
  gpu->image_texture(GL_TEXTURE_2D, frame->image);
  glGenFramebuffers(1, &frame->framebuffer);
  glBindFramebuffer(GL_FRAMEBUFFER, frame->framebuffer);
  glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, frame->texture, 0);
  if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE ||
      glGetError() != GL_NO_ERROR)
    goto fail;
  data->type = SPA_DATA_DmaBuf;
  data->flags = SPA_DATA_FLAG_READWRITE;
  data->fd = frame->fd;
  data->mapoffset = 0;
  data->maxsize = stride * height;
  data->data = NULL;
  *data->chunk = (struct spa_chunk){.size = data->maxsize, .stride = stride};
  return frame;
fail:
  dpe_vk_buffer_destroy(frame);
  return NULL;
}

void dpe_vk_buffer_destroy(struct dpe_vk_buffer *frame) {
  if (!frame)
    return;
  struct dpe_vk_gpu *gpu = frame->gpu;
  if (frame->completion) {
    pw_loop_destroy_source(gpu->loop, frame->completion);
    gpu->pending = false;
  }
  // GL/EGL retain storage while submitted commands use it. Never hand an
  // unfinished frame to PipeWire just to return it during teardown.
  glDeleteFramebuffers(1, &frame->framebuffer);
  glDeleteTextures(1, &frame->texture);
  if (frame->image != EGL_NO_IMAGE_KHR)
    gpu->destroy_image(gpu->display, frame->image);
  if (frame->fd >= 0)
    close(frame->fd);
  if (frame->bo)
    gbm_bo_destroy(frame->bo);
  free(frame);
}

static void ready(void *opaque, int fd, uint32_t mask) {
  bool valid =
      (mask & SPA_IO_IN) && !(mask & (SPA_IO_ERR | SPA_IO_HUP)) && dpe_vk_fence_signaled(fd);
  struct dpe_vk_buffer *frame = opaque;
  pw_loop_destroy_source(frame->gpu->loop, frame->completion);
  frame->completion = NULL;
  frame->gpu->pending = false;
  frame->complete(frame->opaque, valid);
}

static void draw_cursor_image(struct dpe_vk_gpu *gpu, uint32_t width, uint32_t height) {
  const struct spa_meta_cursor *meta = (const void *)gpu->cursor.metadata;
  if (!meta->id)
    return;
  const struct spa_meta_bitmap *bitmap = (const void *)(gpu->cursor.metadata + meta->bitmap_offset);
  if (!gpu->cursor_texture)
    glGenTextures(1, &gpu->cursor_texture);
  glBindTexture(GL_TEXTURE_2D, gpu->cursor_texture);
  if (gpu->cursor_serial != meta->id) {
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, bitmap->size.width, bitmap->size.height, 0, GL_RGBA,
                 GL_UNSIGNED_BYTE, (const uint8_t *)bitmap + bitmap->offset);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    gpu->cursor_serial = meta->id;
  }
  float sx = (float)width / gpu->width, sy = (float)height / gpu->height;
  int x = (meta->position.x - meta->hotspot.x) * sx;
  int y = (meta->position.y - meta->hotspot.y) * sy;
  int w = bitmap->size.width * sx, h = bitmap->size.height * sy;
  if (w < 1 || h < 1)
    return;
  // Exported DMA-BUF rows are consumed top-to-bottom. GL framebuffer row zero
  // is therefore the captured image's top row, unlike a displayed GL surface.
  glViewport(x, y, w, h);
  glUniform1i(gpu->flip_uniform, false);
  glUniform1i(gpu->cursor_uniform, true);
  glEnable(GL_BLEND);
  glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA);
  glDrawArrays(GL_TRIANGLES, 0, 3);
  glDisable(GL_BLEND);
  glUniform1i(gpu->cursor_uniform, false);
}
static void draw_cursor(struct dpe_vk_gpu *gpu, uint32_t width, uint32_t height) {
  if (!gpu->window)
    return;
  dpe_cursor_update(&gpu->cursor, gpu->window, gpu->width, gpu->height);
  draw_cursor_image(gpu, width, height);
}
int dpe_vk_buffer_copy(struct dpe_vk_buffer *frame, void (*complete)(void *, bool), void *opaque) {
  struct dpe_vk_gpu *gpu = frame->gpu;
  if (frame->width != gpu->width || frame->height != gpu->height)
    return -EAGAIN;
  if (gpu->pending || !gpu->input_texture)
    return -EINVAL;
  glBindFramebuffer(GL_FRAMEBUFFER, frame->framebuffer);
  glViewport(0, 0, gpu->width, gpu->height);
  glDisable(GL_DITHER);
  glUseProgram(gpu->program);
  glActiveTexture(GL_TEXTURE0);
  glBindTexture(GL_TEXTURE_2D, gpu->input_texture);
  glUniform1i(gpu->flip_uniform, gpu->flip);
  glUniform1i(gpu->cursor_uniform, false);
  glDrawArrays(GL_TRIANGLES, 0, 3);
  draw_cursor(gpu, gpu->width, gpu->height);
  if (glGetError() != GL_NO_ERROR)
    return -1;
  EGLSyncKHR fence = gpu->create_sync(gpu->display, EGL_SYNC_NATIVE_FENCE_ANDROID, NULL);
  if (fence == EGL_NO_SYNC_KHR)
    return -1;
  glFlush();
  int fd = gpu->dup_fence(gpu->display, fence);
  gpu->destroy_sync(gpu->display, fence);
  if (fd < 0)
    return -1;
  frame->complete = complete;
  frame->opaque = opaque;
  frame->completion =
      pw_loop_add_io(gpu->loop, fd, SPA_IO_IN | SPA_IO_ERR | SPA_IO_HUP, true, ready, frame);
  if (!frame->completion) {
    close(fd);
    return -1;
  }
  gpu->pending = true;
  return 0;
}

const uint8_t *dpe_vk_gpu_uuid(struct dpe_vk_gpu *gpu) { return gpu->uuid; }
int dpe_vk_gpu_fd(struct dpe_vk_gpu *gpu) { return gpu->fd; }
int dpe_vk_gpu_preview(struct dpe_vk_gpu *gpu, uint32_t *width, uint32_t *height, uint8_t *pixels) {
  if (!gpu || gpu->pending || !gpu->input_texture || !pixels || !*width || !*height ||
      *width > 480 || *height > 270)
    return -1;
  if ((uint64_t)*width * gpu->height > (uint64_t)*height * gpu->width)
    *width = (uint64_t)*height * gpu->width / gpu->height;
  else
    *height = (uint64_t)*width * gpu->height / gpu->width;
  if (!*width || !*height)
    return -1;
  if (!gpu->preview_texture) {
    glGenTextures(1, &gpu->preview_texture);
    glGenFramebuffers(1, &gpu->preview_framebuffer);
  }
  glBindTexture(GL_TEXTURE_2D, gpu->preview_texture);
  if (gpu->preview_width != *width || gpu->preview_height != *height) {
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, *width, *height, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
    gpu->preview_width = *width;
    gpu->preview_height = *height;
  }
  glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
  glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
  glBindFramebuffer(GL_FRAMEBUFFER, gpu->preview_framebuffer);
  glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, gpu->preview_texture,
                         0);
  int result = -1;
  if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE)
    goto done;
  glViewport(0, 0, *width, *height);
  glDisable(GL_DITHER);
  glUseProgram(gpu->program);
  glActiveTexture(GL_TEXTURE0);
  glBindTexture(GL_TEXTURE_2D, gpu->input_texture);
  glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
  glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
  glUniform1i(gpu->flip_uniform, gpu->flip);
  glUniform1i(gpu->cursor_uniform, false);
  glDrawArrays(GL_TRIANGLES, 0, 3);
  glPixelStorei(GL_PACK_ALIGNMENT, 1);
  glReadPixels(0, 0, *width, *height, GL_RGBA, GL_UNSIGNED_BYTE, pixels);
  if (glGetError() != GL_NO_ERROR)
    goto done;
  for (size_t i = 0; i < (size_t)*width * *height * 4; i += 4) {
    uint8_t red = pixels[i];
    pixels[i] = pixels[i + 2];
    pixels[i + 2] = red;
    pixels[i + 3] = 255;
  }
  result = 0;
done:
  return result;
}
int dpe_vk_gpu_import(struct dpe_vk_gpu *gpu, const struct dpe_vk_packet *p, const int fds[4]) {
  if (gpu->pending || !p->nfd || p->nfd > 4 || !p->width || !p->height || p->width > 8192 ||
      p->height > 8192 || (p->flags & ~DPE_VK_FLIP))
    return -1;
  if (p->format != GBM_FORMAT_ARGB8888 && p->format != GBM_FORMAT_XRGB8888 &&
      p->format != GBM_FORMAT_ABGR8888 && p->format != GBM_FORMAT_XBGR8888)
    return -1;
  EGLint attrs[64], n = 0;
#define ATTR(k, v)                                                                                 \
  do {                                                                                             \
    attrs[n++] = (k);                                                                              \
    attrs[n++] = (EGLint)(v);                                                                      \
  } while (0)
  ATTR(EGL_WIDTH, p->width);
  ATTR(EGL_HEIGHT, p->height);
  ATTR(EGL_LINUX_DRM_FOURCC_EXT, p->format);
  const EGLint fd_keys[] = {EGL_DMA_BUF_PLANE0_FD_EXT, EGL_DMA_BUF_PLANE1_FD_EXT,
                            EGL_DMA_BUF_PLANE2_FD_EXT, EGL_DMA_BUF_PLANE3_FD_EXT};
  const EGLint lo_keys[] = {EGL_DMA_BUF_PLANE0_MODIFIER_LO_EXT, EGL_DMA_BUF_PLANE1_MODIFIER_LO_EXT,
                            EGL_DMA_BUF_PLANE2_MODIFIER_LO_EXT, EGL_DMA_BUF_PLANE3_MODIFIER_LO_EXT};
  for (unsigned i = 0; i < p->nfd; i++) {
    if (fds[i] < 0 || p->strides[i] == 0 || p->strides[i] > 131072 || p->offsets[i] > INT32_MAX)
      return -1;
    ATTR(fd_keys[i], fds[i]);
    ATTR(fd_keys[i] + 1, p->offsets[i]);
    ATTR(fd_keys[i] + 2, p->strides[i]);
    if (p->modifier != UINT64_C(0x00ffffffffffffff)) {
      ATTR(lo_keys[i], p->modifier);
      ATTR(lo_keys[i] + 1, p->modifier >> 32);
    }
  }
  attrs[n++] = EGL_NONE;
#undef ATTR
  EGLImageKHR image =
      gpu->create_image(gpu->display, EGL_NO_CONTEXT, EGL_LINUX_DMA_BUF_EXT, NULL, attrs);
  if (image == EGL_NO_IMAGE_KHR)
    return -1;
  GLuint texture = 0;
  glGenTextures(1, &texture);
  glBindTexture(GL_TEXTURE_2D, texture);
  gpu->image_texture(GL_TEXTURE_2D, image);
  glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
  glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
  glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
  glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
  if (glGetError() != GL_NO_ERROR) {
    glDeleteTextures(1, &texture);
    gpu->destroy_image(gpu->display, image);
    return -1;
  }
  glDeleteTextures(1, &gpu->input_texture);
  if (gpu->input_image != EGL_NO_IMAGE_KHR)
    gpu->destroy_image(gpu->display, gpu->input_image);
  gpu->input_image = image;
  gpu->input_texture = texture;
  gpu->width = p->width;
  gpu->height = p->height;
  gpu->flip = (p->flags & DPE_VK_FLIP) != 0;
  gpu->window = dpe_vk_window(p);
  return 0;
}
