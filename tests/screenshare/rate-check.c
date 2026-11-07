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
static void jittered(unsigned fps) {
  struct dpe_capture_rate rate;
  dpe_capture_rate_configure(&rate, fps);
  const int jitter[] = {0, -400000, 200000, -300000, 350000};
  for (unsigned i = 0; i < fps * 20; ++i) {
    uint64_t now = 1000000000ull + (uint64_t)i * 1000000000ull / fps + jitter[i % 5];
    assert(dpe_capture_rate_due_with_slack(&rate, now));
    assert(!dpe_capture_rate_due_with_slack(&rate, now));
  }
}
static void bounded_slack(unsigned source, unsigned fps) {
  struct dpe_capture_rate rate;
  dpe_capture_rate_configure(&rate, fps);
  unsigned accepted = 0;
  for (unsigned i = 0; i < source * 20; ++i) {
    uint64_t now = (uint64_t)i * 1000000000ull / source;
    accepted += dpe_capture_rate_due_with_slack(&rate, now);
    // At most one admission can cross the measurement boundary. No missed
    // deadline creates a burst of accumulated frames at this timestamp.
    assert(accepted <= now / rate.interval + 2);
    assert(!dpe_capture_rate_due_with_slack(&rate, now));
  }
  assert(accepted <= fps * 20 + 1);
  assert(accepted >= (source < fps ? source : fps) * 20 - 1);
}
int main(void) {
  jittered(30);
  jittered(60);
  for (unsigned source = 30; source <= 600; source += 30) {
    bounded_slack(source, 30);
    bounded_slack(source, 60);
  }
  bounded_slack(4000, 60);
  bounded_slack(4000, 240);
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
  dpe_capture_rate_reset(&rate);
  assert(dpe_capture_rate_due_with_slack(&rate, UINT64_MAX - 100));
  assert(!dpe_capture_rate_due_with_slack(&rate, UINT64_MAX - 50));
  assert(dpe_capture_rate_due_with_slack(&rate, UINT64_MAX));
  assert(!dpe_capture_rate_due_with_slack(&rate, UINT64_MAX));
  puts("Producer pacing: copy budgets, unlimited mode, slow sources, missed deadlines, reset and "
       "clock bounds passed");
  return 0;
}
