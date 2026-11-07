// Optional real render-node ownership/fence test. No desktop or game is opened;
// only this fixture's cleared GPU buffers are read back into transient memory.
#define _GNU_SOURCE
#include "../../integration/video/vkcapture-gpu.h"
#include <GLES3/gl3.h>
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <pthread.h>
#include <stdio.h>
#include <string.h>
static struct {
  struct spa_source source;
  spa_source_io_func_t callback;
  void *data;
  int fd;
  bool active;
} watch;
static unsigned adds, removes, completions;
static bool fail_watch, retire_during_add, complete_during_add, retire_on_complete;
static struct dpe_vk_gpu *gpu;
static struct dpe_vk_buffer *output;
static void ready(void) {
  struct pollfd event = {.fd = watch.fd, .events = POLLIN};
  assert(poll(&event, 1, 2000) == 1 && event.revents & POLLIN);
  watch.callback(watch.data, watch.fd, SPA_IO_IN);
}
static struct spa_source *add(void *object, int fd, uint32_t mask, bool close,
                              spa_source_io_func_t callback, void *data) {
  (void)object;
  assert(!watch.active && !close && mask == (SPA_IO_IN | SPA_IO_ERR | SPA_IO_HUP));
  if (fail_watch)
    return NULL;
  adds++;
  watch.callback = callback;
  watch.data = data;
  watch.fd = fd;
  watch.active = true;
  if (retire_during_add) {
    retire_during_add = false;
    dpe_vk_buffer_destroy(output);
    output = NULL;
  }
  if (complete_during_add) {
    complete_during_add = false;
    ready();
  }
  return &watch.source;
}
static void remove_watch(void *object, struct spa_source *source) {
  (void)object;
  assert(source == &watch.source && watch.active);
  assert(fcntl(watch.fd, F_GETFD) >= 0); // source removed before descriptor closes
  watch.active = false;
  removes++;
}
static const struct spa_loop_utils_methods methods = {
    .version = SPA_VERSION_LOOP_UTILS_METHODS, .add_io = add, .destroy_source = remove_watch};
static struct spa_loop_utils utils = {
    .iface =
        SPA_INTERFACE_INIT(SPA_TYPE_INTERFACE_LoopUtils, SPA_VERSION_LOOP_UTILS, &methods, NULL)};
static struct pw_loop loop = {.utils = &utils};
static void complete(void *data, bool success) {
  assert(data == &completions && success);
  completions++;
  if (retire_on_complete) {
    assert(!watch.active);
    dpe_vk_buffer_destroy(output);
    output = NULL;
    dpe_vk_gpu_destroy(gpu);
    gpu = NULL;
  }
}
static struct dpe_vk_buffer *make(struct spa_data *data, struct spa_chunk *chunk) {
  *data = (struct spa_data){.chunk = chunk};
  struct dpe_vk_buffer *buffer = dpe_vk_buffer_create(gpu, data, 64, 32);
  assert(buffer && data->fd >= 0 && data->type == SPA_DATA_DmaBuf && chunk->stride >= 256);
  return buffer;
}
static void *foreign(void *data) {
  assert(dpe_vk_gpu_fd(data) == -1 && dpe_vk_gpu_uuid(data) == NULL);
  dpe_vk_gpu_destroy(data);
  dpe_vk_buffer_destroy(output);
  return NULL;
}
int main(int argc, char **argv) {
  assert(dpe_vk_gpu_fd(NULL) == -1 && !dpe_vk_gpu_uuid((void *)1));
  assert(!dpe_vk_gpu_create(NULL, NULL, 1, 1));
  assert(!dpe_vk_buffer_create(NULL, NULL, 64, 32));
  assert(dpe_vk_buffer_copy(NULL, complete, NULL) == -EINVAL);
  dpe_vk_gpu_destroy((void *)1);
  dpe_vk_buffer_destroy((void *)1);
  if (argc == 1) {
    puts("game GPU ABI rejects stale handles without GPU access");
    return 0;
  }
  assert(argc == 2);
  gpu = dpe_vk_gpu_create(argv[1], &loop, 64, 32);
  assert(gpu);
  struct spa_chunk input_chunk, output_chunk;
  struct spa_data input_data, output_data;
  struct dpe_vk_buffer *input = make(&input_data, &input_chunk);
  glClearColor(0.25f, 0.5f, 0.75f, 1);
  glClear(GL_COLOR_BUFFER_BIT);
  glFlush();
  struct dpe_vk_packet packet = dpe_vk_packet(DPE_VK_TEXTURE);
  packet.width = 64;
  packet.height = 32;
  packet.format = 0x34325241;
  packet.nfd = 1;
  packet.strides[0] = input_chunk.stride;
  int fds[4] = {input_data.fd, -1, -1, -1};
  assert(dpe_vk_gpu_import(gpu, &packet, fds) == 0);
  uint8_t pixels[64 * 32 * 4];
  uint32_t w = 64, h = 32;
  assert(dpe_vk_gpu_preview(gpu, &w, &h, pixels) == 0 && w == 64 && h == 32);
  assert(pixels[0] >= 190 && pixels[0] <= 192 && pixels[1] == 128 && pixels[2] == 64 &&
         pixels[3] == 255);
  output = make(&output_data, &output_chunk);
  pthread_t thread;
  assert(!pthread_create(&thread, NULL, foreign, gpu));
  assert(!pthread_join(thread, NULL));
  assert(dpe_vk_gpu_fd(gpu) >= 0);
  for (unsigned i = 0; i < 20; i++) {
    assert(!dpe_vk_buffer_copy(output, complete, &completions));
    ready();
    assert(!watch.active);
  }
  assert(completions == 20 && adds == removes);
  fail_watch = true;
  assert(dpe_vk_buffer_copy(output, complete, &completions) < 0);
  fail_watch = false;
  complete_during_add = true;
  assert(!dpe_vk_buffer_copy(output, complete, &completions));
  assert(completions == 21 && adds == removes);
  retire_during_add = true;
  assert(!dpe_vk_buffer_copy(output, complete, &completions));
  assert(!output && completions == 21 && adds == removes);
  output = make(&output_data, &output_chunk);
  assert(!dpe_vk_buffer_copy(output, complete, &completions));
  spa_source_io_func_t stale = watch.callback;
  void *stale_data = watch.data;
  int stale_fd = watch.fd;
  dpe_vk_buffer_destroy(output);
  output = NULL;
  stale(stale_data, stale_fd, SPA_IO_IN);
  assert(completions == 21 && adds == removes);
  output = make(&output_data, &output_chunk);
  retire_on_complete = true;
  assert(!dpe_vk_buffer_copy(output, complete, &completions));
  ready();
  assert(!gpu && !output && completions == 22 && adds == removes);
  // This remaining buffer retains its GBM device, export and GL context.
  assert(fcntl(input_data.fd, F_GETFD) >= 0);
  dpe_vk_buffer_destroy(input);
  assert(fcntl(input_data.fd, F_GETFD) == -1 && errno == EBADF);
  puts("game Rust GPU: pixel format, fences, reentry, cancellation, stale callbacks and retained "
       "contexts passed");
}
