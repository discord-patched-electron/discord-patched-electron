#include "encoder-abi.h"
#include <assert.h>
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static unsigned emitted, dropped_count, keys, wanted_width, wanted_height;
static FILE *recording;
static unsigned active_codec;
static const char *recovery_path;
static void encoded(void *ctx, const struct encoder_frame_meta *meta, uint64_t info,
                    const uint8_t *bytes, size_t size, const void *recon) {
  (void)ctx;
  (void)recon;
  assert(meta->width == wanted_width && meta->height == wanted_height);
  assert((info >> 32) == UINT32_MAX);
  assert(size > 8);
  if (active_codec == 2)
    fwrite("\x12\0", 1, 2, recording);
  fwrite(bytes, 1, size, recording);
  emitted++;
  keys += (uint32_t)info == 2;
  if ((uint32_t)info == 2) {
    FILE *recovery = fopen(recovery_path, "wb");
    assert(recovery);
    if (active_codec == 2)
      fwrite("\x12\0", 1, 2, recovery);
    fwrite(bytes, 1, size, recovery);
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
    recovery_path = argv[codec + 4];
    const char *output = argv[codec + 2];
    recording = fopen(output, "wb");
    assert(recording);
    emitted = dropped_count = keys = 0;
    wanted_width = wanted_height = 256;
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
    wanted_width = 640;
    wanted_height = codec == 2 ? 352 : 360;
    feed(api, enc, 640, 360, 6);
    fprintf(stderr, "codec%u resized frames done\n", codec);
    options.dimensions.width = 641;
    assert(!api->update(enc, options));
    feed(api, enc, 640, 360, 9);
    api->destroy(enc);
    assert(emitted == 9 && keys == 5 && dropped_count == 3);
    fclose(recording);
    printf("codec%u: suspension, resize, resume, failed-update recovery passed\n", codec);
  }
  return 0;
}
