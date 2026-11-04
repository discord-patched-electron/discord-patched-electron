#define _GNU_SOURCE
#include <assert.h>
#include <stdbool.h>
static bool hardware_vcn4 = true;
#define DPE_ENCODER_VCN4() hardware_vcn4
#include "../../integration/video/encoder.c"

static unsigned creates, updates, destroys;
static uint32_t updated_peak;
static unsigned vendor = 1;
static bool create_ok = true;
static bool emit_sequence;
static bool emit_quality;
static void *fake_handle = (void *)0x123400;
static struct encoder_session expected_session;
static struct encoder_options expected_options;
static const char *quality_vendor;
static uint8_t expected_tunings[0x40];
static void *tunings_stub(void *output, const void *display, const void *session,
                          const void *options) {
  assert(display && session && options == (void *)7);
  memcpy(output, expected_tunings, sizeof(expected_tunings));
  return output;
}
static const char *query_stub(void *display) {
  assert(display == (void *)0x1234);
  return quality_vendor;
}
static void check_quality(void) {
  uint8_t output[0x40], expected[0x40], session[0x38] = {0};
  void *display = (void *)0x1234;
  original_tunings = tunings_stub;
  query_vendor = query_stub;
  const char *vendors[] = {"Mesa Gallium driver 26.2.5 for AMD Radeon RX 7900",
                           "Mesa Gallium driver 26.2.5 for ATI Radeon",
                           "Intel iHD driver",
                           "Mesa Gallium driver for Intel",
                           "NVIDIA NVDEC driver",
                           "AMD proprietary driver",
                           NULL};
  for (size_t i = 0; i < sizeof(vendors) / sizeof(vendors[0]); i++) {
    for (unsigned codec = 0; codec < 3; codec++) {
      for (unsigned level = 0; level <= 4; level += 4) {
        memset(expected_tunings, 0x5a, sizeof(expected_tunings));
        uint32_t present = 1;
        memcpy(expected_tunings + 0x10, &present, 4);
        memcpy(expected_tunings + 0x14, &level, 4);
        memcpy(expected, expected_tunings, sizeof(expected));
        session[0x32] = codec;
        quality_vendor = vendors[i];
        bool changed = i < 2 && codec == 0 && level == 0;
        if (changed) {
          uint32_t desired = 0x13;
          memcpy(expected + 0x14, &desired, 4);
        }
        uint64_t prior = corrected_quality;
        assert(quality_tunings(output, &display, session, (void *)7) == output);
        assert(!memcmp(output, expected, sizeof(output)));
        assert(corrected_quality == prior + changed);
      }
    }
  }
  // An absent optional preset must remain absent, even on Mesa AMD.
  quality_vendor = vendors[0];
  session[0x32] = 0;
  memset(expected_tunings, 0, sizeof(expected_tunings));
  quality_tunings(output, &display, session, (void *)7);
  assert(!memcmp(output, expected_tunings, sizeof(output)));
}

static bool create_stub(uint32_t type, const void *frame, void *context, void *encoded,
                        void *dropped, void **output, struct encoder_session session,
                        struct encoder_options options) {
  assert(type == 0 || type == 3 || type == 5 || type == 6 || type == 10);
  assert(frame == (void *)1 && context == (void *)2);
  assert(encoded == (void *)3 && dropped == (void *)4);
  assert(!memcmp(&session, &expected_session, sizeof(session)));
  assert(!memcmp(&options, &expected_options, sizeof(options)));
  creates++;
  *output = create_ok ? fake_handle : NULL;
  if (create_ok && emit_sequence) {
    uint8_t config[0x50] = {0}, sequence[0x300];
    uint64_t bitrate = options.bitrate_target;
    memcpy(config + 0x20, &bitrate, sizeof(bitrate));
    memcpy(config + 0x30, &options.fps, sizeof(options.fps));
    memcpy(config + 0x40, &options.dimensions, sizeof(options.dimensions));
    corrected_sequence(sequence, config, (void *)7);
  }
  if (create_ok && emit_quality) {
    uint8_t native_session[0x38] = {0}, tunings[0x40];
    native_session[0x32] = session.codec;
    void *display = (void *)0x1234;
    quality_tunings(tunings, &display, native_session, (void *)7);
  }
  return create_ok;
}
static bool update_stub(void *handle, struct encoder_options options) {
  assert(handle == fake_handle);
  struct encoder_options expected = expected_options;
  assert(options.bitrate_maximum == expected.bitrate_maximum ||
         options.bitrate_maximum == expected.bitrate_target);
  updated_peak = options.bitrate_maximum;
  expected.bitrate_maximum = options.bitrate_maximum;
  assert(!memcmp(&options, &expected, sizeof(options)));
  updates++;
  return true;
}
static void destroy_stub(void *handle) {
  assert(handle == fake_handle);
  destroys++;
}
static uint32_t vendor_stub(void *handle) {
  assert(handle == fake_handle);
  return vendor;
}
static bool create(uint32_t type, void **output) {
  return guarded_create(type, (void *)1, (void *)2, (void *)3, (void *)4, output, expected_session,
                        expected_options);
}

static uint8_t expected_info[0x110];
static uint8_t expected_config[0xca];
static const void *expected_settings = (void *)0x444;
static const void *config_stub(const void *settings) {
  assert(settings == expected_settings);
  return expected_config;
}
static void *info_stub(void *output, const void *encoder) {
  assert(encoder);
  memcpy(output, expected_info, sizeof(expected_info));
  return output;
}

static void check_qp_trust(void) {
  uint8_t encoder[16] = {0}, output[0x110], expected[0x110];
  memcpy(encoder + 8, &fake_handle, sizeof(fake_handle));
  original_info = info_stub;
  original_config = config_stub;
  installed_sequence = 0;
  emit_sequence = false;
  emit_quality = true;
  expected_session.maximum = (struct encoder_dimensions){1280, 720};
  expected_options.dimensions = expected_session.maximum;
  const char *vendors[] = {"Mesa Gallium driver 26 for AMD Radeon", "Intel iHD driver",
                           "NVIDIA driver", "AMD proprietary driver"};
  for (unsigned gpu = 0; gpu < 4; gpu++) {
    quality_vendor = vendors[gpu];
    for (unsigned codec = 0; codec < 3; codec++) {
      expected_session.codec = codec;
      for (unsigned level = 0; level <= 4; level += 4) {
        memset(expected_tunings, 0, sizeof(expected_tunings));
        uint32_t present = 1;
        memcpy(expected_tunings + 0x10, &present, 4);
        memcpy(expected_tunings + 0x14, &level, 4);
        void *handle = NULL;
        assert(create(6, &handle));
        for (unsigned contract = 0; contract < 3; contract++) {
          memset(expected_info, 0x5a, sizeof(expected_info));
          expected_info[8] = 1;
          memcpy(expected_info + 0x18, "amd: vaapi", 10);
          expected_info[0x2f] = 10;
          expected_info[0xf0] = contract == 2;
          expected_info[0xf1] = contract != 0;
          memcpy(expected, expected_info, sizeof(expected));
          bool corrected = gpu == 0 && codec == 0 && level == 0 && contract == 0;
          if (corrected) {
            expected[0xf0] = 0;
            expected[0xf1] = 1;
          }
          uint64_t before = corrected_qp_trust;
          assert(trusted_encoder_info(output, encoder) == output);
          assert(!memcmp(output, expected, sizeof(output)));
          assert(corrected_qp_trust == before + corrected);
          if (contract == 0) {
            const uint8_t *gate = bandwidth_config((void *)0x444, output);
            assert(gate[0xc9] == corrected && expected_config[0xc9] == 0);
            output[8] = 0; // No explicit thresholds: native gate unchanged.
            assert(bandwidth_config((void *)0x444, output) == expected_config);
            output[8] = 1;
            output[0x18] = 'X'; // Never infer AMD from global active hardware.
            assert(bandwidth_config((void *)0x444, output) == expected_config);
            output[0x18] = 'a';
            expected_config[0xc9] = 1;
            assert(bandwidth_config((void *)0x444, output) == expected_config);
            expected_config[0xc9] = 0;
          }
        }
        guarded_destroy(handle);
        // Removed/reused handles must never inherit a previous AMD declaration.
        expected_info[0xf1] = 0;
        trusted_encoder_info(output, encoder);
        assert(!memcmp(output, expected_info, sizeof(output)) && !guarded);
        output[0xf0] = 0;
        output[0xf1] = 1;
        assert(bandwidth_config((void *)0x444, output) == expected_config);
      }
    }
  }
  emit_quality = false;
}

static void check_native_bandwidth_gate(void) {
  // Exact reviewed ConfigureBandwidthQualityScaler prefix through its boolean
  // result. The scope generator independently verifies these original bytes.
  // Only the external accessor call and the post-gate test return are relocated.
  static const uint8_t body[] =
      "\x55\x48\x89\xe5\x41\x57\x41\x56\x53\x50\x49\x89\xf6\x48\x89\xfb"
      "\x8b\x47\x78\x83\xe0\xfd\x83\xf8\x01\x75\x33"
      "\x80\xbb\xa8\x09\x00\x00\x01\x75\x2a\x48\x8d\xbb\x68\x02\x00\x00"
      "\xe8\x00\x00\x00\x00"
      "\x80\xb8\xc9\x00\x00\x00\x01\x75\x15\x45\x0f\xb6\xbe\xf0\x00\x00\x00"
      "\x41\x80\xf7\x01\x45\x22\xbe\xf1\x00\x00\x00\xeb\x03\x45\x31\xff"
      "\x41\x0f\xb6\xc7\x48\x83\xc4\x08\x5b\x41\x5e\x41\x5f\x5d\xc3";
  size_t page = (size_t)sysconf(_SC_PAGESIZE), size = sizeof(body) - 1;
  uint8_t *code = mmap(NULL, page, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
  assert(code != MAP_FAILED);
  memcpy(code, body, size);
  int32_t displacement = (int32_t)size - (0x2b + 5);
  memcpy(code + 0x2c, &displacement, 4);
  const uint8_t jump[] = {0xff, 0x25, 0, 0, 0, 0};
  uintptr_t callback = (uintptr_t)bandwidth_config;
  memcpy(code + size, jump, sizeof(jump));
  memcpy(code + size + sizeof(jump), &callback, sizeof(callback));
  assert(!mprotect(code, page, PROT_READ | PROT_EXEC));
  uint8_t manager[0x9b0] = {0}, metadata[0x110] = {0};
  expected_settings = manager + 0x268;
  metadata[8] = 1;
  metadata[0xf1] = 1;
  memcpy(metadata + 0x18, "amd: vaapi", 10);
  metadata[0x2f] = 10;
  struct guarded_encoder entry = {.handle = fake_handle, .amd_vbaq = true};
  assert(!guarded);
  guarded = &entry;
  unsigned (*gate)(const void *, const void *) = (void *)code;
  for (unsigned settings = 0; settings < 2; settings++) {
    manager[0x9a8] = settings;
    for (unsigned degradation = 0; degradation < 4; degradation++) {
      memcpy(manager + 0x78, &degradation, 4);
      assert(gate(manager, metadata) == (settings && (degradation == 1 || degradation == 3)));
    }
  }
  guarded = NULL;
  assert(!gate(manager, metadata));
  expected_settings = (void *)0x444;
  munmap(code, page);
}

static unsigned quality_calls, bandwidth_calls;
static uint32_t transition_codec;
static const void *transition_manager;
static const void *transition_info;
static void quality_stub(void *manager, const void *info) {
  assert(manager == transition_manager && info == transition_info);
  quality_calls++;
}
static void bandwidth_stub(void *manager, const void *info) {
  assert(manager == transition_manager && info == transition_info);
  bandwidth_calls++;
}
static uint32_t codec_stub(const void *settings) {
  assert(settings == (const uint8_t *)transition_manager + 0x268);
  return transition_codec;
}
static bool started_stub(const void *resource) { return *(const bool *)resource; }
static void check_scaler_transitions(void) {
  static uint8_t owner[0x9b0], other[0x9b0], metadata[0x110];
  bool active = true, inactive = false;
  void *resource = &active;
  memcpy(owner + 0x48, &resource, sizeof(resource));
  resource = &inactive;
  memcpy(other + 0x48, &resource, sizeof(resource));
  uint32_t preference = 1;
  memcpy(owner + 0x78, &preference, 4);
  memcpy(other + 0x78, &preference, 4);
  memcpy(metadata + 0x18, "unknown", 7);
  metadata[0x2f] = 7;
  original_configure_quality = quality_stub;
  original_configure_bandwidth = bandwidth_stub;
  original_settings_codec = codec_stub;
  original_scaler_started = started_stub;
  transition_info = metadata;
  transition_codec = 4;
  transition_manager = owner;
  for (unsigned variant = 0; variant < 9; variant++) {
    quality_calls = bandwidth_calls = 0;
    configure_quality((void *)transition_manager, metadata);
    configure_bandwidth((void *)transition_manager, metadata);
    assert(quality_calls == (variant != 0));
    assert(bandwidth_calls == (variant != 0));
    switch (variant) {
    case 0:
      transition_manager = other;
      break; // A different owner has no active scaler.
    case 1:
      transition_manager = owner;
      active = false;
      break; // Startup.
    case 2:
      active = true;
      metadata[0x2f] = 10;
      memcpy(metadata + 0x18, "amd: vaapi", 10);
      break;
    case 3:
      metadata[0x2f] = 7;
      memcpy(metadata + 0x18, "unknown", 7);
      metadata[0xf1] = 1;
      break;
    case 4:
      metadata[0xf1] = 0;
      transition_codec = 5;
      break; // A real codec change.
    case 5:
      transition_codec = 0;
      break; // Cleared settings/destruction.
    case 6:
      transition_codec = 4;
      preference = 0;
      memcpy(owner + 0x78, &preference, 4);
      break;
    case 7:
      preference = 2;
      memcpy(owner + 0x78, &preference, 4);
      break;
    }
  }
}

static unsigned native_rate_queries, native_budget_calls, native_destructor_calls;
static struct optional_rate rate_stub(const void *rate, int64_t now) {
  assert(rate && now == 123);
  native_rate_queries++;
  return (struct optional_rate){777, 1};
}
static void budget_stub(void *manager, int64_t budget) {
  assert(manager == transition_manager);
  native_budget_calls++;
  // Native intentionally retains its last positive allocation on suspension.
  if (budget > 0) {
    uint32_t value = budget;
    memcpy((uint8_t *)manager + 0xec, &value, 4);
    ((uint8_t *)manager)[0xf0] = 1;
  }
}
static void scaler_destroy_stub(void *scaler) {
  assert(scaler && !allocation_owners);
  native_destructor_calls++;
}
static void check_allocation_adaptation(void) {
  static uint8_t manager[0x9b0], resource[0x70], scaler[0xc0], info[0x110];
  void *pointer = resource;
  memcpy(manager + 0x48, &pointer, sizeof(pointer));
  pointer = scaler;
  memcpy(resource + 0x60, &pointer, sizeof(pointer));
  resource[0] = 1; // started_stub
  info[8] = info[0xf1] = 1;
  info[0x2f] = 10;
  memcpy(info + 0x18, "amd: vaapi", 10);
  transition_manager = manager;
  transition_codec = 4;
  original_rate_query = rate_stub;
  original_target_budget = budget_stub;
  original_scaler_destructors[0] = original_scaler_destructors[1] = scaler_destroy_stub;
  struct guarded_encoder encoder = {.amd_vbaq = true};
  guarded = &encoder;
  budget_stub(manager, 9000000);
  bind_allocation(manager, info);
  assert(allocation_owners);
  struct optional_rate result = allocation_rate(scaler + 0x20, 123);
  assert(result.present == 1 && result.value == 9000000 && native_rate_queries == 0);
  target_budget(manager, 600000);
  result = allocation_rate(scaler + 0x20, 123);
  assert(result.present == 1 && result.value == 600000);
  target_budget(manager, 0);
  bind_allocation(manager, info); // Repeated metadata must retain suspended input.
  result = allocation_rate(scaler + 0x20, 123);
  assert(result.present == 0 && result.value == 0 && native_rate_queries == 0);
  target_budget(manager, 9000000);
  result = allocation_rate(scaler + 0x20, 123);
  assert(result.present == 1 && result.value == 9000000);
  result = allocation_rate(scaler + 0x21, 123); // Unrelated/native encoder is unchanged.
  assert(result.value == 777 && native_rate_queries == 1);
  scaler_destructor(scaler);
  assert(native_destructor_calls == 1);
  result = allocation_rate(scaler + 0x20, 123); // Reused address no longer carries ownership.
  assert(result.value == 777 && native_rate_queries == 2);
  manager[0xf0] = 0;
  bind_allocation(manager, info);
  result = allocation_rate(scaler + 0x20, 123);
  assert(!result.present && native_rate_queries == 2);
  scaler_deleting_destructor(scaler);
  assert(native_destructor_calls == 2);
  bind_allocation(manager, info);
  info[0xf1] = 0; // Known fallback discards association before native configuration.
  transition_info = info;
  configure_bandwidth(manager, info);
  assert(!allocation_owners);
  guarded = NULL;
  info[0xf1] = 1;
  bind_allocation(manager, info); // No actual eligible native encoder: no association.
  assert(!allocation_owners);
}

static unsigned envelope_queries, envelope_destroys;
static void *limit_stub(void *output, uint64_t pixels, const void *limits) {
  assert(pixels == (UINT64_C(1) << 32 | 1920 * 1080));
  assert(limits == (void *)0x987);
  memset(output, 0x3c, 20);
  ((uint8_t *)output)[16] = 1;
  return output;
}
static void *envelope_info_stub(void *output, const void *encoder) {
  envelope_queries++;
  return info_stub(output, encoder);
}
static void envelope_info_destroy(void *output) {
  assert(!memcmp(output, expected_info, sizeof(expected_info)));
  envelope_destroys++;
}
static void check_stream_envelope(void) {
  uint8_t owner[0x200] = {0}, output[20], expected[20];
  uintptr_t vtable[13] = {0};
  vtable[12] = (uintptr_t)envelope_info_stub;
  uintptr_t *vtable_pointer = vtable;
  void *encoder = &vtable_pointer;
  memcpy(owner + 0x1e8, &encoder, sizeof(encoder));
  uint32_t codec = 4, content = 1;
  memcpy(owner + 0x118, &codec, 4);
  memcpy(owner + 0x1a0, &content, 4);
  memset(expected_info, 0, sizeof(expected_info));
  expected_info[8] = expected_info[0xf1] = 1;
  expected_info[0x2f] = 10;
  memcpy(expected_info + 0x18, "amd: vaapi", 10);
  struct guarded_encoder entry = {.amd_vbaq = true};
  guarded = &entry;
  original_stream_limit = limit_stub;
  original_info_destructor = envelope_info_destroy;
  uint64_t pixels = UINT64_C(1) << 32 | 1920 * 1080;
  for (unsigned variant = 0; variant < 8; variant++) {
    limit_stub(expected, pixels, (void *)0x987);
    if (variant == 0)
      expected[16] = 0;
    assert(stream_limit(output, pixels, (void *)0x987, owner) == output);
    assert(!memcmp(output, expected, sizeof(output)));
    assert(envelope_queries == envelope_destroys);
    switch (variant) {
    case 0:
      content = 0;
      memcpy(owner + 0x1a0, &content, 4);
      break;
    case 1:
      content = 1;
      memcpy(owner + 0x1a0, &content, 4);
      codec = 5;
      memcpy(owner + 0x118, &codec, 4);
      break;
    case 2:
      codec = 4;
      memcpy(owner + 0x118, &codec, 4);
      expected_info[0xb8] = 16;
      break;
    case 3:
      expected_info[0xb8] = 0;
      expected_info[0x18] = 'X';
      break;
    case 4:
      expected_info[0x18] = 'a';
      expected_info[0xf0] = 1;
      break;
    case 5:
      expected_info[0xf0] = 0;
      expected_info[0xf1] = 0;
      break;
    case 6:
      expected_info[0xf1] = 1;
      guarded = NULL;
      break;
    }
  }
  guarded = NULL;
}

static void *sequence_stub(void *output, const void *config, const void *header) {
  assert(header == (void *)7);
  memset(output, 0x5a, 0x300);
  uint16_t dimensions[] = {(uint16_t)(read_u32(config, 0x40) - 1),
                           (uint16_t)(read_u32(config, 0x44) - 1)};
  memcpy((uint8_t *)output + 0x2d4, dimensions, sizeof(dimensions));
  ((uint8_t *)output)[0x36] = 0;
  return output;
}

static void check_levels(void) {
  assert(dpe_av1_level(1280, 720, 30, 6000000) == 5);
  assert(dpe_av1_level(1280, 720, 60, 6000000) == 8);
  assert(dpe_av1_level(1920, 1080, 30, 6000000) == 8);
  assert(dpe_av1_level(1920, 1080, 60, 6000000) == 9);
  assert(dpe_av1_level(2560, 1440, 60, 6000000) == 12);
  assert(dpe_av1_level(3840, 2160, 30, 6000000) == 12);
  assert(dpe_av1_level(3840, 2160, 60, 6000000) == 13);
  assert(dpe_av1_level(4096, 4096, 60, 6000000) == 16);
  assert(dpe_av1_level(1280, 720, 30, 10000001) == 8);
  assert(dpe_av1_level(1280, 720, 30, 12000001) == 9);
  assert(dpe_av1_level(64, 64, 240, 1000000) == 8);
  assert(dpe_av1_level(0, 720, 30, 6000000) == 31);
  assert(dpe_av1_level(1280, 720, 0, 6000000) == 31);
  assert(dpe_av1_level(1280, 720, 30, 0) == 31);
  assert(dpe_av1_level(UINT32_MAX, UINT32_MAX, UINT32_MAX, UINT64_MAX) == 31);
  uint8_t config[0x50] = {0}, output[0x300], expected[0x300];
  uint32_t fps = 30, width = 1280, height = 720;
  uint64_t bitrate = 6000000;
  memcpy(config + 0x20, &bitrate, sizeof(bitrate));
  memcpy(config + 0x30, &fps, sizeof(fps));
  memcpy(config + 0x40, &width, sizeof(width));
  memcpy(config + 0x44, &height, sizeof(height));
  original_sequence = sequence_stub;
  sequence_stub(expected, config, (void *)7);
  expected[0x36] = 5;
  assert(corrected_sequence(output, config, (void *)7) == output);
  assert(!memcmp(output, expected, sizeof(output)));
  // VBR must use its maximum, not its current target.
  config[0x18] = 1;
  bitrate = 14000000;
  memcpy(config + 0x28, &bitrate, sizeof(bitrate));
  expected[0x36] = 9;
  corrected_sequence(output, config, (void *)7);
  assert(!memcmp(output, expected, sizeof(output)));
  config[0x18] = 2; // Constant quality has no bitrate bound.
  expected[0x36] = 31;
  corrected_sequence(output, config, (void *)7);
  assert(!memcmp(output, expected, sizeof(output)));
}

static void check_vendor_isolation(void) {
  // Reviewed FFI enum values: NvidiaCuda=0, Intel=3, VulkanVaapi=6,
  // NvidiaVulkan=10; vendor Nvidia=0, AMD=1, Intel=2. The installed VAAPI
  // sequence hook must not require unrelated encoders to invoke it, even on
  // a hybrid host that also contains affected AMD VCN4 hardware.
  installed_sequence = 1;
  emit_sequence = false;
  hardware_vcn4 = true;
  expected_session.codec = 2;
  expected_session.fps = 60;
  expected_session.maximum = (struct encoder_dimensions){3840, 2160};
  const struct encoder_options initial_options = {.fps = 60,
                                                  .dimensions = {1920, 1080},
                                                  .bitrate_target = 6000000,
                                                  .bitrate_maximum = 8000000,
                                                  .keyframe_interval = 100};
  const unsigned backends[] = {0, 10};
  unsigned prior_rejections = rejected_creates + rejected_updates;
  for (size_t i = 0; i < sizeof(backends) / sizeof(backends[0]); i++) {
    vendor = 0;
    expected_options = initial_options;
    void *output = NULL;
    assert(create(backends[i], &output) && output == fake_handle && !guarded);
    unsigned prior_updates = updates;
    expected_options.dimensions = (struct encoder_dimensions){1918, 1078};
    expected_options.bitrate_target = 2000000;
    assert(guarded_update(output, expected_options) && updates == prior_updates + 1);
    expected_options.dimensions = (struct encoder_dimensions){3840, 2160};
    expected_options.bitrate_target = 14000000;
    expected_options.bitrate_maximum = 16000000;
    assert(guarded_update(output, expected_options) && updates == prior_updates + 2);
    expected_options.bitrate_target = expected_options.bitrate_maximum = 0;
    assert(guarded_update(output, expected_options) && updates == prior_updates + 3);
    guarded_destroy(output);
    create_ok = false;
    assert(!create(backends[i], &output) && output == NULL && !guarded);
    create_ok = true;
  }
  assert(rejected_creates + rejected_updates == prior_rejections);

  // Intel=3 is present in the cross-platform FFI enum, but this Linux native
  // build rejects it. Do not turn that native rejection into advertised support.
  vendor = 2;
  create_ok = false;
  void *unsupported = NULL;
  assert(!create(3, &unsupported) && unsupported == NULL && !guarded);
  create_ok = true;

  // Intel's Vulkan/VAAPI AV1 uses the shared level correction, but must keep
  // valid even 1080p and adaptive sizes that the AMD VCN4 guard rejects.
  vendor = 2;
  emit_sequence = true;
  expected_options.dimensions = (struct encoder_dimensions){1920, 1080};
  expected_options.bitrate_target = 6000000;
  expected_options.bitrate_maximum = 8000000;
  void *output = NULL;
  assert(create(6, &output) && guarded && !guarded->dimension_guard && guarded->level == 9);
  unsigned prior_updates = updates;
  expected_options.dimensions = (struct encoder_dimensions){1918, 1078};
  assert(guarded_update(output, expected_options) && updates == prior_updates + 1);
  guarded_destroy(output);
  assert(!guarded && rejected_creates + rejected_updates == prior_rejections);
}

static napi_value install_arguments[11];
static const char *install_filename;
static napi_status get_info_stub(napi_env env, napi_callback_info info, size_t *count,
                                 napi_value *args, napi_value *receiver, void **data) {
  (void)env;
  (void)info;
  (void)receiver;
  (void)data;
  assert(*count == 11);
  memcpy(args, install_arguments, sizeof(install_arguments));
  return napi_ok;
}
static napi_status get_string_stub(napi_env env, napi_value value, char *buffer, size_t size,
                                   size_t *length) {
  (void)env;
  (void)value;
  *length = strlen(install_filename);
  assert(*length < size);
  memcpy(buffer, install_filename, *length + 1);
  return napi_ok;
}
static napi_status get_uint_stub(napi_env env, napi_value value, uint32_t *number) {
  (void)env;
  *number = (uintptr_t)value;
  return napi_ok;
}
static napi_status get_undefined_stub(napi_env env, napi_value *value) {
  (void)env;
  *value = (napi_value)1;
  return napi_ok;
}
static napi_status throw_error_stub(napi_env env, const char *code, const char *message) {
  (void)env;
  (void)code;
  fprintf(stderr, "%s\n", message);
  return napi_ok;
}

static void check_lazy_install(char **argv) {
  install_filename = argv[1];
  void *module = dlopen(install_filename, RTLD_LAZY | RTLD_LOCAL);
  assert(module);
  struct link_map *map;
  assert(!dlinfo(module, RTLD_DI_LINKMAP, &map));
  for (int i = 0; i < 10; i++)
    install_arguments[i + 1] = (napi_value)(uintptr_t)strtoul(argv[i + 2], NULL, 10);
  uintptr_t slot = map->l_addr + (uintptr_t)install_arguments[1];
  uintptr_t lazy = map->l_addr + (uintptr_t)install_arguments[3];
  assert(*(uintptr_t *)slot == lazy);
  // dlsym resolves the exported function but does not bind its PLT slot.
  assert(dlsym(module, "cc_encoder_create") ==
         (void *)(map->l_addr + (uintptr_t)install_arguments[2]));
  assert(*(uintptr_t *)slot == lazy);
  assert(lazy_linkage(map->l_addr, slot, lazy));
  assert(!lazy_linkage(map->l_addr, slot + 8, lazy));
  assert(!lazy_linkage(map->l_addr, slot, lazy + 1));
  api_napi_get_cb_info = get_info_stub;
  api_napi_get_value_string_utf8 = get_string_stub;
  api_napi_get_value_uint32 = get_uint_stub;
  api_napi_get_undefined = get_undefined_stub;
  api_napi_throw_error = throw_error_stub;
  assert(install_guard(NULL, NULL) == (napi_value)1);
  assert(*(uintptr_t *)slot == (uintptr_t)guarded_create);
  assert(install_guard(NULL, NULL) == (napi_value)1);
  dlclose(module);
}

static void check_allocation_peak(void) {
  struct guarded_encoder entry = {.handle = fake_handle, .amd_vbaq = true, .level = 31};
  guarded = &entry;
  expected_options = (struct encoder_options){.fps = 60,
                                              .dimensions = {1920, 1080},
                                              .bitrate_target = 9000000,
                                              .bitrate_maximum = 32000000};
  assert(guarded_update(fake_handle, expected_options));
  assert(updated_peak == 9000000);
  expected_options.bitrate_target = 2000000;
  assert(guarded_update(fake_handle, expected_options));
  assert(updated_peak == 2000000);
  guarded = NULL;
  assert(guarded_update(fake_handle, expected_options));
  assert(updated_peak == 32000000);
}

int main(int argc, char **argv) {
  if (argc == 12) {
    check_lazy_install(argv);
    return 0;
  }
  if (argc == 3) {
    assert(all_amd_devices_vcn4(argv[1]) == (atoi(argv[2]) != 0));
    return 0;
  }
  original_create = create_stub;
  check_levels();
  original_update = update_stub;
  original_destroy = destroy_stub;
  original_vendor = vendor_stub;
  check_allocation_peak();
  creates = updates = destroys = 0;
  expected_session = (struct encoder_session){.fps = 60,
                                              .maximum = {1920, 1080},
                                              .format = 3,
                                              .latency = 1,
                                              .keyframe_interval = 100,
                                              .keyframe_interval_ms = 2000,
                                              .codec = 2,
                                              .experiments = "unchanged"};
  expected_options = (struct encoder_options){.fps = 60,
                                              .dimensions = {1920, 1080},
                                              .bitrate_target = 6000000,
                                              .bitrate_maximum = 8000000,
                                              .keyframe_interval = 100};
  void *output = NULL;
  assert(!create(6, &output) && output == NULL && creates == 1 && destroys == 1);
  assert(rejected_creates == 1 && last_rejected.height == 1080);
  assert(verified_hardware && !guarded);

  // Other vendors, codecs, backends and unverified generations retain behavior.
  vendor = 2;
  assert(create(6, &output) && output == fake_handle && !guarded);
  guarded_destroy(output);
  vendor = 1;
  hardware_vcn4 = false;
  assert(create(6, &output) && !guarded);
  guarded_destroy(output);
  hardware_vcn4 = true;
  expected_session.codec = 0;
  assert(create(6, &output) && !guarded);
  guarded_destroy(output);
  expected_session.codec = 2;
  assert(create(0, &output) && !guarded);
  guarded_destroy(output);
  assert(rejected_creates == 1);

  // Keep aligned AV1, and reject a later unaligned adaptive resize before it
  // changes encoder state. Native Drop subsequently owns normal destruction.
  expected_options.dimensions = (struct encoder_dimensions){1280, 720};
  assert(create(6, &output) && guarded && output == fake_handle);
  expected_options.bitrate_target = 4000000;
  assert(guarded_update(output, expected_options) && updates == 1);
  expected_options.dimensions = (struct encoder_dimensions){1920, 1080};
  assert(!guarded_update(output, expected_options) && updates == 1 && rejected_updates == 1);
  guarded_destroy(output);
  assert(!guarded);
  // A reused pointer for H264 must not retain AV1's restriction.
  expected_session.codec = 0;
  assert(create(6, &output));
  assert(guarded_update(output, expected_options) && updates == 2);
  guarded_destroy(output);
  create_ok = false;
  assert(!create(6, &output) && output == NULL);
  create_ok = true;
  expected_session.codec = 2;
  expected_session.fps = 30;
  expected_options.fps = 30;
  expected_options.dimensions = (struct encoder_dimensions){1280, 720};
  expected_options.bitrate_target = 6000000;
  expected_options.bitrate_maximum = 8000000;
  vendor = 2; // Level lifetime protection applies on Intel too.
  installed_sequence = 1;
  assert(!create(6, &output) && output == NULL);
  assert(!strcmp(last_reason, "av1-level-unverified"));
  emit_sequence = true;
  struct construction_limits outer = {.fps = 111};
  constructing = &outer;
  assert(create(6, &output) && guarded->level == 5 && constructing == &outer);
  assert(!outer.observed);
  constructing = NULL;
  unsigned prior_updates = updates;
  expected_options.fps = 60;
  assert(!guarded_update(output, expected_options) && updates == prior_updates);
  assert(!strcmp(last_reason, "av1-level-limit"));
  expected_options.fps = 30;
  assert(guarded_update(output, expected_options) && updates == prior_updates + 1);
  expected_options.bitrate_maximum = 14000000;
  assert(!guarded_update(output, expected_options) && updates == prior_updates + 1);
  guarded_destroy(output);
  // A 60 FPS session initially producing 30 FPS declares its full lifetime
  // ceiling, so recovery to 60 does not cause unnecessary codec fallback.
  expected_session.fps = 60;
  expected_options.bitrate_maximum = 8000000;
  assert(create(6, &output) && guarded->level == 8);
  expected_options.fps = 60;
  assert(guarded_update(output, expected_options) && updates == prior_updates + 2);
  guarded_destroy(output);
  assert(!changes_visible_size((struct encoder_dimensions){3840, 2160}));
  assert(changes_visible_size((struct encoder_dimensions){1921, 1080}));
  check_vendor_isolation();
  check_quality();
  check_qp_trust();
  check_native_bandwidth_gate();
  check_scaler_transitions();
  check_allocation_adaptation();
  check_stream_envelope();
  return 0;
}
