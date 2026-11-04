// Bounded synthetic contention for the isolated screenshare fixture.
// No display, account, capture pixels, filesystem writes or clock changes.
#define _GNU_SOURCE
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES3/gl31.h>
#include <fcntl.h>
#include <gbm.h>
#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static double now(void) {
  struct timespec t;
  clock_gettime(CLOCK_MONOTONIC, &t);
  return t.tv_sec + t.tv_nsec / 1e9;
}

// A separate submission-heavy workload: short desktop-GL draws and frequent
// frame flushes, rather than the long compute dispatch used by "gpu" below.
// This isolates driver/submission contention; it does not model window swaps,
// Java/game logic or a compositor's presentation path.
static int gl_pressure(int argc, char **argv, double seconds) {
  if (argc < 4 || argc > 6)
    return 2;
  bool capped = argc >= 5 && !strcmp(argv[4], "240");
  if (argc >= 5 && !capped && strcmp(argv[4], "uncapped"))
    return 2;
  unsigned long draws = 64;
  if (argc == 6) {
    char *end;
    draws = strtoul(argv[5], &end, 10);
    if (!*argv[5] || *end || draws < 1 || draws > 512)
      return 2;
  }
  int fd = open(argv[3], O_RDWR | O_CLOEXEC | O_NOFOLLOW);
  struct gbm_device *gbm = fd < 0 ? NULL : gbm_create_device(fd);
  if (!gbm)
    return 3;
  EGLDisplay display = eglGetPlatformDisplay(EGL_PLATFORM_GBM_KHR, gbm, NULL);
  if (display == EGL_NO_DISPLAY || !eglInitialize(display, NULL, NULL) ||
      !eglBindAPI(EGL_OPENGL_API))
    return 3;
  const EGLint attributes[] = {EGL_CONTEXT_MAJOR_VERSION,
                               3,
                               EGL_CONTEXT_MINOR_VERSION,
                               3,
                               EGL_CONTEXT_OPENGL_PROFILE_MASK,
                               EGL_CONTEXT_OPENGL_CORE_PROFILE_BIT,
                               EGL_NONE};
  EGLContext context = eglCreateContext(display, EGL_NO_CONFIG_KHR, EGL_NO_CONTEXT, attributes);
  if (context == EGL_NO_CONTEXT ||
      !eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, context))
    return 3;
  // Resolve against the desktop context. Do not rely on GLES library exports
  // selecting desktop entrypoints merely because the function names coincide.
#define GL_PROC(name)                                                                              \
  __typeof__(&name) p_##name = (__typeof__(&name))eglGetProcAddress(#name);                        \
  if (!p_##name)                                                                                   \
  return 3
  GL_PROC(glGetString);
  GL_PROC(glGetError);
  GL_PROC(glCreateShader);
  GL_PROC(glShaderSource);
  GL_PROC(glCompileShader);
  GL_PROC(glGetShaderiv);
  GL_PROC(glDeleteShader);
  GL_PROC(glCreateProgram);
  GL_PROC(glAttachShader);
  GL_PROC(glLinkProgram);
  GL_PROC(glGetProgramiv);
  GL_PROC(glUseProgram);
  GL_PROC(glDeleteProgram);
  GL_PROC(glGenVertexArrays);
  GL_PROC(glBindVertexArray);
  GL_PROC(glDeleteVertexArrays);
  GL_PROC(glGenBuffers);
  GL_PROC(glBindBuffer);
  GL_PROC(glBufferData);
  GL_PROC(glDeleteBuffers);
  GL_PROC(glEnableVertexAttribArray);
  GL_PROC(glVertexAttribPointer);
  GL_PROC(glGenTextures);
  GL_PROC(glBindTexture);
  GL_PROC(glTexImage2D);
  GL_PROC(glTexParameteri);
  GL_PROC(glDeleteTextures);
  GL_PROC(glGenFramebuffers);
  GL_PROC(glBindFramebuffer);
  GL_PROC(glFramebufferTexture2D);
  GL_PROC(glCheckFramebufferStatus);
  GL_PROC(glDeleteFramebuffers);
  GL_PROC(glGenRenderbuffers);
  GL_PROC(glBindRenderbuffer);
  GL_PROC(glRenderbufferStorage);
  GL_PROC(glFramebufferRenderbuffer);
  GL_PROC(glDeleteRenderbuffers);
  GL_PROC(glViewport);
  GL_PROC(glEnable);
  GL_PROC(glClearColor);
  GL_PROC(glClear);
  GL_PROC(glGetUniformLocation);
  GL_PROC(glUniform1f);
  GL_PROC(glUniform2f);
  GL_PROC(glDrawElements);
  GL_PROC(glFenceSync);
  GL_PROC(glClientWaitSync);
  GL_PROC(glDeleteSync);
  GL_PROC(glFlush);
#undef GL_PROC
  const char *sources[] = {
      "#version 330 core\nlayout(location=0) in vec3 position;"
      "layout(location=1) in vec2 uv; uniform vec2 offset; uniform float phase;"
      "out vec2 texcoord; void main(){ float c=cos(phase),s=sin(phase);"
      "vec3 p=vec3(c*position.x+s*position.z,position.y,-s*position.x+c*position.z);"
      "gl_Position=vec4(p.xy*0.11+offset,p.z*0.2,1.0);texcoord=uv;}",
      "#version 330 core\nin vec2 texcoord; uniform sampler2D pixels;out vec4 color;"
      "void main(){color=texture(pixels,texcoord);}",
  };
  GLuint program = p_glCreateProgram();
  for (unsigned i = 0; i < 2; i++) {
    GLuint shader = p_glCreateShader(i ? GL_FRAGMENT_SHADER : GL_VERTEX_SHADER);
    p_glShaderSource(shader, 1, &sources[i], NULL);
    p_glCompileShader(shader);
    GLint ok;
    p_glGetShaderiv(shader, GL_COMPILE_STATUS, &ok);
    if (!ok)
      return 3;
    p_glAttachShader(program, shader);
    p_glDeleteShader(shader);
  }
  p_glLinkProgram(program);
  GLint ok;
  p_glGetProgramiv(program, GL_LINK_STATUS, &ok);
  if (!ok)
    return 3;
  const float vertices[] = {-1, -1, -1, 0, 0, 1, -1, -1, 1, 0, 1, 1, -1, 1, 1, -1, 1, -1, 0, 1,
                            -1, -1, 1,  0, 0, 1, -1, 1,  1, 0, 1, 1, 1,  1, 1, -1, 1, 1,  0, 1};
  const unsigned short indices[] = {0, 1, 2, 2, 3, 0, 4, 6, 5, 6, 4, 7, 0, 4, 5, 5, 1, 0,
                                    3, 2, 6, 6, 7, 3, 0, 3, 7, 7, 4, 0, 1, 5, 6, 6, 2, 1};
  GLuint vao, buffers[2], textures[2], framebuffer, depth;
  p_glGenVertexArrays(1, &vao);
  p_glBindVertexArray(vao);
  p_glGenBuffers(2, buffers);
  p_glBindBuffer(GL_ARRAY_BUFFER, buffers[0]);
  p_glBufferData(GL_ARRAY_BUFFER, sizeof(vertices), vertices, GL_STATIC_DRAW);
  p_glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, buffers[1]);
  p_glBufferData(GL_ELEMENT_ARRAY_BUFFER, sizeof(indices), indices, GL_STATIC_DRAW);
  p_glEnableVertexAttribArray(0);
  p_glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, 5 * sizeof(float), NULL);
  p_glEnableVertexAttribArray(1);
  p_glVertexAttribPointer(1, 2, GL_FLOAT, GL_FALSE, 5 * sizeof(float), (void *)(3 * sizeof(float)));
  p_glGenTextures(2, textures);
  p_glBindTexture(GL_TEXTURE_2D, textures[0]);
  p_glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, 1280, 720, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
  p_glGenRenderbuffers(1, &depth);
  p_glBindRenderbuffer(GL_RENDERBUFFER, depth);
  p_glRenderbufferStorage(GL_RENDERBUFFER, GL_DEPTH_COMPONENT24, 1280, 720);
  p_glGenFramebuffers(1, &framebuffer);
  p_glBindFramebuffer(GL_FRAMEBUFFER, framebuffer);
  p_glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, textures[0], 0);
  p_glFramebufferRenderbuffer(GL_FRAMEBUFFER, GL_DEPTH_ATTACHMENT, GL_RENDERBUFFER, depth);
  if (p_glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE)
    return 3;
  uint8_t pixels[8 * 8 * 4];
  for (unsigned i = 0; i < sizeof(pixels); i++)
    pixels[i] = i % 4 == 3 ? 255 : (uint8_t)(37 * i);
  p_glBindTexture(GL_TEXTURE_2D, textures[1]);
  p_glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, 8, 8, 0, GL_RGBA, GL_UNSIGNED_BYTE, pixels);
  p_glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
  p_glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
  p_glUseProgram(program);
  GLint offset = p_glGetUniformLocation(program, "offset");
  GLint phase = p_glGetUniformLocation(program, "phase");
  p_glViewport(0, 0, 1280, 720);
  p_glEnable(GL_DEPTH_TEST);
  p_glClearColor(0.1f, 0.2f, 0.3f, 1.0f);
  if (offset < 0 || phase < 0 || p_glGetError() != GL_NO_ERROR)
    return 3;
  printf("ready mode=gl renderer=%s version=%s cap=%s draws=%lu size=1280x720 max_inflight=2\n",
         p_glGetString(GL_RENDERER), p_glGetString(GL_VERSION), capped ? "240" : "uncapped", draws);
  fflush(stdout);
  GLsync fences[2] = {NULL, NULL};
  // CPU-observed fence retirement includes polling and cap sleep; this is not
  // a GPU timestamp query or a measurement of GPU execution time.
  double submitted_at[2] = {0, 0}, completion_total = 0, completion_max = 0;
  uint64_t submitted = 0, completed = 0;
  unsigned head = 0, pending = 0, maximum_pending = 0;
  double start = now(), deadline = start + seconds, next = start, active_end = start;
  while (now() < deadline || pending) {
    double time = now();
    // Retire in submission order. Only block when the two-frame queue is full
    // or draining; a 100us timeout does not impose a 1000FPS timer ceiling.
    if (pending) {
      GLenum status =
          p_glClientWaitSync(fences[head], 0, pending == 2 || time >= deadline ? 100000 : 0);
      if (status == GL_WAIT_FAILED || now() > deadline + 2)
        return 3;
      if (status == GL_ALREADY_SIGNALED || status == GL_CONDITION_SATISFIED) {
        double elapsed = now() - submitted_at[head];
        completion_total += elapsed;
        if (elapsed > completion_max)
          completion_max = elapsed;
        p_glDeleteSync(fences[head]);
        head = (head + 1) % 2;
        pending--;
        completed++;
      }
    }
    time = now();
    if (time >= deadline || pending == 2)
      continue;
    if (capped && time < next) {
      double wake = next < deadline ? next : deadline;
      struct timespec sleep_until = {.tv_sec = (time_t)wake,
                                     .tv_nsec = (long)((wake - (time_t)wake) * 1e9)};
      clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &sleep_until, NULL);
      continue;
    }
    unsigned slot = (head + pending) % 2;
    submitted_at[slot] = time;
    p_glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT);
    p_glUniform1f(phase, (float)(submitted % 10000) * 0.001f);
    for (unsigned long i = 0; i < draws; i++) {
      p_glUniform2f(offset, (float)(i % 8) * 0.25f - 0.875f, (float)((i / 8) % 8) * 0.25f - 0.875f);
      p_glDrawElements(GL_TRIANGLES, 36, GL_UNSIGNED_SHORT, NULL);
    }
    fences[slot] = p_glFenceSync(GL_SYNC_GPU_COMMANDS_COMPLETE, 0);
    p_glFlush();
    if (!fences[slot] || p_glGetError() != GL_NO_ERROR)
      return 3;
    submitted++;
    pending++;
    if (pending > maximum_pending)
      maximum_pending = pending;
    active_end = now();
    next = time + 1.0 / 240;
  }
  double finished = now(), active_seconds = active_end - start;
  p_glDeleteFramebuffers(1, &framebuffer);
  p_glDeleteRenderbuffers(1, &depth);
  p_glDeleteTextures(2, textures);
  p_glDeleteBuffers(2, buffers);
  p_glDeleteVertexArrays(1, &vao);
  p_glDeleteProgram(program);
  eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
  eglDestroyContext(display, context);
  eglTerminate(display);
  gbm_device_destroy(gbm);
  close(fd);
  alarm(0);
  printf("completed batches=%llu mode=gl submitted=%llu completed=%llu active_seconds=%.6f "
         "wall_seconds=%.6f submitted_fps=%.1f completed_fps=%.1f max_inflight=%u "
         "fence_observed_us_mean=%.1f fence_observed_us_max=%.1f\n",
         (unsigned long long)completed, (unsigned long long)submitted,
         (unsigned long long)completed, active_seconds, finished - start,
         active_seconds > 0 ? submitted / active_seconds : 0,
         finished > start ? completed / (finished - start) : 0, maximum_pending,
         completed ? completion_total * 1e6 / completed : 0, completion_max * 1e6);
  return submitted && completed == submitted ? 0 : 3;
}

int main(int argc, char **argv) {
  if (argc < 3)
    return 2;
  char *end;
  double seconds = strtod(argv[2], &end);
  if (*end || !isfinite(seconds) || seconds <= 0 || seconds > 15)
    return 2;
  // Also bound initialization and driver waits if the Python supervisor exits.
  // Default SIGALRM terminates this private worker; no load survives indefinitely.
  alarm((unsigned)seconds + 6);
  if (!strcmp(argv[1], "gl"))
    return gl_pressure(argc, argv, seconds);
  uint64_t batches = 0;
  double dispatch_total = 0, dispatch_max = 0;
  if (!strcmp(argv[1], "cpu") && argc == 3) {
    double deadline = now() + seconds;
    volatile uint64_t value = 1;
    puts("ready");
    fflush(stdout);
    do {
      for (unsigned i = 0; i < 100000; i++)
        value = value * UINT64_C(6364136223846793005) + 1;
      batches++;
    } while (now() < deadline);
  } else if (!strcmp(argv[1], "gpu") && argc == 4) {
    int fd = open(argv[3], O_RDWR | O_CLOEXEC | O_NOFOLLOW);
    struct gbm_device *gbm = fd < 0 ? NULL : gbm_create_device(fd);
    if (!gbm)
      return 3;
    EGLDisplay display = eglGetPlatformDisplay(EGL_PLATFORM_GBM_KHR, gbm, NULL);
    if (display == EGL_NO_DISPLAY || !eglInitialize(display, NULL, NULL) ||
        !eglBindAPI(EGL_OPENGL_ES_API))
      return 3;
    const EGLint attributes[] = {EGL_CONTEXT_MAJOR_VERSION, 3, EGL_CONTEXT_MINOR_VERSION, 1,
                                 EGL_NONE};
    EGLContext context = eglCreateContext(display, EGL_NO_CONFIG_KHR, EGL_NO_CONTEXT, attributes);
    if (context == EGL_NO_CONTEXT ||
        !eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, context))
      return 3;
    const char *source = "#version 310 es\nlayout(local_size_x=16,local_size_y=16) in;\n"
                         "layout(std430,binding=0) buffer Data { uint values[]; };\n"
                         "uniform uint seed; void main() { uint i=gl_GlobalInvocationID.y*1024u+"
                         "gl_GlobalInvocationID.x; uint v=i+seed; for(uint j=0u;j<4096u;j++) {"
                         "v^=v<<13; v^=v>>17; v^=v<<5; } values[i]=v; }";
    GLuint shader = glCreateShader(GL_COMPUTE_SHADER);
    glShaderSource(shader, 1, &source, NULL);
    glCompileShader(shader);
    GLint ok;
    glGetShaderiv(shader, GL_COMPILE_STATUS, &ok);
    if (!ok)
      return 3;
    GLuint program = glCreateProgram();
    glAttachShader(program, shader);
    glLinkProgram(program);
    glGetProgramiv(program, GL_LINK_STATUS, &ok);
    if (!ok)
      return 3;
    GLuint buffer;
    glGenBuffers(1, &buffer);
    glBindBuffer(GL_SHADER_STORAGE_BUFFER, buffer);
    glBufferData(GL_SHADER_STORAGE_BUFFER, 1024 * 1024 * 4, NULL, GL_DYNAMIC_DRAW);
    glBindBufferBase(GL_SHADER_STORAGE_BUFFER, 0, buffer);
    glUseProgram(program);
    GLint seed = glGetUniformLocation(program, "seed");
    if (glGetError() != GL_NO_ERROR || seed < 0)
      return 3;
    printf("ready renderer=%s iterations=4096 invocations=1048576\n", glGetString(GL_RENDERER));
    fflush(stdout);
    double deadline = now() + seconds;
    do {
      double submitted = now();
      glUniform1ui(seed, (GLuint)batches);
      glDispatchCompute(64, 64, 1);
      glMemoryBarrier(GL_SHADER_STORAGE_BARRIER_BIT);
      GLsync fence = glFenceSync(GL_SYNC_GPU_COMMANDS_COMPLETE, 0);
      glFlush();
      if (!fence || glGetError() != GL_NO_ERROR)
        return 3;
      // One bounded dispatch in flight. More arithmetic amortizes submission
      // gaps without accumulating a queue of GPU work after the deadline.
      GLenum result;
      do {
        result = glClientWaitSync(fence, 0, 1000000);
        if (result == GL_WAIT_FAILED || now() > deadline + 2)
          return 3;
      } while (result == GL_TIMEOUT_EXPIRED);
      glDeleteSync(fence);
      double elapsed = now() - submitted;
      dispatch_total += elapsed;
      if (elapsed > dispatch_max)
        dispatch_max = elapsed;
      batches++;
    } while (now() < deadline);
    glDeleteBuffers(1, &buffer);
    glDeleteProgram(program);
    glDeleteShader(shader);
    eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
    eglDestroyContext(display, context);
    eglTerminate(display);
    gbm_device_destroy(gbm);
    close(fd);
  } else {
    return 2;
  }
  alarm(0);
  printf("completed batches=%llu dispatch_us_mean=%.1f dispatch_us_max=%.1f\n",
         (unsigned long long)batches, batches ? dispatch_total * 1e6 / batches : 0,
         dispatch_max * 1e6);
  return batches ? 0 : 3;
}
