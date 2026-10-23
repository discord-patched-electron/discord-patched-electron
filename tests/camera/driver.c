// SPDX-License-Identifier: MIT
#include <linux/videodev2.h>
#include <sys/ioctl.h>
#include <stdarg.h>
#include <errno.h>
#include <stdlib.h>
#include <string.h>
int calls = 0;
int ioctl(int fd, unsigned long request, ...) {
  (void)fd;
  va_list args; va_start(args, request); void *arg = va_arg(args, void *); va_end(args);
  if (request == VIDIOC_ENUM_FMT) {
    struct v4l2_fmtdesc *d = arg;
    const char *mode = getenv("DPE_TEST_ENUM");
    if (mode && !strcmp(mode,"failed")) { errno = EIO; return -1; }
    if (mode && !strcmp(mode,"empty")) { errno = EINVAL; return -1; }
    if (d->index > 0) { errno = EINVAL; return -1; }
    d->pixelformat = mode && !strcmp(mode,"changed") ? V4L2_PIX_FMT_NV12 : V4L2_PIX_FMT_MJPEG;
    return 0;
  }
  calls++;
  if (arg == (void *)1) { errno = EFAULT; return -1; }
  if (request == VIDIOC_TRY_FMT) { ((struct v4l2_format *)arg)->fmt.pix.width = 640; return 17; }
  errno = ENOTTY; return -1;
}
