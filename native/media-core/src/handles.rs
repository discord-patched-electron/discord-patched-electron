// SPDX-License-Identifier: MIT
//! Bounded ownership tables for opaque native handles. Tokens are never reused,
//! including between different tables. Destructors run after releasing the lock.
use std::{
    collections::HashMap,
    num::NonZeroUsize,
    sync::{
        Mutex, MutexGuard,
        atomic::{AtomicUsize, Ordering},
    },
};
static NEXT: AtomicUsize = AtomicUsize::new(1);
const SERIAL_MASK: usize = usize::MAX >> 16;
const FAMILY: usize = 0x50e0usize << (usize::BITS - 16);
/// Noncanonical x86-64 pointer namespaces, used only by opaque C pointer APIs.
/// Numeric JS IDs and codec timestamps keep their ordinary scalar tokens.
#[derive(Clone, Copy)]
pub enum PointerKind {
    VideoFrame = 1,
    Capture = 2,
    Encoder = 3,
    Planar = 4,
    EncoderAdapter = 5,
}
impl PointerKind {
    pub fn token(self, serial: usize) -> Option<NonZeroUsize> {
        if serial == 0 || serial > SERIAL_MASK {
            return None;
        }
        NonZeroUsize::new(FAMILY | ((self as usize) << (usize::BITS - 16)) | serial)
    }
    pub fn maximum_serial() -> usize {
        SERIAL_MASK
    }
}
/// Recognize retired/invalid DPE pointers before forwarding foreign objects to
/// the proprietary runtime. Recognition alone never grants object ownership.
pub fn pointer_reserved(value: usize) -> bool {
    value & (0xfff0usize << (usize::BITS - 16)) == FAMILY
}
pub struct Handles<T> {
    entries: Mutex<HashMap<NonZeroUsize, T>>,
    maximum: usize,
    pointer: Option<PointerKind>,
}
impl<T> Handles<T> {
    pub fn new(maximum: usize) -> Self {
        Self {
            entries: Mutex::new(HashMap::new()),
            maximum,
            pointer: None,
        }
    }
    pub fn pointers(maximum: usize, kind: PointerKind) -> Self {
        Self {
            pointer: Some(kind),
            ..Self::new(maximum)
        }
    }
    fn lock(&self) -> MutexGuard<'_, HashMap<NonZeroUsize, T>> {
        self.entries
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
    }
    pub fn insert(&self, value: T) -> Result<NonZeroUsize, T> {
        let mut entries = self.lock();
        if entries.len() >= self.maximum {
            return Err(value);
        }
        let Ok(token) = NEXT.try_update(Ordering::Relaxed, Ordering::Relaxed, |value| {
            if value > SERIAL_MASK {
                None
            } else {
                value.checked_add(1)
            }
        }) else {
            return Err(value);
        };
        let Some(token) = self
            .pointer
            .map_or_else(|| NonZeroUsize::new(token), |kind| kind.token(token))
        else {
            return Err(value);
        };
        entries.insert(token, value);
        Ok(token)
    }
    pub fn get(&self, token: usize) -> Option<T>
    where
        T: Clone,
    {
        self.lock().get(&NonZeroUsize::new(token)?).cloned()
    }
    pub fn contains(&self, token: usize) -> bool {
        NonZeroUsize::new(token).is_some_and(|token| self.lock().contains_key(&token))
    }
    /// Borrow an owned record without making it clonable. The callback must not
    /// reenter this table; externally visible destructors run after removal.
    pub fn with_mut<R>(&self, token: usize, access: impl FnOnce(&mut T) -> R) -> Option<R> {
        self.lock().get_mut(&NonZeroUsize::new(token)?).map(access)
    }
    pub fn remove(&self, token: usize) -> Option<T> {
        self.lock().remove(&NonZeroUsize::new(token)?)
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;
    #[test]
    fn pointer_namespaces_remain_recognizable_after_retirement_without_reusing_scalar_ids() {
        let frames = Handles::pointers(1, PointerKind::VideoFrame);
        let captures = Handles::pointers(1, PointerKind::Capture);
        let numbers = Handles::new(1);
        let frame = frames.insert(1).unwrap();
        let capture = captures.insert(2).unwrap();
        let number = numbers.insert(3).unwrap();
        assert!(pointer_reserved(frame.get()) && pointer_reserved(capture.get()));
        assert!(!pointer_reserved(number.get()) && !pointer_reserved(0));
        assert!(!frames.contains(capture.get()));
        assert!(!captures.contains(frame.get()));
        let _ = frames.remove(frame.get());
        assert!(!frames.contains(frame.get()) && pointer_reserved(frame.get()));
        assert!(PointerKind::Encoder.token(0).is_none());
        assert!(PointerKind::Planar.token(SERIAL_MASK + 1).is_none());
    }
    #[test]
    fn bounds_foreign_tokens_and_stale_handles_preserve_ownership() {
        let first = Handles::new(1);
        let second = Handles::new(1);
        let owner = Arc::new(());
        let token = first.insert(owner.clone()).unwrap();
        assert!(first.insert(owner.clone()).is_err());
        assert_eq!(Arc::strong_count(&owner), 2);
        let other = second.insert(owner.clone()).unwrap();
        assert_ne!(token, other);
        assert!(!first.contains(other.get()));
        assert!(!second.contains(token.get()));
        drop(first.get(token.get()));
        drop(first.remove(token.get()));
        assert!(!first.contains(token.get()));
        assert!(!first.contains(0));
        let fresh = first.insert(owner.clone()).unwrap();
        assert_ne!(fresh, token);
        drop(first.remove(fresh.get()));
        drop(second.remove(other.get()));
        assert_eq!(Arc::strong_count(&owner), 1);
    }
    #[test]
    fn dropping_a_removed_owner_can_reenter_the_table() {
        struct Owner(Arc<Handles<Owner>>);
        impl Drop for Owner {
            fn drop(&mut self) {
                assert!(!self.0.contains(0));
                drop(self.0.lock());
            }
        }
        let table = Arc::new(Handles::new(1));
        let token = table.insert(Owner(table.clone())).ok().unwrap();
        drop(table.remove(token.get()));
        assert_eq!(Arc::strong_count(&table), 1);
    }
}
