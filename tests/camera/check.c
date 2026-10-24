// SPDX-License-Identifier: MIT
#define _GNU_SOURCE
#include <assert.h>
#include <dlfcn.h>
#include <errno.h>
#include <linux/videodev2.h>
#include <stdlib.h>
#include <sys/ioctl.h>
int main(void) {
  void *module = dlopen(getenv("DPE_CAMERA_VOICE_MODULE"), RTLD_NOW);
  assert(module);
  int (*probe)(int, unsigned long, void *) =
      dlsym(module, "_ZN6webrtc18videocapturemodule14DeviceInfoV4l216FillCapabilitiesEi");
  int (*other)(int, unsigned long, void *) = dlsym(module, "other");
  int *calls = dlsym(RTLD_DEFAULT, "calls");
  assert(probe && other && calls);
  struct v4l2_format f = {.type = V4L2_BUF_TYPE_VIDEO_CAPTURE};
  f.fmt.pix.pixelformat = V4L2_PIX_FMT_NV12;
  int bypass = getenv("DPE_TEST_BYPASS") != NULL;
  int result = probe(91, VIDIOC_TRY_FMT, &f);
  assert(result == (bypass ? 17 : -1));
  assert(*calls == (bypass ? 1 : 0));
  if (!bypass)
    assert(errno == EINVAL);
  *calls = 0;
  assert(other(91, VIDIOC_TRY_FMT, &f) == 17);
  assert(*calls == 1);
  assert(other(91, VIDIOC_TRY_FMT, (void *)1) == -1 && errno == EFAULT);
  f.fmt.pix.pixelformat = V4L2_PIX_FMT_MJPEG;
  assert(probe(91, VIDIOC_TRY_FMT, &f) == 17);
  assert(f.fmt.pix.width == 640);
  assert(probe(91, FIONREAD, &f) == -1 && errno == ENOTTY);
  f.type = V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE;
  f.fmt.pix.pixelformat = V4L2_PIX_FMT_NV12;
  assert(probe(91, VIDIOC_TRY_FMT, &f) == 17);
  f.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
  for (int i = 0; i < 3; i++) {
    setenv("DPE_TEST_ENUM", i == 0 ? "failed" : i == 1 ? "empty" : "changed", 1);
    assert(probe(91, VIDIOC_TRY_FMT, &f) == 17);
  }
  return 0;
}
