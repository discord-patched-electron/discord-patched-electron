#include "av1-crop.h"
#include "av1-ivf.h"
#include "encoder-abi.h"
#include <assert.h>
#include <dlfcn.h>
#include <errno.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

// Exercise the real library across callback reentry, stale handles and a
// concurrent destroy. Synthetic pixels only; these packets are discarded.
struct callback_contract {
  const struct dpe_hevc_api *api;
  void *encoder;
  struct encoder_options options;
  pthread_mutex_t mutex;
  pthread_cond_t wake;
  unsigned callbacks;
  bool reentrant, entered, release, destroying, destroyed;
};
static void contract_drop(void *opaque, const struct encoder_frame_meta *meta) {
  (void)opaque;
  (void)meta;
  assert(0 && "No pending frames in synchronous-depth contract");
}
static void contract_encoded(void *opaque, const struct encoder_frame_meta *meta, uint64_t info,
                             const uint8_t *bytes, size_t size, const void *recon) {
  (void)info;
  (void)recon;
  struct callback_contract *state = opaque;
  assert(meta->width == 256 && meta->height == 256 && bytes && size);
  ++state->callbacks;
  if (state->reentrant) {
    state->options.fps = 30;
    assert(state->api->update(state->encoder, state->options));
    state->api->destroy(state->encoder);
    assert(state->api->vendor(state->encoder) == 4);
    assert(!state->api->update(state->encoder, state->options));
    state->api->destroy(state->encoder);
  } else {
    pthread_mutex_lock(&state->mutex);
    state->entered = true;
    pthread_cond_broadcast(&state->wake);
    while (!state->release)
      pthread_cond_wait(&state->wake, &state->mutex);
    pthread_mutex_unlock(&state->mutex);
  }
}
static void *contract_feed(void *opaque) {
  struct callback_contract *state = opaque;
  uint8_t *pixels = malloc(256 * 256 * 3 / 2);
  assert(pixels);
  memset(pixels, 16, 65536);
  memset(pixels + 65536, 128, 32768);
  const struct encoder_plane planes[] = {
      {pixels, 256, 256, 256}, {pixels + 65536, 128, 128, 128}, {pixels + 81920, 128, 128, 128}};
  const struct encoder_frame_meta meta = {.width = 256, .height = 256};
  assert(state->api->i420(state->encoder, &meta,
                          (struct encoder_frame_info){.idr = 1, .timestamp_ms = 1}, planes));
  free(pixels);
  return NULL;
}
static void *contract_destroy(void *opaque) {
  struct callback_contract *state = opaque;
  pthread_mutex_lock(&state->mutex);
  state->destroying = true;
  pthread_cond_broadcast(&state->wake);
  pthread_mutex_unlock(&state->mutex);
  state->api->destroy(state->encoder);
  pthread_mutex_lock(&state->mutex);
  state->destroyed = true;
  pthread_cond_broadcast(&state->wake);
  pthread_mutex_unlock(&state->mutex);
  return NULL;
}
static void count_release(void *opaque) { ++*(unsigned *)opaque; }
static void check_contract(const struct dpe_hevc_api *api, const char *node) {
  const struct encoder_options options = {60, {256, 256}, 500000, 1000000, 3000};
  const struct encoder_session session = {.fps = 60, .maximum = {256, 256}};
  struct callback_contract state = {.api = api,
                                    .options = options,
                                    .mutex = PTHREAD_MUTEX_INITIALIZER,
                                    .wake = PTHREAD_COND_INITIALIZER};
  assert(api->vendor(NULL) == 4 && !api->update(NULL, options));
  api->destroy(NULL);
  unsigned releases = 0;
  struct encoder_nv12 invalid = {.width = 256, .height = 256, .fd = -1};
  struct encoder_frame_meta meta = {.width = 256, .height = 256};
  assert(
      !api->nv12(NULL, &meta, (struct encoder_frame_info){0}, &invalid, &releases, count_release));
  assert(releases == 1);
  for (unsigned reentrant = 0; reentrant < 2; ++reentrant) {
    state.reentrant = reentrant;
    state.encoder = api->create(node, session, options, &state, contract_encoded, contract_drop);
    assert(state.encoder);
    assert(!api->nv12(state.encoder, &meta, (struct encoder_frame_info){0}, &invalid, &releases,
                      count_release));
    assert(releases == reentrant + 2);
    if (reentrant) {
      contract_feed(&state);
    } else {
      pthread_t feeding, destroying;
      assert(!pthread_create(&feeding, NULL, contract_feed, &state));
      pthread_mutex_lock(&state.mutex);
      while (!state.entered)
        pthread_cond_wait(&state.wake, &state.mutex);
      assert(!pthread_create(&destroying, NULL, contract_destroy, &state));
      while (!state.destroying)
        pthread_cond_wait(&state.wake, &state.mutex);
      struct timespec deadline;
      assert(!clock_gettime(CLOCK_REALTIME, &deadline));
      deadline.tv_nsec += 50000000;
      if (deadline.tv_nsec >= 1000000000) {
        ++deadline.tv_sec;
        deadline.tv_nsec -= 1000000000;
      }
      int status = 0;
      while (!state.destroyed && status != ETIMEDOUT)
        status = pthread_cond_timedwait(&state.wake, &state.mutex, &deadline);
      assert(!state.destroyed);
      state.release = true;
      pthread_cond_broadcast(&state.wake);
      pthread_mutex_unlock(&state.mutex);
      assert(!pthread_join(feeding, NULL) && !pthread_join(destroying, NULL));
      assert(state.destroyed);
    }
    assert(api->vendor(state.encoder) == 4 && !api->update(state.encoder, options));
    api->destroy(state.encoder);
  }
  assert(state.callbacks == 2);
  pthread_cond_destroy(&state.wake);
  pthread_mutex_destroy(&state.mutex);
  puts("Private encoder: callback reentry, concurrent destroy and exact lease release passed");
}
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
  check_contract(api, argv[2]);
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
