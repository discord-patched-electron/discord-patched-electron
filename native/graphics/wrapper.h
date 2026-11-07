// SPDX-License-Identifier: GPL-3.0-only
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES3/gl3.h>
#include <GLES2/gl2ext.h>
#include <gbm.h>
#include <drm_fourcc.h>
#include <pipewire/loop.h>
#include <spa/buffer/buffer.h>
#include <linux/sync_file.h>
#include "../../integration/video/kms-gpu.h"
static const uint64_t DPE_GRAPHICS_MOD_INVALID = DRM_FORMAT_MOD_INVALID;
static const uint32_t DPE_GRAPHICS_ARGB = DRM_FORMAT_ARGB8888;
static const uint32_t DPE_GRAPHICS_RENDERING = GBM_BO_USE_RENDERING;
static const uint16_t DPE_GRAPHICS_TEXTURE = DPE_VK_TEXTURE;
static const uint32_t DPE_GRAPHICS_ABGR = DRM_FORMAT_ABGR8888;
static const uint32_t DPE_GRAPHICS_XRGB = DRM_FORMAT_XRGB8888;
static const uint32_t DPE_GRAPHICS_XBGR = DRM_FORMAT_XBGR8888;
static const uint32_t DPE_GRAPHICS_DATA_DMABUF = SPA_DATA_DmaBuf;
static const uint32_t DPE_GRAPHICS_DATA_RW = SPA_DATA_FLAG_READWRITE;
