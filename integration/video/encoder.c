// Narrow compatibility guard for reviewed native encoder C ABIs. The original
// codec-error path handles negotiation; this never relabels encoded payloads.
#define _GNU_SOURCE
#include "av1-level.h"
#include "av1-software-crop.h"
#include "encoder-abi.h"
#include "encoder-hevc.h"
#include <dlfcn.h>
#include <glob.h>
#include <limits.h>
#include <link.h>
#include <node_api.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

// From encoding/ffi.h DWARF in both full-hash-reviewed modules. The two
// aggregates are passed by value on the x86-64 SysV stack, including padding.
typedef bool (*encoder_create_fn)(uint32_t, const void *, void *, void *, void *, void **,
                                  struct encoder_session, struct encoder_options);
typedef bool (*encoder_update_fn)(void *, struct encoder_options);
typedef void (*encoder_destroy_fn)(void *);
typedef uint32_t (*encoder_vendor_fn)(void *);
static encoder_create_fn original_create;
static encoder_update_fn original_update;
static encoder_destroy_fn original_destroy;
static encoder_vendor_fn original_vendor;
static uintptr_t installed_slot;
static uintptr_t installed_base, installed_sequence;
typedef void *(*sequence_fn)(void *, const void *, const void *);
static sequence_fn original_sequence;
static uint64_t corrected_sequences;
static uint32_t last_sequence_fps;
static uint64_t last_sequence_bitrate;
static uint8_t last_sequence_level;
static pthread_mutex_t guard_mutex = PTHREAD_MUTEX_INITIALIZER;
struct guarded_encoder {
  void *handle;
  bool dimension_guard;
  bool amd_vbaq;
  uint8_t level;
  struct guarded_encoder *next;
};
struct construction_limits {
  uint32_t fps;
  uint64_t bitrate;
  uint8_t level;
  bool observed;
  bool amd_vbaq;
};
static _Thread_local struct construction_limits *constructing;
static struct guarded_encoder *guarded;
static uint64_t rejected_creates, rejected_updates;
static struct encoder_dimensions last_rejected;
static bool verified_hardware;
static const char *last_reason;
typedef void *(*tunings_fn)(void *, const void *, const void *, const void *);
typedef const char *(*vendor_query_fn)(void *);
static tunings_fn original_tunings;
static vendor_query_fn query_vendor;
static uintptr_t installed_quality;
static uint64_t corrected_quality;
typedef void *(*encoder_info_fn)(void *, const void *);
static encoder_info_fn original_info;
static uintptr_t installed_info;
static uint64_t corrected_qp_trust;
typedef const void *(*encoder_config_fn)(const void *);
static encoder_config_fn original_config;
static uint64_t bandwidth_contract_updates;
typedef void (*configure_scaler_fn)(void *, const void *);
typedef bool (*scaler_started_fn)(const void *);
typedef uint32_t (*settings_codec_fn)(const void *);
static configure_scaler_fn original_configure_quality, original_configure_bandwidth;
static scaler_started_fn original_scaler_started;
static settings_codec_fn original_settings_codec;
static uintptr_t installed_transitions;
static uint64_t retained_scaler_transitions;

struct allocation_owner {
  void *manager, *scaler;
  uint32_t budget;
  bool present;
  struct allocation_owner *next;
};
static struct allocation_owner *allocation_owners;
struct optional_rate {
  int64_t value;
  uint64_t present;
};
typedef struct optional_rate (*rate_query_fn)(const void *, int64_t);
typedef void (*target_budget_fn)(void *, int64_t);
static rate_query_fn original_rate_query;
static target_budget_fn original_target_budget;
static void (*original_scaler_destructors[2])(void *);
static uintptr_t installed_allocation;
static uint64_t allocation_queries;

static void forget_allocation(void *manager, void *scaler) {
  pthread_mutex_lock(&guard_mutex);
  struct allocation_owner **link = &allocation_owners;
  while (*link) {
    struct allocation_owner *entry = *link;
    if ((manager && entry->manager == manager) || (scaler && entry->scaler == scaler)) {
      *link = entry->next;
      free(entry);
    } else
      link = &entry->next;
  }
  pthread_mutex_unlock(&guard_mutex);
}
static void scaler_destructor(void *scaler) {
  forget_allocation(NULL, scaler);
  original_scaler_destructors[0](scaler);
}
static void scaler_deleting_destructor(void *scaler) {
  forget_allocation(NULL, scaler);
  original_scaler_destructors[1](scaler);
}
static struct optional_rate allocation_rate(const void *statistics, int64_t now) {
  // Only CheckBitrate's local query uses this adapter. ReportEncodeInfo and
  // actual rate counters are unchanged. Low-entropy screens must not be
  // mistaken for a constrained allocation. Native limits/hysteresis remain.
  struct optional_rate result = {0};
  bool owned = false;
  pthread_mutex_lock(&guard_mutex);
  for (struct allocation_owner *entry = allocation_owners; entry; entry = entry->next) {
    if ((uintptr_t)entry->scaler + 0x20 != (uintptr_t)statistics)
      continue;
    owned = true;
    result.value = entry->budget;
    result.present = entry->present && entry->budget != 0;
    allocation_queries++;
    break;
  }
  pthread_mutex_unlock(&guard_mutex);
  return owned ? result : original_rate_query(statistics, now);
}
static void target_budget(void *manager, int64_t budget) {
  original_target_budget(manager, budget);
  pthread_mutex_lock(&guard_mutex);
  for (struct allocation_owner *entry = allocation_owners; entry; entry = entry->next) {
    if (entry->manager != manager)
      continue;
    entry->present = budget > 0 && budget <= UINT32_MAX;
    entry->budget = entry->present ? (uint32_t)budget : 0;
  }
  pthread_mutex_unlock(&guard_mutex);
}
static bool allocation_metadata(const void *info) {
  const uint8_t *metadata = info;
  return metadata[8] == 1 && metadata[0xf0] == 0 && metadata[0xf1] == 1 &&
         ((metadata[0x2f] == 10 && !memcmp(metadata + 0x18, "amd: vaapi", 10)) ||
          (metadata[0x2f] == 12 && !memcmp(metadata + 0x18, "intel: vaapi", 12)));
}
static bool managed_codec(uint32_t codec) {
  return codec == 4 || ((codec == 3 || codec == 5) && dpe_hevc_active());
}
typedef void *(*resolution_limit_fn)(void *, uint64_t, const void *);
static resolution_limit_fn original_stream_limit;
static void (*original_info_destructor)(void *);
static uintptr_t installed_stream_limit;
static uint64_t preserved_stream_envelopes;

static void *stream_limit(void *output, uint64_t pixels, const void *limits, const void *owner) {
  void *result = original_stream_limit(output, pixels, limits);
  uint32_t codec, content;
  memcpy(&codec, (const uint8_t *)owner + 0x118, 4);
  memcpy(&content, (const uint8_t *)owner + 0x1a0, 4);
  if (result != output || !((uint8_t *)output)[16] || !managed_codec(codec) || content != 1)
    return result;
  // ReconfigureEncoder's untrusted-QP policy normally adds a default 4Mbps
  // camera envelope at 1080p. Our QP capability correction must not replace
  // the application's negotiated screenshare min/target/max with that default.
  // Query actual current metadata, not another encoder's cached capabilities.
  void *encoder;
  memcpy(&encoder, (const uint8_t *)owner + 0x1e8, sizeof(encoder));
  if (!encoder)
    return result;
  uintptr_t *vtable;
  memcpy(&vtable, encoder, sizeof(vtable));
  encoder_info_fn query = (encoder_info_fn)vtable[0x60 / sizeof(uintptr_t)];
  _Alignas(16) uint8_t metadata[0x110];
  if (query(metadata, encoder) != metadata)
    return result;
  uintptr_t begin, end;
  memcpy(&begin, metadata + 0xb0, sizeof(begin));
  memcpy(&end, metadata + 0xb8, sizeof(end));
  bool eligible = allocation_metadata(metadata) && begin == end;
  original_info_destructor(metadata);
  bool tracked = eligible && dpe_hevc_active();
  pthread_mutex_lock(&guard_mutex);
  for (struct guarded_encoder *entry = guarded; eligible && entry; entry = entry->next)
    tracked |= entry->amd_vbaq;
  if (tracked) {
    // optional<ResolutionBitrateLimits> is trivial: disengaging this one local
    // result skips only the default envelope rewrite. Native scaler thresholds,
    // SFU/application limits and actual hardware-advertised limits are unchanged.
    ((uint8_t *)output)[16] = 0;
    preserved_stream_envelopes++;
  }
  pthread_mutex_unlock(&guard_mutex);
  return result;
}

static void bind_allocation(void *manager, const void *info) {
  if (!allocation_metadata(info) ||
      !managed_codec(original_settings_codec((const uint8_t *)manager + 0x268)))
    return;
  void *resource, *scaler;
  memcpy(&resource, (uint8_t *)manager + 0x48, sizeof(resource));
  if (!resource || !original_scaler_started(resource))
    return;
  memcpy(&scaler, (uint8_t *)resource + 0x60, sizeof(scaler));
  if (!scaler)
    return;
  pthread_mutex_lock(&guard_mutex);
  for (struct allocation_owner *existing = allocation_owners; existing; existing = existing->next) {
    if (existing->manager == manager && existing->scaler == scaler) {
      pthread_mutex_unlock(&guard_mutex);
      return;
    }
  }
  pthread_mutex_unlock(&guard_mutex);
  forget_allocation(manager, NULL);
  struct allocation_owner *entry = calloc(1, sizeof(*entry));
  if (!entry)
    return;
  entry->manager = manager;
  entry->scaler = scaler;
  memcpy(&entry->budget, (uint8_t *)manager + 0xec, 4);
  entry->present = ((uint8_t *)manager)[0xf0] == 1 && entry->budget != 0;
  bool eligible = dpe_hevc_active();
  pthread_mutex_lock(&guard_mutex);
  for (struct guarded_encoder *encoder = guarded; encoder; encoder = encoder->next)
    eligible |= encoder->amd_vbaq;
  if (eligible) {
    entry->next = allocation_owners;
    allocation_owners = entry;
  } else
    free(entry);
  pthread_mutex_unlock(&guard_mutex);
}

static bool transient_encoder_info(void *manager, const void *info) {
  const uint8_t *metadata = info;
  uint32_t degradation;
  memcpy(&degradation, (const uint8_t *)manager + 0x78, sizeof(degradation));
  uint32_t codec = original_settings_codec((const uint8_t *)manager + 0x268);
  bool owned_hevc = false;
  if (codec == 3 || codec == 5) {
    pthread_mutex_lock(&guard_mutex);
    for (struct allocation_owner *owner = allocation_owners; owner; owner = owner->next)
      owned_hevc |= owner->manager == manager;
    pthread_mutex_unlock(&guard_mutex);
  }
  if ((degradation != 1 && degradation != 3) || metadata[0xf1] || metadata[0x2f] != 7 ||
      memcmp(metadata + 0x18, "unknown", 7) || (codec != 4 && !owned_hevc))
    return false;
  void *resource;
  memcpy(&resource, (const uint8_t *)manager + 0x48, sizeof(resource));
  if (!resource || !original_scaler_started(resource))
    return false;
  // The manager owns this resource and its restrictions. MultiEncoder reports
  // unknown while recreating an encoder for a smaller frame. Removing the old
  // resource here resets its own requested resize before any frame can use it.
  // Keep it until actual backend metadata arrives. No cross-encoder cache is
  // used; Source/disabled, codec changes and known fallbacks take native paths.
  pthread_mutex_lock(&guard_mutex);
  retained_scaler_transitions++;
  pthread_mutex_unlock(&guard_mutex);
  return true;
}

static void configure_quality(void *manager, const void *info) {
  if (!transient_encoder_info(manager, info))
    original_configure_quality(manager, info);
}

static void configure_bandwidth(void *manager, const void *info) {
  if (transient_encoder_info(manager, info))
    return;
  if (!allocation_metadata(info))
    forget_allocation(manager, NULL);
  original_configure_bandwidth(manager, info);
  bind_allocation(manager, info);
}

static const void *bandwidth_config(const void *settings, const void *info) {
  const uint8_t *config = original_config(settings);
  const uint8_t *metadata = info;
  if (config[0xc9] || !allocation_metadata(metadata))
    return config;
  // Both full-hash-reviewed native modules leave QP trust unset. This exact
  // metadata signature can only come from our tracked-H264 capability wrapper,
  // including through MultiEncoder/Simulcast copies. Do not use a TLS last-call
  // association: unrelated encoder queries may interleave on the same queue.
  bool matched = dpe_hevc_active();
  pthread_mutex_lock(&guard_mutex);
  for (struct guarded_encoder *entry = guarded; entry; entry = entry->next)
    if (entry->amd_vbaq) {
      matched = true;
      bandwidth_contract_updates++;
      break;
    }
  pthread_mutex_unlock(&guard_mutex);
  // This call site only reads is_quality_scaling_allowed at 0xc9 before
  // discarding the accessor result. Honor explicit thresholds just as native
  // ConfigureQualityScaler does, without changing the actual encoder config.
  static const uint8_t explicit_threshold_gate[0xca] = {[0xc9] = 1};
  return matched ? explicit_threshold_gate : config;
}

static void *trusted_encoder_info(void *output, const void *encoder) {
  void *result = original_info(output, encoder);
  // RustEncoder.native_ is its first field after the vptr. A handle is eligible
  // only after that exact encoder's synchronous creation selected Mesa AMD
  // H264 VBAQ. Our replacement backends have their own QP contract; other
  // encoders preserve any explicit native capability declaration.
  if (result != output)
    return result;
  void *handle;
  memcpy(&handle, (const uint8_t *)encoder + 8, sizeof(handle));
  if (dpe_hevc_owned(handle)) {
    ((uint8_t *)output)[0xf0] = 0;
    ((uint8_t *)output)[0xf1] = 1;
    pthread_mutex_lock(&guard_mutex);
    corrected_qp_trust++;
    pthread_mutex_unlock(&guard_mutex);
    return result;
  }
  if (((uint8_t *)output)[0xf1])
    return result;
  pthread_mutex_lock(&guard_mutex);
  for (struct guarded_encoder *entry = guarded; entry; entry = entry->next) {
    if (entry->handle != handle || !entry->amd_vbaq)
      continue;
    // Reviewed optional<bool>: value, engaged. Slice-header QP is not a
    // frame-average QP with per-block VBAQ. WebRTC switches to bandwidth
    // quality scaling; bitrate control and CPU adaptation remain intact.
    ((uint8_t *)output)[0xf0] = 0;
    ((uint8_t *)output)[0xf1] = 1;
    corrected_qp_trust++;
    break;
  }
  pthread_mutex_unlock(&guard_mutex);
  return result;
}

static void *quality_tunings(void *output, const void *display, const void *session,
                             const void *options) {
  void *result = original_tunings(output, display, session, options);
  // Reviewed Rust SessionOptions.codec is byte 0x32 (H264=0); Tunings has
  // Option<u32> quality_level at 0x10/0x14. Preserve all rate-control fields,
  // non-default presets and other codecs. The first Display word is VADisplay,
  // as established by its reviewed query_vendor_string implementation.
  uint32_t present, level;
  memcpy(&present, (uint8_t *)output + 0x10, sizeof(present));
  memcpy(&level, (uint8_t *)output + 0x14, sizeof(level));
  if (result != output || ((const uint8_t *)session)[0x32] != 0 || present != 1 || level != 0)
    return result;
  void *va_display;
  memcpy(&va_display, display, sizeof(va_display));
  const char *vendor = query_vendor(va_display);
  // This packed quality value is a Mesa AMD extension, NOT the portable VAAPI
  // quality ordering. Intel/NVIDIA and non-Mesa drivers keep native choices.
  if (!vendor || !strstr(vendor, "Mesa Gallium driver ") ||
      (!strstr(vendor, "AMD") && !strstr(vendor, "ATI")))
    return result;
  // Mesa: valid|balanced|VBAQ. No pre-encode analysis or lookahead pass.
  level = 0x13;
  memcpy((uint8_t *)output + 0x14, &level, sizeof(level));
  if (constructing)
    constructing->amd_vbaq = true;
  pthread_mutex_lock(&guard_mutex);
  corrected_quality++;
  pthread_mutex_unlock(&guard_mutex);
  return result;
}

// There is no reviewed native API exposing the selected DRM device. The actual
// encoder vendor is still checked below. Only apply the VCN4 rule when *all*
// AMD render devices have kernel-reported UVD/VCN major 4; mixed/unknown devices
// retain upstream behavior instead of accidentally restricting newer hardware.
static bool all_amd_devices_vcn4(const char *root) {
  char pattern[PATH_MAX];
  if (snprintf(pattern, sizeof(pattern), "%s/renderD*/device/vendor", root) >= (int)sizeof(pattern))
    return false;
  glob_t vendors = {0};
  if (glob(pattern, 0, NULL, &vendors)) {
    globfree(&vendors);
    return false;
  }
  bool found = false, valid = true;
  for (size_t i = 0; valid && i < vendors.gl_pathc; i++) {
    unsigned vendor;
    FILE *file = fopen(vendors.gl_pathv[i], "re");
    if (!file) {
      valid = false;
      break;
    }
    valid = fscanf(file, "%x", &vendor) == 1;
    fclose(file);
    if (!valid || vendor != 0x1002)
      continue;
    found = true;
    size_t prefix = strlen(vendors.gl_pathv[i]) - strlen("vendor");
    if (snprintf(pattern, sizeof(pattern), "%.*sip_discovery/die/*/UVD/*/major", (int)prefix,
                 vendors.gl_pathv[i]) >= (int)sizeof(pattern)) {
      valid = false;
      break;
    }
    glob_t versions = {0};
    valid = glob(pattern, 0, NULL, &versions) == 0 && versions.gl_pathc > 0;
    for (size_t j = 0; valid && j < versions.gl_pathc; j++) {
      unsigned major;
      file = fopen(versions.gl_pathv[j], "re");
      if (!file) {
        valid = false;
        break;
      }
      valid = fscanf(file, "%u", &major) == 1 && major == 4;
      fclose(file);
    }
    globfree(&versions);
  }
  globfree(&vendors);
  return found && valid;
}
#ifndef DPE_ENCODER_VCN4
#define DPE_ENCODER_VCN4() all_amd_devices_vcn4("/sys/class/drm")
#endif

static bool changes_visible_size(struct encoder_dimensions dimensions) {
  // Mesa's pre-VCN5 AV1 coded-size rule aligns width64/height16. The 1080
  // special case produces 1082, confirmed by actual dav1d receive. Render size
  // is a display hint, not a decoded-frame crop. Preserve valid aligned AV1.
  return dimensions.width && dimensions.height &&
         ((dimensions.width & 63) || (dimensions.height & 15));
}

static void record_rejection(struct encoder_dimensions dimensions, bool update,
                             const char *reason) {
  pthread_mutex_lock(&guard_mutex);
  if (update)
    rejected_updates++;
  else
    rejected_creates++;
  last_rejected = dimensions;
  last_reason = reason;
  pthread_mutex_unlock(&guard_mutex);
  fprintf(stderr, "DPE encoder: %s at %ux%u; using native codec fallback.\n", reason,
          dimensions.width, dimensions.height);
}

static bool guarded_create(uint32_t type, const void *frame, void *context, void *encoded_callback,
                           void *dropped_callback, void **output, struct encoder_session session,
                           struct encoder_options options) {
  int hevc =
      dpe_hevc_create(type, context, encoded_callback, dropped_callback, output, session, options);
  if (hevc >= 0)
    return hevc != 0;
  bool av1 = session.codec == 2 && (type == 5 || type == 6);
  struct construction_limits limits = {
      .fps = session.fps > options.fps ? session.fps : options.fps,
      .bitrate = options.bitrate_maximum > options.bitrate_target ? options.bitrate_maximum
                                                                  : options.bitrate_target,
      .level = 31,
  };
  // Both reviewed VAAPI create chains call the sequence constructor directly
  // before returning. Save/restore supports nested construction. Actual config
  // fields remain authoritative; these are additional session-lifetime ceilings.
  struct construction_limits *previous = constructing;
  constructing = (type == 5 || type == 6) ? &limits : NULL;
  bool result = original_create(type, frame, context, encoded_callback, dropped_callback, output,
                                session, options);
  constructing = previous;
  if (!result || !output || !*output)
    return result;
  if (av1 && installed_sequence && !limits.observed) {
    original_destroy(*output);
    *output = NULL;
    record_rejection(options.dimensions, false, "av1-level-unverified");
    return false;
  }
  bool dimension_guard = av1 && original_vendor(*output) == 1 && DPE_ENCODER_VCN4();
  if (dimension_guard) {
    pthread_mutex_lock(&guard_mutex);
    verified_hardware = true;
    pthread_mutex_unlock(&guard_mutex);
    // Use the resizable backend for this generation even when the first
    // rendition is aligned. A later window resize can require padding; an
    // aligned frame takes the same direct import path without a VPP pass.
    void *aligned_encoder = NULL;
    int aligned = dpe_av1_create(type, context, encoded_callback, dropped_callback,
                                 &aligned_encoder, session, options);
    if (aligned > 0 && aligned_encoder) {
      original_destroy(*output);
      *output = aligned_encoder;
      return true;
    }
    if (changes_visible_size(options.dimensions)) {
      original_destroy(*output);
      *output = NULL;
      record_rejection(options.dimensions, false, "amd-av1-coded-size-padding");
      return false;
    }
  }
  if (!dimension_guard && !limits.observed && !limits.amd_vbaq)
    return result;
  struct guarded_encoder *entry = malloc(sizeof(*entry));
  if (!entry) {
    original_destroy(*output);
    *output = NULL;
    return false;
  }
  entry->handle = *output;
  entry->dimension_guard = dimension_guard;
  entry->amd_vbaq = limits.amd_vbaq;
  entry->level = limits.level;
  pthread_mutex_lock(&guard_mutex);
  entry->next = guarded;
  guarded = entry;
  pthread_mutex_unlock(&guard_mutex);
  return true;
}

static bool guarded_update(void *handle, struct encoder_options options) {
  if (dpe_hevc_owned(handle))
    return dpe_hevc_update(handle, options);
  bool dimension_guard = false;
  uint8_t level = 31;
  pthread_mutex_lock(&guard_mutex);
  for (struct guarded_encoder *entry = guarded; entry; entry = entry->next)
    if (entry->handle == handle) {
      dimension_guard = entry->dimension_guard;
      level = entry->level;
      break;
    }
  pthread_mutex_unlock(&guard_mutex);
  if (dimension_guard && changes_visible_size(options.dimensions)) {
    record_rejection(options.dimensions, true, "amd-av1-coded-size-padding");
    return false;
  }
  uint64_t bitrate = options.bitrate_maximum > options.bitrate_target ? options.bitrate_maximum
                                                                      : options.bitrate_target;
  // Zero-rate updates can suspend transmission. No frame can violate a level
  // while suspended; check again when nonzero frame/bitrate settings resume.
  if (level != 31 && options.fps && bitrate && options.dimensions.width &&
      options.dimensions.height &&
      dpe_av1_level(options.dimensions.width, options.dimensions.height, options.fps, bitrate) >
          level) {
    record_rejection(options.dimensions, true, "av1-level-limit");
    return false;
  }
  return original_update(handle, options);
}

static void guarded_destroy(void *handle) {
  if (dpe_hevc_destroy(handle))
    return;
  pthread_mutex_lock(&guard_mutex);
  for (struct guarded_encoder **entry = &guarded; *entry; entry = &(*entry)->next)
    if ((*entry)->handle == handle) {
      struct guarded_encoder *removed = *entry;
      *entry = removed->next;
      free(removed);
      break;
    }
  pthread_mutex_unlock(&guard_mutex);
  original_destroy(handle);
}

#define API(name) static __typeof__(name) *api_##name
API(napi_get_cb_info);
API(napi_get_value_string_utf8);
API(napi_get_value_uint32);
API(napi_get_undefined);
API(napi_create_string_utf8);
API(napi_create_function);
API(napi_set_named_property);
API(napi_throw_error);

static napi_value fail(napi_env env, const char *message) {
  api_napi_throw_error(env, NULL, message);
  return NULL;
}
static napi_value undefined(napi_env env) {
  napi_value result;
  api_napi_get_undefined(env, &result);
  return result;
}

static int address_protection(uintptr_t base, uintptr_t address, size_t size) {
  const ElfW(Ehdr) *header = (void *)base;
  const ElfW(Phdr) *segments = (void *)(base + header->e_phoff);
  int protection = 0;
  for (int i = 0; i < header->e_phnum; i++) {
    const ElfW(Phdr) *p = &segments[i];
    if (p->p_type == PT_LOAD && address >= base + p->p_vaddr && size <= p->p_memsz &&
        address - base - p->p_vaddr <= p->p_memsz - size)
      protection = (p->p_flags & PF_R ? PROT_READ : 0) | (p->p_flags & PF_W ? PROT_WRITE : 0) |
                   (p->p_flags & PF_X ? PROT_EXEC : 0);
  }
  for (int i = 0; i < header->e_phnum; i++) {
    const ElfW(Phdr) *p = &segments[i];
    if (p->p_type == PT_GNU_RELRO && address >= base + p->p_vaddr && size <= p->p_memsz &&
        address - base - p->p_vaddr <= p->p_memsz - size)
      protection &= ~PROT_WRITE;
  }
  return protection;
}

static bool lazy_linkage(uintptr_t base, uintptr_t slot, uintptr_t lazy) {
  if (lazy < 6 || !(address_protection(base, lazy - 6, 16) & PROT_EXEC))
    return false;
  const uint8_t *stub = (void *)(lazy - 6);
  int32_t displacement, resolver_displacement;
  memcpy(&displacement, stub + 2, sizeof(displacement));
  memcpy(&resolver_displacement, stub + 12, sizeof(resolver_displacement));
  uintptr_t resolver = lazy + 10 + resolver_displacement;
  // The full-hash-reviewed .plt entry jumps through this exact GOT slot, then
  // pushes its relocation index and jumps to the module's lazy resolver. A
  // pointer to an unrelated function/stub is never accepted.
  return stub[0] == 0xff && stub[1] == 0x25 && lazy + displacement == slot && stub[6] == 0x68 &&
         stub[11] == 0xe9 && (address_protection(base, resolver, 12) & PROT_EXEC) &&
         *(uint16_t *)resolver == 0x35ff && *(uint16_t *)(resolver + 6) == 0x25ff;
}

static uint32_t read_u32(const void *data, size_t offset) {
  uint32_t value;
  memcpy(&value, (const uint8_t *)data + offset, sizeof(value));
  return value;
}
static uint64_t read_u64(const void *data, size_t offset) {
  uint64_t value;
  memcpy(&value, (const uint8_t *)data + offset, sizeof(value));
  return value;
}

static void *corrected_sequence(void *output, const void *config, const void *obu_header) {
  void *result = original_sequence(output, config, obu_header);
  // Both reviewed constructors use this exact three-pointer ABI. Native Debug
  // metadata and the RateControl consumer establish these config fields;
  // this does not depend on thread-local state or the thread calling create.
  uint32_t fps = read_u32(config, 0x30), rate_control = read_u32(config, 0x18);
  uint64_t bitrate = rate_control == 0   ? read_u64(config, 0x20)
                     : rate_control == 1 ? read_u64(config, 0x28)
                                         : 0;
  if (constructing) {
    if (constructing->fps > fps)
      fps = constructing->fps;
    // CQ has no upper bound; don't misrepresent it using a transport target.
    if (rate_control <= 1 && constructing->bitrate > bitrate)
      bitrate = constructing->bitrate;
  }
  uint16_t dimensions[2];
  memcpy(dimensions, (uint8_t *)output + 0x2d4, sizeof(dimensions));
  uint8_t level =
      dpe_av1_level((uint32_t)dimensions[0] + 1, (uint32_t)dimensions[1] + 1, fps, bitrate);
  // First operating point level; its tier remains the native Main tier zero.
  // The normal native synthesizer emits seq_tier when level > 7, and VAAPI
  // reads the same corrected field. Never edit already encoded OBU bits.
  ((uint8_t *)output)[0x36] = level;
  if (constructing) {
    constructing->observed = true;
    constructing->level = level;
  }
  pthread_mutex_lock(&guard_mutex);
  corrected_sequences++;
  last_sequence_fps = fps;
  last_sequence_bitrate = bitrate;
  last_sequence_level = level;
  pthread_mutex_unlock(&guard_mutex);
  return result;
}

static uint8_t *sequence_near_page(uintptr_t site, size_t size) {
  uintptr_t center = site & ~(size - 1);
  for (uintptr_t distance = 0x10000; distance < 0x70000000; distance += 0x10000) {
    for (int sign = -1; sign <= 1; sign += 2) {
      if ((sign < 0 && center < distance) || (sign > 0 && center > UINTPTR_MAX - distance))
        continue;
      uintptr_t address = sign < 0 ? center - distance : center + distance;
      void *page = mmap((void *)address, size, PROT_READ | PROT_WRITE,
                        MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0);
      if (page == (void *)address)
        return page;
      if (page != MAP_FAILED)
        munmap(page, size);
    }
  }
  return MAP_FAILED;
}

static napi_value install_levels(napi_env env, napi_callback_info info) {
  napi_value args[4];
  size_t count = 4, length;
  char filename[PATH_MAX];
  uint32_t offsets[3];
  if (api_napi_get_cb_info(env, info, &count, args, NULL, NULL) != napi_ok || count != 4 ||
      api_napi_get_value_string_utf8(env, args[0], filename, sizeof(filename), &length) !=
          napi_ok ||
      !length || length >= sizeof(filename) - 1)
    return fail(env, "Invalid AV1 level correction arguments.");
  for (int i = 0; i < 3; i++)
    if (api_napi_get_value_uint32(env, args[i + 1], &offsets[i]) != napi_ok)
      return fail(env, "Invalid AV1 level correction offset.");
  void *handle = dlopen(filename, RTLD_NOW | RTLD_NOLOAD);
  struct link_map *map;
  if (!handle || dlinfo(handle, RTLD_DI_LINKMAP, &map)) {
    if (handle)
      dlclose(handle);
    return fail(env, "The native AV1 module is not loaded.");
  }
  uintptr_t base = map->l_addr, target = base + offsets[0];
  if (base != installed_base) {
    dlclose(handle);
    return fail(env, "AV1 module differs from reviewed encoder guard.");
  }
  if (installed_sequence) {
    dlclose(handle);
    return installed_sequence == target ? undefined(env) : fail(env, "AV1 module changed.");
  }
  uintptr_t sites[] = {base + offsets[1], base + offsets[2]};
  uint8_t before[2][5];
  const uint8_t prologue[] = {0x48, 0x81, 0xec, 0xec, 0x01, 0x00, 0x00};
  if (!(address_protection(base, target, sizeof(prologue)) & PROT_EXEC) ||
      memcmp((void *)target, prologue, sizeof(prologue)) || sites[1] <= sites[0] ||
      sites[1] - sites[0] > 65536) {
    dlclose(handle);
    return fail(env, "AV1 constructor differs from reviewed native code.");
  }
  for (int i = 0; i < 2; i++) {
    if (address_protection(base, sites[i], 5) != (PROT_READ | PROT_EXEC)) {
      dlclose(handle);
      return fail(env, "AV1 constructor call is outside native code.");
    }
    memcpy(before[i], (void *)sites[i], 5);
    int32_t displacement;
    memcpy(&displacement, before[i] + 1, 4);
    if (before[i][0] != 0xe8 || sites[i] + 5 + displacement != target) {
      dlclose(handle);
      return fail(env, "AV1 constructor call differs from reviewed native code.");
    }
  }
  size_t page_size = (size_t)sysconf(_SC_PAGESIZE);
  uint8_t *trampoline = sequence_near_page(sites[0], page_size);
  if (trampoline == MAP_FAILED) {
    dlclose(handle);
    return fail(env, "Could not reserve AV1 correction code.");
  }
  const uint8_t jump[] = {0xff, 0x25, 0, 0, 0, 0};
  uintptr_t replacement = (uintptr_t)corrected_sequence;
  memcpy(trampoline, jump, sizeof(jump));
  memcpy(trampoline + sizeof(jump), &replacement, sizeof(replacement));
  uintptr_t first = sites[0] & ~(page_size - 1), last = (sites[1] + 4) & ~(page_size - 1);
  size_t span = last - first + page_size;
  if (mprotect(trampoline, page_size, PROT_READ | PROT_EXEC) ||
      mprotect((void *)first, span, PROT_READ | PROT_WRITE | PROT_EXEC)) {
    munmap(trampoline, page_size);
    dlclose(handle);
    return fail(env, "Could not install AV1 level correction.");
  }
  original_sequence = (sequence_fn)target;
  for (int i = 0; i < 2; i++) {
    uint8_t patch[5] = {0xe8};
    int32_t displacement = (intptr_t)trampoline - (intptr_t)(sites[i] + 5);
    memcpy(patch + 1, &displacement, 4);
    memcpy((void *)sites[i], patch, 5);
  }
  if (mprotect((void *)first, span, PROT_READ | PROT_EXEC)) {
    for (int i = 0; i < 2; i++)
      memcpy((void *)sites[i], before[i], 5);
    mprotect((void *)first, span, PROT_READ | PROT_EXEC);
    munmap(trampoline, page_size);
    dlclose(handle);
    return fail(env, "Could not restore AV1 code protection.");
  }
  installed_sequence = target;
  dlclose(handle);
  return undefined(env);
}

static napi_value install_guard(napi_env env, napi_callback_info info) {
  napi_value args[11];
  size_t count = 11, length;
  char filename[PATH_MAX];
  uint32_t offsets[10];
  if (api_napi_get_cb_info(env, info, &count, args, NULL, NULL) != napi_ok || count != 11 ||
      api_napi_get_value_string_utf8(env, args[0], filename, sizeof(filename), &length) !=
          napi_ok ||
      !length || length >= sizeof(filename) - 1)
    return fail(env, "Invalid encoder compatibility arguments.");
  for (int i = 0; i < 10; i++)
    if (api_napi_get_value_uint32(env, args[i + 1], &offsets[i]) != napi_ok)
      return fail(env, "Invalid encoder compatibility offset.");
  void *handle = dlopen(filename, RTLD_NOW | RTLD_NOLOAD);
  struct link_map *map;
  if (!handle || dlinfo(handle, RTLD_DI_LINKMAP, &map)) {
    if (handle)
      dlclose(handle);
    return fail(env, "The native encoder module is not loaded.");
  }
  uintptr_t base = map->l_addr;
  if (installed_slot) {
    dlclose(handle);
    return installed_slot == base + offsets[0] ? undefined(env)
                                               : fail(env, "Encoder module changed.");
  }
  const char *names[] = {"cc_encoder_create", "cc_encoder_update", "cc_encoder_destroy",
                         "cc_encoder_vendor"};
  uintptr_t targets[4], slots[3], pages[3], before[3];
  int protections[3];
  size_t page_size = (size_t)sysconf(_SC_PAGESIZE);
  for (int i = 0; i < 4; i++) {
    targets[i] = base + offsets[i == 3 ? 9 : i * 3 + 1];
    if (dlsym(handle, names[i]) != (void *)targets[i] ||
        !(address_protection(base, targets[i], 1) & PROT_EXEC)) {
      dlclose(handle);
      return fail(env, "Encoder ABI target differs from reviewed native code.");
    }
    if (i == 3)
      continue;
    slots[i] = base + offsets[i * 3];
    uintptr_t lazy = base + offsets[i * 3 + 2];
    protections[i] = address_protection(base, slots[i], sizeof(uintptr_t));
    if (slots[i] % sizeof(uintptr_t) || !(protections[i] & PROT_READ) ||
        (protections[i] & PROT_EXEC) || !lazy_linkage(base, slots[i], lazy)) {
      dlclose(handle);
      return fail(env, "Encoder linkage differs from reviewed native code.");
    }
    before[i] = *(uintptr_t *)slots[i];
    if (before[i] != targets[i] && before[i] != lazy) {
      dlclose(handle);
      return fail(env, "Encoder linkage is neither its reviewed target nor lazy resolver.");
    }
    pages[i] = slots[i] & ~(page_size - 1);
  }
  for (int i = 0; i < 3; i++)
    if (mprotect((void *)pages[i], page_size, protections[i] | PROT_WRITE)) {
      for (int j = 0; j < i; j++)
        mprotect((void *)pages[j], page_size, protections[j]);
      dlclose(handle);
      return fail(env, "Could not prepare encoder linkage.");
    }
  original_create = (encoder_create_fn)targets[0];
  original_update = (encoder_update_fn)targets[1];
  original_destroy = (encoder_destroy_fn)targets[2];
  original_vendor = (encoder_vendor_fn)targets[3];
  uintptr_t replacements[] = {(uintptr_t)guarded_create, (uintptr_t)guarded_update,
                              (uintptr_t)guarded_destroy};
  for (int i = 0; i < 3; i++)
    __atomic_store_n((uintptr_t *)slots[i], replacements[i], __ATOMIC_RELEASE);
  bool restored = true;
  for (int i = 0; i < 3; i++)
    restored &= mprotect((void *)pages[i], page_size, protections[i]) == 0;
  if (!restored) {
    // Install runs before VoiceEngine.initialize, so no encoder can race this
    // rollback. Restore original pointers and original page protections.
    for (int i = 0; i < 3; i++)
      if (!mprotect((void *)pages[i], page_size, protections[i] | PROT_WRITE)) {
        __atomic_store_n((uintptr_t *)slots[i], before[i], __ATOMIC_RELEASE);
        mprotect((void *)pages[i], page_size, protections[i]);
      }
    dlclose(handle);
    return fail(env, "Could not restore encoder linkage protection.");
  }
  installed_slot = slots[0];
  installed_base = base;
  dlclose(handle);
  return undefined(env);
}

static napi_value install_quality(napi_env env, napi_callback_info info) {
  napi_value args[4];
  size_t count = 4, length;
  char filename[PATH_MAX];
  uint32_t offsets[3];
  if (api_napi_get_cb_info(env, info, &count, args, NULL, NULL) != napi_ok || count != 4 ||
      api_napi_get_value_string_utf8(env, args[0], filename, sizeof(filename), &length) !=
          napi_ok ||
      !length || length >= sizeof(filename) - 1)
    return fail(env, "Invalid VAAPI quality arguments.");
  for (int i = 0; i < 3; i++)
    if (api_napi_get_value_uint32(env, args[i + 1], &offsets[i]) != napi_ok)
      return fail(env, "Invalid VAAPI quality offset.");
  void *handle = dlopen(filename, RTLD_NOW | RTLD_NOLOAD);
  struct link_map *map;
  if (!handle || dlinfo(handle, RTLD_DI_LINKMAP, &map)) {
    if (handle)
      dlclose(handle);
    return fail(env, "The native encoder module is not loaded.");
  }
  uintptr_t base = map->l_addr, slot = base + offsets[0], target = base + offsets[1],
            query = base + offsets[2];
  if (installed_quality) {
    dlclose(handle);
    return installed_quality == slot ? undefined(env) : fail(env, "VAAPI quality module changed.");
  }
  int protection = address_protection(base, slot, sizeof(uintptr_t));
  const uint8_t prologue[] = {0x55, 0x41, 0x57, 0x41, 0x56, 0x41, 0x55,
                              0x41, 0x54, 0x53, 0x50, 0x49, 0x89, 0xf6};
  if (base != installed_base || slot % sizeof(uintptr_t) || !(protection & PROT_READ) ||
      (protection & PROT_EXEC) ||
      address_protection(base, target, sizeof(prologue)) != (PROT_READ | PROT_EXEC) ||
      address_protection(base, query, 1) != (PROT_READ | PROT_EXEC) ||
      dlsym(handle, "vaQueryVendorString") != (void *)query ||
      memcmp((void *)target, prologue, sizeof(prologue)) || *(uintptr_t *)slot != target) {
    dlclose(handle);
    return fail(env, "VAAPI quality layout differs from review.");
  }
  size_t page_size = (size_t)sysconf(_SC_PAGESIZE);
  uintptr_t page = slot & ~(page_size - 1);
  if (mprotect((void *)page, page_size, protection | PROT_WRITE)) {
    dlclose(handle);
    return fail(env, "Could not prepare VAAPI quality linkage.");
  }
  original_tunings = (tunings_fn)target;
  query_vendor = (vendor_query_fn)query;
  __atomic_store_n((uintptr_t *)slot, (uintptr_t)quality_tunings, __ATOMIC_RELEASE);
  if (mprotect((void *)page, page_size, protection)) {
    __atomic_store_n((uintptr_t *)slot, target, __ATOMIC_RELEASE);
    mprotect((void *)page, page_size, protection);
    dlclose(handle);
    return fail(env, "Could not restore VAAPI quality linkage protection.");
  }
  installed_quality = slot;
  dlclose(handle);
  return undefined(env);
}

static napi_value install_transitions(napi_env env, napi_callback_info info) {
  napi_value args[11];
  size_t count = 11, length;
  char filename[PATH_MAX];
  uint32_t offsets[10];
  if (api_napi_get_cb_info(env, info, &count, args, NULL, NULL) != napi_ok || count != 11 ||
      api_napi_get_value_string_utf8(env, args[0], filename, sizeof(filename), &length) !=
          napi_ok ||
      !length || length >= sizeof(filename) - 1)
    return fail(env, "Invalid scaler transition arguments.");
  for (int i = 0; i < 10; i++)
    if (api_napi_get_value_uint32(env, args[i + 1], &offsets[i]) != napi_ok)
      return fail(env, "Invalid scaler transition offset.");
  void *handle = dlopen(filename, RTLD_NOW | RTLD_NOLOAD);
  struct link_map *map;
  if (!handle || dlinfo(handle, RTLD_DI_LINKMAP, &map)) {
    if (handle)
      dlclose(handle);
    return fail(env, "The native encoder module is not loaded.");
  }
  uintptr_t base = map->l_addr, sites[6], targets[] = {base + offsets[0], base + offsets[4]};
  if (installed_transitions) {
    dlclose(handle);
    return installed_transitions == base + offsets[1]
               ? undefined(env)
               : fail(env, "Scaler transition module changed.");
  }
  uintptr_t pages[6];
  size_t page_size = (size_t)sysconf(_SC_PAGESIZE), page_count = 0;
  uint8_t before[6][5];
  bool valid = base == installed_base;
  for (int i = 0; i < 2; i++)
    valid &= address_protection(base, targets[i], 1) == (PROT_READ | PROT_EXEC);
  for (int i = 8; i < 10; i++)
    valid &= address_protection(base, base + offsets[i], 1) == (PROT_READ | PROT_EXEC);
  for (int i = 0; valid && i < 6; i++) {
    sites[i] = base + offsets[i < 3 ? i + 1 : i + 2];
    if (address_protection(base, sites[i], 5) != (PROT_READ | PROT_EXEC) ||
        sites[i] % page_size > page_size - 5) {
      valid = false;
      break;
    }
    memcpy(before[i], (void *)sites[i], 5);
    int32_t displacement;
    memcpy(&displacement, before[i] + 1, 4);
    valid &= before[i][0] == 0xe8 && sites[i] + 5 + displacement == targets[i / 3];
    uintptr_t page = sites[i] & ~(page_size - 1);
    bool found = false;
    for (size_t j = 0; j < page_count; j++)
      found |= pages[j] == page;
    if (!found)
      pages[page_count++] = page;
  }
  if (!valid) {
    dlclose(handle);
    return fail(env, "Scaler transition call sites differ from review.");
  }
  uint8_t *trampoline = sequence_near_page(sites[0], page_size);
  if (trampoline == MAP_FAILED) {
    dlclose(handle);
    return fail(env, "Could not reserve scaler transition code.");
  }
  const uint8_t jump[] = {0xff, 0x25, 0, 0, 0, 0};
  uintptr_t replacements[] = {(uintptr_t)configure_quality, (uintptr_t)configure_bandwidth};
  for (int i = 0; i < 2; i++) {
    memcpy(trampoline + i * 16, jump, sizeof(jump));
    memcpy(trampoline + i * 16 + sizeof(jump), &replacements[i], sizeof(uintptr_t));
  }
  for (int i = 0; i < 6; i++) {
    intptr_t displacement = (intptr_t)(trampoline + (i / 3) * 16) - (intptr_t)(sites[i] + 5);
    valid &= displacement >= INT32_MIN && displacement <= INT32_MAX;
  }
  if (!valid || mprotect(trampoline, page_size, PROT_READ | PROT_EXEC)) {
    munmap(trampoline, page_size);
    dlclose(handle);
    return fail(env, "Scaler transition trampoline is unavailable.");
  }
  size_t writable = 0;
  for (; writable < page_count; writable++)
    if (mprotect((void *)pages[writable], page_size, PROT_READ | PROT_WRITE | PROT_EXEC))
      break;
  if (writable != page_count) {
    for (size_t i = 0; i < writable; i++)
      mprotect((void *)pages[i], page_size, PROT_READ | PROT_EXEC);
    munmap(trampoline, page_size);
    dlclose(handle);
    return fail(env, "Could not prepare scaler transition call sites.");
  }
  original_configure_quality = (configure_scaler_fn)targets[0];
  original_configure_bandwidth = (configure_scaler_fn)targets[1];
  original_scaler_started = (scaler_started_fn)(base + offsets[8]);
  original_settings_codec = (settings_codec_fn)(base + offsets[9]);
  for (int i = 0; i < 6; i++) {
    uint8_t patch[5] = {0xe8};
    int32_t displacement = (intptr_t)(trampoline + (i / 3) * 16) - (intptr_t)(sites[i] + 5);
    memcpy(patch + 1, &displacement, 4);
    memcpy((void *)sites[i], patch, 5);
  }
  bool restored = true;
  for (size_t i = 0; i < page_count; i++)
    restored &= mprotect((void *)pages[i], page_size, PROT_READ | PROT_EXEC) == 0;
  if (!restored) {
    bool rolled_back = true;
    for (size_t i = 0; i < page_count; i++) {
      if (mprotect((void *)pages[i], page_size, PROT_READ | PROT_WRITE | PROT_EXEC)) {
        rolled_back = false;
        continue;
      }
      for (int j = 0; j < 6; j++)
        if ((sites[j] & ~(page_size - 1)) == pages[i])
          memcpy((void *)sites[j], before[j], 5);
      mprotect((void *)pages[i], page_size, PROT_READ | PROT_EXEC);
    }
    if (rolled_back)
      munmap(trampoline, page_size);
    dlclose(handle);
    return fail(env, "Could not restore scaler transition code protection.");
  }
  installed_transitions = sites[0];
  dlclose(handle);
  return undefined(env);
}

static napi_value install_allocation(napi_env env, napi_callback_info info) {
  napi_value args[9];
  size_t count = 9, length;
  char filename[PATH_MAX];
  uint32_t offsets[8];
  if (api_napi_get_cb_info(env, info, &count, args, NULL, NULL) != napi_ok || count != 9 ||
      api_napi_get_value_string_utf8(env, args[0], filename, sizeof(filename), &length) !=
          napi_ok ||
      !length || length >= sizeof(filename) - 1)
    return fail(env, "Invalid allocation adaptation arguments.");
  for (int i = 0; i < 8; i++)
    if (api_napi_get_value_uint32(env, args[i + 1], &offsets[i]) != napi_ok)
      return fail(env, "Invalid allocation adaptation offset.");
  void *handle = dlopen(filename, RTLD_NOW | RTLD_NOLOAD);
  struct link_map *map;
  if (!handle || dlinfo(handle, RTLD_DI_LINKMAP, &map)) {
    if (handle)
      dlclose(handle);
    return fail(env, "The native encoder module is not loaded.");
  }
  uintptr_t base = map->l_addr, sites[4], targets[4], pages[4];
  int protections[4];
  size_t page_size = (size_t)sysconf(_SC_PAGESIZE);
  uint8_t before[2][5];
  bool valid = base == installed_base;
  if (installed_allocation) {
    dlclose(handle);
    return installed_allocation == base + offsets[0]
               ? undefined(env)
               : fail(env, "Allocation adaptation module changed.");
  }
  for (int i = 0; i < 4; i++) {
    sites[i] = base + offsets[i * 2];
    targets[i] = base + offsets[i * 2 + 1];
    pages[i] = sites[i] & ~(page_size - 1);
    protections[i] = i < 2 ? PROT_READ | PROT_EXEC : PROT_READ;
    valid &= address_protection(base, targets[i], 1) == (PROT_READ | PROT_EXEC);
    valid &= address_protection(base, sites[i], i < 2 ? 5 : 8) == protections[i];
    valid &= sites[i] % page_size <= page_size - (i < 2 ? 5 : 8);
    if (!valid)
      break;
    if (i < 2) {
      memcpy(before[i], (void *)sites[i], 5);
      int32_t displacement;
      memcpy(&displacement, before[i] + 1, 4);
      valid &= before[i][0] == 0xe8 && sites[i] + 5 + displacement == targets[i];
    } else
      valid &= sites[i] % 8 == 0 && *(uintptr_t *)sites[i] == targets[i];
  }
  if (!valid) {
    dlclose(handle);
    return fail(env, "Allocation adaptation sites differ from review.");
  }
  uint8_t *trampoline = sequence_near_page(sites[0], page_size);
  if (trampoline == MAP_FAILED) {
    dlclose(handle);
    return fail(env, "Could not reserve allocation adaptation code.");
  }
  const uint8_t jump[] = {0xff, 0x25, 0, 0, 0, 0};
  uintptr_t replacements[] = {(uintptr_t)allocation_rate, (uintptr_t)target_budget,
                              (uintptr_t)scaler_destructor, (uintptr_t)scaler_deleting_destructor};
  for (int i = 0; i < 2; i++) {
    memcpy(trampoline + i * 16, jump, sizeof(jump));
    memcpy(trampoline + i * 16 + sizeof(jump), &replacements[i], 8);
    intptr_t distance = (intptr_t)(trampoline + i * 16) - (intptr_t)(sites[i] + 5);
    valid &= distance >= INT32_MIN && distance <= INT32_MAX;
  }
  if (!valid || mprotect(trampoline, page_size, PROT_READ | PROT_EXEC)) {
    munmap(trampoline, page_size);
    dlclose(handle);
    return fail(env, "Allocation adaptation trampoline is unavailable.");
  }
  int writable = 0;
  for (; writable < 4; writable++)
    if (mprotect((void *)pages[writable], page_size, protections[writable] | PROT_WRITE))
      break;
  if (writable != 4) {
    for (int i = 0; i < writable; i++)
      mprotect((void *)pages[i], page_size, protections[i]);
    munmap(trampoline, page_size);
    dlclose(handle);
    return fail(env, "Could not prepare allocation adaptation sites.");
  }
  original_rate_query = (rate_query_fn)targets[0];
  original_target_budget = (target_budget_fn)targets[1];
  original_scaler_destructors[0] = (void (*)(void *))targets[2];
  original_scaler_destructors[1] = (void (*)(void *))targets[3];
  for (int i = 0; i < 4; i++) {
    if (i < 2) {
      uint8_t patch[5] = {0xe8};
      int32_t displacement = (intptr_t)(trampoline + i * 16) - (intptr_t)(sites[i] + 5);
      memcpy(patch + 1, &displacement, 4);
      memcpy((void *)sites[i], patch, 5);
    } else
      __atomic_store_n((uintptr_t *)sites[i], replacements[i], __ATOMIC_RELEASE);
  }
  bool restored = true;
  for (int i = 0; i < 4; i++)
    restored &= mprotect((void *)pages[i], page_size, protections[i]) == 0;
  if (!restored) {
    bool rolled_back = true;
    for (int i = 0; i < 4; i++) {
      if (mprotect((void *)pages[i], page_size, protections[i] | PROT_WRITE)) {
        rolled_back = false;
        continue;
      }
      if (i < 2)
        memcpy((void *)sites[i], before[i], 5);
      else
        __atomic_store_n((uintptr_t *)sites[i], targets[i], __ATOMIC_RELEASE);
      mprotect((void *)pages[i], page_size, protections[i]);
    }
    if (rolled_back)
      munmap(trampoline, page_size);
    dlclose(handle);
    return fail(env, "Could not restore allocation adaptation protections.");
  }
  installed_allocation = sites[0];
  dlclose(handle);
  return undefined(env);
}

static napi_value install_stream_limit(napi_env env, napi_callback_info info) {
  napi_value args[4];
  size_t count = 4, length;
  char filename[PATH_MAX];
  uint32_t offsets[3];
  if (api_napi_get_cb_info(env, info, &count, args, NULL, NULL) != napi_ok || count != 4 ||
      api_napi_get_value_string_utf8(env, args[0], filename, sizeof(filename), &length) !=
          napi_ok ||
      !length || length >= sizeof(filename) - 1)
    return fail(env, "Invalid stream bitrate envelope arguments.");
  for (int i = 0; i < 3; i++)
    if (api_napi_get_value_uint32(env, args[i + 1], &offsets[i]) != napi_ok)
      return fail(env, "Invalid stream bitrate envelope offset.");
  void *handle = dlopen(filename, RTLD_NOW | RTLD_NOLOAD);
  struct link_map *map;
  if (!handle || dlinfo(handle, RTLD_DI_LINKMAP, &map)) {
    if (handle)
      dlclose(handle);
    return fail(env, "The native encoder module is not loaded.");
  }
  uintptr_t base = map->l_addr, site = base + offsets[0], target = base + offsets[1],
            destructor = base + offsets[2];
  if (installed_stream_limit) {
    dlclose(handle);
    return installed_stream_limit == site ? undefined(env)
                                          : fail(env, "Stream bitrate envelope module changed.");
  }
  size_t page_size = (size_t)sysconf(_SC_PAGESIZE);
  uintptr_t page = site & ~(page_size - 1);
  const uint8_t prefix[] = {0x48, 0x8d, 0xbd, 0x70, 0xea, 0xff, 0xff,
                            0x48, 0x8d, 0x95, 0xf0, 0xe7, 0xff, 0xff};
  uint8_t before[5];
  bool valid = base == installed_base && site % page_size <= page_size - 5 &&
               address_protection(base, site - sizeof(prefix), sizeof(prefix) + 5) ==
                   (PROT_READ | PROT_EXEC) &&
               address_protection(base, target, 1) == (PROT_READ | PROT_EXEC) &&
               address_protection(base, destructor, 1) == (PROT_READ | PROT_EXEC);
  if (valid) {
    memcpy(before, (void *)site, 5);
    int32_t displacement;
    memcpy(&displacement, before + 1, 4);
    valid = !memcmp((void *)(site - sizeof(prefix)), prefix, sizeof(prefix)) && before[0] == 0xe8 &&
            site + 5 + displacement == target;
  }
  if (!valid) {
    dlclose(handle);
    return fail(env, "Stream bitrate envelope call differs from review.");
  }
  uint8_t *trampoline = sequence_near_page(site, page_size);
  if (trampoline == MAP_FAILED) {
    dlclose(handle);
    return fail(env, "Could not reserve stream bitrate envelope code.");
  }
  // At this exact reviewed call R12 is VideoStreamEncoder*. Add it as RCX,
  // preserving the original sret, optional pixel count and vector arguments.
  const uint8_t jump[] = {0x4c, 0x89, 0xe1, 0xff, 0x25, 0, 0, 0, 0};
  uintptr_t replacement = (uintptr_t)stream_limit;
  memcpy(trampoline, jump, sizeof(jump));
  memcpy(trampoline + sizeof(jump), &replacement, sizeof(replacement));
  intptr_t distance = (intptr_t)trampoline - (intptr_t)(site + 5);
  if (distance < INT32_MIN || distance > INT32_MAX ||
      mprotect(trampoline, page_size, PROT_READ | PROT_EXEC) ||
      mprotect((void *)page, page_size, PROT_READ | PROT_WRITE | PROT_EXEC)) {
    munmap(trampoline, page_size);
    dlclose(handle);
    return fail(env, "Could not prepare stream bitrate envelope code.");
  }
  original_stream_limit = (resolution_limit_fn)target;
  original_info_destructor = (void (*)(void *))destructor;
  uint8_t patch[5] = {0xe8};
  int32_t displacement = distance;
  memcpy(patch + 1, &displacement, 4);
  memcpy((void *)site, patch, 5);
  if (mprotect((void *)page, page_size, PROT_READ | PROT_EXEC)) {
    // The page is still writable on failure, but re-check before rollback.
    if (!mprotect((void *)page, page_size, PROT_READ | PROT_WRITE | PROT_EXEC)) {
      memcpy((void *)site, before, 5);
      mprotect((void *)page, page_size, PROT_READ | PROT_EXEC);
      munmap(trampoline, page_size);
    }
    dlclose(handle);
    return fail(env, "Could not restore stream bitrate envelope code protection.");
  }
  installed_stream_limit = site;
  dlclose(handle);
  return undefined(env);
}

static napi_value install_qp_trust(napi_env env, napi_callback_info info) {
  napi_value args[5];
  size_t count = 5, length;
  char filename[PATH_MAX];
  uint32_t offsets[4];
  if (api_napi_get_cb_info(env, info, &count, args, NULL, NULL) != napi_ok || count != 5 ||
      api_napi_get_value_string_utf8(env, args[0], filename, sizeof(filename), &length) !=
          napi_ok ||
      !length || length >= sizeof(filename) - 1)
    return fail(env, "Invalid encoder QP capability arguments.");
  for (int i = 0; i < 4; i++)
    if (api_napi_get_value_uint32(env, args[i + 1], &offsets[i]) != napi_ok)
      return fail(env, "Invalid encoder QP capability offset.");
  void *handle = dlopen(filename, RTLD_NOW | RTLD_NOLOAD);
  struct link_map *map;
  if (!handle || dlinfo(handle, RTLD_DI_LINKMAP, &map)) {
    if (handle)
      dlclose(handle);
    return fail(env, "The native encoder module is not loaded.");
  }
  uintptr_t base = map->l_addr, slot = base + offsets[0], target = base + offsets[1],
            call = base + offsets[2], config_target = base + offsets[3];
  if (installed_info) {
    dlclose(handle);
    return installed_info == slot ? undefined(env) : fail(env, "Encoder QP module changed.");
  }
  int protection = address_protection(base, slot, sizeof(uintptr_t));
  const uint8_t prologue[] = {0x41, 0x56, 0x53, 0x50, 0x49, 0x89, 0xf6, 0x48, 0x89, 0xfb};
  const uint8_t gate[] = {0x80, 0xb8, 0xc9, 0, 0, 0, 1, 0x75, 0x15};
  if (base != installed_base || slot % sizeof(uintptr_t) || protection != PROT_READ ||
      address_protection(base, target, sizeof(prologue)) != (PROT_READ | PROT_EXEC) ||
      address_protection(base, call, 5 + sizeof(gate)) != (PROT_READ | PROT_EXEC) ||
      address_protection(base, config_target, 1) != (PROT_READ | PROT_EXEC) ||
      memcmp((void *)target, prologue, sizeof(prologue)) || *(uintptr_t *)slot != target ||
      memcmp((void *)(call + 5), gate, sizeof(gate))) {
    dlclose(handle);
    return fail(env, "Encoder QP capability layout differs from review.");
  }
  uint8_t before[5];
  memcpy(before, (void *)call, sizeof(before));
  int32_t original_displacement;
  memcpy(&original_displacement, before + 1, 4);
  if (before[0] != 0xe8 || call + 5 + original_displacement != config_target) {
    dlclose(handle);
    return fail(env, "Bandwidth scaler accessor differs from review.");
  }
  size_t page_size = (size_t)sysconf(_SC_PAGESIZE);
  uintptr_t page = slot & ~(page_size - 1), code_page = call & ~(page_size - 1);
  size_t code_span = ((call + 4) & ~(page_size - 1)) - code_page + page_size;
  uint8_t *trampoline = sequence_near_page(call, page_size);
  if (trampoline == MAP_FAILED) {
    dlclose(handle);
    return fail(env, "Could not reserve bandwidth capability code.");
  }
  const uint8_t jump[] = {0xff, 0x25, 0, 0, 0, 0};
  uintptr_t replacement = (uintptr_t)bandwidth_config;
  memcpy(trampoline, jump, sizeof(jump));
  memcpy(trampoline + sizeof(jump), &replacement, sizeof(replacement));
  if (mprotect(trampoline, page_size, PROT_READ | PROT_EXEC) ||
      mprotect((void *)page, page_size, PROT_READ | PROT_WRITE) ||
      mprotect((void *)code_page, code_span, PROT_READ | PROT_WRITE | PROT_EXEC)) {
    mprotect((void *)page, page_size, protection);
    munmap(trampoline, page_size);
    dlclose(handle);
    return fail(env, "Could not prepare encoder QP capability linkage.");
  }
  original_info = (encoder_info_fn)target;
  original_config = (encoder_config_fn)config_target;
  uint8_t patch[5] = {0xe8};
  int32_t displacement = (intptr_t)trampoline - (intptr_t)(call + 5);
  memcpy(patch + 1, &displacement, 4);
  memcpy((void *)call, patch, 5);
  __atomic_store_n((uintptr_t *)slot, (uintptr_t)trusted_encoder_info, __ATOMIC_RELEASE);
  bool restored = mprotect((void *)page, page_size, protection) == 0;
  restored &= mprotect((void *)code_page, code_span, PROT_READ | PROT_EXEC) == 0;
  if (!restored) {
    if (!mprotect((void *)page, page_size, protection | PROT_WRITE)) {
      __atomic_store_n((uintptr_t *)slot, target, __ATOMIC_RELEASE);
      mprotect((void *)page, page_size, protection);
    }
    bool call_restored = false;
    if (!mprotect((void *)code_page, code_span, PROT_READ | PROT_WRITE | PROT_EXEC)) {
      memcpy((void *)call, before, sizeof(before));
      call_restored = true;
      mprotect((void *)code_page, code_span, PROT_READ | PROT_EXEC);
    }
    // If rollback cannot write the call site, its target is still live. Keep
    // the trampoline and original_config valid rather than leave a dangling
    // branch. Installation stays failed and subsequent validation rejects it.
    if (call_restored)
      munmap(trampoline, page_size);
    dlclose(handle);
    return fail(env, "Could not restore encoder QP capability protection.");
  }
  installed_info = slot;
  dlclose(handle);
  return undefined(env);
}

static napi_value install_software_av1(napi_env env, napi_callback_info info) {
  napi_value args[11];
  size_t count = 11, length;
  char filename[PATH_MAX];
  uint32_t offsets[10];
  if (api_napi_get_cb_info(env, info, &count, args, NULL, NULL) != napi_ok || count != 11 ||
      api_napi_get_value_string_utf8(env, args[0], filename, sizeof(filename), &length) !=
          napi_ok ||
      !length || length >= sizeof(filename) - 1)
    return fail(env, "Invalid software AV1 crop arguments.");
  for (unsigned i = 0; i < 10; i++)
    if (api_napi_get_value_uint32(env, args[i + 1], &offsets[i]) != napi_ok || !offsets[i])
      return fail(env, "Invalid software AV1 crop offset.");
  if (!dpe_software_av1_install(filename, offsets))
    return fail(env, "Could not install the reviewed software AV1 crop adapter.");
  return undefined(env);
}
static napi_value software_av1_stats(napi_env env, napi_callback_info info) {
  (void)info;
  uint64_t values[6];
  dpe_software_av1_stats(values);
  char json[384];
  snprintf(json, sizeof(json),
           "{\"installed\":%s,\"contexts\":%llu,\"submitted\":%llu,\"cropped\":%llu,"
           "\"rejected\":%llu,\"unmatched\":%llu}",
           values[0] ? "true" : "false", (unsigned long long)values[1],
           (unsigned long long)values[2], (unsigned long long)values[3],
           (unsigned long long)values[4], (unsigned long long)values[5]);
  napi_value value;
  api_napi_create_string_utf8(env, json, NAPI_AUTO_LENGTH, &value);
  return value;
}

static napi_value install_hevc(napi_env env, napi_callback_info info) {
  napi_value args[20];
  size_t count = 20, length;
  char filename[PATH_MAX];
  uint32_t offsets[19];
  if (api_napi_get_cb_info(env, info, &count, args, NULL, NULL) != napi_ok || count != 20 ||
      api_napi_get_value_string_utf8(env, args[0], filename, sizeof(filename), &length) !=
          napi_ok ||
      !length || length >= sizeof(filename) - 1)
    return fail(env, "Invalid HEVC encoder arguments.");
  for (size_t i = 0; i < 19; i++)
    if (api_napi_get_value_uint32(env, args[i + 1], &offsets[i]) != napi_ok)
      return fail(env, "Invalid HEVC encoder offset.");
  if (!dpe_hevc_install(filename, offsets))
    return fail(env, "Could not install the reviewed HEVC encoder backend.");
  return undefined(env);
}

static napi_value guard_stats(napi_env env, napi_callback_info info) {
  (void)info;
  char json[1536];
  uint64_t hevc[6], av1[6];
  dpe_hevc_stats(hevc);
  dpe_av1_stats(av1);
  pthread_mutex_lock(&guard_mutex);
  snprintf(json, sizeof(json),
           "{\"installed\":%s,\"hardwareScope\":\"%s\",\"reason\":%s%s%s,"
           "\"rejectedCreates\":%llu,\"rejectedUpdates\":%llu,\"width\":%u,\"height\":%u,"
           "\"av1LevelsInstalled\":%s,\"correctedSequences\":%llu,\"levelIndex\":%u,"
           "\"levelFps\":%u,\"levelBitrate\":%llu,\"amdQualityInstalled\":%s,"
           "\"amdQualityUpdates\":%llu,\"qpTrustInstalled\":%s,\"qpTrustUpdates\":%llu,"
           "\"bandwidthContractUpdates\":%llu,\"scalerTransitionsInstalled\":%s,"
           "\"retainedScalerTransitions\":%llu,\"allocationInstalled\":%s,\"allocationQueries\":%"
           "llu,\"streamEnvelopeInstalled\":%s,\"streamEnvelopesPreserved\":%llu,"
           "\"hevc\":{\"installed\":%s,\"probes\":%llu,\"gpuFrames\":%llu,"
           "\"cpuFrames\":%llu,\"importFallbacks\":%llu,\"failedFrames\":%llu},"
           "\"av1Alignment\":{\"gpuFrames\":%llu,\"cpuFrames\":%llu,"
           "\"importFallbacks\":%llu,\"failedFrames\":%llu}}",
           installed_slot ? "true" : "false", verified_hardware ? "amd-vcn4" : "unverified",
           last_reason ? "\"" : "", last_reason ? last_reason : "null", last_reason ? "\"" : "",
           (unsigned long long)rejected_creates, (unsigned long long)rejected_updates,
           last_rejected.width, last_rejected.height, installed_sequence ? "true" : "false",
           (unsigned long long)corrected_sequences, last_sequence_level, last_sequence_fps,
           (unsigned long long)last_sequence_bitrate, installed_quality ? "true" : "false",
           (unsigned long long)corrected_quality, installed_info ? "true" : "false",
           (unsigned long long)corrected_qp_trust, (unsigned long long)bandwidth_contract_updates,
           installed_transitions ? "true" : "false",
           (unsigned long long)retained_scaler_transitions, installed_allocation ? "true" : "false",
           (unsigned long long)allocation_queries, installed_stream_limit ? "true" : "false",
           (unsigned long long)preserved_stream_envelopes, hevc[0] ? "true" : "false",
           (unsigned long long)hevc[1], (unsigned long long)hevc[2], (unsigned long long)hevc[3],
           (unsigned long long)hevc[4], (unsigned long long)hevc[5], (unsigned long long)av1[2],
           (unsigned long long)av1[3], (unsigned long long)av1[4], (unsigned long long)av1[5]);
  pthread_mutex_unlock(&guard_mutex);
  napi_value result;
  api_napi_create_string_utf8(env, json, NAPI_AUTO_LENGTH, &result);
  return result;
}

napi_value dpe_encoder_register(napi_env env, napi_value exports) {
#define LOAD(name)                                                                                 \
  api_##name = dlsym(RTLD_DEFAULT, #name);                                                         \
  if (!api_##name)                                                                                 \
  return NULL
  LOAD(napi_get_cb_info);
  LOAD(napi_get_value_string_utf8);
  LOAD(napi_get_value_uint32);
  LOAD(napi_get_undefined);
  LOAD(napi_create_string_utf8);
  LOAD(napi_create_function);
  LOAD(napi_set_named_property);
  LOAD(napi_throw_error);
  napi_value function;
  if (api_napi_create_function(env, "installSoftwareAv1Crop", NAPI_AUTO_LENGTH,
                               install_software_av1, NULL, &function) != napi_ok ||
      api_napi_set_named_property(env, exports, "installSoftwareAv1Crop", function) != napi_ok ||
      api_napi_create_function(env, "softwareAv1CropStats", NAPI_AUTO_LENGTH, software_av1_stats,
                               NULL, &function) != napi_ok ||
      api_napi_set_named_property(env, exports, "softwareAv1CropStats", function) != napi_ok ||
      api_napi_create_function(env, "installEncoderGuard", NAPI_AUTO_LENGTH, install_guard, NULL,
                               &function) != napi_ok ||
      api_napi_set_named_property(env, exports, "installEncoderGuard", function) != napi_ok ||
      api_napi_create_function(env, "installHevcEncoder", NAPI_AUTO_LENGTH, install_hevc, NULL,
                               &function) != napi_ok ||
      api_napi_set_named_property(env, exports, "installHevcEncoder", function) != napi_ok ||
      api_napi_create_function(env, "installAv1Levels", NAPI_AUTO_LENGTH, install_levels, NULL,
                               &function) != napi_ok ||
      api_napi_set_named_property(env, exports, "installAv1Levels", function) != napi_ok ||
      api_napi_create_function(env, "installEncoderQuality", NAPI_AUTO_LENGTH, install_quality,
                               NULL, &function) != napi_ok ||
      api_napi_set_named_property(env, exports, "installEncoderQuality", function) != napi_ok ||
      api_napi_create_function(env, "installScalerTransitions", NAPI_AUTO_LENGTH,
                               install_transitions, NULL, &function) != napi_ok ||
      api_napi_set_named_property(env, exports, "installScalerTransitions", function) != napi_ok ||
      api_napi_create_function(env, "installEncoderAllocation", NAPI_AUTO_LENGTH,
                               install_allocation, NULL, &function) != napi_ok ||
      api_napi_set_named_property(env, exports, "installEncoderAllocation", function) != napi_ok ||
      api_napi_create_function(env, "installStreamBitrateEnvelope", NAPI_AUTO_LENGTH,
                               install_stream_limit, NULL, &function) != napi_ok ||
      api_napi_set_named_property(env, exports, "installStreamBitrateEnvelope", function) !=
          napi_ok ||
      api_napi_create_function(env, "installEncoderQpTrust", NAPI_AUTO_LENGTH, install_qp_trust,
                               NULL, &function) != napi_ok ||
      api_napi_set_named_property(env, exports, "installEncoderQpTrust", function) != napi_ok ||
      api_napi_create_function(env, "encoderGuardStats", NAPI_AUTO_LENGTH, guard_stats, NULL,
                               &function) != napi_ok ||
      api_napi_set_named_property(env, exports, "encoderGuardStats", function) != napi_ok)
    return NULL;
  return exports;
}
