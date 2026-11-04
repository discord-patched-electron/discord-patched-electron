// Preserve the native explicit-sync acquire wait across normal system uptime.
// Only one exact, pre-initialize x86-64 call site is redirected.
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <limits.h>
#include <link.h>
#include <node_api.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

// Reviewed Rust ABI: two i32 descriptors, u64 point, i64 timeout, and an
// opaque nullable anyhow error pointer in rax. Ownership stays with native.
typedef uintptr_t (*wait_fn)(int, int, uint64_t, int64_t);
static wait_fn original_wait;
static uintptr_t installed_site;
static struct {
  atomic_uint_fast64_t calls, corrected, errors, clock_failures;
} counts;
#ifndef DPE_SYNC_CLOCK
#define DPE_SYNC_CLOCK(value) clock_gettime(CLOCK_MONOTONIC, (value))
#endif

static uintptr_t wait_available(int drm_fd, int sync_fd, uint64_t point, int64_t budget) {
  atomic_fetch_add(&counts.calls, 1);
  // The scoped call passes five seconds as a duration. DRM requires an
  // absolute CLOCK_MONOTONIC deadline; zero/infinite/other calls stay native.
  if (budget == INT64_C(5000000000)) {
    struct timespec now;
    int saved_errno = errno;
    int result = DPE_SYNC_CLOCK(&now);
    errno = saved_errno;
    if (!result && now.tv_sec >= 0 && now.tv_nsec >= 0 && now.tv_nsec < 1000000000) {
      uint64_t limit = (uint64_t)INT64_MAX - (uint64_t)budget;
      uint64_t seconds = (uint64_t)now.tv_sec;
      if (seconds > limit / 1000000000ull ||
          (seconds == limit / 1000000000ull && (uint64_t)now.tv_nsec > limit % 1000000000ull))
        budget = INT64_MAX;
      else
        budget += (int64_t)(seconds * 1000000000ull + (uint64_t)now.tv_nsec);
      atomic_fetch_add(&counts.corrected, 1);
    } else {
      atomic_fetch_add(&counts.clock_failures, 1);
    }
  }
  uintptr_t result = original_wait(drm_fd, sync_fd, point, budget);
  if (result)
    atomic_fetch_add(&counts.errors, 1);
  return result;
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
  napi_value value;
  api_napi_get_undefined(env, &value);
  return value;
}
static bool executable(uintptr_t base, uintptr_t address, size_t size) {
  const ElfW(Ehdr) *header = (void *)base;
  const ElfW(Phdr) *segments = (void *)(base + header->e_phoff);
  for (int i = 0; i < header->e_phnum; i++) {
    const ElfW(Phdr) *p = &segments[i];
    if (p->p_type == PT_LOAD && (p->p_flags & (PF_R | PF_X)) == (PF_R | PF_X) &&
        !(p->p_flags & PF_W) && address >= base + p->p_vaddr && size <= p->p_memsz &&
        address - base - p->p_vaddr <= p->p_memsz - size)
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

static napi_value install(napi_env env, napi_callback_info info) {
  napi_value args[3];
  size_t count = 3, length;
  char filename[PATH_MAX];
  uint32_t call_offset, target_offset;
  if (api_napi_get_cb_info(env, info, &count, args, NULL, NULL) != napi_ok || count != 3 ||
      api_napi_get_value_string_utf8(env, args[0], filename, sizeof(filename), &length) !=
          napi_ok ||
      !length || length >= sizeof(filename) - 1 ||
      api_napi_get_value_uint32(env, args[1], &call_offset) != napi_ok ||
      api_napi_get_value_uint32(env, args[2], &target_offset) != napi_ok)
    return fail(env, "Invalid Capture synchronization arguments.");
  void *handle = dlopen(filename, RTLD_NOW | RTLD_NOLOAD);
  struct link_map *map;
  if (!handle || dlinfo(handle, RTLD_DI_LINKMAP, &map)) {
    if (handle)
      dlclose(handle);
    return fail(env, "The native capture module is not loaded.");
  }
  uintptr_t base = map->l_addr, site = base + call_offset, target = base + target_offset;
  if (installed_site) {
    dlclose(handle);
    return installed_site == site ? undefined(env)
                                  : fail(env, "Capture synchronization module changed.");
  }
  // Exact reviewed preparation of the four scalar arguments. No C++ object
  // layout or callee-saved register injection is needed at this call site.
  static const uint8_t context[] =
      "\x48\xb9\x00\xf2\x05\x2a\x01\x00\x00\x00\x89\xef\x44\x89\xf6\x4c\x89\xfa";
  uint8_t before[5];
  if (!executable(base, site - sizeof(context) + 1, sizeof(context) + 4) ||
      !executable(base, target, 1) ||
      memcmp((void *)(site - sizeof(context) + 1), context, sizeof(context) - 1)) {
    dlclose(handle);
    return fail(env, "Capture synchronization register context differs from review.");
  }
  memcpy(before, (void *)site, sizeof(before));
  int32_t displacement;
  memcpy(&displacement, before + 1, 4);
  if (before[0] != 0xe8 || site + 5 + displacement != target) {
    dlclose(handle);
    return fail(env, "Capture synchronization call differs from review.");
  }
  size_t page_size = (size_t)sysconf(_SC_PAGESIZE);
  uint8_t *trampoline = near_page(site, page_size);
  if (trampoline == MAP_FAILED) {
    dlclose(handle);
    return fail(env, "Could not reserve Capture synchronization code.");
  }
  // Tail-call the C wrapper with all original arguments and return registers.
  const uint8_t dispatch[] = {0xff, 0x25, 0, 0, 0, 0};
  uintptr_t replacement = (uintptr_t)wait_available;
  memcpy(trampoline, dispatch, sizeof(dispatch));
  memcpy(trampoline + sizeof(dispatch), &replacement, sizeof(replacement));
  uintptr_t first = site & ~(page_size - 1), last = (site + 4) & ~(page_size - 1);
  size_t span = last - first + page_size;
  if (mprotect(trampoline, page_size, PROT_READ | PROT_EXEC) ||
      mprotect((void *)first, span, PROT_READ | PROT_WRITE | PROT_EXEC)) {
    munmap(trampoline, page_size);
    dlclose(handle);
    return fail(env, "Could not install Capture synchronization adapter.");
  }
  original_wait = (wait_fn)target;
  uint8_t patch[5] = {0xe8};
  displacement = (intptr_t)trampoline - (intptr_t)(site + 5);
  memcpy(patch + 1, &displacement, 4);
  memcpy((void *)site, patch, sizeof(patch));
  if (mprotect((void *)first, span, PROT_READ | PROT_EXEC)) {
    memcpy((void *)site, before, sizeof(before));
    mprotect((void *)first, span, PROT_READ | PROT_EXEC);
    munmap(trampoline, page_size);
    dlclose(handle);
    return fail(env, "Could not restore Capture synchronization code protection.");
  }
  installed_site = site;
  // Native target and executable trampoline are retained for process lifetime.
  return undefined(env);
}

static napi_value stats(napi_env env, napi_callback_info info) {
  (void)info;
  char json[512];
  snprintf(json, sizeof(json),
           "{\"installed\":%s,\"calls\":%llu,\"correctedWaits\":%llu,"
           "\"waitErrors\":%llu,\"clockFailures\":%llu}",
           installed_site ? "true" : "false", (unsigned long long)atomic_load(&counts.calls),
           (unsigned long long)atomic_load(&counts.corrected),
           (unsigned long long)atomic_load(&counts.errors),
           (unsigned long long)atomic_load(&counts.clock_failures));
  napi_value value;
  api_napi_create_string_utf8(env, json, NAPI_AUTO_LENGTH, &value);
  return value;
}

napi_value dpe_capture_sync_register(napi_env env, napi_value exports) {
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
#undef LOAD
  napi_value function;
  if (api_napi_create_function(env, "installCaptureSync", NAPI_AUTO_LENGTH, install, NULL,
                               &function) != napi_ok ||
      api_napi_set_named_property(env, exports, "installCaptureSync", function) != napi_ok ||
      api_napi_create_function(env, "captureSyncStats", NAPI_AUTO_LENGTH, stats, NULL, &function) !=
          napi_ok ||
      api_napi_set_named_property(env, exports, "captureSyncStats", function) != napi_ok)
    return NULL;
  return exports;
}
