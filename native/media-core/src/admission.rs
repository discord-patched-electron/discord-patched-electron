// SPDX-License-Identifier: MIT
//! Closing waits for foreign callbacks without holding the resource mutex. A
//! callback may close its own session; that closes admission immediately and
//! cancels later callbacks instead of waiting for its own stack to unwind.
use std::{
    cell::RefCell,
    sync::{
        Arc, Condvar, Mutex, MutexGuard,
        atomic::{AtomicBool, Ordering},
    },
};
struct Stack {
    gates: [usize; 128],
    len: usize,
}
thread_local! {
    // No allocation, hashing or Thread object cloning in the per-frame path.
    static ACTIVE:RefCell<Stack>=const {RefCell::new(Stack{gates:[0;128],len:0})};
}
#[derive(Default)]
pub struct Admission {
    active: Mutex<usize>,
    closing: AtomicBool,
    idle: Condvar,
}
pub fn lock<T>(value: &Mutex<T>) -> MutexGuard<'_, T> {
    value
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}
pub struct Operation(Arc<Admission>, std::marker::PhantomData<std::rc::Rc<()>>);
impl Admission {
    pub fn enter(self: &Arc<Self>) -> Option<Operation> {
        ACTIVE.with(|stack| {
            let mut stack = stack.borrow_mut();
            if stack.len == stack.gates.len() {
                return None;
            }
            let mut active = lock(&self.active);
            if !self.open() || *active >= 128 {
                return None;
            }
            let index = stack.len;
            // This address is identity only, never dereferenced. Operation's
            // Arc pins its allocation until the matching entry is removed.
            stack.gates[index] = Arc::as_ptr(self) as usize;
            stack.len += 1;
            *active += 1;
            Some(Operation(self.clone(), std::marker::PhantomData))
        })
    }
    pub fn open(&self) -> bool {
        !self.closing.load(Ordering::Acquire)
    }
    /// Returns false for reentrant closure. Its caller must then cancel all
    /// remaining notifications: callback context may be freed on return.
    pub fn close(&self) -> bool {
        let current = ACTIVE.with(|stack| {
            let stack = stack.borrow();
            stack.gates[..stack.len]
                .iter()
                .filter(|&&gate| gate == ptr_identity(self))
                .count()
        });
        let mut active = lock(&self.active);
        self.closing.store(true, Ordering::Release);
        while *active > current {
            active = self
                .idle
                .wait(active)
                .unwrap_or_else(std::sync::PoisonError::into_inner);
        }
        current == 0
    }
}
impl Drop for Operation {
    fn drop(&mut self) {
        ACTIVE.with(|stack| {
            let mut stack = stack.borrow_mut();
            let len = stack.len;
            let index = stack.gates[..len]
                .iter()
                .rposition(|&gate| gate == ptr_identity(&self.0))
                .expect("operation owns admission");
            stack.gates.copy_within(index + 1..len, index);
            stack.len -= 1;
            stack.gates[len - 1] = 0;
        });
        let mut active = lock(&self.0.active);
        *active -= 1;
        // No futex wake syscall unless a closer could actually be waiting.
        if self.0.closing.load(Ordering::Relaxed) {
            self.0.idle.notify_all();
        }
    }
}
fn ptr_identity(gate: &Admission) -> usize {
    gate as *const Admission as usize
}
#[cfg(test)]
mod tests {
    use super::*;
    use std::{sync::mpsc, thread, time::Duration};
    #[test]
    fn independent_and_out_of_order_admissions_keep_exact_reentrant_depth() {
        let first = Arc::new(Admission::default());
        let second = Arc::new(Admission::default());
        let a = first.enter().unwrap();
        let b = second.enter().unwrap();
        let c = first.enter().unwrap();
        drop(a);
        assert!(!second.close());
        assert!(!first.close());
        drop(b);
        assert!(second.close());
        assert!(!first.close());
        drop(c);
        assert!(first.close());
    }
    #[test]
    fn reentrant_close_cancels_admission_without_waiting_for_itself() {
        let gate = Arc::new(Admission::default());
        let first = gate.enter().unwrap();
        let nested = gate.enter().unwrap();
        assert!(!gate.close());
        assert!(gate.enter().is_none());
        assert!(!gate.open());
        drop(nested);
        drop(first);
        assert!(gate.close());
    }
    #[test]
    fn close_waits_until_foreign_callback_context_is_no_longer_used() {
        let gate = Arc::new(Admission::default());
        let (ready, entered) = mpsc::channel();
        let (release, released) = mpsc::channel();
        let worker_gate = gate.clone();
        let worker = thread::spawn(move || {
            let _operation = worker_gate.enter().unwrap();
            ready.send(()).unwrap();
            released.recv().unwrap();
        });
        entered.recv().unwrap();
        let (closed, completion) = mpsc::channel();
        let closer_gate = gate.clone();
        let closer = thread::spawn(move || closed.send(closer_gate.close()).unwrap());
        assert!(completion.recv_timeout(Duration::from_millis(20)).is_err());
        release.send(()).unwrap();
        assert!(completion.recv_timeout(Duration::from_secs(2)).unwrap());
        worker.join().unwrap();
        closer.join().unwrap();
        assert!(gate.enter().is_none());
    }
}
