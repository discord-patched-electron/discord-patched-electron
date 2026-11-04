// Keep WebRTC's down-adaptation on the existing native GPU capture scaler.
// Only one exact, pre-initialize x86-64 call site is redirected.
#define _GNU_SOURCE
#include <dlfcn.h>
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
#include <unistd.h>

struct resolution {
  size_t width, height;
};
// Reviewed libc++ VideoSinkWants prefix. Do not copy or destroy its C++ tail.
struct sink_wants {
  uint8_t rotation, black, padding[2];
  int32_t maximum, target;
  uint8_t has_target, target_padding[3];
  int32_t fps;
};
_Static_assert(offsetof(struct sink_wants, maximum) == 4, "reviewed maximum pixel offset");
_Static_assert(offsetof(struct sink_wants, target) == 8, "reviewed target pixel offset");
_Static_assert(offsetof(struct sink_wants, has_target) == 12, "reviewed optional tag offset");
typedef struct resolution (*scale_fn)(size_t, size_t, uint64_t);
static scale_fn original_scale;
static uintptr_t installed_site;
static struct {
  atomic_uint_fast64_t calls, limited, invalid;
  atomic_uint max_pixels, width, height;
} counts;

static struct resolution scaled(size_t width, size_t height, const struct sink_wants *wants,
                                uint64_t native_target) {
  atomic_fetch_add(&counts.calls, 1);
  if (!wants || wants->rotation > 1 || wants->black > 1 || wants->has_target > 1 ||
      wants->maximum < 4 || (wants->has_target && wants->target < 4)) {
    atomic_fetch_add(&counts.invalid, 1);
    return original_scale(width, height, native_target);
  }
  if (wants->maximum == INT_MAX)
    return original_scale(width, height, native_target);
  unsigned limit = (unsigned)wants->maximum;
  if (wants->has_target && (unsigned)wants->target < limit)
    limit = wants->target;
  uint64_t bounded = (UINT64_C(1) << 32) | limit;
  // The selected optional target can already equal the native value. Still
  // enforce even geometry for a reduced result: later ceil2 can otherwise
  // exceed the maximum by one pixel (e.g. target=max=921599).
  struct resolution result = original_scale(width, height, bounded);
  if (!result.width || !result.height || result.width > UINT32_MAX || result.height > UINT32_MAX)
    return original_scale(width, height, native_target);
  // Native NV12 rounds visible dimensions up to even numbers. A newly adapted
  // odd rectangle must instead fit inside the requested pixel limit.
  bool smaller = (!width && !height) || result.width < width || result.height < height;
  if (smaller) {
    result.width &= ~(size_t)1;
    result.height &= ~(size_t)1;
    if (!result.width || !result.height || result.width * result.height > limit)
      return original_scale(width, height, native_target);
  }
  atomic_fetch_add(&counts.limited, 1);
  atomic_store(&counts.max_pixels, limit);
  atomic_store(&counts.width, result.width);
  atomic_store(&counts.height, result.height);
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
    return fail(env, "Invalid GPU capture scaling arguments.");
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
    return installed_site == site ? undefined(env) : fail(env, "GPU capture module changed.");
  }
  // Proves r15 points at the local, constructed VideoSinkWants and stays live
  // through the call. Includes its construction and both native size branches.
  static const uint8_t context[] =
      "\x4c\x8d\x7c\x24\x28\x4c\x89\xff\x4c\x89\xf6\xe8\x10\x9d\xff\xff"
      "\x8b\xbb\xa0\x00\x00\x00\x8b\xb3\xa4\x00\x00\x00\x48\x89\xf8\x48"
      "\x09\xf0\x74\x34\x48\x8b\x54\x24\x30\xeb\x2f\x8b\xbb\xa0\x00\x00"
      "\x00\x8b\xb3\xa4\x00\x00\x00\x48\xba\x00\x00\x90\x00\x01\x00\x00"
      "\x00\xe8\x7a\x9f\xff\xff\x49\x89\xc4\x49\x89\xd6\x44\x89\x64\x24"
      "\x08\x44\x89\x74\x24\x0c\xeb\x32\x31\xd2";
  uint8_t before[5];
  if (!executable(base, site - sizeof(context) + 1, sizeof(context) + 4) ||
      !executable(base, target, 1) ||
      memcmp((void *)(site - sizeof(context) + 1), context, sizeof(context) - 1)) {
    dlclose(handle);
    return fail(env, "GPU capture scaling register context differs from review.");
  }
  memcpy(before, (void *)site, sizeof(before));
  int32_t displacement;
  memcpy(&displacement, before + 1, 4);
  if (before[0] != 0xe8 || site + 5 + displacement != target) {
    dlclose(handle);
    return fail(env, "GPU capture scaling call differs from review.");
  }
  size_t page_size = (size_t)sysconf(_SC_PAGESIZE);
  uint8_t *trampoline = near_page(site, page_size);
  if (trampoline == MAP_FAILED) {
    dlclose(handle);
    return fail(env, "Could not reserve GPU capture scaling code.");
  }
  // SysV: preserve native optional target in rcx, pass reviewed r15 wants in
  // rdx, and tail-call C. The two-size_t result remains in rax/rdx unchanged.
  const uint8_t dispatch[] = {0x48, 0x89, 0xd1, 0x4c, 0x89, 0xfa, 0xff, 0x25, 0, 0, 0, 0};
  uintptr_t replacement = (uintptr_t)scaled;
  memcpy(trampoline, dispatch, sizeof(dispatch));
  memcpy(trampoline + sizeof(dispatch), &replacement, sizeof(replacement));
  uintptr_t first = site & ~(page_size - 1), last = (site + 4) & ~(page_size - 1);
  size_t span = last - first + page_size;
  if (mprotect(trampoline, page_size, PROT_READ | PROT_EXEC) ||
      mprotect((void *)first, span, PROT_READ | PROT_WRITE | PROT_EXEC)) {
    munmap(trampoline, page_size);
    dlclose(handle);
    return fail(env, "Could not install GPU capture scaling adapter.");
  }
  original_scale = (scale_fn)target;
  uint8_t patch[5] = {0xe8};
  displacement = (intptr_t)trampoline - (intptr_t)(site + 5);
  memcpy(patch + 1, &displacement, 4);
  memcpy((void *)site, patch, sizeof(patch));
  if (mprotect((void *)first, span, PROT_READ | PROT_EXEC)) {
    memcpy((void *)site, before, sizeof(before));
    mprotect((void *)first, span, PROT_READ | PROT_EXEC);
    munmap(trampoline, page_size);
    dlclose(handle);
    return fail(env, "Could not restore GPU capture code protection.");
  }
  installed_site = site;
  // Native target and executable trampoline are retained for process lifetime.
  return undefined(env);
}

static napi_value stats(napi_env env, napi_callback_info info) {
  (void)info;
  char json[512];
  snprintf(json, sizeof(json),
           "{\"installed\":%s,\"calls\":%llu,\"limited\":%llu,\"invalidWants\":%llu,"
           "\"lastMaxPixels\":%u,\"lastWidth\":%u,\"lastHeight\":%u}",
           installed_site ? "true" : "false", (unsigned long long)atomic_load(&counts.calls),
           (unsigned long long)atomic_load(&counts.limited),
           (unsigned long long)atomic_load(&counts.invalid), atomic_load(&counts.max_pixels),
           atomic_load(&counts.width), atomic_load(&counts.height));
  napi_value value;
  api_napi_create_string_utf8(env, json, NAPI_AUTO_LENGTH, &value);
  return value;
}

napi_value dpe_capture_scale_register(napi_env env, napi_value exports) {
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
  if (api_napi_create_function(env, "installCaptureScale", NAPI_AUTO_LENGTH, install, NULL,
                               &function) != napi_ok ||
      api_napi_set_named_property(env, exports, "installCaptureScale", function) != napi_ok ||
      api_napi_create_function(env, "captureScaleStats", NAPI_AUTO_LENGTH, stats, NULL,
                               &function) != napi_ok ||
      api_napi_set_named_property(env, exports, "captureScaleStats", function) != napi_ok)
    return NULL;
  return exports;
}
