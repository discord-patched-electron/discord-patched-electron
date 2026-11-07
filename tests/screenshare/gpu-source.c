// The source renders its own synthetic pixels into a reusable, explicitly
// linear, single-plane BGRA BO. Hardware access requires an explicit render node.
// No CPU pixel mapping/readback, glFinish, host display or portal is used.
// Linear is an interoperability test baseline, not a production modifier policy.
#define _GNU_SOURCE
#include "gpu-source.h"
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES3/gl3.h>
#include <fcntl.h>
#include <gbm.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

// Extension declarations depend on the core GLES types above.
#include <GLES2/gl2ext.h>

struct gpu_source {
  int fd;
  struct gbm_device *gbm;
  EGLDisplay display;
  EGLContext context;
  struct pw_loop *loop;
  uint32_t width, height, pattern;
  GLuint program;
  GLint size_uniform, stamp_uniform, crc_uniform, sequence_uniform, pattern_uniform;
  bool pending;
  PFNEGLCREATEIMAGEKHRPROC create_image;
  PFNEGLDESTROYIMAGEKHRPROC destroy_image;
  PFNGLEGLIMAGETARGETTEXTURE2DOESPROC image_texture;
  PFNEGLCREATESYNCKHRPROC create_sync;
  PFNEGLDESTROYSYNCKHRPROC destroy_sync;
  PFNEGLDUPNATIVEFENCEFDANDROIDPROC dup_fence;
};
struct gpu_frame {
  struct gpu_source *gpu;
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
    fprintf(stderr, "Synthetic GPU shader: %s\n", log);
    glDeleteShader(value);
    return 0;
  }
  return value;
}
static bool program(struct gpu_source *gpu) {
  const char *vertex = "#version 300 es\n"
                       "void main() { vec2 p=vec2((gl_VertexID<<1)&2, gl_VertexID&2);"
                       " gl_Position=vec4(p*2.0-1.0,0.0,1.0); }";
  const char *fragment =
      "#version 300 es\nprecision highp float; precision highp int;\n"
      "uniform uvec2 size; uniform uint stamp, crc, sequence, pattern; out vec4 color;\n"
      "uint hash(uint x) { x^=x>>16; x*=0x7feb352du; x^=x>>15;"
      " x*=0x846ca68bu; return x^(x>>16); }\n"
      "void main() { uvec2 p=uvec2(gl_FragCoord.xy);"
      " if(p.y<size.y*12u/100u) { uint bit=p.x*40u/size.x;"
      " uint byte=bit/8u; uint value=byte==0u?167u:"
      " byte==4u?crc:(stamp>>((3u-byte)*8u));"
      " uint on=((value>>(7u-bit%8u))&1u)^(p.y>=size.y*6u/100u?1u:0u);"
      " color=vec4(vec3(float(on)),1.0); } else if(pattern==3u) {"
      " color=vec4(0.15,0.2,0.25,1.0); } else if(pattern==5u) {"
      " color=p.y>=size.y-4u?vec4(1.0):p.y>=size.y-24u?vec4(0,0,1,1):"
      " p.x<24u?vec4(1,0,0,1):p.x>=size.x-24u?vec4(0,1,0,1):vec4(0.25,0.25,0.25,1);"
      " } else if(pattern!=0u) {"
      // Scroll preserves texture between frames; noise is an explicit worst-case
      // codec stressor. Both are synthetic and keep the timing marker intact.
      " uvec2 q=(pattern==1u||pattern==4u)?"
      " (p+uvec2(sequence*7u,sequence*3u))/(pattern==4u?16u:4u):p;"
      " uint v=hash(q.x^hash(q.y)^(pattern==2u?sequence*0x9e3779b9u:0u));"
      " color=vec4(float(v&255u),float((v>>8)&255u),float((v>>16)&255u),255.0)/255.0;"
      " } else {"
      " uint v=(p.x/20u*13u+p.y/20u*7u+sequence*11u)&255u;"
      " color=vec4(float((v+130u)&255u),float((v+70u)&255u),float(v),255.0)/255.0; } }";
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
  gpu->size_uniform = glGetUniformLocation(gpu->program, "size");
  gpu->stamp_uniform = glGetUniformLocation(gpu->program, "stamp");
  gpu->crc_uniform = glGetUniformLocation(gpu->program, "crc");
  gpu->sequence_uniform = glGetUniformLocation(gpu->program, "sequence");
  gpu->pattern_uniform = glGetUniformLocation(gpu->program, "pattern");
  return success && glGetError() == GL_NO_ERROR;
}

struct gpu_source *gpu_source_create(const char *node, struct pw_loop *loop, uint32_t width,
                                     uint32_t height, unsigned pattern) {
  struct gpu_source *gpu = calloc(1, sizeof(*gpu));
  if (!gpu)
    return NULL;
  gpu->fd = -1;
  gpu->loop = loop;
  gpu->width = width;
  gpu->height = height;
  gpu->pattern = pattern;
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
  fprintf(stderr, "Synthetic DMA-BUF: renderer=%s, modifier=linear, completion=native-fence\n",
          glGetString(GL_RENDERER));
  return gpu;
fail:
  fprintf(stderr, "Synthetic DMA-BUF setup unsupported/failed (EGL 0x%x)\n", eglGetError());
  gpu_source_destroy(gpu);
  return NULL;
}

void gpu_source_set_pattern(struct gpu_source *gpu, unsigned pattern) { gpu->pattern = pattern; }

void gpu_source_destroy(struct gpu_source *gpu) {
  if (!gpu)
    return;
  if (gpu->display != EGL_NO_DISPLAY) {
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

struct gpu_frame *gpu_frame_create(struct gpu_source *gpu, struct spa_data *data) {
  struct gpu_frame *frame = calloc(1, sizeof(*frame));
  if (!frame)
    return NULL;
  frame->gpu = gpu;
  frame->fd = -1;
  const uint64_t linear = 0;
  frame->bo = gbm_bo_create_with_modifiers2(gpu->gbm, gpu->width, gpu->height, GBM_FORMAT_ARGB8888,
                                            &linear, 1, GBM_BO_USE_RENDERING);
  if (!frame->bo || gbm_bo_get_modifier(frame->bo) != linear ||
      gbm_bo_get_plane_count(frame->bo) != 1 || gbm_bo_get_offset(frame->bo, 0) != 0)
    goto fail;
  frame->fd = gbm_bo_get_fd_for_plane(frame->bo, 0);
  uint32_t stride = gbm_bo_get_stride_for_plane(frame->bo, 0);
  if (frame->fd < 0 || stride < gpu->width * 4 || stride > UINT32_MAX / gpu->height)
    goto fail;
  const EGLint attributes[] = {EGL_WIDTH,
                               gpu->width,
                               EGL_HEIGHT,
                               gpu->height,
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
  data->maxsize = stride * gpu->height;
  data->data = NULL;
  *data->chunk = (struct spa_chunk){.size = data->maxsize, .stride = stride};
  return frame;
fail:
  gpu_frame_destroy(frame);
  return NULL;
}

void gpu_frame_destroy(struct gpu_frame *frame) {
  if (!frame)
    return;
  struct gpu_source *gpu = frame->gpu;
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
  (void)fd;
  struct gpu_frame *frame = opaque;
  pw_loop_destroy_source(frame->gpu->loop, frame->completion);
  frame->completion = NULL;
  frame->gpu->pending = false;
  frame->complete(frame->opaque, (mask & SPA_IO_IN) && !(mask & (SPA_IO_ERR | SPA_IO_HUP)));
}

int gpu_frame_render(struct gpu_frame *frame, uint32_t stamp, uint8_t crc, uint32_t sequence,
                     void (*complete)(void *, bool), void *opaque) {
  struct gpu_source *gpu = frame->gpu;
  if (gpu->pending)
    return -1;
  glBindFramebuffer(GL_FRAMEBUFFER, frame->framebuffer);
  glViewport(0, 0, gpu->width, gpu->height);
  glDisable(GL_DITHER);
  glUseProgram(gpu->program);
  glUniform2ui(gpu->size_uniform, gpu->width, gpu->height);
  glUniform1ui(gpu->stamp_uniform, stamp);
  glUniform1ui(gpu->crc_uniform, crc);
  glUniform1ui(gpu->sequence_uniform, sequence);
  glUniform1ui(gpu->pattern_uniform, gpu->pattern == 6   ? ((sequence / 120) % 2 ? 2 : 3)
                                     : gpu->pattern == 7 ? ((sequence / 120) % 2 ? 4 : 3)
                                                         : gpu->pattern);
  glDrawArrays(GL_TRIANGLES, 0, 3);
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
