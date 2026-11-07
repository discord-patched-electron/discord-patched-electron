#define _GNU_SOURCE
#include "../../integration/video/encoder-import-cache.h"
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdio.h>
#include <sys/mman.h>
#include <unistd.h>

static uintptr_t surface(struct dpe_import_entry *entry) {
  uintptr_t value;
  assert(dpe_import_surface(entry, &value));
  return value;
}

struct device {
  unsigned created, destroyed, finished, attempts;
};
static bool create(void *opaque, const struct encoder_nv12 *image, uintptr_t *surface) {
  struct device *device = opaque;
  ++device->attempts;
  assert(fcntl(image->fd, F_GETFD) & FD_CLOEXEC);
  if (image->modifier == 0xdead)
    return false;
  *surface = ++device->created;
  return true;
}
static void destroy(void *opaque, uintptr_t surface) {
  struct device *device = opaque;
  assert(surface && surface <= device->created);
  ++device->destroyed;
}
static void finish(void *opaque) { ++((struct device *)opaque)->finished; }
static unsigned descriptors(void) {
  unsigned count = 0;
  for (int fd = 0; fd < 256; ++fd)
    count += fcntl(fd, F_GETFD) >= 0;
  return count;
}
static struct encoder_nv12 image(void) {
  int fd = memfd_create("synthetic-nv12", MFD_CLOEXEC);
  assert(fd >= 0 && !ftruncate(fd, 65536));
  return (struct encoder_nv12){.width = 128,
                               .height = 128,
                               .fd = fd,
                               .size = 65536,
                               .offset = {0, 16384},
                               .stride = {128, 128}};
}
static void *put(void *entry) {
  dpe_import_put(entry);
  return NULL;
}
int main(void) {
  unsigned baseline = descriptors();
  struct device device = {0};
  struct dpe_import_cache *cache = dpe_import_cache_new(&device, create, destroy, finish);
  assert(cache);
  struct encoder_nv12 a = image();
  struct dpe_import_entry *first = dpe_import_get(cache, &a);
  assert(first && device.created == 1);
  uintptr_t identity = surface(first);
  dpe_import_put(first);
  dpe_import_put(first); // A stale lease cannot decrement another frame's ownership.
  uintptr_t unchanged = 77;
  assert(!dpe_import_surface(first, &unchanged) && unchanged == 77);
  for (unsigned i = 0; i < 1000; ++i) {
    struct encoder_nv12 duplicate = a;
    duplicate.fd = fcntl(a.fd, F_DUPFD_CLOEXEC, 0);
    assert(duplicate.fd >= 0);
    struct dpe_import_entry *entry = dpe_import_get(cache, &duplicate);
    assert(entry && surface(entry) == identity);
    dpe_import_put(entry);
    close(duplicate.fd);
  }
  assert(device.created == 1 && !device.destroyed);
  // Reusing the descriptor number for a different allocation cannot hit cache.
  int old_fd = a.fd;
  close(a.fd);
  a = image();
  assert(a.fd == old_fd);
  struct dpe_import_entry *next = dpe_import_get(cache, &a);
  assert(next && surface(next) != identity);
  dpe_import_put(next);
  // Every layout field must distinguish views of the same allocation.
  for (unsigned field = 0; field < 8; ++field) {
    struct encoder_nv12 changed = a;
    switch (field) {
    case 0:
      changed.width = 64;
      break;
    case 1:
      changed.height = 64;
      break;
    case 2:
      changed.size = 32768;
      break;
    case 3:
      changed.offset[0] = 128;
      break;
    case 4:
      changed.offset[1] = 32768;
      break;
    case 5:
      changed.stride[0] = 256;
      break;
    case 6:
      changed.stride[1] = 256;
      break;
    default:
      changed.modifier = 123;
      break;
    }
    unsigned before = device.created;
    next = dpe_import_get(cache, &changed);
    assert(next && device.created == before + 1);
    dpe_import_put(next);
  }
  // Descriptors lacking a reported extent remain eligible for the caller's
  // original uncached driver import, without pretending they have an identity.
  struct encoder_nv12 unknown = image();
  assert(!ftruncate(unknown.fd, 0));
  assert(!dpe_import_get(cache, &unknown) && errno == ENOTSUP);
  close(unknown.fd);
  // Import failure closes its duplicated descriptor, without poisoning the slot.
  struct encoder_nv12 invalid = a;
  invalid.modifier = 0xdead;
  next = dpe_import_get(cache, &invalid);
  assert(!next && errno == EIO);
  unsigned retained = descriptors();
  for (unsigned i = 0; i < 100; ++i) {
    assert(!dpe_import_get(cache, &invalid));
    assert(descriptors() == retained);
  }
  // In-flight entries cannot be evicted. Closure defers device destruction
  // until concurrent frame releases finish, including repeated references.
  struct encoder_nv12 images[DPE_ENCODER_IMPORTS];
  struct dpe_import_entry *held[DPE_ENCODER_IMPORTS + 1];
  for (unsigned i = 0; i < DPE_ENCODER_IMPORTS; ++i) {
    images[i] = image();
    held[i] = dpe_import_get(cache, &images[i]);
    assert(held[i]);
  }
  held[DPE_ENCODER_IMPORTS] = dpe_import_get(cache, &images[0]);
  assert(held[DPE_ENCODER_IMPORTS] != held[0]);
  assert(surface(held[DPE_ENCODER_IMPORTS]) == surface(held[0]));
  unsigned created = device.created;
  assert(!dpe_import_get(cache, &a) && errno == EAGAIN && device.created == created);
  dpe_import_cache_close(cache);
  dpe_import_cache_close(cache);
  assert(!dpe_import_get(cache, &a) && errno == ECANCELED);
  assert(!device.finished);
  pthread_t threads[DPE_ENCODER_IMPORTS + 1];
  for (unsigned i = 0; i < DPE_ENCODER_IMPORTS + 1; ++i)
    assert(!pthread_create(&threads[i], NULL, put, held[i]));
  for (unsigned i = 0; i < DPE_ENCODER_IMPORTS + 1; ++i)
    assert(!pthread_join(threads[i], NULL));
  assert(device.finished == 1 && device.destroyed == device.created);
  close(a.fd);
  for (unsigned i = 0; i < DPE_ENCODER_IMPORTS; ++i)
    close(images[i].fd);
  assert(descriptors() == baseline);
  uint64_t stats[4];
  dpe_import_stats(stats);
  assert(stats[1] >= 1000 && stats[2] && !stats[3]);
  puts("encoder import reuse, layout identity, FD reuse, eviction and retained leases passed");
}
