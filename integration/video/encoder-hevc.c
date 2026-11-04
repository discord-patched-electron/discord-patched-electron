#define _GNU_SOURCE
#include "encoder-hevc.h"
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <glob.h>
#include <limits.h>
#include <link.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

extern const unsigned char dpe_hevc_image_start[] __attribute__((weak));
extern const unsigned char dpe_hevc_image_end[] __attribute__((weak));
static const struct dpe_hevc_api *backend;
static uintptr_t installed;
static pthread_mutex_t mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t released = PTHREAD_COND_INITIALIZER;
static pthread_once_t probe_once = PTHREAD_ONCE_INIT;
static char devices[2][PATH_MAX];
static char av1_device[PATH_MAX];
static atomic_uint_fast64_t probes;
static struct {
  atomic_uint_fast64_t gpu_frames, cpu_frames, import_fallbacks, failed_frames;
} counts[3];
struct owned_encoder {
  void *handle;
  unsigned users;
  uint32_t vendor;
  uint32_t codec;
  pthread_mutex_t serial;
  struct owned_encoder *next;
};
static struct owned_encoder *encoders;
static bool (*original_native)(void *, const void *, void *, struct encoder_frame_info);
static bool (*original_i420)(void *, const void *, struct encoder_frame_info, struct encoder_plane,
                             struct encoder_plane, struct encoder_plane);
static bool (*original_recon)(void *);
static uint32_t (*original_vendor)(void *);
static void (*original_probe)(const char *, void *);
static const void *(*surface_descriptor)(const void *);
static void (*drop_linux_frame)(void *);
static void *(*clone_i420)(void *, const uint8_t **, size_t *, size_t *, size_t *, size_t *,
                           size_t *);
static void (*drop_i420)(void *);

static struct owned_encoder *acquire(void *handle) {
  pthread_mutex_lock(&mutex);
  struct owned_encoder *entry;
  for (entry = encoders; entry && entry->handle != handle; entry = entry->next) {
  }
  if (entry)
    entry->users++;
  pthread_mutex_unlock(&mutex);
  return entry;
}
static void release(struct owned_encoder *entry) {
  pthread_mutex_lock(&mutex);
  if (!--entry->users)
    pthread_cond_broadcast(&released);
  pthread_mutex_unlock(&mutex);
}
bool dpe_hevc_owned(void *handle) {
  struct owned_encoder *entry = acquire(handle);
  if (entry)
    release(entry);
  return entry != NULL;
}
bool dpe_hevc_active(void) {
  pthread_mutex_lock(&mutex);
  bool active = encoders != NULL;
  pthread_mutex_unlock(&mutex);
  return active;
}

static void probe_devices(void) {
  glob_t matches = {0};
  if (!backend)
    return;
  if (glob("/dev/dri/renderD*", 0, NULL, &matches)) {
    globfree(&matches);
    return;
  }
  // One successful self-test per actual vendor. The original NVENC discovery
  // stays authoritative for NVIDIA, and absent/failed VA-API stays unavailable.
  for (size_t i = 0; i < matches.gl_pathc; i++) {
    const char *node = matches.gl_pathv[i];
    const char *name = strrchr(node, '/');
    char path[PATH_MAX], vendor[16] = {0};
    if (!name || snprintf(path, sizeof(path), "/sys/class/drm/%s/device/vendor", name + 1) >=
                     (int)sizeof(path))
      continue;
    FILE *file = fopen(path, "re");
    if (!file)
      continue;
    bool read = fgets(vendor, sizeof(vendor), file) != NULL;
    fclose(file);
    int index = read && !strncmp(vendor, "0x1002", 6)   ? 0
                : read && !strncmp(vendor, "0x8086", 6) ? 1
                                                        : -1;
    if (index == 0 && !av1_device[0])
      snprintf(av1_device, sizeof(av1_device), "%s", node);
    if (index < 0 || devices[index][0])
      continue;
    atomic_fetch_add(&probes, 1);
    if (backend->probe(node) == (uint32_t)index + 1)
      snprintf(devices[index], sizeof(devices[index]), "%s", node);
  }
  globfree(&matches);
}
static void probe_gpu(const char *helper, void *support) {
  original_probe(helper, support);
  pthread_once(&probe_once, probe_devices);
  if (devices[0][0])
    ((uint8_t *)support)[4] = 1;
  if (devices[1][0])
    ((uint8_t *)support)[7] = 1;
}
static int create_encoder(uint32_t type, void *context, void *encoded, void *dropped, void **output,
                          struct encoder_session session, struct encoder_options options) {
  if (!installed || (session.codec != 1 && session.codec != 2) || (type != 5 && type != 6))
    return -1;
  pthread_once(&probe_once, probe_devices);
  if (!output || !encoded || !dropped)
    return 0;
  *output = NULL;
  for (size_t i = 0; i < 2; i++) {
    if (session.codec == 2 && i != 0)
      continue; // The VCN4 workaround must not select a different vendor.
    const char *node = session.codec == 2 ? av1_device : devices[i];
    if (!node[0])
      continue;
    struct owned_encoder *entry = calloc(1, sizeof(*entry));
    if (!entry)
      return 0;
    if (pthread_mutex_init(&entry->serial, NULL)) {
      free(entry);
      return 0;
    }
    entry->handle = backend->create(node, session, options, context, (encoder_encoded_fn)encoded,
                                    (encoder_dropped_fn)dropped);
    if (!entry->handle) {
      pthread_mutex_destroy(&entry->serial);
      free(entry);
      continue;
    }
    entry->vendor = backend->vendor(entry->handle);
    entry->codec = session.codec;
    pthread_mutex_lock(&mutex);
    entry->next = encoders;
    encoders = entry;
    pthread_mutex_unlock(&mutex);
    *output = entry->handle;
    return 1;
  }
  return 0;
}
int dpe_hevc_create(uint32_t type, void *context, void *encoded, void *dropped, void **output,
                    struct encoder_session session, struct encoder_options options) {
  return session.codec == 1
             ? create_encoder(type, context, encoded, dropped, output, session, options)
             : -1;
}
int dpe_av1_create(uint32_t type, void *context, void *encoded, void *dropped, void **output,
                   struct encoder_session session, struct encoder_options options) {
  return session.codec == 2
             ? create_encoder(type, context, encoded, dropped, output, session, options)
             : -1;
}
bool dpe_hevc_update(void *handle, struct encoder_options options) {
  struct owned_encoder *entry = acquire(handle);
  if (!entry)
    return false;
  pthread_mutex_lock(&entry->serial);
  bool result = backend->update(handle, options);
  pthread_mutex_unlock(&entry->serial);
  release(entry);
  return result;
}
bool dpe_hevc_destroy(void *handle) {
  pthread_mutex_lock(&mutex);
  struct owned_encoder **link = &encoders;
  while (*link && (*link)->handle != handle)
    link = &(*link)->next;
  struct owned_encoder *entry = *link;
  if (entry) {
    *link = entry->next;
    while (entry->users)
      pthread_cond_wait(&released, &mutex);
  }
  pthread_mutex_unlock(&mutex);
  if (!entry)
    return false;
  backend->destroy(handle);
  pthread_mutex_destroy(&entry->serial);
  free(entry);
  return true;
}
static uint32_t encoder_vendor(void *handle) {
  struct owned_encoder *entry = acquire(handle);
  if (!entry)
    return original_vendor(handle);
  uint32_t vendor = entry->vendor;
  release(entry);
  return vendor;
}
static bool supports_recon(void *handle) {
  return dpe_hevc_owned(handle) ? false : original_recon(handle);
}
static void release_gpu_frame(void *opaque) {
  // Arc<LinuxFrame>, borrowed through CcFrameHandle. Its Rust destructor owns
  // the pooled NV12 surface and its allocator; never free it with libc.
  if (__atomic_fetch_sub((size_t *)opaque, 1, __ATOMIC_RELEASE) == 1) {
    __atomic_thread_fence(__ATOMIC_ACQUIRE);
    drop_linux_frame(&opaque);
  }
}
static bool gpu_view(void *frame, struct encoder_nv12 *view, void **lease) {
  uint32_t tag;
  memcpy(&tag, frame, sizeof(tag));
  if (tag != 1)
    return false;
  void *arc, *pooled;
  memcpy(&arc, (uint8_t *)frame + 8, sizeof(arc));
  if (!arc)
    return false;
  memcpy(&pooled, (uint8_t *)arc + 16, sizeof(pooled));
  if (!pooled)
    return false;
  uint32_t descriptor_tag;
  memcpy(&descriptor_tag, (uint8_t *)pooled + 16 + 0xb0, sizeof(descriptor_tag));
  if (descriptor_tag == 2)
    return false;
  // The constructor/import call chain and descriptor accessor are bound to
  // reviewed full module hashes by encoder_scope.py. These are borrowed Rust
  // fields, not a portable format or an assumption about arbitrary DMA-BUFs.
  const uint64_t *descriptor = surface_descriptor((uint8_t *)pooled + 16);
  if (!descriptor || descriptor[2] != 2 || descriptor[10] != 1 || descriptor[12] != 1 ||
      (uint32_t)descriptor[3] != UINT32_C(0x3231564e) || descriptor[6] > UINT32_MAX ||
      descriptor[7] > UINT32_MAX || descriptor[8] > UINT32_MAX)
    return false;
  const uint64_t *planes = (void *)(uintptr_t)descriptor[1];
  if (!planes || !descriptor[11] || planes[0] || planes[3] || planes[1] > UINT32_MAX ||
      planes[2] > UINT32_MAX || planes[4] > UINT32_MAX || planes[5] > UINT32_MAX)
    return false;
  *view = (struct encoder_nv12){.width = descriptor[6],
                                .height = descriptor[7],
                                .fd = *(const int *)(uintptr_t)descriptor[11],
                                .size = descriptor[8],
                                .offset = {planes[1], planes[4]},
                                .stride = {planes[2], planes[5]},
                                .modifier = descriptor[4]};
  size_t previous = __atomic_fetch_add((size_t *)arc, 1, __ATOMIC_RELAXED);
  if (!previous || previous >= PTRDIFF_MAX) {
    __atomic_fetch_sub((size_t *)arc, 1, __ATOMIC_RELAXED);
    return false;
  }
  *lease = arc;
  return true;
}
static bool native_frame(void *handle, const void *opaque_meta, void *frame,
                         struct encoder_frame_info info) {
  struct owned_encoder *entry = acquire(handle);
  if (!entry)
    return original_native(handle, opaque_meta, frame, info);
  const struct encoder_frame_meta *meta = opaque_meta;
  bool result = false;
  pthread_mutex_lock(&entry->serial);
  struct encoder_nv12 view;
  void *lease = NULL;
  if (frame && gpu_view(frame, &view, &lease)) {
    result = backend->nv12(handle, meta, info, &view, lease, release_gpu_frame);
    atomic_fetch_add(&counts[entry->codec].gpu_frames, 1);
  } else if (frame) {
    const uint8_t *pixels = NULL;
    size_t size = 0, sy = 0, suv = 0, u = 0, v = 0;
    lease = clone_i420(frame, &pixels, &size, &sy, &suv, &u, &v);
    uint32_t width = meta->width, height = meta->height;
    if (lease && pixels && width && height && sy >= width && suv >= (width + 1) / 2 &&
        sy <= size / height && u <= size && v <= size && suv <= (size - u) / ((height + 1) / 2) &&
        suv <= (size - v) / ((height + 1) / 2)) {
      const struct encoder_plane planes[] = {{pixels, sy, width, height},
                                             {pixels + u, suv, (width + 1) / 2, (height + 1) / 2},
                                             {pixels + v, suv, (width + 1) / 2, (height + 1) / 2}};
      result = backend->i420(handle, meta, info, planes);
      atomic_fetch_add(&counts[entry->codec].cpu_frames, 1);
      atomic_fetch_add(&counts[entry->codec].import_fallbacks, 1);
    }
    if (lease)
      drop_i420(lease);
  }
  pthread_mutex_unlock(&entry->serial);
  if (!result)
    atomic_fetch_add(&counts[entry->codec].failed_frames, 1);
  release(entry);
  return result;
}
static bool cpu_frame(void *handle, const void *meta, struct encoder_frame_info info,
                      struct encoder_plane y, struct encoder_plane u, struct encoder_plane v) {
  struct owned_encoder *entry = acquire(handle);
  if (!entry)
    return original_i420(handle, meta, info, y, u, v);
  struct encoder_plane planes[] = {y, u, v};
  pthread_mutex_lock(&entry->serial);
  bool result = backend->i420(handle, meta, info, planes);
  pthread_mutex_unlock(&entry->serial);
  atomic_fetch_add(&counts[entry->codec].cpu_frames, 1);
  if (!result)
    atomic_fetch_add(&counts[entry->codec].failed_frames, 1);
  release(entry);
  return result;
}

static int protection(uintptr_t base, uintptr_t address, size_t size) {
  const ElfW(Ehdr) *header = (void *)base;
  const ElfW(Phdr) *segments = (void *)(base + header->e_phoff);
  int result = 0;
  for (int i = 0; i < header->e_phnum; i++) {
    const ElfW(Phdr) *segment = &segments[i];
    if (segment->p_type == PT_LOAD && address >= base + segment->p_vaddr &&
        size <= segment->p_memsz && address - base - segment->p_vaddr <= segment->p_memsz - size)
      result = (segment->p_flags & PF_R ? PROT_READ : 0) |
               (segment->p_flags & PF_W ? PROT_WRITE : 0) |
               (segment->p_flags & PF_X ? PROT_EXEC : 0);
    if (segment->p_type == PT_GNU_RELRO && address >= base + segment->p_vaddr &&
        size <= segment->p_memsz && address - base - segment->p_vaddr <= segment->p_memsz - size)
      result &= ~PROT_WRITE;
  }
  return result;
}
static bool load_backend(void) {
  if (backend)
    return true;
  const char *replacement = getenv("DPE_HEVC_BACKEND");
  int fd = -1;
  char path[64];
  if (!replacement || !*replacement) {
    if (!dpe_hevc_image_start || !dpe_hevc_image_end)
      return true; // Deliberate camera-only developer/test build.
    size_t size = dpe_hevc_image_end - dpe_hevc_image_start;
    if (!size || size > 16 * 1024 * 1024)
      return false;
    fd = memfd_create("dpe-hevc", MFD_CLOEXEC | MFD_ALLOW_SEALING);
    if (fd < 0)
      return false;
    size_t written = 0;
    while (written < size) {
      ssize_t count = write(fd, dpe_hevc_image_start + written, size - written);
      if (count < 0 && errno == EINTR)
        continue;
      if (count <= 0) {
        close(fd);
        return false;
      }
      written += (size_t)count;
    }
    if (fcntl(fd, F_ADD_SEALS, F_SEAL_WRITE | F_SEAL_GROW | F_SEAL_SHRINK | F_SEAL_SEAL) < 0) {
      close(fd);
      return false;
    }
    snprintf(path, sizeof(path), "/proc/self/fd/%d", fd);
    replacement = path;
  }
  void *library = dlopen(replacement, RTLD_NOW | RTLD_LOCAL);
  if (fd >= 0)
    close(fd);
  if (!library)
    return false;
  const struct dpe_hevc_api *(*get_api)(void) = dlsym(library, "dpe_hevc_api");
  const struct dpe_hevc_api *api = get_api ? get_api() : NULL;
  if (!api || api->abi != DPE_HEVC_ABI || api->size != sizeof(*api) || !api->probe ||
      !api->create || !api->update || !api->i420 || !api->nv12 || !api->destroy || !api->vendor) {
    dlclose(library);
    return false;
  }
  backend = api; // Retained with the native callbacks for process lifetime.
  return true;
}
bool dpe_hevc_install(const char *filename, const uint32_t offsets[19]) {
  void *module = dlopen(filename, RTLD_NOW | RTLD_NOLOAD);
  struct link_map *map;
  if (!module || dlinfo(module, RTLD_DI_LINKMAP, &map)) {
    if (module)
      dlclose(module);
    return false;
  }
  uintptr_t base = map->l_addr;
  if (installed) {
    dlclose(module);
    return installed == base;
  }
  const char *names[] = {"cc_encoder_add_frame_native", "cc_encoder_add_frame_i420",
                         "cc_encoder_supports_recon_frame", "cc_encoder_vendor",
                         "cc_probe_gpu_encoder"};
  uintptr_t before[5], slots[5], targets[5], pages[5];
  int protections[5];
  size_t page_size = (size_t)sysconf(_SC_PAGESIZE);
  bool valid = true;
  for (size_t i = 0; i < 5; i++) {
    slots[i] = base + offsets[3 * i];
    targets[i] = base + offsets[3 * i + 1];
    protections[i] = protection(base, slots[i], sizeof(uintptr_t));
    pages[i] = slots[i] & ~(page_size - 1);
    valid &= dlsym(module, names[i]) == (void *)targets[i] &&
             (protection(base, targets[i], 1) & PROT_EXEC) && slots[i] % sizeof(uintptr_t) == 0 &&
             (protections[i] & PROT_READ) && !(protections[i] & PROT_EXEC);
    if (!valid)
      break;
    before[i] = *(uintptr_t *)slots[i];
    valid &=
        before[i] == targets[i] || (offsets[3 * i + 2] && before[i] == base + offsets[3 * i + 2]);
  }
  const char *suffixes[] = {"19PooledVulkanSurface10descriptor",
                            "13capture_linux10LinuxFrameE9drop_slow"};
  for (size_t i = 0; i < 2 && valid; i++) {
    Dl_info symbol;
    uintptr_t target = base + offsets[15 + i];
    valid &= (protection(base, target, 1) & PROT_EXEC) && dladdr((void *)target, &symbol) &&
             symbol.dli_sname && strstr(symbol.dli_sname, suffixes[i]);
  }
  valid &= dlsym(module, "cc_video_frame_clone_i420") == (void *)(base + offsets[17]) &&
           dlsym(module, "cc_i420_buffer_destroy") == (void *)(base + offsets[18]);
  if (!valid) {
    dlclose(module);
    return false;
  }
  if (!load_backend()) {
    fprintf(stderr, "DPE HEVC backend unavailable; keeping native codec capabilities.\n");
    dlclose(module);
    return true;
  }
  if (!backend) {
    dlclose(module);
    return true;
  }
  for (size_t i = 0; i < 5; i++) {
    if (mprotect((void *)pages[i], page_size, protections[i] | PROT_WRITE)) {
      for (size_t j = 0; j < i; j++)
        mprotect((void *)pages[j], page_size, protections[j]);
      dlclose(module);
      return false;
    }
  }
  original_native = (void *)targets[0];
  original_i420 = (void *)targets[1];
  original_recon = (void *)targets[2];
  original_vendor = (void *)targets[3];
  original_probe = (void *)targets[4];
  surface_descriptor = (void *)(base + offsets[15]);
  drop_linux_frame = (void *)(base + offsets[16]);
  clone_i420 = (void *)(base + offsets[17]);
  drop_i420 = (void *)(base + offsets[18]);
  const uintptr_t replacements[] = {(uintptr_t)native_frame, (uintptr_t)cpu_frame,
                                    (uintptr_t)supports_recon, (uintptr_t)encoder_vendor,
                                    (uintptr_t)probe_gpu};
  for (size_t i = 0; i < 5; i++)
    __atomic_store_n((uintptr_t *)slots[i], replacements[i], __ATOMIC_RELEASE);
  bool restored = true;
  for (size_t i = 0; i < 5; i++)
    restored &= mprotect((void *)pages[i], page_size, protections[i]) == 0;
  if (!restored) {
    for (size_t i = 0; i < 5; i++)
      if (!mprotect((void *)pages[i], page_size, protections[i] | PROT_WRITE)) {
        __atomic_store_n((uintptr_t *)slots[i], before[i], __ATOMIC_RELEASE);
        mprotect((void *)pages[i], page_size, protections[i]);
      }
    dlclose(module);
    return false;
  }
  installed = base;
  dlclose(module);
  return true;
}
void dpe_hevc_stats(uint64_t result[6]) {
  result[0] = installed != 0;
  result[1] = atomic_load(&probes);
  result[2] = atomic_load(&counts[1].gpu_frames);
  result[3] = atomic_load(&counts[1].cpu_frames);
  result[4] = atomic_load(&counts[1].import_fallbacks);
  result[5] = atomic_load(&counts[1].failed_frames);
}
void dpe_av1_stats(uint64_t result[6]) {
  result[0] = installed != 0;
  result[1] = 0;
  result[2] = atomic_load(&counts[2].gpu_frames);
  result[3] = atomic_load(&counts[2].cpu_frames);
  result[4] = atomic_load(&counts[2].import_fallbacks);
  result[5] = atomic_load(&counts[2].failed_frames);
}
