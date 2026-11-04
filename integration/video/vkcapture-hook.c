// SPDX-License-Identifier: MIT
// Linked into the GPL-2.0-or-later opted-in producer hooks, with source supplied.
#define _GNU_SOURCE
#include "vkcapture-hook.h"
#include "capture-rate.h"
#include <errno.h>
#include <fcntl.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

static struct {
  int fd;
  bool enabled, accepted, active, held, ready, direct;
  uint64_t generation, sequence, pts, next_connect, retired_generation, retired_sequence;
  uint8_t uuid[16], identity[16];
  char path[sizeof(((struct sockaddr_un *)0)->sun_path)];
  struct dpe_capture_rate rate;
} hook = {.fd = -1};
static atomic_flag presenting = ATOMIC_FLAG_INIT;
bool dpe_hook_enter(void) {
  return !atomic_flag_test_and_set_explicit(&presenting, memory_order_acquire);
}
void dpe_hook_leave(void) { atomic_flag_clear_explicit(&presenting, memory_order_release); }
static uint64_t now_ns(void) {
  struct timespec t;
  if (clock_gettime(CLOCK_MONOTONIC, &t) < 0)
    return 0;
  return (uint64_t)t.tv_sec * 1000000000u + t.tv_nsec;
}
static void disconnect_hook(void) {
  if (hook.fd >= 0)
    close(hook.fd);
  hook.fd = -1;
  hook.accepted = hook.ready = hook.held = false;
  // Leave active set until the graphics owner destroys the old capture object.
}
static bool send_packet(struct dpe_vk_packet *packet, int count, const int *fds) {
  struct iovec iov = {.iov_base = packet, .iov_len = sizeof(*packet)};
  union {
    struct cmsghdr align;
    char bytes[CMSG_SPACE(4 * sizeof(int))];
  } ancillary = {0};
  struct msghdr msg = {.msg_iov = &iov, .msg_iovlen = 1};
  if (count) {
    msg.msg_control = ancillary.bytes;
    msg.msg_controllen = CMSG_SPACE((size_t)count * sizeof(int));
    struct cmsghdr *c = CMSG_FIRSTHDR(&msg);
    c->cmsg_level = SOL_SOCKET;
    c->cmsg_type = SCM_RIGHTS;
    c->cmsg_len = CMSG_LEN((size_t)count * sizeof(int));
    memcpy(CMSG_DATA(c), fds, (size_t)count * sizeof(int));
  }
  ssize_t n = sendmsg(hook.fd, &msg, MSG_NOSIGNAL | MSG_DONTWAIT);
  if (n == sizeof(*packet))
    return true;
  // Backpressure terminates capture, never blocks the application's present.
  disconnect_hook();
  return false;
}
bool dpe_hook_enabled(void) { return hook.enabled; }
bool dpe_hook_direct(void) { return hook.direct; }
void dpe_hook_identity(const uint8_t uuid[16]) {
  if (hook.fd < 0 && !hook.active)
    memcpy(hook.identity, uuid, 16);
}
void dpe_hook_init(void) {
  const char *path = getenv("DPE_VKCAPTURE_SOCKET");
  if (!path)
    return;
  hook.enabled = true; // Invalid explicit DPE configuration must not capture into OBS.
  if (path[0] != '/' || strlen(path) >= sizeof(hook.path))
    return;
  memcpy(hook.path, path, strlen(path) + 1);
  dpe_capture_rate_configure(&hook.rate, 60);
}
void dpe_hook_update(void) {
  uint64_t now = now_ns();
  if (!hook.enabled || !hook.path[0])
    return;
  if (hook.fd < 0) {
    if (now < hook.next_connect || hook.active)
      return;
    hook.next_connect = now + 1000000000u;
    struct stat st;
    if (lstat(hook.path, &st) || !S_ISSOCK(st.st_mode) || st.st_uid != geteuid() ||
        (st.st_mode & 0077))
      return;
    int fd = socket(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
    struct sockaddr_un addr = {.sun_family = AF_UNIX};
    memcpy(addr.sun_path, hook.path, sizeof(addr.sun_path));
    struct ucred cred;
    socklen_t size = sizeof(cred);
    if (fd < 0 || connect(fd, (struct sockaddr *)&addr, sizeof(addr)) ||
        getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &cred, &size) || cred.uid != geteuid()) {
      if (fd >= 0)
        close(fd);
      return;
    }
    hook.fd = fd;
    struct dpe_vk_packet packet = dpe_vk_packet(DPE_VK_HELLO);
    packet.flags = DPE_VK_DIRECT;
    memcpy(packet.uuid, hook.identity, 16);
    int comm = open("/proc/self/comm", O_RDONLY | O_CLOEXEC);
    if (comm >= 0) {
      ssize_t n = read(comm, packet.name, sizeof(packet.name) - 1);
      if (n > 0 && packet.name[n - 1] == '\n')
        packet.name[n - 1] = 0;
      close(comm);
    }
    if (!send_packet(&packet, 0, NULL))
      return;
  }
  for (unsigned i = 0; i < 4; i++) {
    struct dpe_vk_packet p;
    ssize_t n = recv(hook.fd, &p, sizeof(p), MSG_DONTWAIT | MSG_TRUNC);
    if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK))
      break;
    if (n <= 0 || !dpe_vk_valid(&p, (size_t)n)) {
      disconnect_hook();
      return;
    }
    if (p.type == DPE_VK_CONTROL) {
      if (p.fps < 1 || p.fps > 240 || (p.flags & ~(DPE_VK_CAPTURE | DPE_VK_DIRECT))) {
        disconnect_hook();
        return;
      }
      bool direct = (p.flags & DPE_VK_DIRECT) != 0;
      if (hook.active && (memcmp(hook.uuid, p.uuid, 16) ||
                          ((p.flags & DPE_VK_CAPTURE) && direct != hook.direct))) {
        disconnect_hook();
        return;
      }
      memcpy(hook.uuid, p.uuid, 16);
      hook.accepted = (p.flags & DPE_VK_CAPTURE) != 0;
      hook.direct = direct;
      dpe_capture_rate_configure(&hook.rate, p.fps);
    } else if (p.type == DPE_VK_RELEASE && hook.retired_generation &&
               p.generation == hook.retired_generation && p.sequence == hook.retired_sequence) {
      // A release can cross the swapchain retirement notification on the socket.
      // It only acknowledges retired storage; it cannot release the new lease.
      hook.retired_generation = hook.retired_sequence = 0;
    } else if (p.type == DPE_VK_RELEASE && hook.ready && p.generation == hook.generation &&
               p.sequence == hook.sequence) {
      hook.held = hook.ready = false;
    } else {
      disconnect_hook();
      return;
    }
  }
}
bool dpe_hook_starting(void) { return !hook.active && hook.fd >= 0 && hook.accepted; }
bool dpe_hook_stopping(void) { return hook.active && (hook.fd < 0 || !hook.accepted); }
bool dpe_hook_active(void) { return hook.active; }
void dpe_hook_stop(void) {
  if (hook.ready) {
    hook.retired_generation = hook.generation;
    hook.retired_sequence = hook.sequence;
  }
  if (hook.active && hook.fd >= 0) {
    struct dpe_vk_packet p = dpe_vk_packet(DPE_VK_STOP);
    p.generation = hook.generation;
    send_packet(&p, 0, NULL);
  }
  hook.active = hook.held = hook.ready = false;
}
void dpe_hook_unavailable(void) {
  if (hook.active || !hook.accepted || !hook.direct || hook.fd < 0)
    return;
  // Do not retry expensive allocation on every present. Only a new CONTROL
  // may admit another attempt, including the consumer's copied-buffer fallback.
  hook.accepted = false;
  hook.held = hook.ready = false;
  struct dpe_vk_packet p = dpe_vk_packet(DPE_VK_UNSUPPORTED);
  p.generation = hook.generation;
  send_packet(&p, 0, NULL);
}
void dpe_hook_texture(int width, int height, int format, int strides[4], int offsets[4],
                      uint64_t modifier, bool flip, uint32_t color, int count, int fds[4],
                      uint32_t window) {
  if (width < 1 || height < 1 || width > 16384 || height > 16384 || count < 1 || count > 4 ||
      color != 0) {
    disconnect_hook();
    return;
  }
  struct dpe_vk_packet p = dpe_vk_packet(DPE_VK_TEXTURE);
  p.generation = ++hook.generation;
  p.width = width;
  p.height = height;
  p.format = format;
  p.modifier = modifier;
  p.flags = flip ? DPE_VK_FLIP : 0;
  p.nfd = count;
  memcpy(p.name, &window, sizeof(window));
  memcpy(p.strides, strides, (size_t)count * sizeof(int));
  memcpy(p.offsets, offsets, (size_t)count * sizeof(int));
  hook.active = send_packet(&p, count, fds);
  hook.held = hook.ready = false;
}
bool dpe_hook_due(void) {
  if (!hook.active || !hook.accepted || hook.held || !dpe_capture_rate_due(&hook.rate, now_ns()))
    return false;
  hook.held = true;
  hook.pts = now_ns();
  hook.sequence++;
  return true;
}
void dpe_hook_cancel(void) {
  // An exported frame still belongs to the consumer until its exact release.
  // Only a capture that has not submitted or published anything can be skipped.
  if (hook.held && !hook.ready)
    hook.held = false;
}
void dpe_hook_complete(bool success) {
  if (!success) {
    disconnect_hook();
    return;
  }
  if (!hook.held || !hook.active || hook.fd < 0)
    return;
  struct dpe_vk_packet p = dpe_vk_packet(DPE_VK_FRAME);
  p.generation = hook.generation;
  p.sequence = hook.sequence;
  p.pts = hook.pts;
  hook.ready = send_packet(&p, 0, NULL);
}
bool dpe_hook_device(const uint8_t uuid[16]) { return memcmp(hook.uuid, uuid, 16) == 0; }

void dpe_hook_complete_fenced(int fd) {
  if (fd < 0) {
    dpe_hook_complete(false);
    return;
  }
  if (hook.held && hook.active && hook.fd >= 0) {
    struct dpe_vk_packet p = dpe_vk_packet(DPE_VK_FRAME);
    p.generation = hook.generation;
    p.sequence = hook.sequence;
    p.pts = hook.pts;
    p.flags = DPE_VK_SYNC_FD;
    p.nfd = 1;
    hook.ready = send_packet(&p, 1, &fd);
  }
  close(fd);
}
