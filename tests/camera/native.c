// SPDX-License-Identifier: MIT
#include <sys/ioctl.h>
// A named function gives the shim an exact range, like the real voice module.
__attribute__((noinline))
int probe(int fd, unsigned long request, void *arg)
  __asm__("_ZN6webrtc18videocapturemodule14DeviceInfoV4l216FillCapabilitiesEi");
int probe(int fd, unsigned long request, void *arg) {
  return ioctl(fd, request, arg);
}
__attribute__((noinline))
int other(int fd, unsigned long request, void *arg) {
  return ioctl(fd, request, arg);
}
