// SPDX-License-Identifier: MIT
#include "../../integration/video/encoder-abi.h"
#include "../../integration/video/encoder-import-cache.h"
#include <va/va.h>
#include <va/va_drmcommon.h>
#include <va/va_vpp.h>
#include <libavcodec/avcodec.h>
#include <libavcodec/h264_levels.h>
#include <libavcodec/vaapi_encode.h>
#include <libavutil/hwcontext.h>
#include <libavutil/hwcontext_vaapi.h>
#include <libavutil/opt.h>
