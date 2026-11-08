// SPDX-License-Identifier: GPL-3.0-only
// Actual wire parsing and selection policy, using sockets and fake descriptors.
// Does not open DRM, start GSR, create EGL or capture a desktop.
#define _GNU_SOURCE
#include "../../integration/video/kms-client.h"
#include <assert.h>
#include <dirent.h>
#include <drm_fourcc.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

static unsigned descriptors(void) {
  DIR *directory = opendir("/proc/self/fd");
  assert(directory);
  unsigned count = 0;
  while (readdir(directory))
    count++;
  closedir(directory);
  return count;
}
static void send_response(int socket, const gsr_kms_response *response, size_t size, int count) {
  struct iovec vector = {.iov_base = (void *)response, .iov_len = size};
  unsigned char control[CMSG_SPACE(sizeof(int) * 32)] = {0};
  struct msghdr message = {.msg_iov = &vector, .msg_iovlen = 1};
  int fd = open("/dev/null", O_RDONLY | O_CLOEXEC);
  assert(fd >= 0);
  if (count) {
    message.msg_control = control;
    message.msg_controllen = CMSG_SPACE(sizeof(int) * count);
    struct cmsghdr *header = CMSG_FIRSTHDR(&message);
    header->cmsg_level = SOL_SOCKET;
    header->cmsg_type = SCM_RIGHTS;
    header->cmsg_len = CMSG_LEN(sizeof(int) * count);
    for (int i = 0; i < count; i++)
      memcpy((char *)CMSG_DATA(header) + i * sizeof(int), &fd, sizeof(int));
  }
  assert(sendmsg(socket, &message, MSG_NOSIGNAL) == (ssize_t)size);
  close(fd);
}
static gsr_kms_response scene(void) {
  return (gsr_kms_response){.version = 6,
                            .num_items = 2,
                            .items = {{.num_dma_bufs = 1,
                                       .dma_buf = {{.fd = 9, .pitch = 16384}},
                                       .width = 4096,
                                       .height = 2160,
                                       .pixel_format = DRM_FORMAT_XRGB8888,
                                       .connector_id = 95,
                                       .plane_type = KMS_PLANE_TYPE_PRIMARY,
                                       .src_x = 1920,
                                       .src_y = 0,
                                       .src_w = 1920,
                                       .src_h = 1080,
                                       .dst_w = 1920,
                                       .dst_h = 1080},
                                      {.num_dma_bufs = 1,
                                       .dma_buf = {{.fd = 10, .pitch = 256}},
                                       .width = 64,
                                       .height = 64,
                                       .pixel_format = DRM_FORMAT_ARGB8888,
                                       .connector_id = 95,
                                       .plane_type = KMS_PLANE_TYPE_CURSOR,
                                       .src_w = 64,
                                       .src_h = 64,
                                       .dst_x = -8,
                                       .dst_y = 1050,
                                       .dst_w = 64,
                                       .dst_h = 64,
                                       .zpos = 2}}};
}
int main(void) {
  struct dpe_kms_output output = {.connector = 95, .width = 1920, .height = 1080};
  struct dpe_kms_scene selected;
  struct dpe_kms_layout layout = {.planes = 2, .primary_present = true, .cursor_present = true};
  struct dpe_kms_layout only_primary = {.planes = 1, .primary_present = true};
  gsr_kms_response response = scene();
  assert(DPE_KMS_VALID == dpe_kms_scene(&response, &output, &layout, &selected));
  assert(selected.primary == &response.items[0] && selected.cursor == &response.items[1]);
  layout.primary_needs_opaque = true;
  assert(DPE_KMS_VALID == dpe_kms_scene(&response, &output, &layout, &selected));
  response.items[0].pixel_format = DRM_FORMAT_ARGB8888;
  assert(DPE_KMS_UNSUPPORTED == dpe_kms_scene(&response, &output, &layout, &selected));
  layout.primary_needs_opaque = false;
  assert(DPE_KMS_VALID == dpe_kms_scene(&response, &output, &layout, &selected));
  // SDR compositors may scan out RGB10 without HDR. Import normalizes it on
  // the GPU, and premultiplied RGB over black needs no alpha multiplication.
  response.items[0].pixel_format = DRM_FORMAT_ABGR2101010;
  assert(DPE_KMS_VALID == dpe_kms_scene(&response, &output, &layout, &selected));
  layout.primary_needs_opaque = true;
  assert(DPE_KMS_UNSUPPORTED == dpe_kms_scene(&response, &output, &layout, &selected));
  response.items[0].pixel_format = DRM_FORMAT_XBGR2101010;
  assert(DPE_KMS_VALID == dpe_kms_scene(&response, &output, &layout, &selected));
  layout.primary_needs_opaque = false;
  response = scene();
  response.items[0].has_hdr_metadata = true; // SDR metadata is not itself HDR.
  assert(DPE_KMS_VALID == dpe_kms_scene(&response, &output, &layout, &selected));
  response.items[0].hdr_metadata.hdmi_metadata_type1.eotf = 2;
  assert(DPE_KMS_UNSUPPORTED == dpe_kms_scene(&response, &output, &layout, &selected));
  response = scene();
  response.items[0].rotation = KMS_ROT_90;
  assert(DPE_KMS_UNSUPPORTED == dpe_kms_scene(&response, &output, &layout, &selected));
  response = scene();
  response.items[0].src_x = 4090;
  assert(DPE_KMS_UNSUPPORTED == dpe_kms_scene(&response, &output, &layout, &selected));
  response = scene();
  response.items[1].plane_type = KMS_PLANE_TYPE_OVERLAY;
  assert(DPE_KMS_UNSUPPORTED == dpe_kms_scene(&response, &output, &layout, &selected));
  response = scene();
  response.items[1].connector_id = 96;
  assert(DPE_KMS_CHANGED ==
         dpe_kms_scene(&response, &output, &layout, &selected)); // Missing selected cursor.
  assert(DPE_KMS_VALID ==
         dpe_kms_scene(&response, &output, &only_primary, &selected)); // Other monitor is excluded.
  response.items[1].connector_id = 0;
  assert(DPE_KMS_UNSUPPORTED == dpe_kms_scene(&response, &output, &only_primary, &selected));
  response = scene();
  response.items[1].zpos = 0;
  assert(DPE_KMS_UNSUPPORTED == dpe_kms_scene(&response, &output, &layout, &selected));
  response = scene();
  response.num_items = 8;
  assert(DPE_KMS_UNSUPPORTED ==
         dpe_kms_scene(&response, &output, &layout, &selected)); // Possible truncation.
  response.num_items = 0;
  assert(DPE_KMS_CHANGED == dpe_kms_scene(&response, &output, &layout, &selected));

  int sockets[2];
  assert(!socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, sockets));
  unsigned initial = descriptors();
  gsr_kms_response decoded;
  response = scene();
  send_response(sockets[0], &response, sizeof(response), 2);
  assert(dpe_kms_receive(sockets[1], &decoded) == 1);
  assert(descriptors() == initial + 2);
  assert(fcntl(decoded.items[0].dma_buf[0].fd, F_GETFD) & FD_CLOEXEC);
  assert(fcntl(decoded.items[1].dma_buf[0].fd, F_GETFD) & FD_CLOEXEC);
  dpe_kms_response_close(&decoded);
  assert(descriptors() == initial);
  for (int kind = 0; kind < 9; kind++) {
    response = scene();
    int sent = 2;
    size_t size = sizeof(response);
    if (kind == 0)
      response.version = 5;
    if (kind == 1)
      response.num_items = 9;
    if (kind == 2)
      response.items[0].num_dma_bufs = 5;
    if (kind == 3)
      sent = 1;
    if (kind == 4)
      sent = 32;
    if (kind == 5)
      size = 12;
    if (kind == 6)
      response.result = KMS_RESULT_FAILED_TO_GET_PLANES;
    if (kind == 7)
      *(unsigned char *)&response.items[0].has_hdr_metadata = 2;
    if (kind == 8)
      response.items[0].num_dma_bufs = -1;
    send_response(sockets[0], &response, size, sent);
    assert(dpe_kms_receive(sockets[1], &decoded) == -1);
    assert(!decoded.num_items && descriptors() == initial);
  }
  response = scene();
  response.result = KMS_RESULT_FAILED_TO_GET_PLANE;
  strcpy(response.err_msg, "drmfb handle is NULL");
  send_response(sockets[0], &response, sizeof(response), 2);
  assert(dpe_kms_receive(sockets[1], &decoded) == DPE_KMS_PERMISSION_DENIED);
  assert(!decoded.num_items && descriptors() == initial);
  response.num_items = 0;
  send_response(sockets[0], &response, sizeof(response), 0);
  assert(dpe_kms_receive(sockets[1], &decoded) == DPE_KMS_PERMISSION_DENIED);
  // A malformed denial must not become a trustworthy authorization request.
  response.version = 5;
  send_response(sockets[0], &response, sizeof(response), 0);
  assert(dpe_kms_receive(sockets[1], &decoded) == -1);
  assert(!decoded.num_items && descriptors() == initial);
  assert(dpe_kms_receive(sockets[1], &decoded) == 0);
  close(sockets[0]);
  close(sockets[1]);
  puts("KMS crop/cursor policy, HDR rejection, protocol bounds and descriptor ownership passed");
  return 0;
}
