// SPDX-License-Identifier: MIT
#include <va/va.h>
#include <va/va_drm.h>
#include <va/va_drmcommon.h>
#include <va/va_vpp.h>
#include <drm_fourcc.h>
#include "../../integration/video/encoder-abi.h"
#include "../../integration/video/direct-capture-gpu.h"
#include "../../integration/video/direct-capture.h"
#include <linux/sync_file.h>
#include <linux/videodev2.h>
// Layout-compatible with the checksum-pinned public Electron video SDK header.
struct dpe_discord_yuv {
  const uint8_t *y, *u, *v;
  int32_t y_stride, u_stride, v_stride;
};
struct dpe_discord_frame {
  int64_t timestamp_us;
  union { struct dpe_discord_yuv yuv; void *electron; } frame;
  int32_t width, height, type, rotation;
};
#include <node_api.h>
#include <link.h>
