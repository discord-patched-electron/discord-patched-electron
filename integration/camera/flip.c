// Camera-only conversion hook. Installed once, before VoiceEngine.initialize;
// toggles subsequently touch only an atomic flag, never executable memory.
#define _GNU_SOURCE
#include <dlfcn.h>
#include <limits.h>
#include <link.h>
#include <node_api.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

typedef int (*convert_fn)(const uint8_t *, size_t, uint8_t *, int, uint8_t *, int, uint8_t *, int,
                          int, int, int, int, int, int, int, uint32_t);
static convert_fn original;
static atomic_bool enabled;
static uintptr_t installed_site;

static void mirror_plane(uint8_t *plane, int stride, int width, int height) {
  for (int y = 0; y < height; y++, plane += stride) {
    for (int x = 0; x < width / 2; x++) {
      uint8_t byte = plane[x];
      plane[x] = plane[width - x - 1];
      plane[width - x - 1] = byte;
    }
  }
}

static int convert_frame(const uint8_t *src, size_t length, uint8_t *y, int ys, uint8_t *u, int us,
                         uint8_t *v, int vs, int cx, int cy, int sw, int sh, int cw, int ch,
                         int rotation, uint32_t fourcc) {
  int result = original(src, length, y, ys, u, us, v, vs, cx, cy, sw, sh, cw, ch, rotation, fourcc);
  if (!result && atomic_load_explicit(&enabled, memory_order_relaxed)) {
    int width = rotation == 90 || rotation == 270 ? abs(ch) : cw;
    int height = rotation == 90 || rotation == 270 ? cw : abs(ch);
    mirror_plane(y, ys, width, height);
    mirror_plane(u, us, (width + 1) / 2, (height + 1) / 2);
    mirror_plane(v, vs, (width + 1) / 2, (height + 1) / 2);
  }
  return result;
}

// This library is also LD_PRELOADed into non-Node helpers. Resolve Node-API only
// when process.dlopen registers the addon, so those processes need no Node symbols.
#define API(name) static __typeof__(name) *api_##name
API(napi_get_cb_info);
API(napi_get_value_bool);
API(napi_get_value_string_utf8);
API(napi_get_value_uint32);
API(napi_get_undefined);
API(napi_create_function);
API(napi_set_named_property);
API(napi_throw_error);

static napi_value failure(napi_env env, const char *message) {
  api_napi_throw_error(env, NULL, message);
  return NULL;
}
static napi_value undefined(napi_env env) {
  napi_value value;
  api_napi_get_undefined(env, &value);
  return value;
}
static napi_value set_enabled(napi_env env, napi_callback_info info) {
  size_t count = 1;
  napi_value value;
  bool next;
  if (api_napi_get_cb_info(env, info, &count, &value, NULL, NULL) != napi_ok || count != 1 ||
      api_napi_get_value_bool(env, value, &next) != napi_ok || !installed_site)
    return failure(env, "Camera flip is not initialized.");
  atomic_store_explicit(&enabled, next, memory_order_relaxed);
  return undefined(env);
}

static void *near_page(uintptr_t site, size_t page_size) {
  // An indirect tail jump preserves every register and the stack arguments.
  // Reserve only unused addresses; never replace an existing mapping.
  uintptr_t center = site & ~(page_size - 1);
  for (uintptr_t distance = 0x10000; distance < 0x70000000; distance += 0x10000) {
    for (int sign = -1; sign <= 1; sign += 2) {
      if ((sign < 0 && center < distance) || (sign > 0 && center > UINTPTR_MAX - distance))
        continue;
      uintptr_t address = sign < 0 ? center - distance : center + distance;
      void *page = mmap((void *)address, page_size, PROT_READ | PROT_WRITE,
                        MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0);
      if (page == (void *)address)
        return page;
      if (page != MAP_FAILED)
        munmap(page, page_size);
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
      length == 0 || length >= sizeof(filename) - 1 ||
      api_napi_get_value_uint32(env, args[1], &call_offset) != napi_ok ||
      api_napi_get_value_uint32(env, args[2], &target_offset) != napi_ok)
    return failure(env, "Invalid camera flip metadata.");
  // Do not load an uninitialized voice engine. JS verifies its on-disk checksum
  // and the reviewed function fingerprint before supplying these offsets.
  void *handle = dlopen(filename, RTLD_NOW | RTLD_NOLOAD);
  struct link_map *map;
  if (!handle || dlinfo(handle, RTLD_DI_LINKMAP, &map))
    return failure(env, "The native camera module is not loaded.");
  uintptr_t site = map->l_addr + call_offset, target = map->l_addr + target_offset;
  if (installed_site) {
    dlclose(handle);
    return installed_site == site ? undefined(env) : failure(env, "Camera module changed.");
  }
  // Bound both addresses to the module's executable PT_LOAD segment before reading.
  const ElfW(Ehdr) *header = (void *)map->l_addr;
  const ElfW(Phdr) *segments = (void *)(map->l_addr + header->e_phoff);
  bool valid_site = false, valid_target = false;
  for (int i = 0; i < header->e_phnum; i++) {
    const ElfW(Phdr) *p = &segments[i];
    if (p->p_type != PT_LOAD || !(p->p_flags & PF_X))
      continue;
    valid_site |= call_offset >= p->p_vaddr && call_offset + 5ull <= p->p_vaddr + p->p_memsz;
    valid_target |= target_offset >= p->p_vaddr && target_offset < p->p_vaddr + p->p_memsz;
  }
  int32_t before = 0;
  if (valid_site)
    memcpy(&before, (void *)(site + 1), sizeof(before));
  if (!valid_site || !valid_target || *(uint8_t *)site != 0xe8 || site + 5 + before != target) {
    dlclose(handle);
    return failure(env, "The native camera conversion call does not match.");
  }
  size_t page_size = (size_t)sysconf(_SC_PAGESIZE);
  uint8_t *trampoline = near_page(site, page_size);
  if (trampoline == MAP_FAILED) {
    dlclose(handle);
    return failure(env, "Could not reserve the camera conversion adapter.");
  }
  const uint8_t jump[] = {0xff, 0x25, 0, 0, 0, 0};
  uintptr_t adapter = (uintptr_t)convert_frame;
  memcpy(trampoline, jump, sizeof(jump));
  memcpy(trampoline + sizeof(jump), &adapter, sizeof(adapter));
  uintptr_t first = site & ~(page_size - 1), last = (site + 4) & ~(page_size - 1);
  size_t span = last - first + page_size;
  if (mprotect(trampoline, page_size, PROT_READ | PROT_EXEC) ||
      mprotect((void *)first, span, PROT_READ | PROT_WRITE | PROT_EXEC)) {
    munmap(trampoline, page_size);
    dlclose(handle);
    return failure(env, "Could not install the camera conversion adapter.");
  }
  original = (convert_fn)target;
  int32_t displacement = (intptr_t)trampoline - (intptr_t)(site + 5);
  memcpy((void *)(site + 1), &displacement, sizeof(displacement));
  // Installation precedes native engine initialization, hence no camera thread
  // can execute this call while its displacement is being replaced.
  if (mprotect((void *)first, span, PROT_READ | PROT_EXEC)) {
    memcpy((void *)(site + 1), &before, sizeof(before));
    mprotect((void *)first, span, PROT_READ | PROT_EXEC);
    munmap(trampoline, page_size);
    dlclose(handle);
    return failure(env, "Could not restore camera code protection.");
  }
  installed_site = site;
  // Retain the module handle and trampoline for this process's lifetime.
  return undefined(env);
}

extern napi_value dpe_capture_register(napi_env, napi_value) __attribute__((weak));
extern napi_value dpe_capture_upstream_register(napi_env, napi_value) __attribute__((weak));
extern napi_value dpe_capture_scale_register(napi_env, napi_value) __attribute__((weak));
extern napi_value dpe_capture_sync_register(napi_env, napi_value) __attribute__((weak));
extern napi_value dpe_capture_picker_register(napi_env, napi_value) __attribute__((weak));
extern napi_value dpe_encoder_register(napi_env, napi_value) __attribute__((weak));

__attribute__((visibility("default"))) napi_value napi_register_module_v1(napi_env env,
                                                                          napi_value exports) {
#define LOAD(name)                                                                                 \
  api_##name = dlsym(RTLD_DEFAULT, #name);                                                         \
  if (!api_##name)                                                                                 \
  return NULL
  LOAD(napi_get_cb_info);
  LOAD(napi_get_value_bool);
  LOAD(napi_get_value_string_utf8);
  LOAD(napi_get_value_uint32);
  LOAD(napi_get_undefined);
  LOAD(napi_create_function);
  LOAD(napi_set_named_property);
  LOAD(napi_throw_error);
  napi_value fn;
  if (api_napi_create_function(env, "install", NAPI_AUTO_LENGTH, install, NULL, &fn) != napi_ok ||
      api_napi_set_named_property(env, exports, "install", fn) != napi_ok ||
      api_napi_create_function(env, "setEnabled", NAPI_AUTO_LENGTH, set_enabled, NULL, &fn) !=
          napi_ok ||
      api_napi_set_named_property(env, exports, "setEnabled", fn) != napi_ok)
    return NULL;
  if (dpe_capture_register && !dpe_capture_register(env, exports))
    return NULL;
  if (dpe_capture_upstream_register && !dpe_capture_upstream_register(env, exports))
    return NULL;
  if (dpe_capture_scale_register && !dpe_capture_scale_register(env, exports))
    return NULL;
  if (dpe_capture_sync_register && !dpe_capture_sync_register(env, exports))
    return NULL;
  if (dpe_capture_picker_register && !dpe_capture_picker_register(env, exports))
    return NULL;
  return dpe_encoder_register ? dpe_encoder_register(env, exports) : exports;
}
