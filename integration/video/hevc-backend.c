// Built into a separate shared object with a private, minimal FFmpeg. Never
// link a second system libavcodec into Electron's global symbol namespace.
#include "av1-crop.h"
#include "av1-level.h"
#include "encoder-abi.h"
#include "hevc-level.h"
#include <glob.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <va/va_drmcommon.h>
#include <va/va_vpp.h>

#include "libavcodec/vaapi_encode.h"
#include <libavcodec/avcodec.h>
#include <libavutil/hwcontext.h>
#include <libavutil/hwcontext_vaapi.h>
#include <libavutil/opt.h>

#define PENDING_FRAMES 4
struct pending_frame {
  bool used;
  int64_t pts;
  struct encoder_frame_meta meta;
  struct dpe_av1_crop crop;
};
struct hevc_encoder {
  char *node;
  AVCodecContext *codec;
  AVBufferRef *device, *frames, *upload_frames;
  AVFrame *cpu, *input, *black;
  VASurfaceID *initialized_surfaces;
  size_t initialized_count, initialized_capacity;
  AVPacket *packet;
  struct encoder_session session;
  struct encoder_options options;
  struct encoder_dimensions opened_size, encoded_size;
  struct pending_frame pending[PENDING_FRAMES];
  void *context;
  encoder_encoded_fn encoded;
  encoder_dropped_fn dropped;
  int64_t last_pts;
  uint32_t vendor;
  VAConfigID filter_config;
  VAContextID filter_context;
  bool filtering, vcn4_av1;
};

static bool dimensions_valid(struct encoder_dimensions size) {
  return size.width >= 128 && size.height >= 128 && !(size.width & 1) && !(size.height & 1) &&
         size.width <= 8192 && size.height <= 8192 &&
         (uint64_t)size.width * size.height <= UINT64_C(8192) * 4320;
}
static bool options_valid(struct encoder_options options) {
  return dimensions_valid(options.dimensions) && options.fps <= 240 &&
         options.bitrate_target <= options.bitrate_maximum &&
         options.bitrate_maximum <= INT_MAX / 2;
}
static void close_codec(struct hevc_encoder *encoder) {
  avcodec_free_context(&encoder->codec);
  av_frame_free(&encoder->cpu);
  av_frame_free(&encoder->input);
  av_frame_free(&encoder->black);
  free(encoder->initialized_surfaces);
  encoder->initialized_surfaces = NULL;
  encoder->initialized_count = encoder->initialized_capacity = 0;
  av_packet_free(&encoder->packet);
  av_buffer_unref(&encoder->frames);
  av_buffer_unref(&encoder->upload_frames);
  if (encoder->filtering) {
    AVHWDeviceContext *device = (void *)encoder->device->data;
    AVVAAPIDeviceContext *va = device->hwctx;
    vaDestroyContext(va->display, encoder->filter_context);
    vaDestroyConfig(va->display, encoder->filter_config);
    encoder->filtering = false;
  }
  // Freeing the codec and input releases imported surfaces and their producer
  // leases before telling the caller which outstanding frames were dropped.
  for (size_t i = 0; i < PENDING_FRAMES; i++) {
    if (encoder->pending[i].used) {
      encoder->pending[i].used = false;
      encoder->dropped(encoder->context, &encoder->pending[i].meta);
    }
  }
}
static void destroy(void *opaque) {
  struct hevc_encoder *encoder = opaque;
  if (!encoder)
    return;
  close_codec(encoder);
  av_buffer_unref(&encoder->device);
  free(encoder->node);
  free(encoder);
}

static uint32_t device_vendor(AVBufferRef *device) {
  AVHWDeviceContext *context = (void *)device->data;
  AVVAAPIDeviceContext *va = context->hwctx;
  const char *vendor = vaQueryVendorString(va->display);
  if (vendor && (strstr(vendor, "AMD") || strstr(vendor, "ATI")))
    return 1;
  if (vendor && (strstr(vendor, "Intel") || strstr(vendor, "iHD") || strstr(vendor, "i965")))
    return 2;
  return 4;
}
static uint32_t vendor(void *opaque) { return ((struct hevc_encoder *)opaque)->vendor; }

// Query this render node, not another GPU in a mixed-device system. Unknown
// kernel discovery data retains the generic aligned-surface path.
static bool device_vcn4(const char *node) {
  struct stat status;
  if (stat(node, &status) || !S_ISCHR(status.st_mode))
    return false;
  char pattern[PATH_MAX];
  if (snprintf(pattern, sizeof(pattern),
               "/sys/dev/char/%u:%u/device/ip_discovery/die/*/UVD/*/major", major(status.st_rdev),
               minor(status.st_rdev)) >= (int)sizeof(pattern))
    return false;
  glob_t versions = {0};
  bool valid = glob(pattern, 0, NULL, &versions) == 0 && versions.gl_pathc;
  for (size_t i = 0; valid && i < versions.gl_pathc; i++) {
    FILE *file = fopen(versions.gl_pathv[i], "r");
    unsigned version;
    valid = file && fscanf(file, "%u", &version) == 1 && version == 4;
    if (file)
      fclose(file);
  }
  globfree(&versions);
  return valid;
}
static bool native_short_padding(struct hevc_encoder *encoder, struct encoder_dimensions size) {
  // Mesa's VCN4 rule is generic: an already aligned width and height 8 mod 16
  // can use firmware-generated two-row padding (1080 -> 1082, 360 -> 362).
  return encoder->vcn4_av1 && !(size.width & 63) && (size.height & 15) == 8;
}

static struct encoder_dimensions coded_size(struct hevc_encoder *encoder,
                                            struct encoder_options options) {
  struct encoder_dimensions size = options.dimensions;
  if (encoder->session.codec == 2 && encoder->vendor == 1 && !native_short_padding(encoder, size)) {
    size.width = (size.width + 63u) & ~63u;
    size.height = (size.height + 15u) & ~15u;
  }
  return size;
}

static int stream_level(struct hevc_encoder *encoder, struct encoder_options options) {
  uint32_t width = encoder->session.maximum.width > options.dimensions.width
                       ? encoder->session.maximum.width
                       : options.dimensions.width;
  uint32_t height = encoder->session.maximum.height > options.dimensions.height
                        ? encoder->session.maximum.height
                        : options.dimensions.height;
  uint32_t fps = encoder->session.fps > options.fps ? encoder->session.fps : options.fps;
  if (encoder->session.codec == 2) {
    if (encoder->vendor == 1) {
      width = (width + 63u) & ~63u;
      height = (height + 15u) & ~15u;
    }
    return dpe_av1_level(width, height, fps, options.bitrate_maximum);
  }
  return (int)dpe_hevc_level(width, height, fps, options.bitrate_maximum);
}

static AVBufferRef *frame_pool(struct hevc_encoder *encoder, struct encoder_dimensions size) {
  AVBufferRef *pool = av_hwframe_ctx_alloc(encoder->device);
  if (!pool)
    return NULL;
  AVHWFramesContext *frames = (void *)pool->data;
  frames->format = AV_PIX_FMT_VAAPI;
  frames->sw_format = AV_PIX_FMT_NV12;
  frames->width = size.width;
  frames->height = size.height;
  if (av_hwframe_ctx_init(pool) < 0)
    av_buffer_unref(&pool);
  return pool;
}

static bool open_codec(struct hevc_encoder *encoder, struct encoder_options options) {
  const AVCodec *codec =
      avcodec_find_encoder_by_name(encoder->session.codec == 2 ? "av1_vaapi" : "hevc_vaapi");
  if (!codec || !options_valid(options) || !options.fps || !options.bitrate_target)
    return false;
  encoder->options = options;
  encoder->opened_size = options.dimensions;
  struct encoder_dimensions coded = coded_size(encoder, options);
  encoder->encoded_size = coded;
  // Input surfaces keep their original size; firmware adds two bottom rows.
  // Report actual bitstream dimensions independently of the input context.
  if (native_short_padding(encoder, coded))
    encoder->encoded_size.height += 2;
  encoder->frames = frame_pool(encoder, coded);
  if (!encoder->frames)
    return false;
  // GPU inputs are imported rather than copied into this pool. FFmpeg uses
  // this context to describe the input format and allocate reconstructed DPB.
  encoder->codec = avcodec_alloc_context3(codec);
  if (!encoder->codec)
    return false;
  AVCodecContext *context = encoder->codec;
  context->width = coded.width;
  context->height = coded.height;
  context->pix_fmt = AV_PIX_FMT_VAAPI;
  context->hw_frames_ctx = av_buffer_ref(encoder->frames);
  context->time_base = (AVRational){1, 1000};
  context->framerate = (AVRational){options.fps, 1};
  context->bit_rate = options.bitrate_target;
  context->rc_max_rate = options.bitrate_maximum;
  // Keep a one-second reservoir: a per-frame cap starves scene-cut keyframes.
  context->rc_buffer_size = options.bitrate_target;
  context->gop_size =
      encoder->session.keyframe_interval ? (int)encoder->session.keyframe_interval : 6000;
  context->max_b_frames = 0;
  context->level = stream_level(encoder, options);
  if (!context->level && encoder->session.codec == 1)
    return false;
  context->flags |= AV_CODEC_FLAG_LOW_DELAY;
  if (!context->hw_frames_ctx || av_opt_set(context->priv_data, "rc_mode", "VBR", 0) < 0 ||
      av_opt_set_int(context->priv_data, "async_depth", 1, 0) < 0)
    return false;
  // Mesa's packed quality extension is not a portable VAAPI quality scale.
  AVHWDeviceContext *device = (void *)encoder->device->data;
  AVVAAPIDeviceContext *va = device->hwctx;
  const char *driver = vaQueryVendorString(va->display);
  if (encoder->vendor == 1 && driver && strstr(driver, "Mesa Gallium driver "))
    context->compression_level = 0x13;
  if (avcodec_open2(context, codec, NULL) < 0)
    return false;
  if (coded.width != options.dimensions.width || coded.height != options.dimensions.height) {
    encoder->upload_frames = frame_pool(encoder, options.dimensions);
    if (!encoder->upload_frames ||
        vaCreateConfig(va->display, VAProfileNone, VAEntrypointVideoProc, NULL, 0,
                       &encoder->filter_config) != VA_STATUS_SUCCESS)
      return false;
    if (vaCreateContext(va->display, encoder->filter_config, coded.width, coded.height,
                        VA_PROGRESSIVE, NULL, 0, &encoder->filter_context) != VA_STATUS_SUCCESS) {
      vaDestroyConfig(va->display, encoder->filter_config);
      return false;
    }
    encoder->filtering = true;
  }
  encoder->input = av_frame_alloc();
  encoder->packet = av_packet_alloc();
  return encoder->input && encoder->packet;
}

static void *create(const char *node, struct encoder_session session,
                    struct encoder_options options, void *context, encoder_encoded_fn encoded,
                    encoder_dropped_fn dropped) {
  if (!node || !encoded || !dropped || !options_valid(options) ||
      session.keyframe_interval > INT_MAX || (session.codec != 1 && session.codec != 2))
    return NULL;
  struct hevc_encoder *encoder = calloc(1, sizeof(*encoder));
  if (!encoder)
    return NULL;
  encoder->context = context;
  encoder->encoded = encoded;
  encoder->dropped = dropped;
  encoder->session = session;
  encoder->node = malloc(strlen(node) + 1);
  if (!encoder->node)
    goto fail;
  memcpy(encoder->node, node, strlen(node) + 1);
  if (av_hwdevice_ctx_create(&encoder->device, AV_HWDEVICE_TYPE_VAAPI, node, NULL, 0) < 0)
    goto fail;
  encoder->vendor = device_vendor(encoder->device);
  AVHWDeviceContext *device = (void *)encoder->device->data;
  AVVAAPIDeviceContext *va = device->hwctx;
  const char *driver = vaQueryVendorString(va->display);
  encoder->vcn4_av1 = encoder->session.codec == 2 && encoder->vendor == 1 && driver &&
                      strstr(driver, "Mesa Gallium driver ") && device_vcn4(node);
  if ((encoder->vendor != 1 && encoder->vendor != 2) || !open_codec(encoder, options))
    goto fail;
  return encoder;
fail:
  destroy(encoder);
  return NULL;
}

static bool receive_packets(struct hevc_encoder *encoder) {
  int result;
  while ((result = avcodec_receive_packet(encoder->codec, encoder->packet)) >= 0) {
    struct pending_frame *pending = NULL;
    for (size_t i = 0; i < PENDING_FRAMES; i++)
      if (encoder->pending[i].used && encoder->pending[i].pts == encoder->packet->pts) {
        pending = &encoder->pending[i];
        break;
      }
    if (!pending) {
      av_packet_unref(encoder->packet);
      return false;
    }
    pending->used = false;
    if (encoder->session.codec == 2 && (encoder->packet->flags & AV_PKT_FLAG_KEY) &&
        (pending->crop.width != pending->meta.width ||
         pending->crop.height != pending->meta.height)) {
      // AOM crop metadata describes the original picture, independently of
      // hardware coded alignment. Reannounce at each keyframe; stock decoders
      // can ignore it. No pixel work or new Discord packet type is needed.
      uint8_t crop_obu[12];
      size_t offset, length = dpe_av1_crop_write(crop_obu, pending->crop);
      int previous_size = encoder->packet->size;
      if (!length || !dpe_av1_crop_position(encoder->packet->data, previous_size, &offset) ||
          av_grow_packet(encoder->packet, (int)length) < 0) {
        av_packet_unref(encoder->packet);
        return false;
      }
      memmove(encoder->packet->data + offset + length, encoder->packet->data + offset,
              previous_size - offset);
      memcpy(encoder->packet->data + offset, crop_obu, length);
    }
    // Slice-header QP is not a frame average with adaptive quantization. The
    // adapter advertises that contract instead of presenting it as trusted QP.
    uint64_t info =
        ((uint64_t)UINT32_MAX << 32) | ((encoder->packet->flags & AV_PKT_FLAG_KEY) ? 2 : 1);
    encoder->encoded(encoder->context, &pending->meta, info, encoder->packet->data,
                     encoder->packet->size, NULL);
    av_packet_unref(encoder->packet);
  }
  return result == AVERROR(EAGAIN) || result == AVERROR_EOF;
}

static void release_source(void *opaque, uint8_t *unused) {
  (void)unused;
  AVFrame *source = opaque;
  av_frame_free(&source);
}

// Mesa's compositor VPP path leaves pixels outside output_region untouched.
// Initialize each pooled output once with neutral NV12 black. Steady-state
// frames retain one VPP pass, with no CPU readback or per-frame black upload.
static bool initialize_padding(struct hevc_encoder *encoder, AVFrame *output) {
  VASurfaceID surface = (VASurfaceID)(uintptr_t)output->data[3];
  for (size_t i = 0; i < encoder->initialized_count; i++)
    if (encoder->initialized_surfaces[i] == surface)
      return true;
  if (encoder->initialized_count == encoder->initialized_capacity) {
    size_t capacity = encoder->initialized_capacity ? encoder->initialized_capacity * 2 : 8;
    VASurfaceID *next = realloc(encoder->initialized_surfaces, capacity * sizeof(*next));
    if (!next)
      return false;
    encoder->initialized_surfaces = next;
    encoder->initialized_capacity = capacity;
  }
  if (!encoder->black) {
    AVFrame *black = av_frame_alloc();
    if (!black)
      return false;
    black->format = AV_PIX_FMT_NV12;
    black->width = output->width;
    black->height = output->height;
    if (av_frame_get_buffer(black, 32) < 0) {
      av_frame_free(&black);
      return false;
    }
    for (int y = 0; y < black->height; y++)
      memset(black->data[0] + y * black->linesize[0], 16, black->width);
    for (int y = 0; y < black->height / 2; y++)
      memset(black->data[1] + y * black->linesize[1], 128, black->width);
    encoder->black = black;
  }
  if (av_hwframe_transfer_data(output, encoder->black, 0) < 0)
    return false;
  encoder->initialized_surfaces[encoder->initialized_count++] = surface;
  return true;
}

static bool filter_input(struct hevc_encoder *encoder) {
  if (!encoder->filtering)
    return true;
  AVFrame *output = av_frame_alloc();
  if (!output || av_hwframe_get_buffer(encoder->frames, output, 0) < 0 ||
      !initialize_padding(encoder, output)) {
    av_frame_free(&output);
    return false;
  }
  // Keep the borrowed producer surface leased through VPP and encode. VA-API
  // retains surface dependencies; releasing the input at vaEndPicture would
  // allow its capture pool to overwrite it while the GPU is still reading.
  AVFrame *source = av_frame_clone(encoder->input);
  output->opaque_ref =
      source ? av_buffer_create((uint8_t *)source, sizeof(*source), release_source, source, 0)
             : NULL;
  if (!output->opaque_ref) {
    av_frame_free(&source);
    av_frame_free(&output);
    return false;
  }
  AVHWDeviceContext *device = (void *)encoder->device->data;
  AVVAAPIDeviceContext *va = device->hwctx;
  VARectangle input_region = {0, 0, encoder->input->width, encoder->input->height};
  // Keep the entire source at 1:1 sampling. Even offsets preserve the NV12
  // chroma grid; unequal padding differs by at most two luma pixels.
  VARectangle output_region = {((output->width - encoder->input->width) / 2) & ~1,
                               ((output->height - encoder->input->height) / 2) & ~1,
                               encoder->input->width, encoder->input->height};
  VAProcPipelineParameterBuffer parameters = {
      .surface = (VASurfaceID)(uintptr_t)encoder->input->data[3],
      .surface_region = &input_region,
      .output_region = &output_region,
      .output_background_color = 0xff000000,
      .surface_color_standard = VAProcColorStandardBT709,
      .output_color_standard = VAProcColorStandardBT709,
  };
  VABufferID buffer;
  bool success =
      vaCreateBuffer(va->display, encoder->filter_context, VAProcPipelineParameterBufferType,
                     sizeof(parameters), 1, &parameters, &buffer) == VA_STATUS_SUCCESS;
  if (success) {
    success = vaBeginPicture(va->display, encoder->filter_context,
                             (VASurfaceID)(uintptr_t)output->data[3]) == VA_STATUS_SUCCESS;
    if (success) {
      bool rendered =
          vaRenderPicture(va->display, encoder->filter_context, &buffer, 1) == VA_STATUS_SUCCESS;
      success = vaEndPicture(va->display, encoder->filter_context) == VA_STATUS_SUCCESS && rendered;
      if (!success)
        vaSyncSurface(va->display, (VASurfaceID)(uintptr_t)output->data[3]);
    }
    vaDestroyBuffer(va->display, buffer);
  }
  if (success) {
    av_frame_unref(encoder->input);
    av_frame_move_ref(encoder->input, output);
  }
  av_frame_free(&output);
  return success;
}
static bool send_frame(struct hevc_encoder *encoder, const struct encoder_frame_meta *meta,
                       struct encoder_frame_info info) {
  if (!filter_input(encoder)) {
    av_frame_unref(encoder->input);
    return false;
  }
  struct pending_frame *pending = NULL;
  for (size_t i = 0; i < PENDING_FRAMES; i++)
    if (!encoder->pending[i].used) {
      pending = &encoder->pending[i];
      break;
    }
  if (!pending) {
    av_frame_unref(encoder->input);
    return false;
  }
  int64_t pts = info.timestamp_ms <= INT64_MAX ? (int64_t)info.timestamp_ms : 0;
  if (pts <= encoder->last_pts)
    pts = encoder->last_pts + 1;
  encoder->last_pts = pts;
  *pending = (struct pending_frame){.used = true, .pts = pts, .meta = *meta};
  pending->meta.width = encoder->encoded_size.width;
  pending->meta.height = encoder->encoded_size.height;
  pending->crop = (struct dpe_av1_crop){
      .x = ((encoder->codec->width - meta->width) / 2) & ~1u,
      .y = ((encoder->codec->height - meta->height) / 2) & ~1u,
      .width = meta->width,
      .height = meta->height,
  };
  encoder->input->pts = pts;
  encoder->input->pict_type = info.idr ? AV_PICTURE_TYPE_I : AV_PICTURE_TYPE_NONE;
  int result = avcodec_send_frame(encoder->codec, encoder->input);
  if (result < 0 && encoder->filtering) {
    AVHWDeviceContext *device = (void *)encoder->device->data;
    AVVAAPIDeviceContext *va = device->hwctx;
    // A failed submission need not retain AVFrame references. Finish VPP
    // before releasing the lease that prevents the producer reusing input.
    vaSyncSurface(va->display, (VASurfaceID)(uintptr_t)encoder->input->data[3]);
  }
  av_frame_unref(encoder->input);
  if (result < 0) {
    pending->used = false;
    return false;
  }
  return receive_packets(encoder);
}

static bool update(void *opaque, struct encoder_options options) {
  struct hevc_encoder *encoder = opaque;
  if (!options_valid(options))
    return false;
  if (!options.fps || !options.bitrate_target) {
    // Suspension does not reset codec history, submit a zero-sized HRD, or
    // manufacture frames to keep a reported bitrate high.
    encoder->options = options;
    return true;
  }
  int required_level = stream_level(encoder, options);
  if (encoder->session.codec == 1 && !required_level)
    return false;
  if (!encoder->codec || options.dimensions.width != encoder->opened_size.width ||
      options.dimensions.height != encoder->opened_size.height ||
      required_level > encoder->codec->level) {
    struct encoder_options previous = encoder->options;
    if (encoder->codec) {
      previous.dimensions = encoder->opened_size;
      previous.fps = encoder->codec->framerate.num;
      previous.bitrate_target = encoder->codec->bit_rate;
      previous.bitrate_maximum = encoder->codec->rc_max_rate;
    }
    // Retire the old hardware session before initializing its replacement.
    // Recover the previous settings if the new geometry cannot be opened.
    close_codec(encoder);
    av_buffer_unref(&encoder->device);
    if (av_hwdevice_ctx_create(&encoder->device, AV_HWDEVICE_TYPE_VAAPI, encoder->node, NULL, 0) <
        0)
      return false;
    if (open_codec(encoder, options))
      return true;
    close_codec(encoder);
    if (!open_codec(encoder, previous))
      close_codec(encoder);
    return false;
  }
  bool changed = encoder->options.fps != options.fps ||
                 encoder->options.bitrate_target != options.bitrate_target ||
                 encoder->options.bitrate_maximum != options.bitrate_maximum;
  encoder->options = options;
  if (!changed)
    return true;
  encoder->codec->bit_rate = options.bitrate_target;
  encoder->codec->rc_max_rate = options.bitrate_maximum;
  encoder->codec->rc_buffer_size = options.bitrate_target;
  encoder->codec->framerate = (AVRational){options.fps, 1};
  // These point at the existing global misc buffers. The pinned FFmpeg patch
  // submits them between IDRs; changing AVCodecContext alone has no effect.
  VAAPIEncodeContext *va = encoder->codec->priv_data;
  va->rc_params.bits_per_second = options.bitrate_maximum;
  va->rc_params.target_percentage =
      ((uint64_t)options.bitrate_target * 100 + options.bitrate_maximum - 1) /
      options.bitrate_maximum;
  va->rc_params.window_size = (uint64_t)options.bitrate_target * 1000 / options.bitrate_maximum;
  va->hrd_params.buffer_size = options.bitrate_target;
  va->hrd_params.initial_buffer_fullness = (uint64_t)options.bitrate_target * 3 / 4;
  va->fr_params.framerate = (1u << 16) | options.fps;
  va->dpe_rate_update_pending = 1;
  return true;
}
static bool suspended(struct hevc_encoder *encoder, const struct encoder_frame_meta *meta) {
  if (encoder->options.fps && encoder->options.bitrate_target)
    return false;
  encoder->dropped(encoder->context, meta);
  return true;
}

static bool i420(void *opaque, const struct encoder_frame_meta *meta,
                 struct encoder_frame_info info, const struct encoder_plane planes[3]) {
  struct hevc_encoder *encoder = opaque;
  if (suspended(encoder, meta))
    return true;
  if (!encoder->codec || meta->width != encoder->options.dimensions.width ||
      meta->height != encoder->options.dimensions.height)
    return false;
  uint32_t width = encoder->options.dimensions.width, height = encoder->options.dimensions.height;
  for (size_t p = 0; p < 3; p++) {
    size_t w = p ? width / 2 : width, h = p ? height / 2 : height;
    if (!planes[p].data || planes[p].width < w || planes[p].height < h || planes[p].stride < w ||
        planes[p].stride > INT_MAX || h > SIZE_MAX / planes[p].stride)
      return false;
  }
  if (!encoder->cpu) {
    encoder->cpu = av_frame_alloc();
    if (!encoder->cpu)
      return false;
    encoder->cpu->format = AV_PIX_FMT_NV12;
    encoder->cpu->width = width;
    encoder->cpu->height = height;
    if (av_frame_get_buffer(encoder->cpu, 32) < 0)
      return false;
  }
  if (av_frame_make_writable(encoder->cpu) < 0)
    return false;
  for (uint32_t y = 0; y < height; y++)
    memcpy(encoder->cpu->data[0] + y * encoder->cpu->linesize[0],
           planes[0].data + y * planes[0].stride, width);
  for (uint32_t y = 0; y < height / 2; y++)
    for (uint32_t x = 0; x < width / 2; x++) {
      encoder->cpu->data[1][y * encoder->cpu->linesize[1] + 2 * x] =
          planes[1].data[y * planes[1].stride + x];
      encoder->cpu->data[1][y * encoder->cpu->linesize[1] + 2 * x + 1] =
          planes[2].data[y * planes[2].stride + x];
    }
  av_frame_unref(encoder->input);
  if (av_hwframe_get_buffer(encoder->upload_frames ? encoder->upload_frames : encoder->frames,
                            encoder->input, 0) < 0 ||
      av_hwframe_transfer_data(encoder->input, encoder->cpu, 0) < 0)
    return false;
  return send_frame(encoder, meta, info);
}

struct imported_surface {
  VADisplay display;
  VASurfaceID surface;
  void *lease;
  void (*release)(void *);
};
static void release_surface(void *opaque, uint8_t *unused) {
  (void)unused;
  struct imported_surface *surface = opaque;
  vaDestroySurfaces(surface->display, &surface->surface, 1);
  surface->release(surface->lease);
  free(surface);
}
static bool nv12(void *opaque, const struct encoder_frame_meta *meta,
                 struct encoder_frame_info info, const struct encoder_nv12 *input, void *lease,
                 void (*release)(void *)) {
  struct hevc_encoder *encoder = opaque;
  if (suspended(encoder, meta)) {
    release(lease);
    return true;
  }
  if (!encoder->codec)
    goto fail;
  if (input->width != encoder->options.dimensions.width ||
      input->height != encoder->options.dimensions.height || input->fd < 0 || !input->size)
    goto fail;
  for (size_t p = 0; p < 2; p++) {
    uint64_t rows = p ? input->height / 2 : input->height;
    if (input->stride[p] < input->width || input->offset[p] >= input->size ||
        input->offset[p] + (rows - 1) * input->stride[p] + input->width > input->size)
      goto fail;
  }
  struct imported_surface *surface = calloc(1, sizeof(*surface));
  if (!surface)
    goto fail;
  AVHWDeviceContext *device = (void *)encoder->device->data;
  AVVAAPIDeviceContext *va = device->hwctx;
  surface->display = va->display;
  surface->lease = lease;
  surface->release = release;
  VADRMPRIMESurfaceDescriptor descriptor = {.fourcc = VA_FOURCC_NV12,
                                            .width = input->width,
                                            .height = input->height,
                                            .num_objects = 1,
                                            .num_layers = 1};
  descriptor.objects[0].fd = input->fd;
  descriptor.objects[0].size = input->size;
  descriptor.objects[0].drm_format_modifier = input->modifier;
  descriptor.layers[0].drm_format = VA_FOURCC_NV12;
  descriptor.layers[0].num_planes = 2;
  for (size_t p = 0; p < 2; p++) {
    descriptor.layers[0].offset[p] = input->offset[p];
    descriptor.layers[0].pitch[p] = input->stride[p];
  }
  VASurfaceAttrib attributes[] = {
      {.type = VASurfaceAttribMemoryType,
       .flags = VA_SURFACE_ATTRIB_SETTABLE,
       .value = {.type = VAGenericValueTypeInteger,
                 .value.i = VA_SURFACE_ATTRIB_MEM_TYPE_DRM_PRIME_2}},
      {.type = VASurfaceAttribExternalBufferDescriptor,
       .flags = VA_SURFACE_ATTRIB_SETTABLE,
       .value = {.type = VAGenericValueTypePointer, .value.p = &descriptor}},
  };
  if (vaCreateSurfaces(surface->display, VA_RT_FORMAT_YUV420, input->width, input->height,
                       &surface->surface, 1, attributes, 2) != VA_STATUS_SUCCESS) {
    free(surface);
    goto fail;
  }
  av_frame_unref(encoder->input);
  encoder->input->format = AV_PIX_FMT_VAAPI;
  encoder->input->width = input->width;
  encoder->input->height = input->height;
  encoder->input->data[3] = (void *)(uintptr_t)surface->surface;
  encoder->input->hw_frames_ctx =
      av_buffer_ref(encoder->upload_frames ? encoder->upload_frames : encoder->frames);
  encoder->input->buf[0] =
      av_buffer_create((uint8_t *)surface, sizeof(*surface), release_surface, surface, 0);
  if (!encoder->input->buf[0]) {
    release_surface(surface, NULL);
    return false;
  }
  if (!encoder->input->hw_frames_ctx) {
    av_frame_unref(encoder->input);
    return false;
  }
  return send_frame(encoder, meta, info);
fail:
  release(lease);
  return false;
}

struct probe_result {
  bool vps, sps, pps, picture;
};
static void probe_encoded(void *opaque, const struct encoder_frame_meta *meta, uint64_t info,
                          const uint8_t *data, size_t size, const void *recon) {
  (void)meta;
  (void)info;
  (void)recon;
  struct probe_result *result = opaque;
  for (size_t i = 0; i + 5 < size; i++) {
    if (data[i] || data[i + 1] || data[i + 2] != 1)
      continue;
    uint8_t type = (data[i + 3] >> 1) & 63;
    result->vps |= type == 32;
    result->sps |= type == 33;
    result->pps |= type == 34;
    result->picture |= type == 19 || type == 20;
  }
}
static void probe_dropped(void *opaque, const struct encoder_frame_meta *meta) {
  (void)opaque;
  (void)meta;
}
static uint32_t probe(const char *node) {
  struct probe_result result = {0};
  struct encoder_options options = {30, {256, 256}, 500000, 1000000, 3000};
  struct encoder_session session = {.fps = 30, .maximum = {256, 256}, .codec = 1};
  struct hevc_encoder *encoder =
      create(node, session, options, &result, probe_encoded, probe_dropped);
  if (!encoder)
    return 4;
  uint8_t *pixels = malloc(256 * 256 * 3 / 2);
  if (!pixels) {
    destroy(encoder);
    return 4;
  }
  memset(pixels, 16, 256 * 256);
  memset(pixels + 256 * 256, 128, 256 * 256 / 2);
  const struct encoder_plane planes[] = {
      {pixels, 256, 256, 256}, {pixels + 65536, 128, 128, 128}, {pixels + 81920, 128, 128, 128}};
  bool valid = true;
  for (unsigned i = 0; i < 3 && valid; i++) {
    struct encoder_frame_meta meta = {.width = 256, .height = 256};
    struct encoder_frame_info info = {.idr = i == 0, .timestamp_ms = i * 34};
    valid = i420(encoder, &meta, info, planes);
  }
  uint32_t selected = encoder->vendor;
  free(pixels);
  destroy(encoder);
  return valid && result.vps && result.sps && result.pps && result.picture ? selected : 4;
}

__attribute__((visibility("default"))) const struct dpe_hevc_api *dpe_hevc_api(void) {
  static const struct dpe_hevc_api api = {DPE_HEVC_ABI, sizeof(api), probe,   create, update,
                                          i420,         nv12,        destroy, vendor};
  return &api;
}
