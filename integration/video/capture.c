// Optional, version-scoped PipeWire capture measurements and backlog reduction.
// Installed before VoiceEngine.initialize. Never modifies a running installation.
#define _GNU_SOURCE
#include "capture-rate.h"
#include <dlfcn.h>
#include <errno.h>
#include <limits.h>
#include <link.h>
#include <node_api.h>
#include <pipewire/stream.h>
#include <spa/buffer/meta.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#define CAPTURE_LIMIT 32
#define HISTOGRAM_SIZE 24
#ifndef DPE_CAPTURE_BEFORE_DEQUEUE
#define DPE_CAPTURE_BEFORE_DEQUEUE() ((void)0)
#endif
// This pair is the verified x86-64 return layout of Rust's Option<Buffer>.
struct captured_buffer {
  struct pw_buffer *buffer;
  struct pw_stream *stream;
};
typedef struct captured_buffer (*dequeue_fn)(struct pw_stream *);
typedef void (*release_fn)(struct captured_buffer *);
typedef int (*return_fn)(struct pw_stream *, struct pw_buffer *);
typedef int (*error_fn)(struct pw_stream *, int, const char *, ...);
static dequeue_fn original_dequeue;
static release_fn original_release;
static return_fn return_buffer;
static error_fn stream_error;
static atomic_bool optimize;
// Low 8 bits hold FPS, upper bits a generation. Reconfiguring a stopped share
// must reset its clock even if the callback never observed the intervening stop.
static atomic_uint_fast64_t pacing_config;
static _Thread_local uint64_t pacing_generation;
static _Thread_local struct pw_stream *pacing_stream;
static _Thread_local struct dpe_capture_rate pacing_clock;
static uintptr_t installed_site;
static _Thread_local struct pw_buffer *processing_buffer;
static _Thread_local uint64_t processing_started;

struct counters {
  atomic_uint_fast64_t callbacks, frames, dmabuf, memfd, other, empty, skipped;
  atomic_uint_fast64_t conservative, return_errors, max_dequeued, pts_known, pts_unknown;
  atomic_uint_fast64_t meta_types;
  atomic_uint_fast64_t paced, pacing_bypass;
  atomic_uint_fast64_t sync_timeline, sync_invalid, sync_absent;
  atomic_uint_fast64_t processing_us[HISTOGRAM_SIZE];
};
static struct counters counts;

static uint64_t monotonic_ns(void) {
  struct timespec now;
  clock_gettime(CLOCK_MONOTONIC, &now);
  return (uint64_t)now.tv_sec * 1000000000ull + now.tv_nsec;
}
#ifndef DPE_CAPTURE_TIME_NS
#define DPE_CAPTURE_TIME_NS() monotonic_ns()
#endif

static void configure_pacing(unsigned fps) {
  uint64_t previous = atomic_load_explicit(&pacing_config, memory_order_relaxed);
  if ((previous & 255) != fps)
    atomic_store_explicit(&pacing_config, ((previous + 256) & ~255ull) | fps, memory_order_relaxed);
}

static bool frame_due(struct pw_stream *stream, uint64_t config, uint64_t now) {
  if (pacing_stream != stream || pacing_generation != config) {
    pacing_stream = stream;
    pacing_generation = config;
    dpe_capture_rate_configure(&pacing_clock, config & 255);
  }
  // Scheduling jitter around a 30/60 Hz deadline must not halve a correctly
  // paced source. Allow at most 1ms early, keeping the original phase. Repeated
  // callbacks cannot accumulate credit or push an unbounded queue into native.
  uint64_t slack = pacing_clock.interval / 8;
  if (slack > 1000000)
    slack = 1000000;
  if (pacing_clock.started && now > pacing_clock.last && now < pacing_clock.next &&
      pacing_clock.next - now <= slack) {
    pacing_clock.last = now;
    pacing_clock.next = UINT64_MAX - pacing_clock.next < pacing_clock.interval
                            ? UINT64_MAX
                            : pacing_clock.next + pacing_clock.interval;
    return true;
  }
  return dpe_capture_rate_due(&pacing_clock, now);
}

static void maximum(atomic_uint_fast64_t *value, uint64_t next) {
  uint64_t previous = atomic_load_explicit(value, memory_order_relaxed);
  while (previous < next &&
         !atomic_compare_exchange_weak_explicit(value, &previous, next, memory_order_relaxed,
                                                memory_order_relaxed)) {
  }
}

// Only full raw video frames whose metadata is consumed independently by the
// reviewed native callback may supersede each other. In particular, leave
// explicit-sync release, cursor-only updates and unfamiliar metadata to native.
static bool supersedable(const struct pw_buffer *pw) {
  const struct spa_buffer *buffer = pw ? pw->buffer : NULL;
  if (!buffer || !buffer->n_datas || buffer->n_datas > 4 || !buffer->datas ||
      (buffer->n_metas && !buffer->metas))
    return false;
  for (uint32_t i = 0; i < buffer->n_datas; i++) {
    const struct spa_data *data = &buffer->datas[i];
    // PipeWire permits zero DMA-BUF sizes, but the reviewed native callback
    // drops a zero first chunk before dispatching by memory type. Never discard
    // an older usable frame in favor of one this native consumer will drop.
    if ((data->type != SPA_DATA_DmaBuf && data->type != SPA_DATA_MemFd) || !data->chunk ||
        !data->chunk->size || data->chunk->flags)
      return false;
  }
  for (uint32_t i = 0; i < buffer->n_metas; i++) {
    const struct spa_meta *meta = &buffer->metas[i];
    if (!meta->data)
      return false;
    switch (meta->type) {
    case SPA_META_Header:
      if (meta->size < sizeof(struct spa_meta_header) ||
          ((struct spa_meta_header *)meta->data)->flags)
        return false;
      break;
    case SPA_META_VideoCrop:
    case SPA_META_VideoDamage:
      if (meta->size < sizeof(struct spa_meta_region))
        return false;
      break;
    case SPA_META_Busy:
      if (meta->size < sizeof(struct spa_meta_busy))
        return false;
      break;
    default:
      return false;
    }
  }
  return true;
}

// Observe the contract the reviewed native extractor actually understands.
// Metadata presence alone does not establish valid acquire/release objects, nor
// does this observation prove that the producer attached a successful fence.
static void observe_sync(const struct spa_buffer *buffer) {
  if (!buffer || !buffer->datas || !buffer->n_datas || buffer->n_datas > 64 ||
      buffer->datas[0].type != SPA_DATA_DmaBuf)
    return;
  if (buffer->n_metas > 64 || (buffer->n_metas && !buffer->metas)) {
    atomic_fetch_add_explicit(&counts.sync_invalid, 1, memory_order_relaxed);
    return;
  }
  const struct spa_meta *timeline = NULL;
  for (uint32_t i = 0; i < buffer->n_metas; i++)
    if (buffer->metas[i].type == 9) { // Stable SPA_META_SyncTimeline ABI.
      timeline = &buffer->metas[i];
      break;
    }
  if (!timeline) {
    atomic_fetch_add_explicit(&counts.sync_absent, 1, memory_order_relaxed);
    return;
  }
  bool valid = timeline->data && timeline->size >= 24 && buffer->n_datas >= 3;
  // The native consumer imports the final two data entries as acquire/release.
  for (uint32_t i = buffer->n_datas >= 2 ? buffer->n_datas - 2 : 0; i < buffer->n_datas; i++)
    valid &= buffer->datas[i].type == 5 && // Stable SPA_DATA_SyncObj ABI.
             buffer->datas[i].fd >= 0 && buffer->datas[i].fd <= INT_MAX;
  atomic_fetch_add_explicit(valid ? &counts.sync_timeline : &counts.sync_invalid, 1,
                            memory_order_relaxed);
}

static struct captured_buffer capture_dequeue(struct pw_stream *stream) {
  DPE_CAPTURE_BEFORE_DEQUEUE();
  atomic_fetch_add_explicit(&counts.callbacks, 1, memory_order_relaxed);
  struct captured_buffer chosen = original_dequeue(stream);
  if (!chosen.buffer)
    return chosen;
  if (atomic_load_explicit(&optimize, memory_order_relaxed) && return_buffer) {
    struct captured_buffer buffers[CAPTURE_LIMIT];
    buffers[0] = chosen;
    size_t size = 1;
    bool safe = supersedable(chosen.buffer);
    while (size < CAPTURE_LIMIT) {
      struct captured_buffer next = original_dequeue(stream);
      if (!next.buffer)
        break;
      buffers[size++] = next;
      safe &= supersedable(next.buffer);
    }
    maximum(&counts.max_dequeued, size);
    // At the limit, preserve order rather than claiming to have found the newest.
    if (safe && size < CAPTURE_LIMIT) {
      for (size_t i = 0; i + 1 < size; i++) {
        original_release(&buffers[i]);
        atomic_fetch_add_explicit(&counts.skipped, 1, memory_order_relaxed);
      }
      chosen = buffers[size - 1];
    } else {
      atomic_fetch_add_explicit(&counts.conservative, 1, memory_order_relaxed);
      // PipeWire return_buffer pushes to the FRONT and reverses dequeue's
      // ownership bookkeeping. Restore in reverse order, preserving all metadata
      // and leaving acquire/release timelines for the original native consumer.
      for (size_t i = size; i-- > 1;) {
        if (return_buffer(stream, buffers[i].buffer) < 0) {
          // A valid dequeued buffer has a reserved queue slot. Treat a violated
          // return contract as a fatal stream error; do not release an unsignaled
          // explicit-sync buffer or pretend its pixels were consumed.
          atomic_fetch_add_explicit(&counts.return_errors, 1, memory_order_relaxed);
          atomic_store_explicit(&optimize, false, memory_order_relaxed);
          stream_error(stream, -EIO, "DPE capture buffer return failed");
        }
      }
    }
  }
  uint64_t config = atomic_load_explicit(&pacing_config, memory_order_relaxed);
  if (config & 255) {
    if (!supersedable(chosen.buffer)) {
      atomic_fetch_add_explicit(&counts.pacing_bypass, 1, memory_order_relaxed);
    } else if (!frame_due(stream, config, DPE_CAPTURE_TIME_NS())) {
      original_release(&chosen);
      atomic_fetch_add_explicit(&counts.paced, 1, memory_order_relaxed);
      return (struct captured_buffer){NULL, stream};
    }
  }
  atomic_fetch_add_explicit(&counts.frames, 1, memory_order_relaxed);
  struct spa_buffer *buffer = chosen.buffer->buffer;
  observe_sync(buffer);
  if (buffer && buffer->metas && buffer->n_metas <= 64)
    for (uint32_t i = 0; i < buffer->n_metas; i++)
      if (buffer->metas[i].type < 64)
        atomic_fetch_or_explicit(&counts.meta_types, 1ull << buffer->metas[i].type,
                                 memory_order_relaxed);
  if (buffer && buffer->n_datas && buffer->datas) {
    struct spa_data *data = &buffer->datas[0];
    atomic_uint_fast64_t *counter = data->type == SPA_DATA_DmaBuf  ? &counts.dmabuf
                                    : data->type == SPA_DATA_MemFd ? &counts.memfd
                                                                   : &counts.other;
    atomic_fetch_add_explicit(counter, 1, memory_order_relaxed);
    if (!data->chunk || (data->chunk->flags & SPA_CHUNK_FLAG_EMPTY) ||
        (data->type != SPA_DATA_DmaBuf && !data->chunk->size))
      atomic_fetch_add_explicit(&counts.empty, 1, memory_order_relaxed);
    struct spa_meta_header *header =
        spa_buffer_find_meta_data(buffer, SPA_META_Header, sizeof(*header));
    atomic_fetch_add_explicit(header && header->pts >= 0 ? &counts.pts_known : &counts.pts_unknown,
                              1, memory_order_relaxed);
  }
  processing_buffer = chosen.buffer;
  processing_started = monotonic_ns();
  return chosen;
}

static void capture_release(struct captured_buffer *buffer) {
  if (buffer->buffer == processing_buffer) {
    uint64_t us = (monotonic_ns() - processing_started) / 1000;
    unsigned int bucket = 0;
    while (us > 1 && bucket + 1 < HISTOGRAM_SIZE) {
      us >>= 1;
      bucket++;
    }
    atomic_fetch_add_explicit(&counts.processing_us[bucket], 1, memory_order_relaxed);
    processing_buffer = NULL;
  }
  original_release(buffer);
}

#define API(name) static __typeof__(name) *api_##name
API(napi_get_cb_info);
API(napi_get_value_bool);
API(napi_get_value_string_utf8);
API(napi_get_value_uint32);
API(napi_get_value_double);
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

static bool in_segment(uintptr_t base, uintptr_t address, size_t size, unsigned flags) {
  const ElfW(Ehdr) *header = (void *)base;
  const ElfW(Phdr) *segments = (void *)(base + header->e_phoff);
  for (int i = 0; i < header->e_phnum; i++) {
    const ElfW(Phdr) *p = &segments[i];
    if (p->p_type == PT_LOAD && (p->p_flags & flags) == flags && address >= base + p->p_vaddr &&
        size <= p->p_memsz && address - base - p->p_vaddr <= p->p_memsz - size)
      return true;
  }
  return false;
}

static uint8_t *near_page(uintptr_t site, size_t size) {
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

static napi_value install_capture(napi_env env, napi_callback_info info) {
  napi_value args[6];
  size_t count = 6, length;
  char filename[PATH_MAX];
  uint32_t offsets[4];
  bool latest;
  if (api_napi_get_cb_info(env, info, &count, args, NULL, NULL) != napi_ok || count != 6 ||
      api_napi_get_value_string_utf8(env, args[0], filename, sizeof(filename), &length) !=
          napi_ok ||
      !length || length >= sizeof(filename) - 1 ||
      api_napi_get_value_bool(env, args[5], &latest) != napi_ok)
    return fail(env, "Invalid capture adapter arguments.");
  for (int i = 0; i < 4; i++)
    if (api_napi_get_value_uint32(env, args[i + 1], &offsets[i]) != napi_ok)
      return fail(env, "Invalid capture adapter offset.");
  void *handle = dlopen(filename, RTLD_NOW | RTLD_NOLOAD);
  struct link_map *map;
  if (!handle || dlinfo(handle, RTLD_DI_LINKMAP, &map)) {
    if (handle)
      dlclose(handle);
    return fail(env, "The native capture module is not loaded.");
  }
  uintptr_t base = map->l_addr, sites[2] = {base + offsets[0], base + offsets[2]};
  uintptr_t targets[2] = {base + offsets[1], base + offsets[3]};
  if (installed_site) {
    dlclose(handle);
    return installed_site == sites[0] ? undefined(env) : fail(env, "Capture module changed.");
  }
  uint8_t before[2][6];
  for (int i = 0; i < 2; i++) {
    if (!in_segment(base, sites[i], 6, PF_X) || !in_segment(base, targets[i], 1, PF_X)) {
      dlclose(handle);
      return fail(env, "Capture adapter address is outside native code.");
    }
    memcpy(before[i], (void *)sites[i], 6);
    int32_t displacement;
    memcpy(&displacement, before[i] + 2, 4);
    uintptr_t slot = sites[i] + 6 + displacement;
    if (before[i][0] != 0xff || before[i][1] != 0x15 ||
        !in_segment(base, slot, sizeof(uintptr_t), PF_R) || *(uintptr_t *)slot != targets[i]) {
      dlclose(handle);
      return fail(env, "Capture adapter call differs from reviewed native code.");
    }
  }
  if (sites[1] <= sites[0] || sites[1] - sites[0] > 65536) {
    dlclose(handle);
    return fail(env, "Capture adapter calls are not in one bounded callback.");
  }
  size_t page_size = (size_t)sysconf(_SC_PAGESIZE);
  uint8_t *trampoline = near_page(sites[0], page_size);
  if (trampoline == MAP_FAILED) {
    dlclose(handle);
    return fail(env, "Could not reserve capture adapter code.");
  }
  uintptr_t replacements[2] = {(uintptr_t)capture_dequeue, (uintptr_t)capture_release};
  const uint8_t jump[] = {0xff, 0x25, 0, 0, 0, 0};
  for (int i = 0; i < 2; i++) {
    memcpy(trampoline + i * 16, jump, 6);
    memcpy(trampoline + i * 16 + 6, &replacements[i], 8);
  }
  uintptr_t first = sites[0] & ~(page_size - 1), last = (sites[1] + 5) & ~(page_size - 1);
  size_t span = last - first + page_size;
  if (mprotect(trampoline, page_size, PROT_READ | PROT_EXEC) ||
      mprotect((void *)first, span, PROT_READ | PROT_WRITE | PROT_EXEC)) {
    munmap(trampoline, page_size);
    dlclose(handle);
    return fail(env, "Could not install capture adapter.");
  }
  original_dequeue = (dequeue_fn)targets[0];
  original_release = (release_fn)targets[1];
  for (int i = 0; i < 2; i++) {
    uint8_t patch[6] = {0xe8, 0, 0, 0, 0, 0x90};
    int32_t displacement = (intptr_t)(trampoline + i * 16) - (intptr_t)(sites[i] + 5);
    memcpy(patch + 1, &displacement, 4);
    memcpy((void *)sites[i], patch, 6);
  }
  if (mprotect((void *)first, span, PROT_READ | PROT_EXEC)) {
    for (int i = 0; i < 2; i++)
      memcpy((void *)sites[i], before[i], 6);
    mprotect((void *)first, span, PROT_READ | PROT_EXEC);
    munmap(trampoline, page_size);
    dlclose(handle);
    return fail(env, "Could not restore capture code protection.");
  }
  // Optional API: older PipeWire keeps the measured native FIFO behavior.
  void *pipewire = dlopen("libpipewire-0.3.so.0", RTLD_NOW | RTLD_LOCAL);
  if (pipewire) {
    return_buffer = dlsym(pipewire, "pw_stream_return_buffer");
    stream_error = dlsym(pipewire, "pw_stream_set_error");
  }
  atomic_store(&optimize, latest && return_buffer && stream_error);
  installed_site = sites[0];
  // Retain both libraries and executable trampoline for process lifetime.
  return undefined(env);
}

static napi_value set_capture_rate(napi_env env, napi_callback_info info) {
  napi_value arg;
  size_t count = 1;
  double fps;
  if (api_napi_get_cb_info(env, info, &count, &arg, NULL, NULL) != napi_ok || count != 1 ||
      api_napi_get_value_double(env, arg, &fps) != napi_ok || !(fps >= 0 && fps <= 240) ||
      (unsigned)fps != fps)
    return fail(env, "Capture rate must be an integer from 0 through 240.");
  configure_pacing((unsigned)fps);
  return undefined(env);
}

static napi_value capture_stats(napi_env env, napi_callback_info info) {
  (void)info;
  char json[4096];
#define GET(field) ((unsigned long long)atomic_load_explicit(&counts.field, memory_order_relaxed))
  int length = snprintf(
      json, sizeof(json),
      "{\"installed\":%s,\"latestEnabled\":%s,\"callbacks\":%llu,\"frames\":%llu,"
      "\"dmabufFrames\":%llu,\"memfdFrames\":%llu,\"otherFrames\":%llu,\"emptyFrames\":%llu,"
      "\"skippedFrames\":%llu,\"conservativeQueues\":%llu,\"returnErrors\":%llu,"
      "\"maxDequeued\":%llu,\"knownPts\":%llu,\"unknownPts\":%llu,\"metadataTypes\":\"0x%llx\","
      "\"pacingFps\":%u,\"pacedFrames\":%llu,\"pacingBypassFrames\":%llu,"
      "\"syncTimelineFrames\":%llu,\"syncTimelineInvalidFrames\":%llu,"
      "\"noSyncTimelineFrames\":%llu,"
      "\"processingUsLog2\":[",
      installed_site ? "true" : "false", atomic_load(&optimize) ? "true" : "false", GET(callbacks),
      GET(frames), GET(dmabuf), GET(memfd), GET(other), GET(empty), GET(skipped), GET(conservative),
      GET(return_errors), GET(max_dequeued), GET(pts_known), GET(pts_unknown), GET(meta_types),
      (unsigned)(atomic_load(&pacing_config) & 255), GET(paced), GET(pacing_bypass),
      GET(sync_timeline), GET(sync_invalid), GET(sync_absent));
  for (int i = 0; i < HISTOGRAM_SIZE; i++)
    length += snprintf(json + length, sizeof(json) - length, "%s%llu", i ? "," : "",
                       GET(processing_us[i]));
  snprintf(json + length, sizeof(json) - length, "]}");
  napi_value result;
  api_napi_create_string_utf8(env, json, NAPI_AUTO_LENGTH, &result);
  return result;
}

napi_value dpe_capture_register(napi_env env, napi_value exports) {
#define LOAD(name)                                                                                 \
  api_##name = dlsym(RTLD_DEFAULT, #name);                                                         \
  if (!api_##name)                                                                                 \
  return NULL
  LOAD(napi_get_cb_info);
  LOAD(napi_get_value_bool);
  LOAD(napi_get_value_string_utf8);
  LOAD(napi_get_value_uint32);
  LOAD(napi_get_value_double);
  LOAD(napi_get_undefined);
  LOAD(napi_create_string_utf8);
  LOAD(napi_create_function);
  LOAD(napi_set_named_property);
  LOAD(napi_throw_error);
  napi_value function;
  if (api_napi_create_function(env, "installCapture", NAPI_AUTO_LENGTH, install_capture, NULL,
                               &function) != napi_ok ||
      api_napi_set_named_property(env, exports, "installCapture", function) != napi_ok ||
      api_napi_create_function(env, "captureStats", NAPI_AUTO_LENGTH, capture_stats, NULL,
                               &function) != napi_ok ||
      api_napi_set_named_property(env, exports, "captureStats", function) != napi_ok ||
      api_napi_create_function(env, "setCaptureRate", NAPI_AUTO_LENGTH, set_capture_rate, NULL,
                               &function) != napi_ok ||
      api_napi_set_named_property(env, exports, "setCaptureRate", function) != napi_ok)
    return NULL;
  return exports;
}
