// SPDX-License-Identifier: MIT
//! Limit background work's competition with capture and rendering. Linux nice
//! values apply to individual threads; this never changes another process,
//! realtime policy, affinity, cgroup or an inherited lower priority.
use std::io;
thread_local! { static APPLIED: std::cell::Cell<bool> = const { std::cell::Cell::new(false) }; }
pub fn current_nice() -> io::Result<i32> {
    // SAFETY: errno is thread-local; getpriority with ID zero reads only this
    // calling Linux thread. Its legitimate -1 result needs a cleared errno.
    unsafe {
        *libc::__errno_location() = 0;
        let value = libc::getpriority(libc::PRIO_PROCESS, 0);
        if value == -1 && *libc::__errno_location() != 0 {
            Err(io::Error::last_os_error())
        } else {
            Ok(value)
        }
    }
}
pub fn background_current_thread() -> io::Result<i32> {
    let previous = current_nice()?;
    if APPLIED.get() {
        return Ok(previous);
    }
    if previous < 5 {
        let target = (previous + 5).min(5);
        // SAFETY: ID zero targets only the caller. Increasing niceness lowers
        // priority and needs no elevated scheduling permissions on regular Linux.
        if unsafe { libc::setpriority(libc::PRIO_PROCESS, 0, target) } != 0 {
            return Err(io::Error::last_os_error());
        }
    }
    APPLIED.set(true);
    current_nice()
}
/// Best-effort fair scheduling only. The caller must opt in; no privilege,
/// resource limit, policy, affinity or priority of another thread is changed.
pub fn balanced_current_thread() -> io::Result<i32> {
    let previous = current_nice()?;
    let mut limit = libc::rlimit {
        rlim_cur: 0,
        rlim_max: 0,
    };
    // SAFETY: initialized writable resource-limit output, queried without mutation.
    if unsafe { libc::getrlimit(libc::RLIMIT_NICE, &mut limit) } != 0 {
        return Err(io::Error::last_os_error());
    }
    let target = dpe_core::adaptation::capture_nice(previous, limit.rlim_cur);
    if target != previous {
        // SAFETY: ID zero changes only the caller, within its existing user
        // resource limit. Ordinary CFS/EEVDF scheduling policy is retained.
        if unsafe { libc::setpriority(libc::PRIO_PROCESS, 0, target) } != 0 {
            let error = io::Error::last_os_error();
            return if matches!(error.raw_os_error(), Some(libc::EPERM | libc::EACCES)) {
                Ok(previous)
            } else {
                Err(error)
            };
        }
    }
    current_nice()
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn capture_priority_changes_only_the_new_opted_in_thread() {
        let original = current_nice().unwrap();
        std::thread::spawn(|| {
            // SAFETY: lower only this newly created test thread to ordinary
            // priority, so the permitted-upgrade path is exercised when the
            // host grants it. This never alters the test runner's priority.
            assert_eq!(unsafe { libc::setpriority(libc::PRIO_PROCESS, 0, 0) }, 0);
            let previous = current_nice().unwrap();
            let current = balanced_current_thread().unwrap();
            assert!(current == previous || current == -2);
            assert_eq!(balanced_current_thread().unwrap(), current);
        })
        .join()
        .unwrap();
        assert_eq!(current_nice().unwrap(), original);
    }
    #[test]
    fn only_the_new_background_thread_changes_priority() {
        let original = current_nice().unwrap();
        let worker = std::thread::spawn(|| {
            let inherited = current_nice().unwrap();
            let result = background_current_thread().unwrap();
            assert_eq!(
                result,
                if inherited < 5 {
                    (inherited + 5).min(5)
                } else {
                    inherited
                }
            );
            assert_eq!(background_current_thread().unwrap(), result);
        });
        worker.join().unwrap();
        assert_eq!(current_nice().unwrap(), original);
    }
}
