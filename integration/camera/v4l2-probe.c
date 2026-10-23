// SPDX-License-Identifier: MIT
// Skip unsupported formats in Discord's legacy WebRTC capability matrix only.
// The package supplies the exact ELF function range and module path. All other
// callers, requests and supported formats retain the original ioctl behavior.
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <linux/videodev2.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>

typedef int (*ioctl_fn)(int, unsigned long, ...);
static ioctl_fn next_ioctl;
static pthread_once_t once = PTHREAD_ONCE_INIT;
static uintptr_t probe_start, probe_end;
static const char *voice_module;

static uintptr_t address(const char *name) {
  const char *value = getenv(name);
  char *end;
  if (!value || !*value) return 0;
  errno = 0;
  unsigned long long parsed = strtoull(value, &end, 0);
  return errno || *end || parsed > UINTPTR_MAX ? 0 : (uintptr_t)parsed;
}

static void initialize(void) {
  next_ioctl = (ioctl_fn)dlsym(RTLD_NEXT, "ioctl");
  if (getenv("DPE_CAMERA_FAST_PROBE") &&
      !strcmp(getenv("DPE_CAMERA_FAST_PROBE"), "0")) return;
  voice_module = getenv("DPE_CAMERA_VOICE_MODULE");
  probe_start = address("DPE_CAMERA_PROBE_START");
  probe_end = address("DPE_CAMERA_PROBE_END");
}

// No cache: hotplug, descriptor reuse and input changes must be re-enumerated.
// Unknown/failed/truncated enumeration keeps the upstream probing behavior.
static int unsupported(int fd, const struct v4l2_format *format) {
  for (unsigned i = 0; i < 256; ++i) {
    struct v4l2_fmtdesc description = {
        .index = i, .type = V4L2_BUF_TYPE_VIDEO_CAPTURE};
    if (next_ioctl(fd, VIDIOC_ENUM_FMT, &description) < 0)
      return i > 0 && errno == EINVAL;
    if (description.pixelformat == format->fmt.pix.pixelformat) return 0;
  }
  return 0;
}

__attribute__((visibility("default")))
int ioctl(int fd, unsigned long request, ...) {
  va_list args;
  va_start(args, request);
  void *argument = va_arg(args, void *);
  va_end(args);
  int saved_errno = errno;
  pthread_once(&once, initialize);
  if (!next_ioctl) { errno = ENOSYS; return -1; }
  if (request == VIDIOC_TRY_FMT && argument && voice_module &&
      probe_start && probe_end > probe_start) {
    void *caller = __builtin_extract_return_addr(__builtin_return_address(0));
    Dl_info info;
    if (dladdr(caller, &info) && info.dli_fname &&
        !strcmp(info.dli_fname, voice_module)) {
      uintptr_t offset = (uintptr_t)caller - (uintptr_t)info.dli_fbase;
      if (offset >= probe_start && offset < probe_end &&
          ((struct v4l2_format *)argument)->type == V4L2_BUF_TYPE_VIDEO_CAPTURE &&
          unsupported(fd, argument)) {
        errno = EINVAL;
        return -1;
      }
    }
  }
  errno = saved_errno;
  return next_ioctl(fd, request, argument);
}
