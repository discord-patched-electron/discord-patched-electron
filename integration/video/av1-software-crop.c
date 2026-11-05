// Crop only the caller's output picture view; decoder references and allocation
// ownership retain their full geometry. The six call sites belong exclusively
// to the reviewed Discord WebRTC dav1d wrapper, before engine initialization.
#define _GNU_SOURCE
#include "av1-software-crop.h"
#include "av1-crop.h"
#include <dlfcn.h>
#include <limits.h>
#include <link.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <unistd.h>

#define SNAPSHOTS 128
#define CONTEXTS 128
struct snapshot {
  int64_t ticket, timestamp;
  struct dpe_av1_crop crop;
};
struct decoder {
  void *context;
  struct dpe_av1_crop_state state;
  struct snapshot frames[SNAPSHOTS];
  int64_t ticket;
  struct decoder *next_decoder;
};
static struct decoder *decoders;
static pthread_mutex_t mutex = PTHREAD_MUTEX_INITIALIZER;
static int (*original_open)(void **, const void *);
static int (*original_send)(void *, struct dpe_dav1d_data *);
static int (*original_get)(void *, struct dpe_dav1d_picture *);
static void (*original_close)(void **);
static uintptr_t installed;
static struct {
  atomic_uint_fast64_t contexts, submitted, cropped, rejected, unmatched;
} counts;

// dav1d requires serialized API use for each context. Only this short registry
// lookup is shared between streams; decoding never holds a global lock.
static struct decoder *lookup(void *context) {
  pthread_mutex_lock(&mutex);
  struct decoder *entry = decoders;
  while (entry && entry->context != context)
    entry = entry->next_decoder;
  pthread_mutex_unlock(&mutex);
  return entry;
}
static int opened(void **context, const void *settings) {
  int result = original_open(context, settings);
  if (!result && context && *context) {
    struct decoder *entry = calloc(1, sizeof(*entry));
    if (entry) {
      pthread_mutex_lock(&mutex);
      if (atomic_load(&counts.contexts) < CONTEXTS) {
        entry->context = *context;
        entry->next_decoder = decoders;
        decoders = entry;
        atomic_fetch_add(&counts.contexts, 1);
        entry = NULL;
      }
      pthread_mutex_unlock(&mutex);
      free(entry);
    }
  }
  return result;
}
static int sent(void *context, struct dpe_dav1d_data *data) {
  struct decoder *entry = lookup(context);
  if (!entry || !data || !data->data || !data->size)
    return original_send(context, data);
  struct dpe_av1_crop_state next = entry->state;
  struct dpe_av1_crop crop = dpe_av1_crop_read(data->data, data->size, &next);
  int64_t timestamp = data->timestamp;
  const uint8_t *bytes = data->data;
  if (entry->ticket == INT64_MAX) {
    memset(entry->frames, 0, sizeof(entry->frames));
    entry->ticket = 0;
  }
  int64_t ticket = ++entry->ticket;
  // Public dav1d metadata is passed through without influencing decode. Give
  // each accepted input a private identity, restoring the original timestamp
  // on output. This also handles delayed/reordered output and metadata resets.
  data->timestamp = ticket;
  int result = original_send(context, data);
  if (result) {
    if (data->data == bytes)
      data->timestamp = timestamp;
    return result;
  }
  entry->state = next;
  entry->frames[(uint64_t)ticket % SNAPSHOTS] = (struct snapshot){ticket, timestamp, crop};
  atomic_fetch_add(&counts.submitted, 1);
  return result;
}
static bool crop_picture(struct dpe_dav1d_picture *picture, struct dpe_av1_crop crop) {
  if (picture->width <= 0 || picture->height <= 0 || picture->depth != 8 ||
      (picture->layout != 1 && picture->layout != 3) ||
      !dpe_av1_crop_valid(crop, picture->width, picture->height))
    return false;
  uintptr_t offsets[3];
  for (unsigned plane = 0; plane < 3; plane++) {
    unsigned divisor = plane && picture->layout == 1 ? 2 : 1;
    ptrdiff_t stride = picture->stride[plane != 0];
    uint32_t x = crop.x / divisor, y = crop.y / divisor;
    if (!picture->plane[plane] || stride < (picture->width + divisor - 1) / divisor ||
        y > (PTRDIFF_MAX - x) / stride)
      return false;
    offsets[plane] = (uintptr_t)y * stride + x;
    if (offsets[plane] > UINTPTR_MAX - (uintptr_t)picture->plane[plane])
      return false;
  }
  for (unsigned plane = 0; plane < 3; plane++)
    picture->plane[plane] += offsets[plane];
  picture->width = crop.width;
  picture->height = crop.height;
  return true;
}
static int received(void *context, struct dpe_dav1d_picture *picture) {
  int result = original_get(context, picture);
  struct decoder *entry = result ? NULL : lookup(context);
  if (!entry || !picture)
    return result;
  struct snapshot *frame = &entry->frames[(uint64_t)picture->timestamp % SNAPSHOTS];
  if (picture->timestamp > 0 && frame->ticket == picture->timestamp) {
    picture->timestamp = frame->timestamp;
    frame->ticket = 0;
    if (frame->crop.width) {
      if (crop_picture(picture, frame->crop))
        atomic_fetch_add(&counts.cropped, 1);
      else
        atomic_fetch_add(&counts.rejected, 1);
    }
    return result;
  }
  atomic_fetch_add(&counts.unmatched, 1);
  return result;
}
static void closed(void **context) {
  pthread_mutex_lock(&mutex);
  struct decoder **at = &decoders;
  while (*at && (!context || (*at)->context != *context))
    at = &(*at)->next_decoder;
  if (*at) {
    struct decoder *entry = *at;
    *at = entry->next_decoder;
    free(entry);
    atomic_fetch_sub(&counts.contexts, 1);
  }
  pthread_mutex_unlock(&mutex);
  original_close(context);
}

static bool executable(uintptr_t base, uintptr_t address, size_t size) {
  const ElfW(Ehdr) *header = (void *)base;
  const ElfW(Phdr) *segments = (void *)(base + header->e_phoff);
  for (int i = 0; i < header->e_phnum; i++) {
    const ElfW(Phdr) *p = &segments[i];
    if (p->p_type == PT_LOAD && (p->p_flags & (PF_R | PF_X | PF_W)) == (PF_R | PF_X) &&
        address >= base + p->p_vaddr && size <= p->p_memsz &&
        address - base - p->p_vaddr <= p->p_memsz - size)
      return true;
  }
  return false;
}
static uint8_t *near_page(uintptr_t site, size_t size) {
  uintptr_t center = site & ~(size - 1);
  for (uintptr_t distance = 0x10000; distance < 0x70000000; distance += 0x10000)
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
  return MAP_FAILED;
}
int dpe_software_av1_install(const char *filename, const uint32_t offsets[10]) {
  void *handle = dlopen(filename, RTLD_NOW | RTLD_NOLOAD);
  struct link_map *map;
  if (!handle || dlinfo(handle, RTLD_DI_LINKMAP, &map)) {
    if (handle)
      dlclose(handle);
    return 0;
  }
  uintptr_t base = map->l_addr;
  if (installed) {
    dlclose(handle);
    return installed == base + offsets[0];
  }
  uintptr_t targets[4], sites[6], low = UINTPTR_MAX, high = 0;
  const unsigned roles[] = {0, 1, 2, 3, 3, 3};
  uint8_t before[6][5];
  for (unsigned i = 0; i < 4; i++) {
    targets[i] = base + offsets[i];
    if (!executable(base, targets[i], 1))
      goto fail;
  }
  for (unsigned i = 0; i < 6; i++) {
    sites[i] = base + offsets[i + 4];
    if (!executable(base, sites[i], 5))
      goto fail;
    memcpy(before[i], (void *)sites[i], 5);
    int32_t relative;
    memcpy(&relative, before[i] + 1, 4);
    if (before[i][0] != (i == 3 ? 0xe9 : 0xe8) || sites[i] + 5 + relative != targets[roles[i]])
      goto fail;
    if (sites[i] < low)
      low = sites[i];
    if (sites[i] + 5 > high)
      high = sites[i] + 5;
  }
  size_t page_size = (size_t)sysconf(_SC_PAGESIZE);
  uint8_t *trampoline = near_page(low, page_size);
  if (trampoline == MAP_FAILED)
    goto fail;
  uintptr_t replacements[] = {(uintptr_t)opened, (uintptr_t)sent, (uintptr_t)received,
                              (uintptr_t)closed};
  for (unsigned i = 0; i < 4; i++) {
    const uint8_t jump[] = {0xff, 0x25, 0, 0, 0, 0};
    memcpy(trampoline + i * 16, jump, sizeof(jump));
    memcpy(trampoline + i * 16 + sizeof(jump), &replacements[i], sizeof(uintptr_t));
  }
  for (unsigned i = 0; i < 6; i++) {
    intptr_t delta = (intptr_t)(trampoline + roles[i] * 16) - (intptr_t)(sites[i] + 5);
    if (delta < INT32_MIN || delta > INT32_MAX) {
      munmap(trampoline, page_size);
      goto fail;
    }
  }
  uintptr_t first = low & ~(page_size - 1);
  size_t span = ((high - 1) & ~(page_size - 1)) - first + page_size;
  if (!executable(base, first, span) || mprotect(trampoline, page_size, PROT_READ | PROT_EXEC) ||
      mprotect((void *)first, span, PROT_READ | PROT_WRITE | PROT_EXEC)) {
    munmap(trampoline, page_size);
    goto fail;
  }
  original_open = (void *)targets[0];
  original_send = (void *)targets[1];
  original_get = (void *)targets[2];
  original_close = (void *)targets[3];
  for (unsigned i = 0; i < 6; i++) {
    int32_t delta = (intptr_t)(trampoline + roles[i] * 16) - (intptr_t)(sites[i] + 5);
    memcpy((void *)(sites[i] + 1), &delta, 4);
  }
  if (mprotect((void *)first, span, PROT_READ | PROT_EXEC)) {
    for (unsigned i = 0; i < 6; i++)
      memcpy((void *)sites[i], before[i], 5);
    mprotect((void *)first, span, PROT_READ | PROT_EXEC);
    munmap(trampoline, page_size);
    goto fail;
  }
  installed = targets[0];
  // Retain native handle and executable trampoline for process lifetime.
  return 1;
fail:
  dlclose(handle);
  return 0;
}
void dpe_software_av1_stats(uint64_t result[6]) {
  result[0] = installed != 0;
  result[1] = atomic_load(&counts.contexts);
  result[2] = atomic_load(&counts.submitted);
  result[3] = atomic_load(&counts.cropped);
  result[4] = atomic_load(&counts.rejected);
  result[5] = atomic_load(&counts.unmatched);
}
