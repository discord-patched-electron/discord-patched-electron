#include "../../integration/video/capture-rate.h"
#include <assert.h>
#include <stdio.h>
static unsigned count(unsigned source_fps, unsigned fps) {
  struct dpe_capture_rate rate;
  dpe_capture_rate_configure(&rate, fps);
  unsigned copies = 0;
  for (unsigned i = 0; i < source_fps; i++)
    copies += dpe_capture_rate_due(&rate, i * 1000000000ull / source_fps);
  return copies;
}
int main(void) {
  assert(count(240, 60) == 60);
  assert(count(240, 30) == 30);
  assert(count(60, 30) == 30);
  assert(count(30, 60) == 30);
  assert(count(240, 0) == 240);
  struct dpe_capture_rate rate;
  dpe_capture_rate_configure(&rate, 60);
  assert(dpe_capture_rate_due(&rate, 0));
  assert(!dpe_capture_rate_due(&rate, 0));
  assert(dpe_capture_rate_due(&rate, 3000000000ull));
  // Missing deadlines does not grant accumulated copies at the same timestamp.
  for (int i = 0; i < 100; i++)
    assert(!dpe_capture_rate_due(&rate, 3000000000ull));
  assert(dpe_capture_rate_due(&rate, 1)); // Rebase after a backwards clock jump.
  dpe_capture_rate_reset(&rate);
  assert(dpe_capture_rate_due(&rate, 1)); // New stream/resize captures immediately.
  assert(dpe_capture_rate_due(&rate, UINT64_MAX));
  assert(!dpe_capture_rate_due(&rate, UINT64_MAX));
  puts("Producer pacing: copy budgets, unlimited mode, slow sources, missed deadlines, reset and "
       "clock bounds passed");
  return 0;
}
