// Reviewed PipeWire C ABI hooks. Producer negotiation is independent of the
// downstream frame gate, and unknown/fixed-rate producers retain native caps.
#define _GNU_SOURCE
#include <dlfcn.h>
#include <limits.h>
#include <link.h>
#include <node_api.h>
#include <pipewire/context.h>
#include <pipewire/core.h>
#include <pipewire/loop.h>
#include <pipewire/stream.h>
#include <pthread.h>
#include <spa/param/video/format-utils.h>
#include <spa/pod/iter.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#define UPSTREAM_PARAMS 32
#define UPSTREAM_POD_BYTES 8192
#define UPSTREAM_STREAMS 16
typedef int (*connect_fn)(struct pw_stream *, enum pw_direction, uint32_t, enum pw_stream_flags,
                          const struct spa_pod **, uint32_t);
typedef int (*update_fn)(struct pw_stream *, const struct spa_pod **, uint32_t);
typedef void (*listen_fn)(struct pw_stream *, struct spa_hook *, const struct pw_stream_events *,
                          void *);
static connect_fn original_connect;
static update_fn original_update;
static listen_fn add_listener;
static __typeof__(pw_stream_get_core) *stream_core;
static __typeof__(pw_core_get_context) *core_context;
static __typeof__(pw_context_get_main_loop) *context_loop;
static __typeof__(pw_stream_get_state) *stream_state;
static uintptr_t installed_slot, connect_return, update_return;
static bool runtime_mode;
static atomic_uint_fast64_t requested;
static pthread_mutex_t registry_mutex = PTHREAD_MUTEX_INITIALIZER;
struct caps {
  const struct spa_pod *pods[UPSTREAM_PARAMS];
  unsigned count;
};
struct upstream {
  struct upstream *next;
  struct pw_stream *stream;
  struct spa_hook listener;
  pthread_t owner;
  atomic_uint refs;
  bool destroyed, eligible, busy, pending, disabled, restore_needed;
  unsigned applied, pending_fps, preferred;
  struct spa_fraction original_maximum;
  uint64_t failed_generation, revision, pending_generation, deadline;
  struct pw_loop *loop;
  struct spa_source *timer;
  struct caps original;
};
static struct upstream *streams;
static struct {
  atomic_uint_fast64_t active, observed, supported, changes, restored, failures, bypassed;
  atomic_uint negotiated_num, negotiated_denom, applied_fps, pending_fps;
  atomic_uint width, height;
  atomic_uint_fast64_t timeouts, async_errors;
  atomic_uint_fast64_t initial_preferences, late_rate_changes, initial_without_rate;
  atomic_uint preferred_fps;
} counts;

static bool raw_enum(const struct spa_pod *pod) {
  uint32_t type, subtype;
  if (!pod || pod->size > UPSTREAM_POD_BYTES || pod->size < sizeof(struct spa_pod_object_body) ||
      !spa_pod_is_object(pod) || SPA_POD_SIZE(pod) % 8 ||
      SPA_POD_OBJECT_TYPE(pod) != SPA_TYPE_OBJECT_Format ||
      SPA_POD_OBJECT_ID(pod) != SPA_PARAM_EnumFormat)
    return false;
  return spa_format_parse(pod, &type, &subtype) >= 0 && type == SPA_MEDIA_TYPE_video &&
         subtype == SPA_MEDIA_SUBTYPE_raw;
}

static void clear_caps(struct caps *caps) {
  for (unsigned i = 0; i < caps->count; i++)
    free((void *)caps->pods[i]);
  memset(caps, 0, sizeof(*caps));
}

static bool copy_caps(struct caps *out, const struct spa_pod **params, uint32_t count) {
  memset(out, 0, sizeof(*out));
  if (!params || !count || count > UPSTREAM_PARAMS)
    return false;
  for (uint32_t i = 0; i < count; i++) {
    if (!raw_enum(params[i]))
      continue;
    void *copy = malloc(SPA_POD_SIZE(params[i]));
    if (!copy) {
      clear_caps(out);
      return false;
    }
    memcpy(copy, params[i], SPA_POD_SIZE(params[i]));
    out->pods[out->count++] = copy;
  }
  return out->count != 0;
}

// Append a standards-defined property to a copy. No native Rust layout or
// producer-owned format bytes are changed. Reviewed native offers omit this
// property; an unexpected existing maximum is preserved by declining the edit.
static struct spa_pod *capped_pod(const struct spa_pod *pod, unsigned fps) {
  if (!raw_enum(pod) || !fps || fps > 240 ||
      spa_pod_find_prop(pod, NULL, SPA_FORMAT_VIDEO_maxFramerate))
    return NULL;
  struct {
    uint32_t key, flags;
    struct spa_pod_choice choice;
    struct spa_fraction values[3];
  } property = {
      .key = SPA_FORMAT_VIDEO_maxFramerate,
      .choice = {.pod = {.size =
                             sizeof(struct spa_pod_choice_body) + 3 * sizeof(struct spa_fraction),
                         .type = SPA_TYPE_Choice},
                 .body = {.type = SPA_CHOICE_Range,
                          .child = {.size = sizeof(struct spa_fraction),
                                    .type = SPA_TYPE_Fraction}}},
      .values = {{fps, 1}, {1, 1}, {fps, 1}},
  };
  _Static_assert(sizeof(property) == 56, "SPA fraction range property");
  size_t size = SPA_POD_SIZE(pod);
  struct spa_pod *copy = malloc(size + sizeof(property));
  if (!copy)
    return NULL;
  memcpy(copy, pod, size);
  memcpy((uint8_t *)copy + size, &property, sizeof(property));
  copy->size += sizeof(property);
  return copy;
}

// A preference never excludes a previously acceptable producer maximum.
// DROP removes the property when a producer does not advertise it. In
// particular, fixed-rate offers and existing maximums stay byte-for-byte
// unchanged. An existing framerate choice must include variable rate (0/1).
static struct spa_pod *preferred_pod(const struct spa_pod *pod, unsigned fps) {
  if (!raw_enum(pod))
    return NULL;
  const struct spa_pod_prop *rate = spa_pod_find_prop(pod, NULL, SPA_FORMAT_VIDEO_framerate);
  if (!rate)
    return NULL;
  uint32_t count, choice;
  const struct spa_pod *child = spa_pod_get_values(&rate->value, &count, &choice);
  if (child->type != SPA_TYPE_Fraction || child->size != sizeof(struct spa_fraction) || !count)
    return NULL;
  const struct spa_fraction *values = SPA_POD_BODY(child);
  bool variable = false;
  if (choice == SPA_CHOICE_None)
    variable = values[0].num == 0 && values[0].denom > 0;
  else if (choice == SPA_CHOICE_Range && count == 3)
    variable = values[1].num == 0 && values[1].denom > 0 && values[2].denom > 0;
  else if (choice == SPA_CHOICE_Enum)
    for (uint32_t i = 1; i < count; i++)
      variable |= values[i].num == 0 && values[i].denom > 0;
  if (!variable)
    return NULL;
  struct spa_pod *copy = capped_pod(pod, fps);
  if (!copy)
    return NULL;
  struct spa_pod_prop *prop = (void *)spa_pod_find_prop(copy, NULL, SPA_FORMAT_VIDEO_maxFramerate);
  struct spa_fraction *range = SPA_POD_CHOICE_VALUES(&prop->value);
  prop->flags = SPA_POD_PROP_FLAG_DROP;
  range[1] = SPA_FRACTION(0, 1);
  range[2] = SPA_FRACTION(UINT32_MAX, 1);
  return copy;
}

// Holds copies only for changed pods; the borrowed originals remain valid for
// the synchronous PipeWire call. Never omit an unrelated native parameter.
static bool rewrite(const struct spa_pod **params, uint32_t count, unsigned fps,
                    struct caps *copies, const struct spa_pod **output, bool preference) {
  memset(copies, 0, sizeof(*copies));
  if (!params || !count || count > UPSTREAM_PARAMS)
    return false;
  for (uint32_t i = 0; i < count; i++) {
    output[i] = params[i];
    if (!raw_enum(params[i]))
      continue;
    struct spa_pod *copy = preference ? preferred_pod(params[i], fps) : capped_pod(params[i], fps);
    if (!copy) {
      if (preference)
        continue;
      clear_caps(copies);
      return false;
    }
    copies->pods[copies->count++] = copy;
    output[i] = copy;
  }
  return copies->count != 0;
}

static void release(struct upstream *state) {
  if (atomic_fetch_sub(&state->refs, 1) == 1) {
    clear_caps(&state->original);
    free(state);
  }
}

static struct upstream *find_stream(struct pw_stream *stream) {
  pthread_mutex_lock(&registry_mutex);
  struct upstream *state = streams;
  while (state && state->stream != stream)
    state = state->next;
  if (state)
    atomic_fetch_add(&state->refs, 1);
  pthread_mutex_unlock(&registry_mutex);
  return state;
}

static void destroyed(void *data) {
  struct upstream *state = data;
  pthread_mutex_lock(&registry_mutex);
  struct upstream **entry = &streams;
  while (*entry && *entry != state)
    entry = &(*entry)->next;
  if (!*entry) {
    pthread_mutex_unlock(&registry_mutex);
    return;
  }
  *entry = state->next;
  state->destroyed = true;
  atomic_fetch_sub(&counts.active, 1);
  atomic_store(&counts.applied_fps, 0);
  atomic_store(&counts.preferred_fps, 0);
  atomic_store(&counts.width, 0);
  atomic_store(&counts.height, 0);
  atomic_store(&counts.negotiated_num, 0);
  atomic_store(&counts.negotiated_denom, 0);
  pthread_mutex_unlock(&registry_mutex);
  if (state->timer) {
    pw_loop_destroy_source(state->loop, state->timer);
    state->timer = NULL;
  }
  atomic_store(&counts.pending_fps, 0);
  spa_hook_remove(&state->listener);
  release(state);
}

static uint64_t monotonic_ns(void) {
  struct timespec now;
  clock_gettime(CLOCK_MONOTONIC, &now);
  return (uint64_t)now.tv_sec * 1000000000 + now.tv_nsec;
}

static void cancel_pending(struct upstream *state) {
  state->pending = false;
  atomic_store(&counts.pending_fps, 0);
  state->applied = 0;
  atomic_store(&counts.applied_fps, 0);
}

static void negotiated(void *data, uint32_t id, const struct spa_pod *pod) {
  struct upstream *state = data;
  if (id != SPA_PARAM_Format || !pod || !pthread_equal(state->owner, pthread_self()))
    return;
  struct spa_video_info_raw raw = {0};
  bool parsed = spa_format_video_raw_parse(pod, &raw) >= 0;
  atomic_store(&counts.width, parsed ? raw.size.width : 0);
  atomic_store(&counts.height, parsed ? raw.size.height : 0);
  bool eligible = parsed && raw.framerate.num == 0 && raw.max_framerate.num > 0 &&
                  raw.max_framerate.denom > 0 &&
                  spa_pod_find_prop(pod, NULL, SPA_FORMAT_VIDEO_maxFramerate);
  state->eligible = eligible;
  atomic_fetch_add(&counts.observed, 1);
  if (eligible) {
    if (!state->original_maximum.num)
      state->original_maximum = raw.max_framerate;
    atomic_fetch_add(&counts.supported, 1);
    atomic_store(&counts.negotiated_num, raw.max_framerate.num);
    atomic_store(&counts.negotiated_denom, raw.max_framerate.denom);
  } else {
    atomic_store(&counts.negotiated_num, 0);
    atomic_store(&counts.negotiated_denom, 0);
  }
  if (!runtime_mode) {
    state->applied =
        eligible && state->preferred &&
                raw.max_framerate.num == (uint64_t)state->preferred * raw.max_framerate.denom
            ? state->preferred
            : 0;
    atomic_store(&counts.applied_fps, state->applied);
    return;
  }
  // update_params only publishes an offer. Count a rate as applied only after
  // the producer has selected a matching Format, including asynchronous replies.
  // Restoring requires the original observed maximum: a late capped reply must
  // not masquerade as acknowledgment of the subsequently restored offers.
  if (state->pending && eligible &&
      (state->pending_fps
           ? raw.max_framerate.num <= (uint64_t)state->pending_fps * raw.max_framerate.denom
           : state->original_maximum.num &&
                 (uint64_t)raw.max_framerate.num * state->original_maximum.denom ==
                     (uint64_t)state->original_maximum.num * raw.max_framerate.denom)) {
    state->applied = state->pending_fps;
    state->pending = false;
    atomic_store(&counts.pending_fps, 0);
    atomic_store(&counts.applied_fps, state->applied);
    atomic_fetch_add(state->applied ? &counts.changes : &counts.restored, 1);
  }
}

static void state_changed(void *data, enum pw_stream_state old, enum pw_stream_state state,
                          const char *error) {
  (void)old;
  (void)error;
  struct upstream *tracked = data;
  if (state != PW_STREAM_STATE_ERROR || !pthread_equal(tracked->owner, pthread_self()))
    return;
  tracked->disabled = true;
  tracked->failed_generation = atomic_load(&requested);
  tracked->restore_needed = tracked->pending || tracked->applied;
  cancel_pending(tracked);
  atomic_store(&counts.width, 0);
  atomic_store(&counts.height, 0);
  atomic_store(&counts.negotiated_num, 0);
  atomic_store(&counts.negotiated_denom, 0);
  atomic_fetch_add(&counts.async_errors, 1);
  // Do not reconnect or claim ERROR recovery. The reviewed native listener
  // also receives ERROR and sends a CaptureSourceMessage to its owner.
}

static void publish(struct upstream *state, unsigned fps, uint64_t generation) {
  struct caps copies = {0}, originals = {0};
  const struct spa_pod *params[UPSTREAM_PARAMS];
  if (!copy_caps(&originals, state->original.pods, state->original.count) ||
      (fps && !rewrite(originals.pods, originals.count, fps, &copies, params, false))) {
    state->failed_generation = generation;
    atomic_fetch_add(&counts.bypassed, 1);
    clear_caps(&originals);
    return;
  }
  uint64_t revision = state->revision;
  state->busy = true;
  state->pending = true;
  state->pending_fps = fps;
  state->pending_generation = generation;
  state->deadline = monotonic_ns() + 2000000000;
  state->applied = 0;
  atomic_store(&counts.applied_fps, 0);
  atomic_store(&counts.pending_fps, fps);
  int result = original_update(state->stream, fps ? params : originals.pods, originals.count);
  clear_caps(&copies);
  clear_caps(&originals);
  if (!state->destroyed && state->revision == revision && result < 0) {
    cancel_pending(state);
    atomic_fetch_add(&counts.failures, 1);
    state->failed_generation = generation;
    state->restore_needed = fps != 0;
  }
  state->busy = false;
}

// A main-loop timer, independent of process callbacks, detects missing Format
// acknowledgments. Restoration is attempted once and is only counted on ack.
// A true ERROR stream cannot be reset by update_params; this experimental path
// deliberately leaves reconnect/restart to the native owner.
static void apply_pending(void *data) {
  struct upstream *state = data;
  if (!runtime_mode || state->destroyed || state->busy ||
      !pthread_equal(state->owner, pthread_self()))
    return;
  atomic_fetch_add(&state->refs, 1);
  uint64_t generation = atomic_load(&requested);
  if (state->pending && monotonic_ns() >= state->deadline) {
    bool was_cap = state->pending_fps != 0;
    state->failed_generation = state->pending_generation;
    state->disabled = true;
    cancel_pending(state);
    state->restore_needed = was_cap;
    atomic_fetch_add(&counts.timeouts, 1);
  }
  if (state->restore_needed) {
    state->restore_needed = false;
    if (!stream_state || stream_state(state->stream, NULL) != PW_STREAM_STATE_ERROR)
      publish(state, 0, generation);
  } else if (!state->pending) {
    unsigned fps = state->eligible && !state->disabled ? generation & 255 : 0;
    if (state->applied != fps && state->failed_generation != generation)
      publish(state, fps, generation);
  }
  release(state);
}

static void tick(void *data, uint64_t expirations) {
  (void)expirations;
  apply_pending(data);
}

static const struct pw_stream_events events = {
    .version = PW_VERSION_STREAM_EVENTS,
    .destroy = destroyed,
    .state_changed = state_changed,
    .param_changed = negotiated,
};

static int track_connect(struct pw_stream *stream, enum pw_direction direction, uint32_t target,
                         enum pw_stream_flags flags, const struct spa_pod **params,
                         uint32_t count) {
  struct caps originals;
  // The reviewed capture threads connect with AUTOCONNECT only. In particular,
  // a realtime process callback must never perform allocation/renegotiation.
  if (direction != PW_DIRECTION_INPUT || flags != PW_STREAM_FLAG_AUTOCONNECT ||
      !copy_caps(&originals, params, count))
    return original_connect(stream, direction, target, flags, params, count);
  struct upstream *state = calloc(1, sizeof(*state));
  if (!state) {
    clear_caps(&originals);
    return original_connect(stream, direction, target, flags, params, count);
  }
  state->stream = stream;
  state->owner = pthread_self();
  state->original = originals;
  state->failed_generation = UINT64_MAX;
  atomic_init(&state->refs, 2); // listener ownership plus this connect call
  pthread_mutex_lock(&registry_mutex);
  if (atomic_load(&counts.active) >= UPSTREAM_STREAMS) {
    pthread_mutex_unlock(&registry_mutex);
    atomic_store(&state->refs, 1);
    release(state);
    return original_connect(stream, direction, target, flags, params, count);
  }
  state->next = streams;
  streams = state;
  atomic_fetch_add(&counts.active, 1);
  atomic_store(&counts.preferred_fps, 0);
  atomic_store(&counts.applied_fps, 0);
  atomic_store(&counts.width, 0);
  atomic_store(&counts.height, 0);
  atomic_store(&counts.negotiated_num, 0);
  atomic_store(&counts.negotiated_denom, 0);
  pthread_mutex_unlock(&registry_mutex);
  struct pw_core *core = runtime_mode ? stream_core(stream) : NULL;
  struct pw_context *context = core ? core_context(core) : NULL;
  state->loop = context ? context_loop(context) : NULL;
  if (state->loop) {
    state->timer = pw_loop_add_timer(state->loop, tick, state);
    struct timespec interval = {.tv_nsec = 100000000};
    if (state->timer &&
        pw_loop_update_timer(state->loop, state->timer, &interval, &interval, false) < 0) {
      pw_loop_destroy_source(state->loop, state->timer);
      state->timer = NULL;
    }
  }
  if (runtime_mode && !state->timer) {
    state->disabled = true;
    atomic_fetch_add(&counts.bypassed, 1);
  }
  struct caps copies = {0};
  const struct spa_pod *preferred[UPSTREAM_PARAMS];
  unsigned fps = atomic_load(&requested) & 255;
  bool changed = !runtime_mode && fps && rewrite(params, count, fps, &copies, preferred, true);
  if (changed) {
    state->preferred = fps;
    atomic_store(&counts.preferred_fps, fps);
    atomic_fetch_add(&counts.initial_preferences, 1);
  } else if (!runtime_mode && !fps) {
    atomic_fetch_add(&counts.initial_without_rate, 1);
  }
  add_listener(stream, &state->listener, &events, state);
  // The default only changes the initial preference. Experimental runtime mode
  // first observes untouched offers before attempting any stricter cap.
  int result =
      original_connect(stream, direction, target, flags, changed ? preferred : params, count);
  clear_caps(&copies);
  if (result < 0 && !state->destroyed)
    destroyed(state);
  release(state);
  return result;
}

static int upstream_connect(struct pw_stream *stream, enum pw_direction direction, uint32_t target,
                            enum pw_stream_flags flags, const struct spa_pod **params,
                            uint32_t count) {
  if ((uintptr_t)__builtin_return_address(0) != connect_return)
    return original_connect(stream, direction, target, flags, params, count);
  return track_connect(stream, direction, target, flags, params, count);
}

static int track_update(struct upstream *state, const struct spa_pod **params, uint32_t count) {
  struct caps originals = {0};
  bool offers = copy_caps(&originals, params, count), was_busy = state->busy;
  uint64_t revision = state->revision;
  state->busy = true;
  // Native format/modifier updates pass through intact. The timer reapplies a
  // cap to the latest successfully published native offers on its next tick.
  int result = original_update(state->stream, params, count);
  if (!state->destroyed && offers && result >= 0 && state->revision == revision) {
    clear_caps(&state->original);
    state->original = originals;
    memset(&originals, 0, sizeof(originals));
    state->revision++;
    cancel_pending(state);
  }
  state->busy = was_busy;
  clear_caps(&originals);
  return result;
}

static int upstream_update(struct pw_stream *stream, const struct spa_pod **params,
                           uint32_t count) {
  if (!runtime_mode || (uintptr_t)__builtin_return_address(0) != update_return)
    return original_update(stream, params, count);
  struct upstream *state = find_stream(stream);
  if (!state)
    return original_update(stream, params, count);
  int result = pthread_equal(state->owner, pthread_self()) ? track_update(state, params, count)
                                                           : original_update(stream, params, count);
  release(state);
  return result;
}

#define API(name) static __typeof__(name) *api_##name
API(napi_get_cb_info);
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

static bool valid_linkage(uintptr_t base, uintptr_t slot, uintptr_t target, uintptr_t call) {
  if (!(address_protection(base, target, 14) & PROT_EXEC) ||
      !(address_protection(base, call, 6) & PROT_EXEC))
    return false;
  const uint8_t *code = (void *)call, *stub = (void *)target;
  int32_t displacement, pointer;
  memcpy(&displacement, code + 2, 4);
  memcpy(&pointer, stub + 7, 4);
  const uint8_t prefix[] = {0x55, 0x48, 0x89, 0xe5, 0x48, 0x8b, 0x05};
  const uint8_t suffix[] = {0x5d, 0xff, 0xe0};
  return code[0] == 0xff && code[1] == 0x15 && call + 6 + displacement == slot &&
         !memcmp(stub, prefix, sizeof(prefix)) && !memcmp(stub + 11, suffix, sizeof(suffix)) &&
         (address_protection(base, target + 11 + pointer, sizeof(uintptr_t)) & PROT_WRITE);
}

static napi_value install(napi_env env, napi_callback_info info) {
  napi_value args[8];
  size_t count = 8, length;
  char filename[PATH_MAX];
  uint32_t offsets[6], mode;
  if (api_napi_get_cb_info(env, info, &count, args, NULL, NULL) != napi_ok || count != 8 ||
      api_napi_get_value_string_utf8(env, args[0], filename, sizeof(filename), &length) !=
          napi_ok ||
      !length || length >= sizeof(filename) - 1)
    return fail(env, "Invalid upstream capture arguments.");
  if (api_napi_get_value_uint32(env, args[7], &mode) != napi_ok || mode > 1)
    return fail(env, "Invalid upstream capture mode.");
  for (int i = 0; i < 6; i++)
    if (api_napi_get_value_uint32(env, args[i + 1], &offsets[i]) != napi_ok)
      return fail(env, "Invalid upstream capture offset.");
  void *handle = dlopen(filename, RTLD_NOW | RTLD_NOLOAD);
  struct link_map *map;
  if (!handle || dlinfo(handle, RTLD_DI_LINKMAP, &map)) {
    if (handle)
      dlclose(handle);
    return fail(env, "The native capture module is not loaded.");
  }
  uintptr_t base = map->l_addr, slots[2], targets[2], calls[2], pages[2];
  int protections[2];
  size_t page_size = (size_t)sysconf(_SC_PAGESIZE);
  if (installed_slot) {
    dlclose(handle);
    return installed_slot == base + offsets[0] && runtime_mode == (mode == 1)
               ? undefined(env)
               : fail(env, "Upstream capture module changed.");
  }
  for (int i = 0; i < 2; i++) {
    slots[i] = base + offsets[i * 3];
    targets[i] = base + offsets[i * 3 + 1];
    calls[i] = base + offsets[i * 3 + 2];
    protections[i] = address_protection(base, slots[i], sizeof(uintptr_t));
    if (slots[i] % sizeof(uintptr_t) || !(protections[i] & PROT_READ) ||
        protections[i] & PROT_EXEC || !valid_linkage(base, slots[i], targets[i], calls[i]) ||
        *(uintptr_t *)slots[i] != targets[i]) {
      dlclose(handle);
      return fail(env, "Upstream capture linkage differs from reviewed native code.");
    }
    pages[i] = slots[i] & ~(page_size - 1);
  }
  void *pipewire = dlopen("libpipewire-0.3.so.0", RTLD_NOW | RTLD_LOCAL);
  if (pipewire) {
    add_listener = dlsym(pipewire, "pw_stream_add_listener");
    stream_core = dlsym(pipewire, "pw_stream_get_core");
    core_context = dlsym(pipewire, "pw_core_get_context");
    context_loop = dlsym(pipewire, "pw_context_get_main_loop");
    stream_state = dlsym(pipewire, "pw_stream_get_state");
  }
  if (!add_listener ||
      (mode == 1 && (!stream_core || !core_context || !context_loop || !stream_state))) {
    if (pipewire)
      dlclose(pipewire);
    dlclose(handle);
    return fail(env, "PipeWire stream listeners are unavailable.");
  }
  for (int i = 0; i < 2; i++) {
    if (mprotect((void *)pages[i], page_size, protections[i] | PROT_WRITE)) {
      for (int j = 0; j < i; j++)
        mprotect((void *)pages[j], page_size, protections[j]);
      dlclose(pipewire);
      dlclose(handle);
      return fail(env, "Could not prepare upstream capture linkage.");
    }
  }
  original_connect = (connect_fn)targets[0];
  original_update = (update_fn)targets[1];
  runtime_mode = mode == 1;
  connect_return = calls[0] + 6;
  update_return = calls[1] + 6;
  uintptr_t replacements[] = {(uintptr_t)upstream_connect, (uintptr_t)upstream_update};
  for (int i = 0; i < 2; i++)
    __atomic_store_n((uintptr_t *)slots[i], replacements[i], __ATOMIC_RELEASE);
  bool restored = true;
  for (int i = 0; i < 2; i++)
    restored &= mprotect((void *)pages[i], page_size, protections[i]) == 0;
  if (!restored) {
    bool rolled_back = true;
    for (int i = 0; i < 2; i++)
      if (!mprotect((void *)pages[i], page_size, protections[i] | PROT_WRITE)) {
        __atomic_store_n((uintptr_t *)slots[i], targets[i], __ATOMIC_RELEASE);
        mprotect((void *)pages[i], page_size, protections[i]);
      } else {
        rolled_back = false;
      }
    // A remaining hook retains these entry points. Never unload its library
    // references when rollback is uncertain, even though installation fails.
    if (rolled_back) {
      dlclose(pipewire);
      dlclose(handle);
    }
    return fail(env, "Could not restore upstream capture linkage protection.");
  }
  installed_slot = slots[0];
  // Keep the library reference while its listener entry point is retained.
  dlclose(handle);
  return undefined(env);
}

static napi_value set_rate(napi_env env, napi_callback_info info) {
  napi_value arg;
  size_t count = 1;
  double fps;
  if (api_napi_get_cb_info(env, info, &count, &arg, NULL, NULL) != napi_ok || count != 1 ||
      api_napi_get_value_double(env, arg, &fps) != napi_ok || !(fps >= 0 && fps <= 240) ||
      (unsigned)fps != fps)
    return fail(env, "Upstream capture rate must be an integer from 0 through 240.");
  uint64_t old = atomic_load(&requested);
  if ((old & 255) != (unsigned)fps) {
    atomic_store(&requested, ((old + 256) & ~255ull) | (unsigned)fps);
    if (!runtime_mode && atomic_load(&counts.active))
      atomic_fetch_add(&counts.late_rate_changes, 1);
  }
  return undefined(env);
}

static napi_value stats(napi_env env, napi_callback_info info) {
  (void)info;
  char json[1024];
#define GET(name) ((unsigned long long)atomic_load(&counts.name))
  snprintf(json, sizeof(json),
           "{\"installed\":%s,\"mode\":\"%s\",\"requestedFps\":%u,\"activeStreams\":%llu,"
           "\"observedFormats\":%llu,\"supportedFormats\":%llu,\"rateChanges\":%llu,"
           "\"restorations\":%llu,\"errors\":%llu,\"bypassed\":%llu,"
           "\"pendingFps\":%u,\"ackTimeouts\":%llu,\"asyncErrors\":%llu,"
           "\"appliedFps\":%u,\"negotiatedMaxFpsNumerator\":%u,"
           "\"negotiatedMaxFpsDenominator\":%u,\"preferredFps\":%u,"
           "\"initialPreferences\":%llu,\"initialWithoutRate\":%llu,\"lateRateChanges\":%llu,"
           "\"width\":%u,\"height\":%u}",
           installed_slot ? "true" : "false", runtime_mode ? "runtime" : "initial",
           (unsigned)(atomic_load(&requested) & 255), GET(active), GET(observed), GET(supported),
           GET(changes), GET(restored), GET(failures), GET(bypassed),
           atomic_load(&counts.pending_fps), GET(timeouts), GET(async_errors),
           atomic_load(&counts.applied_fps), atomic_load(&counts.negotiated_num),
           atomic_load(&counts.negotiated_denom), atomic_load(&counts.preferred_fps),
           GET(initial_preferences), GET(initial_without_rate), GET(late_rate_changes),
           atomic_load(&counts.active) == 1 ? atomic_load(&counts.width) : 0,
           atomic_load(&counts.active) == 1 ? atomic_load(&counts.height) : 0);
#undef GET
  napi_value result;
  api_napi_create_string_utf8(env, json, NAPI_AUTO_LENGTH, &result);
  return result;
}

napi_value dpe_capture_upstream_register(napi_env env, napi_value exports) {
#define LOAD(name)                                                                                 \
  api_##name = dlsym(RTLD_DEFAULT, #name);                                                         \
  if (!api_##name)                                                                                 \
  return NULL
  LOAD(napi_get_cb_info);
  LOAD(napi_get_value_string_utf8);
  LOAD(napi_get_value_uint32);
  LOAD(napi_get_value_double);
  LOAD(napi_get_undefined);
  LOAD(napi_create_string_utf8);
  LOAD(napi_create_function);
  LOAD(napi_set_named_property);
  LOAD(napi_throw_error);
#undef LOAD
  napi_value function;
  if (api_napi_create_function(env, "installCaptureUpstreamV2", NAPI_AUTO_LENGTH, install, NULL,
                               &function) != napi_ok ||
      api_napi_set_named_property(env, exports, "installCaptureUpstreamV2", function) != napi_ok ||
      api_napi_create_function(env, "setCaptureUpstreamRate", NAPI_AUTO_LENGTH, set_rate, NULL,
                               &function) != napi_ok ||
      api_napi_set_named_property(env, exports, "setCaptureUpstreamRate", function) != napi_ok ||
      api_napi_create_function(env, "captureUpstreamStats", NAPI_AUTO_LENGTH, stats, NULL,
                               &function) != napi_ok ||
      api_napi_set_named_property(env, exports, "captureUpstreamStats", function) != napi_ok)
    return NULL;
  return exports;
}
