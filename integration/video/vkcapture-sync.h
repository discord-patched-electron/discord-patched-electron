// SPDX-License-Identifier: MIT
#ifndef DPE_VKCAPTURE_SYNC_H
#define DPE_VKCAPTURE_SYNC_H
#include <linux/sync_file.h>
#include <stdbool.h>
#include <sys/ioctl.h>
// A readable sync_file may represent an error, not successful GPU completion.
static inline bool dpe_vk_fence_signaled(int fd) {
  struct sync_file_info info = {0};
  return ioctl(fd, SYNC_IOC_FILE_INFO, &info) == 0 && info.status > 0;
}
#endif
