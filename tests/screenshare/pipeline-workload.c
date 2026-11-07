// Owned, bounded synthetic GL window. Never reads or saves framebuffer pixels.
#define _POSIX_C_SOURCE 200809L
#define GL_GLEXT_PROTOTYPES
#include <GL/gl.h>
#include <GL/glext.h>
#include <SDL3/SDL.h>
#include <errno.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define TITLE "DPE capture pipeline benchmark"
#define APP_ID "org.dpe.CapturePipelineBenchmark"
static volatile sig_atomic_t stopped;
static void stop(int signal_number) {
  (void)signal_number;
  stopped = 1;
}
static void deadline(int signal_number) {
  (void)signal_number;
  _exit(124);
}
static uint64_t now(clockid_t clock) {
  struct timespec value;
  if (clock_gettime(clock, &value))
    _exit(125);
  return (uint64_t)value.tv_sec * 1000000000ull + value.tv_nsec;
}
static uint8_t checksum(uint32_t value) {
  const uint8_t bytes[] = {0xa7, value >> 16, value >> 8, value};
  uint8_t crc = 0;
  for (unsigned i = 0; i < 4; i++) {
    crc ^= bytes[i];
    for (unsigned j = 0; j < 8; j++)
      crc = (crc << 1) ^ ((crc & 128) ? 7 : 0);
  }
  return crc;
}
static GLuint compile(GLenum kind, const char *source) {
  GLuint shader = glCreateShader(kind);
  glShaderSource(shader, 1, &source, NULL);
  glCompileShader(shader);
  GLint okay;
  glGetShaderiv(shader, GL_COMPILE_STATUS, &okay);
  if (!okay) {
    char error[2048];
    glGetShaderInfoLog(shader, sizeof(error), NULL, error);
    fprintf(stderr, "shader: %s\n", error);
    glDeleteShader(shader);
    return 0;
  }
  return shader;
}
static unsigned number(const char *text, unsigned minimum, unsigned maximum) {
  char *end;
  errno = 0;
  unsigned long value = strtoul(text, &end, 10);
  return *text && !*end && !errno && value >= minimum && value <= maximum ? value : 0;
}
int main(int argc, char **argv) {
  bool fullscreen = argc > 3 && !strcmp(argv[argc - 1], "--fullscreen");
  if (fullscreen)
    argc--;
  unsigned seconds = argc > 1 ? number(argv[1], 1, 90) : 0;
  unsigned fps = argc > 2 ? number(argv[2], 60, 4000) : 0;
  unsigned width = argc > 3 ? number(argv[3], 80, 4096) : 1920;
  unsigned height = argc > 4 ? number(argv[4], 64, 4096) : 1080;
  unsigned pattern = argc > 5 && !strcmp(argv[5], "scroll") ? 1 : 0;
  if (argc < 3 || argc > 6 || !seconds || !width || !height ||
      ((fps != 60 && fps != 120 && fps != 240 && fps != 600 && fps != 2000 && fps != 4000) &&
       strcmp(argv[2], "uncapped")) ||
      (argc > 5 && strcmp(argv[5], "scroll") && strcmp(argv[5], "tiles"))) {
    fprintf(
        stderr,
        "usage: %s SECONDS[1..90] 60|120|240|600|2000|4000|uncapped [WIDTH HEIGHT [tiles|scroll]] "
        "[--fullscreen]\n",
        argv[0]);
    return 2;
  }
  struct sigaction action = {.sa_handler = stop};
  sigemptyset(&action.sa_mask);
  sigaction(SIGINT, &action, NULL);
  sigaction(SIGTERM, &action, NULL);
  action.sa_handler = deadline;
  sigaction(SIGALRM, &action, NULL);
  alarm(seconds + 5);
  SDL_SetHint(SDL_HINT_APP_ID, APP_ID);
  SDL_SetHint(SDL_HINT_APP_NAME, TITLE);
  if (!SDL_Init(SDL_INIT_VIDEO)) {
    fprintf(stderr, "SDL: %s\n", SDL_GetError());
    return 3;
  }
  SDL_GL_SetAttribute(SDL_GL_CONTEXT_MAJOR_VERSION, 3);
  SDL_GL_SetAttribute(SDL_GL_CONTEXT_MINOR_VERSION, 3);
  SDL_GL_SetAttribute(SDL_GL_CONTEXT_PROFILE_MASK, SDL_GL_CONTEXT_PROFILE_CORE);
  SDL_GL_SetAttribute(SDL_GL_DOUBLEBUFFER, 1);
  char title[128];
  snprintf(title, sizeof(title), "%s [%ld]", TITLE, (long)getpid());
  SDL_Window *window =
      SDL_CreateWindow(title, width, height,
                       SDL_WINDOW_OPENGL | SDL_WINDOW_BORDERLESS | SDL_WINDOW_NOT_FOCUSABLE |
                           SDL_WINDOW_RESIZABLE | (fullscreen ? SDL_WINDOW_FULLSCREEN : 0));
  SDL_GLContext context = window ? SDL_GL_CreateContext(window) : NULL;
  int result = 3;
  GLuint program = 0, vertex = 0, fragment = 0, vao = 0;
  GLsync fences[2] = {NULL, NULL};
  if (!context) {
    fprintf(stderr, "SDL: %s\n", SDL_GetError());
    goto cleanup;
  }
  if (!SDL_GL_SetSwapInterval(0)) {
    fprintf(stderr, "uncapped swap unavailable: %s\n", SDL_GetError());
    goto cleanup;
  }
  const char *vs = "#version 330 core\nvoid main(){vec2 "
                   "p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);gl_Position=vec4(p*2.0-1.0,0,1);}";
  const char *fs =
      "#version 330 core\nuniform uvec2 size;uniform uint stamp,crc,motion,pattern;out vec4 color;"
      "uint hash(uint x){x^=x>>16;x*=0x7feb352du;x^=x>>15;x*=0x846ca68bu;return x^(x>>16);}"
      "void main(){uvec2 p=uvec2(gl_FragCoord.xy);p.y=size.y-1u-p.y;"
      "if(p.y<size.y*12u/100u){uint bit=p.x*40u/size.x;uint byte=bit/8u;"
      "uint value=byte==0u?167u:byte==4u?crc:(stamp>>((3u-byte)*8u));"
      "uint on=((value>>(7u-bit%8u))&1u)^(p.y>=size.y*6u/100u?1u:0u);color=vec4(vec3(float(on)),1);"
      "}else if(pattern==1u){uvec2 q=(p+uvec2(motion*7u,motion*3u))/4u;uint v=hash(q.x^hash(q.y));"
      "color=vec4(float(v&255u),float((v>>8)&255u),float((v>>16)&255u),255)/255.0;"
      "}else{uint v=(p.x/20u*13u+p.y/20u*7u+motion*11u)&255u;"
      "color=vec4(float((v+130u)&255u),float((v+70u)&255u),float(v),255)/255.0;}}";
  vertex = compile(GL_VERTEX_SHADER, vs);
  fragment = compile(GL_FRAGMENT_SHADER, fs);
  if (!vertex || !fragment)
    goto cleanup;
  program = glCreateProgram();
  glAttachShader(program, vertex);
  glAttachShader(program, fragment);
  glLinkProgram(program);
  GLint okay = 0;
  glGetProgramiv(program, GL_LINK_STATUS, &okay);
  if (!okay) {
    fprintf(stderr, "program link failed\n");
    goto cleanup;
  }
  glGenVertexArrays(1, &vao);
  glBindVertexArray(vao);
  glUseProgram(program);
  GLint size = glGetUniformLocation(program, "size"),
        stamp_uniform = glGetUniformLocation(program, "stamp");
  GLint crc_uniform = glGetUniformLocation(program, "crc"),
        motion = glGetUniformLocation(program, "motion");
  glUniform1ui(glGetUniformLocation(program, "pattern"), pattern);
  printf("{\"event\":\"ready\",\"title\":\"%s\",\"appId\":\"%s\",\"requestedWidth\":%u,"
         "\"requestedHeight\":%u,\"cap\":%u,\"pattern\":\"%s\",\"maxInFlight\":2}\n",
         title, APP_ID, width, height, fps, pattern ? "scroll" : "tiles");
  fflush(stdout);
  uint64_t start = now(CLOCK_MONOTONIC), cpu_start = now(CLOCK_PROCESS_CPUTIME_ID);
  uint64_t end = start + seconds * 1000000000ull, next = start, frames = 0, observed = 0;
  uint64_t waits = 0, max_pending = 0, swap_ns = 0;
  int actual_width = 0, actual_height = 0;
  result = 0;
  while (!stopped && now(CLOCK_MONOTONIC) < end) {
    SDL_Event event;
    while (SDL_PollEvent(&event))
      if (event.type == SDL_EVENT_QUIT)
        stopped = 1;
    if (stopped)
      break;
    unsigned slot = frames % 2;
    if (fences[slot]) {
      GLenum status = glClientWaitSync(fences[slot], 0, 1000000);
      if (status == GL_TIMEOUT_EXPIRED) {
        waits++;
        continue;
      }
      if (status == GL_WAIT_FAILED) {
        result = 4;
        break;
      }
      glDeleteSync(fences[slot]);
      fences[slot] = NULL;
      observed++;
    }
    if (fps && now(CLOCK_MONOTONIC) < next) {
      struct timespec until = {.tv_sec = next / 1000000000ull, .tv_nsec = next % 1000000000ull};
      while (clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &until, NULL) == EINTR && !stopped) {
      }
    }
    uint64_t generated = now(CLOCK_MONOTONIC);
    if (stopped || generated >= end)
      break;
    if (!SDL_GetWindowSizeInPixels(window, &actual_width, &actual_height) || actual_width < 80 ||
        actual_height < 64) {
      result = 4;
      break;
    }
    uint32_t stamp = (generated / 1000000) & 0xffffff;
    glViewport(0, 0, actual_width, actual_height);
    glUniform2ui(size, actual_width, actual_height);
    glUniform1ui(stamp_uniform, stamp);
    glUniform1ui(crc_uniform, checksum(stamp));
    glUniform1ui(motion, (generated - start) / 16666667ull);
    glDrawArrays(GL_TRIANGLES, 0, 3);
    fences[slot] = glFenceSync(GL_SYNC_GPU_COMMANDS_COMPLETE, 0);
    if (!fences[slot] || glGetError() != GL_NO_ERROR) {
      result = 4;
      break;
    }
    glFlush();
    unsigned pending = !!fences[0] + !!fences[1];
    if (pending > max_pending)
      max_pending = pending;
    uint64_t before_swap = now(CLOCK_MONOTONIC);
    if (!SDL_GL_SwapWindow(window)) {
      result = 4;
      break;
    }
    swap_ns += now(CLOCK_MONOTONIC) - before_swap;
    frames++;
    if (fps) {
      next += 1000000000ull / fps;
      if (next < generated)
        next = generated + 1000000000ull / fps;
    }
  }
  for (unsigned i = 0; i < 2; i++)
    if (fences[i]) {
      GLenum status = glClientWaitSync(fences[i], 0, 0);
      if (status == GL_ALREADY_SIGNALED || status == GL_CONDITION_SATISFIED)
        observed++;
    }
  double wall = (now(CLOCK_MONOTONIC) - start) / 1e9;
  double cpu = (now(CLOCK_PROCESS_CPUTIME_ID) - cpu_start) / 1e9;
  printf("{\"event\":\"finished\",\"submittedFrames\":%llu,\"observedCompletedDraws\":%llu,"
         "\"submittedFps\":%.3f,\"wallSeconds\":%.6f,\"cpuSeconds\":%.6f,\"oneCoreCpuPercent\":%."
         "3f,\"swapWallSeconds\":%.6f,\"fencePollTimeouts\":%llu,\"maxObservedPendingDraws\":%llu,"
         "\"actualWidth\":%d,\"actualHeight\":%d,\"status\":%d}\n",
         (unsigned long long)frames, (unsigned long long)observed, frames / wall, wall, cpu,
         cpu / wall * 100, swap_ns / 1e9, (unsigned long long)waits,
         (unsigned long long)max_pending, actual_width, actual_height, result);
  fflush(stdout);
cleanup:
  if (context) {
    for (unsigned i = 0; i < 2; i++)
      if (fences[i])
        glDeleteSync(fences[i]);
    if (vao)
      glDeleteVertexArrays(1, &vao);
    if (program)
      glDeleteProgram(program);
    if (vertex)
      glDeleteShader(vertex);
    if (fragment)
      glDeleteShader(fragment);
    SDL_GL_DestroyContext(context);
  }
  if (window)
    SDL_DestroyWindow(window);
  SDL_Quit();
  alarm(0);
  return result;
}
