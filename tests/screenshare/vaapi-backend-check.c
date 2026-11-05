#include "av1-crop.h"
#include "av1-ivf.h"
#include "encoder-abi.h"
#include <assert.h>
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static unsigned emitted, dropped_count, keys, wanted_width, wanted_height;
static unsigned source_width, source_height;
static FILE *recording;
static unsigned active_codec;
static struct dpe_av1_crop_state crop_state;
static const char *recovery_path;
static void encoded(void *ctx, const struct encoder_frame_meta *meta, uint64_t info,
                    const uint8_t *bytes, size_t size, const void *recon) {
  (void)ctx;
  (void)recon;
  bool native_short = active_codec == 2 && !(source_width & 63) && (source_height & 15) == 8 &&
                      meta->height == source_height + 2;
  assert(meta->width == wanted_width && (meta->height == wanted_height || native_short));
  assert((info >> 32) == UINT32_MAX);
  assert(size > 8);
  if (active_codec == 2) {
    if (!emitted)
      ivf_header(recording, meta->width, meta->height);
    ivf_packet(recording, bytes, size, emitted);
    struct dpe_av1_crop crop = dpe_av1_crop_read(bytes, size, &crop_state);
    if (meta->width != source_width || meta->height != source_height) {
      assert(crop.x == (((meta->width - source_width) / 2) & ~1u));
      assert(crop.y == (((meta->height - source_height) / 2) & ~1u));
      assert(crop.width == source_width && crop.height == source_height);
    } else {
      assert(!crop.width && !crop.height);
    }
  } else {
    assert(fwrite(bytes, 1, size, recording) == size);
  }
  emitted++;
  keys += (uint32_t)info == 2;
  if ((uint32_t)info == 2) {
    FILE *recovery = fopen(recovery_path, "wb");
    assert(recovery);
    if (active_codec == 2) {
      ivf_header(recovery, meta->width, meta->height);
      ivf_packet(recovery, bytes, size, 0);
      struct dpe_av1_crop_state fresh = {0};
      struct dpe_av1_crop crop = dpe_av1_crop_read(bytes, size, &fresh);
      assert(crop.x == crop_state.active.x && crop.y == crop_state.active.y &&
             crop.width == crop_state.active.width && crop.height == crop_state.active.height);
    } else {
      assert(fwrite(bytes, 1, size, recovery) == size);
    }
    assert(!fclose(recovery));
  }
}
static void dropped(void *ctx, const struct encoder_frame_meta *meta) {
  (void)ctx;
  (void)meta;
  dropped_count++;
}
static void feed(const struct dpe_hevc_api *api, void *enc, unsigned width, unsigned height,
                 unsigned first) {
  source_width = wanted_width = width;
  source_height = wanted_height = height;
  if (active_codec == 2 && api->vendor(enc) == 1) {
    wanted_width = (width + 63u) & ~63u;
    wanted_height = (height + 15u) & ~15u;
  }
  uint8_t *pixels = malloc(width * height * 3 / 2);
  assert(pixels);
  memset(pixels, 80, width * height);
  memset(pixels + width * height, 128, width * height / 2);
  struct encoder_plane p[3] = {{pixels, width, width, height},
                               {pixels + width * height, width / 2, width / 2, height / 2},
                               {pixels + width * height * 5 / 4, width / 2, width / 2, height / 2}};
  for (unsigned i = 0; i < 3; i++) {
    struct encoder_frame_meta meta = {.width = width, .height = height};
    struct encoder_frame_info info = {.idr = i == 2, .timestamp_ms = (first + i) * 17};
    assert(api->i420(enc, &meta, info, p));
  }
  free(pixels);
}
int main(int argc, char **argv) {
  setbuf(stderr, NULL);
  assert(argc == 7);
  void *lib = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
  if (!lib) {
    fprintf(stderr, "%s\n", dlerror());
    return 1;
  }
  const struct dpe_hevc_api *(*get)(void) = dlsym(lib, "dpe_hevc_api");
  assert(get);
  const struct dpe_hevc_api *api = get();
  assert(api->abi == DPE_HEVC_ABI && api->size == sizeof(*api));
  for (unsigned codec = 1; codec <= 2; codec++) {
    active_codec = codec;
    memset(&crop_state, 0, sizeof(crop_state));
    recovery_path = argv[codec + 4];
    const char *output = argv[codec + 2];
    recording = fopen(output, "wb");
    assert(recording);
    emitted = dropped_count = keys = 0;
    fprintf(stderr, "codec%u create\n", codec);
    struct encoder_session session = {.fps = 60, .maximum = {1920, 1080}, .codec = codec};
    struct encoder_options options = {60, {256, 256}, 500000, 1000000, 3000};
    void *enc = api->create(argv[2], session, options, NULL, encoded, dropped);
    assert(enc);
    fprintf(stderr, "codec%u initial feed\n", codec);
    feed(api, enc, 256, 256, 0);
    fprintf(stderr, "codec%u initial done emitted%u\n", codec, emitted);
    fprintf(stderr, "codec%u suspend\n", codec);
    options.fps = 0;
    options.bitrate_target = 0;
    options.dimensions = (struct encoder_dimensions){640, 360};
    assert(api->update(enc, options));
    feed(api, enc, 640, 360, 3);
    assert(dropped_count == 3);
    fprintf(stderr, "codec%u resume resize\n", codec);
    options.fps = 60;
    options.bitrate_target = 750000;
    assert(api->update(enc, options));
    fprintf(stderr, "codec%u resize done\n", codec);
    feed(api, enc, 640, 360, 6);
    fprintf(stderr, "codec%u resized frames done\n", codec);
    options.dimensions.width = 641;
    assert(!api->update(enc, options));
    feed(api, enc, 640, 360, 9);
    // Switch from two-row firmware padding to centred VPP padding and back,
    // then a genuine aligned picture. Crop state must be replaced and cleared.
    const struct encoder_dimensions sizes[] = {
        {1920, 1080}, {1918, 1078}, {1920, 1080}, {1920, 1088}};
    for (unsigned i = 0; i < sizeof(sizes) / sizeof(sizes[0]); i++) {
      options.dimensions = sizes[i];
      assert(api->update(enc, options));
      feed(api, enc, sizes[i].width, sizes[i].height, 12 + 3 * i);
    }
    api->destroy(enc);
    assert(emitted == 21 && keys == 13 && dropped_count == 3);
    fclose(recording);
    printf(
        "codec%u: suspension, resize, resume, failed-update and crop-transition recovery passed\n",
        codec);
  }
  return 0;
}
