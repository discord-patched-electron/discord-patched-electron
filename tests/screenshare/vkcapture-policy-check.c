// Links the patched upstream capture.c with a synthetic clock. It never calls
// socket setup, texture import, graphics APIs or application presentation.
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include "capture.h"
#include <assert.h>
#include <stdlib.h>
#include <time.h>
static uint64_t now;
int fixture_clock_gettime(clockid_t clock, struct timespec *value) {
  (void)clock;
  *value = (struct timespec){.tv_sec = now / 1000000000, .tv_nsec = now % 1000000000};
  return 0;
}
static unsigned copies(const char *setting) {
  if (setting)
    setenv("DPE_VKCAPTURE_MAX_FPS", setting, 1);
  else
    unsetenv("DPE_VKCAPTURE_MAX_FPS");
  capture_init();
  unsigned result = 0;
  for (unsigned i = 0; i < 240; i++) {
    now = i * 1000000000ull / 240;
    result += capture_frame_due();
  }
  return result;
}
int main(void) {
  assert(copies("60") == 60);
  assert(copies("30") == 30);
  assert(copies("240") == 240);
  assert(copies(NULL) == 240);
  for (const char **value =
           (const char *[]){"", "0", "-1", "241", "60fps", "999999999999999999999999", NULL};
       *value; value++)
    assert(copies(*value) == 240);
  assert(copies("60") == 60);
  capture_stop();
  assert(capture_frame_due());
  assert(!capture_frame_due());
  now += 3000000000ull;
  assert(capture_frame_due());
  assert(!capture_frame_due());
  return 0;
}
