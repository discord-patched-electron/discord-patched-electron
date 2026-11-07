// SPDX-License-Identifier: MIT
//! Pointer-free callback admission. Native staging leases remain in the bridge;
//! this model compares identities without reading or owning foreign storage.
#[derive(Clone, Copy, Default, Debug, PartialEq, Eq)]
pub struct Counts {
    pub callbacks: u64,
    pub fresh: u64,
    pub duplicate: u64,
    pub unknown: u64,
    pub forwarded: u64,
    pub dropped: u64,
    pub heartbeats: u64,
}
pub struct Delivery {
    pub counts: Counts,
    identity: Option<usize>,
    rate: u32,
    fast: bool,
    last_forward: u64,
    next_unknown: u64,
}
impl Delivery {
    pub fn new(rate: u32, fast: bool) -> Self {
        Self {
            counts: Counts::default(),
            identity: None,
            rate,
            fast: fast && rate > 0 && rate < 240,
            last_forward: 0,
            next_unknown: 0,
        }
    }
    pub fn configure(&mut self, rate: u32, fast: bool) {
        let fast = fast && rate > 0 && rate < 240;
        if self.rate != rate || self.fast != fast {
            self.next_unknown = 0;
        }
        self.rate = rate;
        self.fast = fast;
    }
    pub fn fast(&self) -> bool {
        self.fast
    }
    pub fn rate(&self) -> u32 {
        self.rate
    }
    pub fn tracking(&self) -> (Option<usize>, u64, u64) {
        (self.identity, self.last_forward, self.next_unknown)
    }
    /// Observe only successful nonempty native callbacks. `Some(identity)`
    /// requires a retained staging lease; acquire it before retiring the old
    /// lease so allocator address reuse cannot impersonate a duplicate frame.
    pub fn observe(&mut self, identity: Option<usize>, now: u64) -> bool {
        let identity = identity.filter(|&address| address != 0);
        let duplicate = identity.is_some() && identity == self.identity;
        self.counts.callbacks = self.counts.callbacks.saturating_add(1);
        if identity.is_some() {
            if duplicate {
                self.counts.duplicate = self.counts.duplicate.saturating_add(1);
            } else {
                self.counts.fresh = self.counts.fresh.saturating_add(1);
            }
            self.identity = identity;
        } else {
            self.counts.unknown = self.counts.unknown.saturating_add(1);
        }
        let mut forward = true;
        if self.fast && now != 0 {
            if duplicate && self.last_forward != 0 && now >= self.last_forward {
                forward = now - self.last_forward >= 500_000_000;
                self.counts.heartbeats = self.counts.heartbeats.saturating_add(u64::from(forward));
            } else if identity.is_none() {
                let period = 1_000_000_000 / u64::from(self.rate);
                if self.next_unknown == 0 || now < self.last_forward {
                    self.next_unknown = if self.last_forward != 0 && now >= self.last_forward {
                        self.last_forward.saturating_add(period)
                    } else {
                        now
                    };
                }
                forward = now >= self.next_unknown;
                if forward {
                    self.next_unknown = self.next_unknown.saturating_add(
                        ((now - self.next_unknown) / period + 1).saturating_mul(period),
                    );
                }
            }
        }
        if forward {
            self.counts.forwarded = self.counts.forwarded.saturating_add(1);
            self.last_forward = now;
            if identity.is_some() && self.fast && now != 0 {
                self.next_unknown = now.saturating_add(1_000_000_000 / u64::from(self.rate));
            }
        } else {
            self.counts.dropped = self.counts.dropped.saturating_add(1);
        }
        forward
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn identity_replacement_unknown_cadence_and_heartbeats_preserve_native_timing() {
        let mut policy = Delivery::new(60, true);
        assert!(policy.observe(Some(10), 1_000_000_000));
        assert!(!policy.observe(Some(10), 1_004_000_000));
        assert!(policy.observe(Some(11), 1_008_000_000));
        assert!(policy.observe(Some(11), 1_508_000_000));
        assert!(!policy.observe(None, 1_509_000_000));
        assert!(policy.observe(None, 1_525_000_000));
        assert_eq!(
            (
                policy.counts.fresh,
                policy.counts.duplicate,
                policy.counts.unknown,
                policy.counts.heartbeats
            ),
            (2, 2, 2, 1)
        );
        policy.configure(0, true);
        assert!(!policy.fast());
        assert!(policy.observe(None, 1_525_000_001));
        policy.configure(60, false);
        assert!(policy.observe(Some(11), 1_525_000_002));
        policy.configure(30, true);
        assert_eq!(policy.rate(), 30);
        assert!(policy.observe(Some(12), 2_000_000_000));
        assert!(policy.observe(None, 1)); // clock reversal resets the fallback phase
    }
    #[test]
    fn jittered_240hz_callbacks_do_not_turn_requested_60_into_48_or_repay_missed_slots() {
        for rate in [30, 60, 120] {
            let mut policy = Delivery::new(rate, true);
            let base = 10_000_000_000u64;
            for i in 0..2400u64 {
                let now = base + i * 1_000_000_000 / 240 + (i % 3) * 200_000;
                policy.observe(None, now);
                assert!(
                    policy.counts.forwarded <= (now - base) / (1_000_000_000 / u64::from(rate)) + 1
                );
            }
            assert_eq!(policy.counts.forwarded, u64::from(rate) * 10);
            let before = policy.counts.forwarded;
            let resumed = base + 12_000_000_000;
            for i in 0..32 {
                policy.observe(None, resumed + i);
            }
            assert_eq!(policy.counts.forwarded, before + 1);
        }
    }
}
