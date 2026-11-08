// SPDX-License-Identifier: MIT
#include "../../integration/video/vkcapture-relay.h"
#include <drm_fourcc.h>
#include <linux/sync_file.h>
#include <pipewire/context.h>
#include <pipewire/main-loop.h>
#include <pipewire/core.h>
#include <pipewire/stream.h>
#include <spa/buffer/meta.h>
#include <spa/param/format.h>
#include <spa/param/param.h>
#include <spa/param/video/raw.h>
#include <spa/pod/pod.h>
#include <xf86drm.h>
static const uint32_t DPE_RELAY_ARGB = DRM_FORMAT_ARGB8888;
static const uint32_t DPE_RELAY_XRGB = DRM_FORMAT_XRGB8888;
static const uint32_t DPE_RELAY_ABGR = DRM_FORMAT_ABGR8888;
static const uint32_t DPE_RELAY_XBGR = DRM_FORMAT_XBGR8888;
static const uint32_t DPE_RELAY_MAGIC = DPE_VK_MAGIC;
static const uint32_t DPE_RELAY_VERSION = DPE_VK_VERSION;
static const uint32_t DPE_RELAY_TEXTURE = DPE_VK_TEXTURE;
static const uint32_t DPE_POD_OBJECT = SPA_TYPE_Object;
static const uint32_t DPE_POD_ID = SPA_TYPE_Id;
static const uint32_t DPE_POD_FRACTION = SPA_TYPE_Fraction;
static const uint32_t DPE_POD_RECTANGLE = SPA_TYPE_Rectangle;
static const uint32_t DPE_POD_CHOICE = SPA_TYPE_Choice;
static const uint32_t DPE_POD_FORMAT = SPA_TYPE_OBJECT_Format;
static const uint32_t DPE_POD_ENUM_FORMAT = SPA_PARAM_EnumFormat;
static const uint32_t DPE_POD_MEDIA_TYPE = SPA_FORMAT_mediaType;
static const uint32_t DPE_POD_MEDIA_SUBTYPE = SPA_FORMAT_mediaSubtype;
static const uint32_t DPE_POD_VIDEO = SPA_MEDIA_TYPE_video;
static const uint32_t DPE_POD_RAW = SPA_MEDIA_SUBTYPE_raw;
static const uint32_t DPE_POD_SIZE = SPA_FORMAT_VIDEO_size;
static const uint32_t DPE_POD_RATE = SPA_FORMAT_VIDEO_framerate;
static const uint32_t DPE_POD_MAX_RATE = SPA_FORMAT_VIDEO_maxFramerate;
#ifdef SPA_POD_PROP_FLAG_DROP
static const uint32_t DPE_POD_DROP = SPA_POD_PROP_FLAG_DROP;
#else
// Older SPA filters cannot express a preference that disappears when absent.
static const uint32_t DPE_POD_DROP = 0;
#endif
static const uint32_t DPE_POD_RANGE = SPA_CHOICE_Range;
static const uint32_t DPE_POD_NONE = SPA_CHOICE_None;
static const uint32_t DPE_POD_ENUM = SPA_CHOICE_Enum;
static const uint32_t DPE_POD_FLAGS = SPA_CHOICE_Flags;
static const uint32_t DPE_POD_INT = SPA_TYPE_Int;
static const uint32_t DPE_POD_LONG = SPA_TYPE_Long;
static const uint32_t DPE_POD_VIDEO_FORMAT = SPA_FORMAT_VIDEO_format;
static const uint32_t DPE_POD_MODIFIER = SPA_FORMAT_VIDEO_modifier;
static const uint32_t DPE_POD_DONT_FIXATE = SPA_POD_PROP_FLAG_DONT_FIXATE;
static const uint32_t DPE_POD_MANDATORY = SPA_POD_PROP_FLAG_MANDATORY;
static const uint32_t DPE_POD_BUFFERS = SPA_TYPE_OBJECT_ParamBuffers;
static const uint32_t DPE_PARAM_BUFFERS = SPA_PARAM_Buffers;
static const uint32_t DPE_POD_BUFFER_COUNT = SPA_PARAM_BUFFERS_buffers;
static const uint32_t DPE_POD_BLOCKS = SPA_PARAM_BUFFERS_blocks;
static const uint32_t DPE_POD_BUFFER_SIZE = SPA_PARAM_BUFFERS_size;
static const uint32_t DPE_POD_STRIDE = SPA_PARAM_BUFFERS_stride;
static const uint32_t DPE_POD_DATA_TYPE = SPA_PARAM_BUFFERS_dataType;
static const uint32_t DPE_POD_META = SPA_TYPE_OBJECT_ParamMeta;
static const uint32_t DPE_PARAM_META = SPA_PARAM_Meta;
static const uint32_t DPE_POD_META_TYPE = SPA_PARAM_META_type;
static const uint32_t DPE_POD_META_SIZE = SPA_PARAM_META_size;
static const uint32_t DPE_PARAM_RELAY_META = DPE_RELAY_META;
static const uint32_t DPE_PARAM_RELAY_TYPE = DPE_RELAY_META_TYPE;
static const uint32_t DPE_POD_HEADER = SPA_META_Header;
static const uint32_t DPE_STREAM_EVENTS_VERSION = PW_VERSION_STREAM_EVENTS;
static const uint32_t DPE_STREAM_INPUT = PW_DIRECTION_INPUT;
static const uint32_t DPE_STREAM_OUTPUT = PW_DIRECTION_OUTPUT;
static const uint32_t DPE_STREAM_DRIVER = PW_STREAM_FLAG_DRIVER;
static const uint32_t DPE_STREAM_ALLOC = PW_STREAM_FLAG_ALLOC_BUFFERS;
static const int DPE_STREAM_UNCONNECTED = PW_STREAM_STATE_UNCONNECTED;
static const uint32_t DPE_STREAM_AUTOCONNECT = PW_STREAM_FLAG_AUTOCONNECT;
static const int DPE_STREAM_ERROR = PW_STREAM_STATE_ERROR;
static const uint32_t DPE_PARAM_FORMAT = SPA_PARAM_Format;
