// SPDX-License-Identifier: GPL-3.0-only
// DPE's privileged protocol boundary around the pinned GSR DRM export routines.
// No arbitrary command execution, file paths, image storage or encoding here.
#define _GNU_SOURCE
#define main dpe_gsr_reference_main
#include "vendor/gsr-kms-server.inc"
#undef main
#include <ctype.h>
#include <poll.h>
#include <signal.h>
#include <stdlib.h>
#include <sys/inotify.h>
#include <sys/stat.h>

#define DPE_ROOT "/usr/local/libexec/discord-patched-electron"
#define DPE_MARKER "DPE-KMS-2\n"

static bool dpe_marker_valid(uid_t uid, char path[128]) {
  snprintf(path, 128, DPE_ROOT "/permissions/%u", (unsigned)uid);
  int fd = open(path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
  if (fd < 0)
    return false;
  struct stat info;
  char contents[16];
  bool valid = !fstat(fd, &info) && S_ISREG(info.st_mode) && !info.st_uid &&
               !(info.st_mode & 022) && read(fd, contents, sizeof(contents)) == 10 &&
               !memcmp(contents, DPE_MARKER, 10);
  close(fd);
  return valid;
}
static bool dpe_socket_path(const char *path, uid_t uid) {
  const char *prefix = "/tmp/dpe-kms-";
  size_t length = strlen(prefix);
  if (strlen(path) != length + 6 + 7 || strncmp(path, prefix, length) ||
      strcmp(path + length + 6, "/socket"))
    return false;
  for (unsigned i = 0; i < 6; i++)
    if (!isalnum((unsigned char)path[length + i]))
      return false;
  char directory[64];
  memcpy(directory, path, length + 6);
  directory[length + 6] = 0;
  int parent = open(directory, O_PATH | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
  if (parent < 0)
    return false;
  struct stat dir, socket;
  bool valid = !fstat(parent, &dir) && dir.st_uid == uid && (dir.st_mode & 0777) == 0700 &&
               !fstatat(parent, "socket", &socket, AT_SYMLINK_NOFOLLOW) &&
               S_ISSOCK(socket.st_mode) && socket.st_uid == uid;
  close(parent);
  return valid;
}
static int dpe_card_open(const char *path) {
  const char *prefix = "/dev/dri/card";
  size_t length = strlen(prefix);
  if (strncmp(path, prefix, length) || !path[length] || strlen(path) > length + 10)
    return -1;
  for (const char *p = path + length; *p; p++)
    if (!isdigit((unsigned char)*p))
      return -1;
  int fd = open(path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
  if (fd < 0)
    return -1;
  struct stat info;
  // Query DRM's actual node type, not a hardcoded PCI device or minor range.
  if (fstat(fd, &info) || !S_ISCHR(info.st_mode) || info.st_uid ||
      drmGetNodeTypeFromFd(fd) != DRM_NODE_PRIMARY ||
      drmSetClientCap(fd, DRM_CLIENT_CAP_UNIVERSAL_PLANES, 1) ||
      drmSetClientCap(fd, DRM_CLIENT_CAP_ATOMIC, 1)) {
    close(fd);
    return -1;
  }
  return fd;
}
static bool dpe_peer(int fd, uid_t uid, pid_t pid, int type) {
  struct ucred peer;
  socklen_t size = sizeof(peer);
  int domain = 0, actual_type = 0;
  if (getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &peer, &size) || peer.uid != uid || peer.pid != pid)
    return false;
  size = sizeof(domain);
  if (getsockopt(fd, SOL_SOCKET, SO_DOMAIN, &domain, &size) || domain != AF_UNIX)
    return false;
  size = sizeof(actual_type);
  return !getsockopt(fd, SOL_SOCKET, SO_TYPE, &actual_type, &size) && actual_type == type;
}
// The initial stream accepts exactly one FD handoff. All later requests are
// fixed-size sequenced packets without FDs. Close every unexpected descriptor.
static bool dpe_request(int fd, bool initial, uid_t uid, pid_t pid, int *replacement) {
  gsr_kms_request request = {0};
  unsigned char control[CMSG_SPACE(sizeof(int) * 4)] = {0};
  struct iovec vector = {.iov_base = &request, .iov_len = sizeof(request)};
  struct msghdr message = {.msg_iov = &vector,
                           .msg_iovlen = 1,
                           .msg_control = control,
                           .msg_controllen = sizeof(control)};
  ssize_t size = recvmsg(fd, &message, MSG_WAITALL | MSG_CMSG_CLOEXEC);
  bool valid =
      size == sizeof(request) && !(message.msg_flags & (MSG_TRUNC | MSG_CTRUNC)) &&
      request.version == GSR_KMS_PROTOCOL_VERSION &&
      request.type == (initial ? KMS_REQUEST_TYPE_REPLACE_CONNECTION : KMS_REQUEST_TYPE_GET_KMS);
  int received = -1, count = 0;
  for (struct cmsghdr *header = CMSG_FIRSTHDR(&message); header;
       header = CMSG_NXTHDR(&message, header)) {
    if (header->cmsg_level != SOL_SOCKET || header->cmsg_type != SCM_RIGHTS ||
        header->cmsg_len < CMSG_LEN(0)) {
      valid = false;
      continue;
    }
    size_t bytes = header->cmsg_len - CMSG_LEN(0);
    valid &= bytes % sizeof(int) == 0;
    for (size_t i = 0; i < bytes / sizeof(int); i++) {
      int descriptor;
      memcpy(&descriptor, (char *)CMSG_DATA(header) + i * sizeof(int), sizeof(descriptor));
      if (!count)
        received = descriptor;
      else
        close(descriptor);
      count++;
    }
  }
  valid &= count == (initial ? 1 : 0);
  if (valid && initial)
    valid = dpe_peer(received, uid, pid, SOCK_SEQPACKET);
  if (valid && initial)
    *replacement = received;
  else if (received >= 0)
    close(received);
  return valid;
}
static bool dpe_send(int fd, const gsr_kms_response *response) {
  int fds[GSR_KMS_MAX_ITEMS * GSR_KMS_MAX_DMA_BUFS], count = 0;
  if (response->num_items < 0 || response->num_items > GSR_KMS_MAX_ITEMS)
    return false;
  for (int i = 0; i < response->num_items; i++) {
    const gsr_kms_response_item *item = &response->items[i];
    if (item->num_dma_bufs < 1 || item->num_dma_bufs > GSR_KMS_MAX_DMA_BUFS)
      return false;
    for (int j = 0; j < item->num_dma_bufs; j++)
      fds[count++] = item->dma_buf[j].fd;
  }
  unsigned char control[CMSG_SPACE(sizeof(fds))] = {0};
  struct iovec vector = {.iov_base = (void *)response, .iov_len = sizeof(*response)};
  struct msghdr message = {.msg_iov = &vector, .msg_iovlen = 1};
  if (count) {
    message.msg_control = control;
    message.msg_controllen = CMSG_SPACE(sizeof(int) * count);
    struct cmsghdr *header = CMSG_FIRSTHDR(&message);
    header->cmsg_level = SOL_SOCKET;
    header->cmsg_type = SCM_RIGHTS;
    header->cmsg_len = CMSG_LEN(sizeof(int) * count);
    memcpy(CMSG_DATA(header), fds, sizeof(int) * count);
  }
  return sendmsg(fd, &message, MSG_NOSIGNAL | MSG_DONTWAIT) == sizeof(*response);
}
#ifndef DPE_KMS_SERVER_FIXTURE
int main(int argc, char **argv) {
  if (argc != 3 || geteuid() != 0)
    return 1;
  const char *identity = getenv("PKEXEC_UID");
  if (!identity || !*identity)
    return 1;
  for (const char *p = identity; *p; p++)
    if (!isdigit((unsigned char)*p))
      return 1;
  char *end;
  errno = 0;
  unsigned long parsed = strtoul(identity, &end, 10);
  if (errno || *end || !parsed || parsed > UINT32_MAX)
    return 1;
  uid_t uid = (uid_t)parsed;
  pid_t parent = getppid();
  char marker[128];
  if (!dpe_marker_valid(uid, marker) || !dpe_socket_path(argv[1], uid))
    return 1;
  int watcher = inotify_init1(IN_CLOEXEC | IN_NONBLOCK), socket_fd = -1, result = 1;
  gsr_drm drm = {.drmfd = -1};
  if (watcher < 0 ||
      inotify_add_watch(watcher, marker,
                        IN_DELETE_SELF | IN_MOVE_SELF | IN_ATTRIB | IN_DONT_FOLLOW) < 0 ||
      !dpe_marker_valid(uid, marker))
    goto done;
  drm.drmfd = dpe_card_open(argv[2]);
  if (drm.drmfd < 0)
    goto done;
  socket_fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
  struct sockaddr_un address = {.sun_family = AF_UNIX};
  snprintf(address.sun_path, sizeof(address.sun_path), "%s", argv[1]);
  struct timeval timeout = {.tv_sec = 2};
  if (socket_fd < 0 || setsockopt(socket_fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout)) ||
      connect(socket_fd, (void *)&address, sizeof(address)) ||
      !dpe_peer(socket_fd, uid, parent, SOCK_STREAM))
    goto done;
  int replacement = -1;
  if (!dpe_request(socket_fd, true, uid, parent, &replacement))
    goto done;
  close(socket_fd);
  socket_fd = replacement;
  gsr_kms_response response = {.version = GSR_KMS_PROTOCOL_VERSION};
  if (!dpe_send(socket_fd, &response))
    goto done;
  for (;;) {
    struct pollfd wait[] = {{.fd = socket_fd, .events = POLLIN}, {.fd = watcher, .events = POLLIN}};
    int polled = poll(wait, 2, -1);
    if (polled < 0 && errno == EINTR)
      continue;
    if (polled < 0 || wait[1].revents || !(wait[0].revents & POLLIN))
      break;
    if (!dpe_request(socket_fd, false, uid, parent, &replacement))
      break;
    response = (gsr_kms_response){.version = GSR_KMS_PROTOCOL_VERSION};
    kms_get_fb(&drm, &response);
    bool sent = dpe_send(socket_fd, &response);
    kms_response_close_fds(&response);
    if (!sent)
      break;
  }
  result = 0;
done:
  if (drm.drmfd >= 0)
    close(drm.drmfd);
  if (socket_fd >= 0)
    close(socket_fd);
  if (watcher >= 0)
    close(watcher);
  return result;
}
#endif
