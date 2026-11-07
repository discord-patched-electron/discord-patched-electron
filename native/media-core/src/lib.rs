// SPDX-License-Identifier: MIT
#![forbid(unsafe_code)]
pub mod adaptation;
pub mod admission;
pub mod av1_crop;
pub mod control;
pub mod cursor;
pub mod delivery;
pub mod direct;
pub mod geometry;
pub mod handles;
pub mod levels;

use std::sync::{Condvar, Mutex, MutexGuard};
use std::time::{Duration, Instant};

pub enum Next<T> {
    Item(T),
    TimedOut,
    Closed,
}

/// A bounded handoff, with ownership of superseded items returned to the caller.
/// Destructors never execute while the queue lock is held.
pub struct Latest<T> {
    state: Mutex<(bool, Option<T>)>,
    changed: Condvar,
}
impl<T> Default for Latest<T> {
    fn default() -> Self {
        Self {
            state: Mutex::new((false, None)),
            changed: Condvar::new(),
        }
    }
}
impl<T> Latest<T> {
    fn lock(&self) -> MutexGuard<'_, (bool, Option<T>)> {
        self.state
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
    }
    pub fn replace(&self, value: T) -> Result<Option<T>, T> {
        let old = {
            let mut state = self.lock();
            if state.0 {
                return Err(value);
            }
            state.1.replace(value)
        };
        self.changed.notify_one();
        Ok(old)
    }
    pub fn receive(&self) -> Option<T> {
        let mut state = self.lock();
        loop {
            if state.0 {
                return None;
            }
            if let Some(value) = state.1.take() {
                return Some(value);
            }
            state = self
                .changed
                .wait(state)
                .unwrap_or_else(std::sync::PoisonError::into_inner);
        }
    }
    pub fn receive_timeout(&self, timeout: Duration) -> Next<T> {
        let began = Instant::now();
        let mut state = self.lock();
        loop {
            if state.0 {
                return Next::Closed;
            }
            if let Some(value) = state.1.take() {
                return Next::Item(value);
            }
            let Some(remaining) = timeout.checked_sub(began.elapsed()) else {
                return Next::TimedOut;
            };
            let (next, _) = self
                .changed
                .wait_timeout(state, remaining)
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            state = next;
        }
    }
    pub fn close(&self) -> Option<T> {
        let pending = {
            let mut state = self.lock();
            state.0 = true;
            state.1.take()
        };
        self.changed.notify_all();
        pending
    }
}

/// Even, bounded preview geometry. Full-resolution dimensions remain untouched.
pub fn preview_size(width: u32, height: u32, bound: u32) -> Option<(u32, u32)> {
    if !(2..=8192).contains(&width) || !(2..=8192).contains(&height) || !(2..=1024).contains(&bound)
    {
        return None;
    }
    let denominator = width.max(height).max(bound);
    let w = ((u64::from(width) * u64::from(bound) / u64::from(denominator)) as u32 & !1).max(2);
    let h = ((u64::from(height) * u64::from(bound) / u64::from(denominator)) as u32 & !1).max(2);
    Some((w, h))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;
    #[test]
    fn bounded_replacement_and_close_release_ownership() {
        let queue = Latest::default();
        let value = Arc::new(());
        assert!(queue.replace(value.clone()).unwrap().is_none());
        let old = queue.replace(value.clone()).unwrap().unwrap();
        assert_eq!(Arc::strong_count(&value), 3);
        drop(old);
        drop(queue.close());
        assert_eq!(Arc::strong_count(&value), 1);
        assert!(queue.receive().is_none());
        assert!(queue.replace(value.clone()).is_err());
        assert_eq!(Arc::strong_count(&value), 1);
    }
    #[test]
    fn close_wakes_a_blocked_receiver() {
        let queue = Arc::new(Latest::<u32>::default());
        let other = queue.clone();
        let worker = std::thread::spawn(move || other.receive());
        let _ = queue.close();
        assert_eq!(worker.join().unwrap(), None);
    }
    #[test]
    fn timeout_preserves_future_delivery_and_closed_state() {
        let queue = Latest::default();
        assert!(matches!(
            queue.receive_timeout(Duration::ZERO),
            Next::TimedOut
        ));
        assert!(queue.replace(7).is_ok());
        assert!(matches!(
            queue.receive_timeout(Duration::ZERO),
            Next::Item(7)
        ));
        let _ = queue.close();
        assert!(matches!(
            queue.receive_timeout(Duration::ZERO),
            Next::Closed
        ));
    }
    #[test]
    fn preview_geometry_is_even_bounded_and_not_upscaled() {
        assert_eq!(preview_size(1920, 1080, 320), Some((320, 180)));
        assert_eq!(preview_size(1080, 1920, 320), Some((180, 320)));
        assert_eq!(preview_size(944, 1018, 320), Some((296, 320)));
        assert_eq!(preview_size(64, 48, 320), Some((64, 48)));
        assert_eq!(preview_size(0, 1, 320), None);
        assert_eq!(preview_size(u32::MAX, 1, 320), None);
    }
}

pub mod pacing;
