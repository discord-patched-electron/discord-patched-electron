// MIT-licensed producer pacing. A skipped capture never sleeps or limits the
// application's presentation. Deadlines retain phase and never build a queue.
#ifndef DPE_CAPTURE_RATE_H
#define DPE_CAPTURE_RATE_H
#include <stdbool.h>
#include <stdint.h>
struct dpe_capture_rate {
  uint64_t interval, next, last;
  bool started;
};
static inline void dpe_capture_rate_configure(struct dpe_capture_rate *rate, unsigned fps) {
  *rate = (struct dpe_capture_rate){.interval = fps ? 1000000000ull / fps : 0};
}
static inline void dpe_capture_rate_reset(struct dpe_capture_rate *rate) { rate->started = false; }
static inline bool dpe_capture_rate_due(struct dpe_capture_rate *rate, uint64_t now) {
  if (!rate->interval)
    return true;
  uint64_t delay = rate->interval;
  if (rate->started && now >= rate->last) {
    bool due = now != rate->last && now >= rate->next;
    rate->last = now;
    if (!due)
      return false;
    delay -= (now - rate->next) % rate->interval;
  }
  rate->started = true;
  rate->last = now;
  rate->next = UINT64_MAX - now < delay ? UINT64_MAX : now + delay;
  return true;
}
#endif
