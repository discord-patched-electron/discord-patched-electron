// SPDX-License-Identifier: MIT
// Real local socket/SCM_RIGHTS, synthetic timestamps; no graphics API or game.
#define _GNU_SOURCE
#include "../../integration/video/vkcapture-hook.h"
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>
static uint64_t now = 1000000000;
int fixture_clock_gettime(clockid_t id, struct timespec *t) {
  assert(id == CLOCK_MONOTONIC);
  t->tv_sec = now / 1000000000;
  t->tv_nsec = now % 1000000000;
  return 0;
}
static struct dpe_vk_packet receive(int fd, int *descriptor) {
  struct dpe_vk_packet p;
  char control[CMSG_SPACE(4 * sizeof(int))];
  struct iovec iov = {.iov_base = &p, .iov_len = sizeof(p)};
  struct msghdr msg = {
      .msg_iov = &iov, .msg_iovlen = 1, .msg_control = control, .msg_controllen = sizeof(control)};
  assert(recvmsg(fd, &msg, MSG_CMSG_CLOEXEC) == sizeof(p));
  assert(dpe_vk_valid(&p, sizeof(p)));
  struct cmsghdr *c = CMSG_FIRSTHDR(&msg);
  if (descriptor) {
    assert(c && c->cmsg_type == SCM_RIGHTS);
    memcpy(descriptor, CMSG_DATA(c), sizeof(int));
  } else
    assert(!c);
  return p;
}
int main(void) {
  char dir[] = "/tmp/dpe-vk-handoff-XXXXXX";
  assert(mkdtemp(dir));
  struct sockaddr_un address = {.sun_family = AF_UNIX};
  assert(snprintf(address.sun_path, sizeof(address.sun_path), "%s/hook.sock", dir) > 0);
  int listener = socket(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0);
  assert(listener >= 0);
  assert(!bind(listener, (struct sockaddr *)&address, sizeof(address)));
  assert(!chmod(address.sun_path, 0600));
  assert(!listen(listener, 1));
  assert(!setenv("DPE_VKCAPTURE_SOCKET", address.sun_path, 1));
  dpe_hook_init();
  assert(dpe_hook_enabled());
  assert(dpe_hook_enter());
  assert(!dpe_hook_enter());
  dpe_hook_leave();
  assert(dpe_hook_enter());
  dpe_hook_leave();
  dpe_hook_update();
  int peer = accept4(listener, NULL, NULL, SOCK_CLOEXEC | SOCK_NONBLOCK);
  assert(peer >= 0);
  struct dpe_vk_packet hello = receive(peer, NULL);
  assert(hello.type == DPE_VK_HELLO && hello.flags == DPE_VK_DIRECT);
  assert(!dpe_hook_starting());
  struct dpe_vk_packet control = dpe_vk_packet(DPE_VK_CONTROL);
  control.flags = DPE_VK_CAPTURE;
  control.fps = 60;
  memset(control.uuid, 0xab, 16);
  assert(send(peer, &control, sizeof(control), 0) == sizeof(control));
  dpe_hook_update();
  assert(dpe_hook_starting());
  assert(dpe_hook_device(control.uuid));
  assert(!dpe_hook_direct()); // Existing copy-mode controls keep their behavior.
  control.flags |= DPE_VK_DIRECT;
  assert(send(peer, &control, sizeof(control), 0) == sizeof(control));
  dpe_hook_update();
  assert(dpe_hook_starting());
  assert(dpe_hook_direct());
  dpe_hook_unavailable();
  struct dpe_vk_packet unsupported = receive(peer, NULL);
  assert(unsupported.type == DPE_VK_UNSUPPORTED && unsupported.generation == 0 &&
         unsupported.flags == 0 && unsupported.nfd == 0);
  // A failed allocation stays quiescent even while a game presents rapidly.
  for (unsigned i = 0; i < 100; i++) {
    now += 250000;
    dpe_hook_unavailable();
    dpe_hook_update();
    assert(!dpe_hook_starting() && !dpe_hook_active() && !dpe_hook_due());
  }
  char byte;
  assert(recv(peer, &byte, 1, MSG_DONTWAIT) < 0 && errno == EAGAIN);
  control.flags = DPE_VK_CAPTURE;
  assert(send(peer, &control, sizeof(control), 0) == sizeof(control));
  dpe_hook_update();
  assert(dpe_hook_starting() && !dpe_hook_direct());
  dpe_hook_unavailable(); // Copy-mode attempts do not emit direct-mode errors.
  assert(dpe_hook_starting());
  control.flags |= DPE_VK_DIRECT;
  assert(send(peer, &control, sizeof(control), 0) == sizeof(control));
  dpe_hook_update();
  assert(dpe_hook_starting() && dpe_hook_direct());
  int buffer = memfd_create("synthetic", MFD_CLOEXEC);
  assert(buffer >= 0);
  assert(!ftruncate(buffer, 16384));
  int strides[4] = {256}, offsets[4] = {0}, fds[4] = {buffer, -1, -1, -1};
  dpe_hook_texture(64, 64, 0x34325241, strides, offsets, 0, false, 0, 1, fds, 42);
  int duplicate = -1;
  struct dpe_vk_packet texture = receive(peer, &duplicate);
  assert(texture.type == DPE_VK_TEXTURE && texture.generation == 1 && texture.nfd == 1);
  assert(fcntl(duplicate, F_GETFD) & FD_CLOEXEC);
  close(duplicate);
  dpe_hook_unavailable(); // An active texture cannot be revoked by this path.
  assert(dpe_hook_active() && !dpe_hook_stopping());
  assert(recv(peer, &byte, 1, MSG_DONTWAIT) < 0 && errno == EAGAIN);
  assert(dpe_hook_due());
  assert(!dpe_hook_due());
  dpe_hook_cancel();
  assert(!dpe_hook_due()); // A skipped busy GPU frame retains its deadline.
  now += 17000000;
  assert(dpe_hook_due());
  assert(recv(peer, &byte, 1, MSG_DONTWAIT) < 0); // No readiness before GPU completion.
  dpe_hook_complete(true);
  struct dpe_vk_packet frame = receive(peer, NULL);
  assert(frame.type == DPE_VK_FRAME && frame.sequence == 2 && frame.generation == 1 &&
         frame.pts == now);
  now += 1000000000;
  dpe_hook_cancel();       // Cancellation cannot revoke a published consumer lease.
  assert(!dpe_hook_due()); // No overwrite despite many elapsed periods.
  struct dpe_vk_packet release = dpe_vk_packet(DPE_VK_RELEASE);
  release.generation = frame.generation;
  release.sequence = frame.sequence;
  assert(send(peer, &release, sizeof(release), 0) == sizeof(release));
  dpe_hook_update();
  assert(dpe_hook_due());
  int completion_fd = dup(buffer);
  assert(completion_fd >= 0);
  dpe_hook_complete_fenced(completion_fd);
  assert(fcntl(completion_fd, F_GETFD) < 0);
  frame = receive(peer, &duplicate);
  assert(frame.sequence == 3 && frame.flags == DPE_VK_SYNC_FD && frame.nfd == 1);
  assert(fcntl(duplicate, F_GETFD) & FD_CLOEXEC);
  close(duplicate);
  assert(!dpe_hook_due());
  // A resize retires the old lease; a crossed exact ACK must not release
  // the new generation or disconnect a healthy game.
  dpe_hook_stop();
  assert(receive(peer, NULL).type == DPE_VK_STOP);
  dpe_hook_texture(64, 64, 0x34325241, strides, offsets, 0, false, 0, 1, fds, 42);
  texture = receive(peer, &duplicate);
  assert(texture.generation == 2);
  close(duplicate);
  release.generation = frame.generation;
  release.sequence = frame.sequence;
  assert(send(peer, &release, sizeof(release), 0) == sizeof(release));
  dpe_hook_update();
  assert(!dpe_hook_stopping());
  now += 1000000000;
  assert(dpe_hook_due());
  dpe_hook_complete(true);
  frame = receive(peer, NULL);
  assert(frame.generation == 2);
  // Wrong sequence for the current generation remains a hard failure.
  release.generation = frame.generation;
  release.sequence = 1;
  assert(send(peer, &release, sizeof(release), 0) == sizeof(release));
  dpe_hook_update();
  assert(dpe_hook_stopping());
  assert(!dpe_hook_due());
  dpe_hook_stop();
  close(peer);
  close(buffer);
  close(listener);
  unlink(address.sun_path);
  rmdir(dir);
  puts(
      "game handoff: completion, release ownership, stale ACK rejection, bounded copy gate passed");
  return 0;
}
