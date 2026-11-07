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
// Admit up to 1 ms early for frame-driven sources at the requested rate.
// Preserve the original phase: this shifts admission, never builds credit.
// Strict users (including stock hook compatibility) keep dpe_capture_rate_due.
static inline bool dpe_capture_rate_due_with_slack(struct dpe_capture_rate *rate, uint64_t now) {
  uint64_t slack = rate->interval / 8;
  if (slack > 1000000)
    slack = 1000000;
  if (rate->started && rate->next != UINT64_MAX && now > rate->last && now < rate->next &&
      rate->next - now <= slack) {
    rate->last = now;
    rate->next =
        UINT64_MAX - rate->next < rate->interval ? UINT64_MAX : rate->next + rate->interval;
    return true;
  }
  return dpe_capture_rate_due(rate, now);
}
#endif
