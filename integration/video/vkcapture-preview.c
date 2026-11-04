// SPDX-License-Identifier: MIT
// One explicitly selected idle game's thumbnail. No PipeWire, files or encoder.
#define _GNU_SOURCE
#include "vkcapture-compat.h"
#include "vkcapture-gpu.h"
#include "vkcapture-sync.h"
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <glob.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>
static uint64_t milliseconds(void) {
  struct timespec now;
  clock_gettime(CLOCK_MONOTONIC, &now);
  return (uint64_t)now.tv_sec * 1000 + now.tv_nsec / 1000000;
}
static bool wait_readable(int fd, uint64_t deadline) {
  uint64_t now = milliseconds();
  if (now >= deadline)
    return false;
  struct pollfd event = {.fd = fd, .events = POLLIN};
  return poll(&event, 1, (int)(deadline - now)) > 0 && (event.revents & POLLIN) &&
         !(event.revents & (POLLERR | POLLHUP | POLLNVAL));
}
static void base64(const uint8_t *data, size_t length) {
  static const char alphabet[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  for (size_t i = 0; i < length; i += 3) {
    unsigned value = (unsigned)data[i] << 16;
    if (i + 1 < length)
      value |= (unsigned)data[i + 1] << 8;
    if (i + 2 < length)
      value |= data[i + 2];
    putchar(alphabet[value >> 18]);
    putchar(alphabet[(value >> 12) & 63]);
    putchar(i + 1 < length ? alphabet[(value >> 6) & 63] : '=');
    putchar(i + 2 < length ? alphabet[value & 63] : '=');
  }
}
int main(int argc, char **argv) {
  int fd = -1;
  bool compat = false, have_uuid = false, persistent = false;
  uint32_t width = 0, height = 0;
  uint8_t uuid[16] = {0};
  const char *node = "auto";
  for (int i = 1; i < argc; i += 2) {
    if (i + 1 >= argc)
      return 2;
    char *end;
    unsigned long value = strtoul(argv[i + 1], &end, 10);
    if (!strcmp(argv[i], "--hook-fd") && !*end && value <= INT32_MAX)
      fd = (int)value;
    else if (!strcmp(argv[i], "--width") && !*end && value >= 1 && value <= 480)
      width = value;
    else if (!strcmp(argv[i], "--height") && !*end && value >= 1 && value <= 270)
      height = value;
    else if (!strcmp(argv[i], "--persistent") && !strcmp(argv[i + 1], "1"))
      persistent = true;
    else if (!strcmp(argv[i], "--render-node"))
      node = argv[i + 1];
    else if (!strcmp(argv[i], "--hook-protocol") && !strcmp(argv[i + 1], "obs"))
      compat = true;
    else if (!strcmp(argv[i], "--device-uuid") && strlen(argv[i + 1]) == 32) {
      have_uuid = true;
      for (unsigned j = 0; j < 16; j++) {
        char byte[] = {argv[i + 1][j * 2], argv[i + 1][j * 2 + 1], 0};
        uuid[j] = strtoul(byte, &end, 16);
        if (*end || !isxdigit((unsigned char)byte[0]) || !isxdigit((unsigned char)byte[1]))
          return 2;
      }
    } else
      return 2;
  }
  struct ucred peer;
  socklen_t peer_size = sizeof(peer), kind_size = sizeof(int);
  int kind;
  if (fd < 0 || !width || !height || (!compat && !have_uuid) ||
      (strcmp(node, "auto") && strncmp(node, "/dev/dri/renderD", 16)) ||
      getsockopt(fd, SOL_SOCKET, SO_TYPE, &kind, &kind_size) ||
      kind != (compat ? SOCK_STREAM : SOCK_SEQPACKET) ||
      getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &peer, &peer_size) || peer.uid != geteuid())
    return 2;
  alarm(4);
  pw_init(NULL, NULL);
  struct pw_main_loop *main = pw_main_loop_new(NULL);
  if (!main)
    return 1;
  struct dpe_vk_gpu *gpu = NULL;
  if (!strcmp(node, "auto")) {
    glob_t nodes = {0};
    if (!glob("/dev/dri/renderD*", 0, NULL, &nodes))
      for (size_t i = 0; i < nodes.gl_pathc && i < 16 && !gpu; i++) {
        struct dpe_vk_gpu *candidate =
            dpe_vk_gpu_create(nodes.gl_pathv[i], pw_main_loop_get_loop(main), 1, 1);
        if (candidate && (compat || !memcmp(dpe_vk_gpu_uuid(candidate), uuid, 16)))
          gpu = candidate;
        else
          dpe_vk_gpu_destroy(candidate);
      }
    globfree(&nodes);
  } else {
    gpu = dpe_vk_gpu_create(node, pw_main_loop_get_loop(main), 1, 1);
    if (gpu && !compat && memcmp(dpe_vk_gpu_uuid(gpu), uuid, 16)) {
      dpe_vk_gpu_destroy(gpu);
      gpu = NULL;
    }
  }
  int status = 1;
  uint8_t *pixels = NULL;
  if (!gpu)
    goto done;
  struct dpe_vk_packet control = dpe_vk_packet(DPE_VK_CONTROL);
  control.flags = DPE_VK_CAPTURE;
  control.fps = persistent ? 30 : 1;
  memcpy(control.uuid, dpe_vk_gpu_uuid(gpu), 16);
  struct dpe_obs_control obs = {.capturing = 1};
  memcpy(obs.uuid, control.uuid, 16);
  const void *message = compat ? (const void *)&obs : (const void *)&control;
  size_t message_size = compat ? sizeof(obs) : sizeof(control);
  if (send(fd, message, message_size, MSG_DONTWAIT | MSG_NOSIGNAL) != (ssize_t)message_size)
    goto done;
  pixels = malloc((size_t)width * height * 4);
  if (!pixels)
    goto done;
  uint64_t generation = 0, sequence = 0;
  do {
    alarm(4);
    uint64_t deadline = milliseconds() + 2500;
    uint8_t wire[128];
    size_t buffered = 0;
    int fds[4] = {-1, -1, -1, -1};
    unsigned descriptors = 0;
    struct pollfd update = {.fd = fd, .events = POLLIN};
    bool ready = compat && generation && poll(&update, 1, 0) == 0;
    for (unsigned iteration = 0; iteration < 32 && !ready; iteration++) {
      if (!wait_readable(fd, deadline))
        break;
      union {
        struct cmsghdr align;
        char bytes[CMSG_SPACE(16 * sizeof(int))];
      } ancillary;
      struct iovec iov = {.iov_base = wire + buffered, .iov_len = sizeof(wire) - buffered};
      struct msghdr msg = {.msg_iov = &iov,
                           .msg_iovlen = 1,
                           .msg_control = ancillary.bytes,
                           .msg_controllen = sizeof(ancillary)};
      ssize_t n = recvmsg(fd, &msg, MSG_CMSG_CLOEXEC | MSG_DONTWAIT);
      bool valid = n > 0 && !(msg.msg_flags & (MSG_TRUNC | MSG_CTRUNC));
      for (struct cmsghdr *c = CMSG_FIRSTHDR(&msg); n >= 0 && c; c = CMSG_NXTHDR(&msg, c)) {
        if (c->cmsg_level != SOL_SOCKET || c->cmsg_type != SCM_RIGHTS ||
            c->cmsg_len < CMSG_LEN(0)) {
          valid = false;
          continue;
        }
        size_t count = (c->cmsg_len - CMSG_LEN(0)) / sizeof(int);
        int *received = (int *)CMSG_DATA(c);
        for (size_t j = 0; j < count; j++)
          if (descriptors < 4)
            fds[descriptors++] = received[j];
          else {
            close(received[j]);
            valid = false;
          }
      }
      if (!valid)
        break;
      buffered += (size_t)n;
      if (buffered != sizeof(wire)) {
        if (!compat)
          break;
        continue;
      }
      buffered = 0;
      struct dpe_vk_packet packet;
      memcpy(&packet, wire, sizeof(packet));
      if (compat) {
        struct dpe_obs_texture texture;
        memcpy(&texture, wire, sizeof(texture));
        valid = dpe_obs_texture_packet(&texture, descriptors, generation + 1, &packet);
      } else
        valid = dpe_vk_valid(&packet, sizeof(packet));
      if (valid && packet.type == DPE_VK_TEXTURE && descriptors == packet.nfd && descriptors) {
        valid = dpe_vk_gpu_import(gpu, &packet, fds) == 0;
        generation = packet.generation;
        if (compat && valid) {
          // Upstream advertises allocation before its first copy and has no frame
          // event. Give that present a bounded head start, as OBS samples on tick.
          poll(NULL, 0, 25);
          ready = true;
        }
      } else if (valid && packet.type == DPE_VK_FRAME && packet.generation == generation &&
                 generation && packet.sequence > sequence) {
        ready = (!packet.flags && !packet.nfd && !descriptors) ||
                (packet.flags == DPE_VK_SYNC_FD && packet.nfd == 1 && descriptors == 1 &&
                 wait_readable(fds[0], deadline) && dpe_vk_fence_signaled(fds[0]));
        valid = ready;
        if (ready)
          sequence = packet.sequence;
      } else
        valid = false;
      for (unsigned j = 0; j < descriptors; j++)
        close(fds[j]);
      descriptors = 0;
      if (!valid)
        break;
    }
    for (unsigned j = 0; j < descriptors; j++)
      close(fds[j]);
    if (ready) {
      uint32_t frame_width = width, frame_height = height;
      if (!dpe_vk_gpu_preview(gpu, &frame_width, &frame_height, pixels)) {
        // glReadPixels completes this thumbnail's GPU read before returning.
        // Only now release the exact hook lease; one following frame may wait
        // in the socket, but the producer can never overwrite an owned frame.
        if (!compat) {
          struct dpe_vk_packet release = dpe_vk_packet(DPE_VK_RELEASE);
          release.generation = generation;
          release.sequence = sequence;
          if (send(fd, &release, sizeof(release), MSG_DONTWAIT | MSG_NOSIGNAL) != sizeof(release))
            break;
        }
        printf("{\"width\":%u,\"height\":%u,\"format\":\"BGRA\",\"pixels\":\"", frame_width,
               frame_height);
        base64(pixels, (size_t)frame_width * frame_height * 4);
        puts("\"}");
        status = fflush(stdout) ? 1 : 0;
      }
    }
    if (!ready || status || !persistent)
      break;
    char demand;
    if (read(STDIN_FILENO, &demand, 1) != 1 || demand != '\n')
      break;
    status = 1;
  } while (true);
done:
  // Closing the sole socket owner retires the hook's capture allocation. A
  // fresh handshake is required before any later preview or real screenshare.
  close(fd);
  free(pixels);
  dpe_vk_gpu_destroy(gpu);
  pw_main_loop_destroy(main);
  pw_deinit();
  return status;
}
