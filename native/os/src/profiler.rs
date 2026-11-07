// SPDX-License-Identifier: MIT
//! Opt-in aggregate CPU/wall timings. No frame contents or source identities.
use std::sync::{
    OnceLock,
    atomic::{AtomicU64, Ordering},
};
static ENABLED: OnceLock<bool> = OnceLock::new();
const STAGES: usize = 32;
struct Counters {
    count: AtomicU64,
    wall: AtomicU64,
    cpu: AtomicU64,
    max_wall: AtomicU64,
}
static COUNTERS: [Counters; STAGES] = [const {
    Counters {
        count: AtomicU64::new(0),
        wall: AtomicU64::new(0),
        cpu: AtomicU64::new(0),
        max_wall: AtomicU64::new(0),
    }
}; STAGES];
pub struct Span(u32, Stamp);
impl Span {
    pub fn new(stage: u32) -> Self {
        Self(stage, dpe_profile_begin(stage))
    }
}
impl Drop for Span {
    fn drop(&mut self) {
        dpe_profile_end(self.0, self.1);
    }
}
#[derive(Default, Clone, Copy)]
#[repr(C)]
pub struct Stamp {
    wall: u64,
    cpu: u64,
}
fn clock(id: libc::clockid_t) -> u64 {
    let mut t = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    // SAFETY: valid initialized timespec output for these fixed Linux clocks.
    if unsafe { libc::clock_gettime(id, &mut t) } != 0 {
        return 0;
    }
    (t.tv_sec as u64)
        .saturating_mul(1_000_000_000)
        .saturating_add(t.tv_nsec as u64)
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_profile_begin(stage: u32) -> Stamp {
    if stage as usize >= STAGES
        || !*ENABLED
            .get_or_init(|| std::env::var_os("DPE_CAPTURE_PROFILE").is_some_and(|s| s == "1"))
    {
        return Stamp::default();
    }
    Stamp {
        wall: clock(libc::CLOCK_MONOTONIC),
        cpu: clock(libc::CLOCK_THREAD_CPUTIME_ID),
    }
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_profile_end(stage: u32, start: Stamp) {
    if start.wall == 0 {
        return;
    }
    let Some(c) = COUNTERS.get(stage as usize) else {
        return;
    };
    let wall = clock(libc::CLOCK_MONOTONIC).saturating_sub(start.wall);
    c.count.fetch_add(1, Ordering::Relaxed);
    c.wall.fetch_add(wall, Ordering::Relaxed);
    c.cpu.fetch_add(
        clock(libc::CLOCK_THREAD_CPUTIME_ID).saturating_sub(start.cpu),
        Ordering::Relaxed,
    );
    c.max_wall.fetch_max(wall, Ordering::Relaxed);
}
/// # Safety
/// `output` points to four writable uint64 values.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_profile_stage(stage: u32, output: *mut u64) -> bool {
    let Some(c) = COUNTERS.get(stage as usize) else {
        return false;
    };
    if output.is_null() {
        return false;
    }
    let values = [
        c.count.load(Ordering::Relaxed),
        c.wall.load(Ordering::Relaxed),
        c.cpu.load(Ordering::Relaxed),
        c.max_wall.load(Ordering::Relaxed),
    ];
    // SAFETY: caller owns the output array; the local source has four initialized elements.
    unsafe {
        std::ptr::copy_nonoverlapping(values.as_ptr(), output, 4);
    }
    true
}
