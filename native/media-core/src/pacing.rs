// SPDX-License-Identifier: MIT
//! Frame-driven admission keeps phase without sleeping, queuing frame credit
//! or limiting the application's own presents. Clock reversal starts a phase.
#[derive(Clone, Copy)]
pub struct Pacer {
    interval: u64,
    phase: Option<(u64, u64)>, // Last observation and next deadline.
}
impl Pacer {
    pub fn new(fps: u32) -> Option<Self> {
        (1..=240).contains(&fps).then(|| Self {
            interval: 1_000_000_000 / u64::from(fps),
            phase: None,
        })
    }
    pub fn admit(&mut self, now: u64) -> bool {
        let next = if let Some((last, deadline)) = self.phase {
            if now >= last {
                if now == last {
                    return false;
                }
                if now < deadline {
                    let slack = (self.interval / 8).min(1_000_000);
                    if deadline == u64::MAX || deadline - now > slack {
                        self.phase = Some((now, deadline));
                        return false;
                    }
                    deadline.saturating_add(self.interval)
                } else {
                    now.saturating_add(self.interval - (now - deadline) % self.interval)
                }
            } else {
                now.saturating_add(self.interval)
            }
        } else {
            now.saturating_add(self.interval)
        };
        self.phase = Some((now, next));
        true
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn jittered_presents_preserve_rate_without_credit_after_a_stall() {
        assert!(Pacer::new(0).is_none());
        assert!(Pacer::new(241).is_none());
        let mut rate = Pacer::new(60).unwrap();
        let count = (0..2400u64)
            .filter(|n| rate.admit(n * 1_000_000_000 / 240))
            .count();
        assert_eq!(count, 600);
        assert!(rate.admit(20_000_000_000));
        assert!(!rate.admit(20_000_000_001));
        assert!(!rate.admit(20_000_000_001));
        assert!(rate.admit(21_000_000_000));
    }
    #[test]
    fn early_admission_clock_reversal_and_saturated_deadlines_are_bounded() {
        let mut rate = Pacer::new(60).unwrap();
        assert!(rate.admit(0));
        assert!(!rate.admit(15_000_000));
        assert!(rate.admit(16_000_000));
        assert!(!rate.admit(31_000_000));
        assert!(rate.admit(33_000_000));
        assert!(rate.admit(1));
        assert!(!rate.admit(1));
        assert!(rate.admit(u64::MAX));
        assert!(!rate.admit(u64::MAX));
    }
}
