// SPDX-License-Identifier: GPL-3.0-only
// Exercise the privileged server's boundary without privileges or DRM access.
#define DPE_KMS_SERVER_FIXTURE
#include "../../integration/video/kms-server.c"
#include <assert.h>
#include <dirent.h>
static unsigned descriptor_count(void) {
  DIR *dir = opendir("/proc/self/fd");
  assert(dir);
  unsigned count = 0;
  while (readdir(dir))
    count++;
  closedir(dir);
  return count;
}
static void request(int socket, uint32_t version, int type, int descriptor) {
  gsr_kms_request data = {.version = version, .type = type, .new_connection_fd = -1};
  struct iovec vector = {.iov_base = &data, .iov_len = sizeof(data)};
  char control[CMSG_SPACE(sizeof(int))] = {0};
  struct msghdr message = {.msg_iov = &vector, .msg_iovlen = 1};
  if (descriptor >= 0) {
    message.msg_control = control;
    message.msg_controllen = sizeof(control);
    struct cmsghdr *header = CMSG_FIRSTHDR(&message);
    header->cmsg_level = SOL_SOCKET;
    header->cmsg_type = SCM_RIGHTS;
    header->cmsg_len = CMSG_LEN(sizeof(int));
    memcpy(CMSG_DATA(header), &descriptor, sizeof(descriptor));
  }
  assert(sendmsg(socket, &message, MSG_NOSIGNAL) == sizeof(data));
}
int main(void) {
  char directory[] = "/tmp/dpe-kms-XXXXXX";
  assert(mkdtemp(directory));
  char socket_path[64];
  snprintf(socket_path, sizeof(socket_path), "%s/socket", directory);
  int listener = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
  struct sockaddr_un address = {.sun_family = AF_UNIX};
  snprintf(address.sun_path, sizeof(address.sun_path), "%s", socket_path);
  assert(!bind(listener, (void *)&address, sizeof(address)));
  assert(dpe_socket_path(socket_path, getuid()));
  assert(!dpe_socket_path(socket_path, getuid() + 1));
  assert(!dpe_socket_path("/tmp/elsewhere/socket", getuid()));
  assert(!chmod(directory, 0755));
  assert(!dpe_socket_path(socket_path, getuid()));
  assert(!chmod(directory, 0700));
  assert(!unlink(socket_path));
  assert(!symlink("/tmp/other-socket", socket_path));
  assert(!dpe_socket_path(socket_path, getuid()));
  assert(!unlink(socket_path));
  close(listener);
  assert(!rmdir(directory));
  assert(dpe_card_open("/tmp/arbitrary") == -1);
  assert(dpe_card_open("/dev/dri/renderD128") == -1);
  assert(dpe_card_open("/dev/dri/card1/../../something") == -1);

  int wire[2], replacement[2], accepted = -1;
  assert(!socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, wire));
  assert(!socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, replacement));
  unsigned initial = descriptor_count();
  request(wire[0], 6, KMS_REQUEST_TYPE_REPLACE_CONNECTION, replacement[1]);
  assert(dpe_request(wire[1], true, getuid(), getpid(), &accepted));
  assert(accepted >= 0 && (fcntl(accepted, F_GETFD) & FD_CLOEXEC));
  close(accepted);
  for (unsigned variant = 0; variant < 5; variant++) {
    int bad = open("/dev/null", O_RDONLY | O_CLOEXEC);
    request(wire[0], variant == 0 ? 5 : 6,
            variant == 1 ? KMS_REQUEST_TYPE_GET_KMS : KMS_REQUEST_TYPE_REPLACE_CONNECTION,
            variant == 2 ? bad : replacement[1]);
    close(bad);
    assert(!dpe_request(wire[1], true, variant == 3 ? getuid() + 1 : getuid(),
                        variant == 4 ? getpid() + 1 : getpid(), &accepted));
    assert(descriptor_count() == initial);
  }
  request(wire[0], 6, KMS_REQUEST_TYPE_GET_KMS, -1);
  assert(dpe_request(wire[1], false, getuid(), getpid(), &accepted));
  request(wire[0], 6, KMS_REQUEST_TYPE_GET_KMS, replacement[1]);
  assert(!dpe_request(wire[1], false, getuid(), getpid(), &accepted));
  assert(descriptor_count() == initial);
  gsr_kms_response response = {.version = 6};
  assert(dpe_send(wire[1], &response));
  assert(recv(wire[0], &response, sizeof(response), 0) == sizeof(response));
  assert(response.version == 6 && !response.num_items);
  close(wire[0]);
  close(wire[1]);
  close(replacement[0]);
  close(replacement[1]);
  puts("KMS server paths, peer credentials, strict requests and descriptor cleanup passed");
}
