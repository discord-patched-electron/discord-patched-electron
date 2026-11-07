#define _GNU_SOURCE
#include "../../integration/video/direct-capture-gpu.h"
#include "../../integration/video/direct-capture-wire.h"
#include "../../integration/video/direct-capture.h"
#include <assert.h>
#include <poll.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/socket.h>
#include <unistd.h>
struct config {
  int32_t fd;
  uint32_t node;
};
struct options {
  uint32_t source, fps, width, height;
  void (*handler)(void *, uint32_t, const char *, void *);
  void *data;
  const struct config *config;
};
static atomic_uint errors;
bool dpe_direct_capture_ready(void) { return true; }
struct dpe_direct_gpu *dpe_direct_gpu_create(const char *path) {
  (void)path;
  abort();
}
void dpe_direct_gpu_destroy(struct dpe_direct_gpu *gpu) { assert(!gpu); }
void *dpe_direct_gpu_convert(struct dpe_direct_gpu *gpu, const struct dpe_direct_packet *p,
                             const int fds[DPE_DIRECT_FDS], unsigned w, unsigned h) {
  (void)gpu;
  (void)p;
  (void)fds;
  (void)w;
  (void)h;
  abort();
}
bool dpe_direct_frame_destroy(void *frame) {
  (void)frame;
  abort();
}
static void callback(void *frame, uint32_t status, const char *error, void *data) {
  assert(!frame && status == 1 && error);
  if (data) {
    void *capture = *(void **)data;
    int32_t result = -1;
    assert(dpe_direct_capture_destroy(capture, &result) && !result);
    assert(!dpe_direct_capture_destroy(capture, &result));
  }
  atomic_fetch_add(&errors, 1);
}
static struct dpe_direct_packet receive(int fd) {
  struct pollfd wait = {.fd = fd, .events = POLLIN};
  assert(poll(&wait, 1, 1000) == 1);
  struct dpe_direct_packet p;
  int fds[DPE_DIRECT_FDS];
  unsigned count;
  assert(dpe_direct_receive(fd, &p, fds, &count) == 1 && !count);
  return p;
}
int main(void) {
  for (unsigned i = 0; i < 30; ++i) {
    int pair[2];
    assert(!socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, pair));
    struct config config = {.fd = pair[0], .node = DPE_DIRECT_NODE};
    struct options o = {
        .fps = 60, .width = 1920, .height = 1080, .handler = callback, .config = &config};
    void *capture = NULL;
    assert(dpe_direct_capture_new(&o, &capture) == 0 && capture);
    struct dpe_direct_packet p = receive(pair[1]);
    assert(p.type == DPE_DIRECT_START && p.fps == 60);
    for (unsigned j = 0; j < 4; ++j) {
      o.fps = (unsigned[]){0, 30, 60, 0}[j];
      int32_t result = -1;
      assert(dpe_direct_capture_update(capture, &o, &result) && !result);
      p = receive(pair[1]);
      assert(p.type == DPE_DIRECT_RATE && p.fps == o.fps);
    }
    int32_t result;
    if (i & 1) {
      unsigned before = atomic_load(&errors);
      close(pair[1]);
      for (unsigned j = 0; j < 1000 && atomic_load(&errors) == before; ++j)
        usleep(1000);
      assert(atomic_load(&errors) == before + 1);
    } else {
      p = dpe_direct_packet(DPE_DIRECT_FRAME); // Missing generation/sequence fails closed.
      assert(dpe_direct_send(pair[1], &p, NULL, 0));
      unsigned expected = i + 1;
      for (unsigned j = 0; j < 1000 && atomic_load(&errors) < expected; ++j)
        usleep(1000);
      assert(atomic_load(&errors) == expected);
      close(pair[1]);
    }
    assert(dpe_direct_capture_destroy(capture, &result) && !result);
    assert(!dpe_direct_capture_destroy(capture, &result));
    uint64_t values[5];
    dpe_direct_capture_stats(values);
    assert(!values[0] && !values[1]);
  }
  for (unsigned i = 0; i < 10; ++i) {
    int pair[2];
    assert(!socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, pair));
    struct config config = {.fd = pair[0], .node = DPE_DIRECT_NODE};
    void *capture = NULL;
    struct options o = {.fps = 60, .handler = callback, .data = &capture, .config = &config};
    unsigned before = atomic_load(&errors);
    assert(dpe_direct_capture_new(&o, &capture) == 0 && capture);
    assert(receive(pair[1]).type == DPE_DIRECT_START);
    close(pair[1]); // The error callback destroys its own capture reentrantly.
    uint64_t values[5] = {1};
    for (unsigned j = 0; j < 1000; ++j) {
      dpe_direct_capture_stats(values);
      if (!values[0] && atomic_load(&errors) == before + 1)
        break;
      usleep(1000);
    }
    assert(!values[0] && atomic_load(&errors) == before + 1);
    int32_t result;
    assert(!dpe_direct_capture_destroy(capture, &result));
  }
  puts("direct worker pause/rate/source-loss/cleanup passed");
}
