#define _GNU_SOURCE
#include "../../integration/video/direct-capture-wire.h"
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <sys/eventfd.h>
#include <sys/socket.h>
#include <unistd.h>

static unsigned descriptors(void) {
  unsigned count = 0;
  for (int fd = 0; fd < 256; ++fd)
    count += fcntl(fd, F_GETFD) >= 0;
  return count;
}
int main(void) {
  int sockets[2], fds[DPE_DIRECT_FDS];
  assert(!socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, sockets));
  assert(dpe_direct_peer(sockets[0]));
  struct dpe_direct_packet p = dpe_direct_packet(DPE_DIRECT_FRAME), received;
  unsigned count;
  assert(dpe_direct_receive(sockets[0], &received, fds, &count) == 0);
  int source = eventfd(0, EFD_CLOEXEC);
  assert(source >= 0);
  p.descriptors = 1;
  assert(dpe_direct_send(sockets[1], &p, &source, 1));
  assert(dpe_direct_receive(sockets[0], &received, fds, &count) == 1 && count == 1);
  assert(fcntl(fds[0], F_GETFD) & FD_CLOEXEC);
  dpe_direct_close(fds, count);
  unsigned baseline = descriptors();
  // Invalid headers and ancillary overflow close all received descriptors.
  for (unsigned i = 0; i < 100; ++i) {
    assert(dpe_direct_send(sockets[1], &p, &source, 1));
    assert(dpe_direct_receive_raw(sockets[0], &received, sizeof(received), fds, 0, &count) == -1);
    assert(count == 0 && descriptors() == baseline);
    p.descriptors = 0;
    assert(dpe_direct_send(sockets[1], &p, NULL, 0));
    assert(dpe_direct_receive_raw(sockets[0], &received, sizeof(received) - 1, fds, DPE_DIRECT_FDS,
                                  &count) == -1);
    assert(descriptors() == baseline);
    p.descriptors = 1;
  }
  p.abi++;
  assert(!dpe_direct_send(sockets[1], &p, &source, 1));
  assert(send(sockets[1], &p, sizeof(p), MSG_NOSIGNAL) == sizeof(p));
  assert(dpe_direct_receive(sockets[0], &received, fds, &count) == -1);
  close(sockets[1]);
  assert(dpe_direct_receive(sockets[0], &received, fds, &count) == -1);
  close(source);
  close(sockets[0]);
  puts("direct wire ownership, bounds and EOF passed");
}
