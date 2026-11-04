#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <time.h>

static struct timespec clock_value;
static int clock_result, clock_calls;
static int test_clock(struct timespec *value) {
  clock_calls++;
  *value = clock_value;
  errno = EIO;
  return clock_result;
}
#define DPE_SYNC_CLOCK(value) test_clock(value)
#include "../../integration/video/capture-sync.c"

static int expected_errno;
static int64_t observed_timeout;
static uintptr_t native_error;
static uintptr_t native_wait(int drm, int sync, uint64_t point, int64_t timeout) {
  assert(drm == 19 && sync == 37 && point == UINT64_C(0x123456789abcdef));
  assert(errno == expected_errno);
  observed_timeout = timeout;
  errno = ENOSPC;
  return native_error;
}
static void check(wait_fn function, int64_t input, int64_t expected) {
  errno = expected_errno = EAGAIN;
  assert(function(19, 37, UINT64_C(0x123456789abcdef), input) == native_error);
  assert(observed_timeout == expected);
  assert(errno == ENOSPC); // Preserve the native helper's errno and opaque return.
}
int main(void) {
  original_wait = native_wait;
  clock_value = (struct timespec){123456, 987654321};
  check(wait_available, INT64_C(5000000000), INT64_C(123461987654321));
  assert(atomic_load(&counts.corrected) == 1 && clock_calls == 1);
  clock_value = (struct timespec){0, 0};
  check(wait_available, INT64_C(5000000000), INT64_C(5000000000));

  // The largest representable deadline and a nanosecond beyond it saturate.
  const int64_t maximum_start = INT64_MAX - INT64_C(5000000000);
  clock_value = (struct timespec){maximum_start / 1000000000, maximum_start % 1000000000};
  check(wait_available, INT64_C(5000000000), INT64_MAX);
  clock_value.tv_nsec++;
  check(wait_available, INT64_C(5000000000), INT64_MAX);
  clock_value = (struct timespec){INT64_MAX, 0};
  check(wait_available, INT64_C(5000000000), INT64_MAX);

  int before = clock_calls;
  check(wait_available, 0, 0);
  check(wait_available, -1, -1);
  check(wait_available, INT64_MAX, INT64_MAX);
  check(wait_available, 123, 123);
  assert(clock_calls == before);

  clock_result = -1;
  native_error = UINT64_C(0xfedcba987654321); // Opaque anyhow pointer: never dereference/free it.
  check(wait_available, INT64_C(5000000000), INT64_C(5000000000));
  assert(atomic_load(&counts.clock_failures) == 1 && atomic_load(&counts.errors) == 1);
  clock_result = 0;
  clock_value = (struct timespec){-1, 0};
  check(wait_available, INT64_C(5000000000), INT64_C(5000000000));
  clock_value = (struct timespec){1, 1000000000};
  check(wait_available, INT64_C(5000000000), INT64_C(5000000000));
  clock_value = (struct timespec){1, -1};
  check(wait_available, INT64_C(5000000000), INT64_C(5000000000));
  assert(atomic_load(&counts.clock_failures) == 4);

  // Exercise the actual emitted tail jump with all four scalar arguments.
  uint8_t *trampoline =
      mmap(NULL, 4096, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
  assert(trampoline != MAP_FAILED);
  const uint8_t dispatch[] = {0xff, 0x25, 0, 0, 0, 0};
  uintptr_t function = (uintptr_t)wait_available;
  memcpy(trampoline, dispatch, sizeof(dispatch));
  memcpy(trampoline + sizeof(dispatch), &function, sizeof(function));
  assert(!mprotect(trampoline, 4096, PROT_READ | PROT_EXEC));
  clock_value = (struct timespec){123, 456};
  check((wait_fn)trampoline, INT64_C(5000000000), INT64_C(128000000456));
  assert(!munmap(trampoline, 4096));
  puts("Explicit-sync deadline, overflow, errno, opaque return and dispatch passed");
  return 0;
}
