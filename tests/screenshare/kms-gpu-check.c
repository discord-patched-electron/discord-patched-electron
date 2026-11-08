// SPDX-License-Identifier: GPL-3.0-only
// Cross-language stale-handle guards; an explicit render node also exercises
// real GBM/EGL ownership without capturing a monitor or mapping any pixels.
#define _GNU_SOURCE
#include "../../integration/video/kms-gpu.h"
#include <GLES3/gl3.h>
#include <assert.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdio.h>

static bool fail_framebuffer;
GLenum __real_glCheckFramebufferStatus(GLenum);
GLenum __wrap_glCheckFramebufferStatus(GLenum target) {
  return fail_framebuffer ? GL_FRAMEBUFFER_UNSUPPORTED : __real_glCheckFramebufferStatus(target);
}
static unsigned descriptors(void) {
  DIR *directory = opendir("/proc/self/fd");
  assert(directory);
  unsigned count = 0;
  struct dirent *entry;
  while ((entry = readdir(directory)))
    count += entry->d_name[0] != '.';
  closedir(directory);
  return count;
}

static void *wrong_thread(void *gpu) {
  assert(dpe_kms_gpu_fd(gpu) == -1);
  dpe_kms_gpu_destroy(gpu);
  return NULL;
}
int main(int argc, char **argv) {
  struct dpe_kms_gpu *invalid = (void *)(uintptr_t)-1;
  struct dpe_vk_packet packet;
  int fds[4];
  assert(dpe_kms_gpu_fd(invalid) == -1);
  dpe_kms_gpu_destroy(invalid);
  dpe_kms_buffer_destroy((void *)invalid);
  assert(!dpe_kms_gpu_create(NULL));
  assert(!dpe_kms_buffer_create(invalid, 320, 180, &packet, fds));
  assert(dpe_kms_buffer_render(NULL, NULL) == -1);
  if (argc == 2) {
    struct dpe_kms_gpu *gpu = dpe_kms_gpu_create(argv[1]);
    assert(gpu);
    int device = dpe_kms_gpu_fd(gpu);
    assert(device >= 0);
    pthread_t other;
    assert(!pthread_create(&other, NULL, wrong_thread, gpu));
    assert(!pthread_join(other, NULL));
    assert(dpe_kms_gpu_fd(gpu) == device);
    assert(!dpe_kms_buffer_create(gpu, 0, 180, &packet, fds));
    assert(!dpe_kms_buffer_create(gpu, 8193, 180, &packet, fds));
    struct dpe_kms_buffer *buffer = dpe_kms_buffer_create(gpu, 320, 180, &packet, fds);
    assert(buffer && dpe_vk_valid(&packet, sizeof(packet)));
    assert(packet.type == DPE_VK_TEXTURE && packet.width == 320 && packet.height == 180);
    assert(packet.nfd == 1 && packet.strides[0] >= 1280 && !packet.offsets[0]);
    assert(fds[0] >= 0 && fds[1] == -1 && fds[2] == -1 && fds[3] == -1);
    assert(fcntl(fds[0], F_GETFD) & FD_CLOEXEC);
    unsigned before = descriptors();
    fail_framebuffer = true;
    for (unsigned i = 0; i < 8; i++)
      assert(!dpe_kms_buffer_create(gpu, 320, 180, &packet, fds));
    fail_framebuffer = false;
    assert(descriptors() == before);
    dpe_kms_gpu_destroy(gpu);
    dpe_kms_gpu_destroy(gpu);
    assert(dpe_kms_gpu_fd(gpu) == -1);
    assert(fcntl(device, F_GETFD) >= 0 && fcntl(fds[0], F_GETFD) >= 0);
    dpe_kms_buffer_destroy(buffer);
    assert(fcntl(device, F_GETFD) == -1 && errno == EBADF);
    assert(fcntl(fds[0], F_GETFD) == -1 && errno == EBADF);
    dpe_kms_buffer_destroy(buffer);
    assert(dpe_kms_buffer_render(buffer, NULL) == -1);
    puts("Real GBM/EGL ownership and cross-thread retirement passed");
  } else {
    assert(argc == 1);
    puts("Graphics ABI stale-handle checks passed without GPU access");
  }
}
