// Generated luma detail and edge pattern; never opens a display or account.
#include "av1-ivf.h"
#include "encoder-abi.h"
#include <assert.h>
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static FILE *output;
static unsigned codec, emitted;
static void encoded(void *context, const struct encoder_frame_meta *meta, uint64_t info,
                    const uint8_t *bytes, size_t size, const void *recon) {
  (void)context;
  (void)info;
  (void)recon;
  assert(meta->width && meta->height && size);
  if (codec == 2) {
    if (!emitted)
      ivf_header(output, meta->width, meta->height);
    ivf_packet(output, bytes, size, emitted);
  } else {
    assert(fwrite(bytes, 1, size, output) == size);
  }
  emitted++;
}
static void dropped(void *context, const struct encoder_frame_meta *meta) {
  (void)context;
  (void)meta;
  abort();
}
int main(int argc, char **argv) {
  assert(argc == 7);
  void *library = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
  assert(library);
  const struct dpe_hevc_api *(*get)(void) = dlsym(library, "dpe_hevc_api");
  assert(get);
  const struct dpe_hevc_api *api = get();
  unsigned width = atoi(argv[4]), height = atoi(argv[5]);
  codec = atoi(argv[6]);
  output = fopen(argv[3], "wb");
  assert(output);
  struct encoder_session session = {.fps = 60, .maximum = {width, height}, .codec = codec};
  struct encoder_options options = {60, {width, height}, 9000000, 9000000, 3000};
  void *encoder = api->create(argv[2], session, options, NULL, encoded, dropped);
  assert(encoder);
  printf("%u\n", api->vendor(encoder));
  uint8_t *pixels = malloc(width * height * 3 / 2);
  assert(pixels);
  for (unsigned y = 0; y < height; y++)
    for (unsigned x = 0; x < width; x++) {
      unsigned value = 80;
      if (x < 4 || y < 4 || x >= width - 4 || y >= height - 4)
        value = 220;
      else if (y >= 32 && y < 96)
        value = y & 1 ? 170 : 60;
      else if (x >= 32 && x < 96)
        value = x & 1 ? 170 : 60;
      pixels[y * width + x] = value;
    }
  memset(pixels + width * height, 128, width * height / 2);
  struct encoder_plane planes[] = {
      {pixels, width, width, height},
      {pixels + width * height, width / 2, width / 2, height / 2},
      {pixels + width * height * 5 / 4, width / 2, width / 2, height / 2}};
  for (unsigned i = 0; i < 3; i++) {
    struct encoder_frame_meta meta = {.width = width, .height = height};
    struct encoder_frame_info info = {.idr = i == 0, .timestamp_ms = i * 17};
    assert(api->i420(encoder, &meta, info, planes));
  }
  api->destroy(encoder);
  assert(emitted == 3);
  free(pixels);
  assert(!fclose(output));
  return 0;
}
