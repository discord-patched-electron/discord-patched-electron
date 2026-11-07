// SPDX-License-Identifier: MIT
use crate::protocol::*;
use dpe_core::pacing::Pacer;
use dpe_os::wire;
use std::{
    ffi::CString,
    io::Read,
    os::{
        fd::{AsFd, AsRawFd, BorrowedFd, FromRawFd, OwnedFd},
        unix::{ffi::OsStrExt, fs::MetadataExt},
    },
    sync::{
        Mutex, MutexGuard, OnceLock,
        atomic::{AtomicBool, Ordering},
    },
};
static PRESENTING: AtomicBool = AtomicBool::new(false);
struct Lease {
    sequence: u64,
    pts: u64,
    published: bool,
}
#[derive(Default)]
struct Profile {
    due: u64,
    held: u64,
    paced: u64,
    published: u64,
    acknowledged: u64,
    cancelled: u64,
    lease_ns: u64,
    max_lease_ns: u64,
}
struct Hook {
    enabled: bool,
    path: Option<CString>,
    socket: Option<OwnedFd>,
    accepted: bool,
    active: bool,
    paused: bool,
    direct: bool,
    generation: u64,
    sequence: u64,
    next_connect: u64,
    uuid: [u8; 16],
    identity: [u8; 16],
    lease: Option<Lease>,
    retired: Option<(u64, u64)>,
    pacer: Pacer,
    profile: Option<Profile>,
}
impl Default for Hook {
    fn default() -> Self {
        Self {
            enabled: false,
            path: None,
            socket: None,
            accepted: false,
            active: false,
            paused: false,
            direct: false,
            generation: 0,
            sequence: 0,
            next_connect: 0,
            uuid: [0; 16],
            identity: [0; 16],
            lease: None,
            retired: None,
            pacer: Pacer::new(60).expect("valid default FPS"),
            profile: None,
        }
    }
}
fn state() -> MutexGuard<'static, Hook> {
    static HOOK: OnceLock<Mutex<Hook>> = OnceLock::new();
    HOOK.get_or_init(|| Mutex::new(Hook::default()))
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}
fn now_ns() -> u64 {
    let mut value = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    // SAFETY: fixed monotonic clock and initialized, correctly sized output.
    if unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut value) } != 0 {
        return 0;
    }
    (value.tv_sec as u64)
        .saturating_mul(1_000_000_000)
        .saturating_add(value.tv_nsec as u64)
}
impl Hook {
    fn disconnect(&mut self) {
        self.socket = None;
        self.accepted = false;
        self.paused = false;
        self.lease = None;
        // Graphics ownership survives until its owner explicitly retires it.
    }
    fn send(&mut self, packet: &Packet, descriptors: &[BorrowedFd<'_>]) -> bool {
        if self.socket.as_ref().is_some_and(|socket| {
            wire::send_raw(socket.as_fd(), &packet.bytes(), descriptors).is_ok()
        }) {
            true
        } else {
            self.disconnect();
            false
        }
    }
    fn connect(&mut self, now: u64) {
        if self.socket.is_some() || self.active || now < self.next_connect {
            return;
        }
        self.next_connect = now.saturating_add(1_000_000_000);
        let Some(path) = &self.path else {
            return;
        };
        let path_os = std::ffi::OsStr::from_bytes(path.to_bytes());
        let Ok(metadata) = std::fs::symlink_metadata(path_os) else {
            return;
        };
        // SAFETY: geteuid only reads the current process identity.
        let uid = unsafe { libc::geteuid() };
        if metadata.mode() & libc::S_IFMT != libc::S_IFSOCK
            || metadata.uid() != uid
            || metadata.mode() & 0o077 != 0
        {
            return;
        }
        // SAFETY: creates one unique nonblocking, close-on-exec socket.
        let raw = unsafe {
            libc::socket(
                libc::AF_UNIX,
                libc::SOCK_SEQPACKET | libc::SOCK_CLOEXEC | libc::SOCK_NONBLOCK,
                0,
            )
        };
        if raw < 0 {
            return;
        }
        // SAFETY: successful socket returned unique descriptor ownership.
        let socket = unsafe { OwnedFd::from_raw_fd(raw) };
        // SAFETY: integer/byte-only address admits zero initialization.
        let mut address: libc::sockaddr_un = unsafe { std::mem::zeroed() };
        address.sun_family = libc::AF_UNIX as libc::sa_family_t;
        for (to, &from) in address.sun_path.iter_mut().zip(path.to_bytes()) {
            *to = from as libc::c_char;
        }
        // SAFETY: initialized complete Unix address, bounded NUL-terminated
        // path checked at init. The owned fd remains live during both calls.
        if unsafe {
            libc::connect(
                socket.as_raw_fd(),
                (&raw const address).cast(),
                size_of_val(&address) as libc::socklen_t,
            )
        } != 0
            || !wire::dpe_direct_peer(socket.as_raw_fd())
        {
            return;
        }
        self.socket = Some(socket);
        let mut hello = Packet::new(HELLO);
        hello.flags = DIRECT | PAUSE;
        hello.uuid = self.identity;
        if let Ok(mut comm) = std::fs::File::open("/proc/self/comm")
            && let Ok(count) = comm.read(&mut hello.name[..15])
            && count > 0
            && hello.name[count - 1] == b'\n'
        {
            hello.name[count - 1] = 0;
        }
        self.send(&hello, &[]);
    }
    fn update(&mut self) {
        if !self.enabled || self.path.is_none() {
            return;
        }
        let now = now_ns();
        self.connect(now);
        for _ in 0..4 {
            let Some(socket) = &self.socket else {
                break;
            };
            let mut bytes = [0u8; 128];
            match wire::receive(socket.as_fd(), &mut bytes, 0) {
                Ok(None) => break,
                Ok(Some(_)) => {}
                Err(_) => {
                    self.disconnect();
                    break;
                }
            }
            let Some(packet) = Packet::parse(&bytes) else {
                self.disconnect();
                break;
            };
            if packet.kind == CONTROL {
                if packet.fps > 240
                    || (packet.fps == 0 && packet.flags & CAPTURE == 0)
                    || packet.flags & !(CAPTURE | DIRECT) != 0
                    || packet.nfd != 0
                {
                    self.disconnect();
                    break;
                }
                let direct = packet.flags & DIRECT != 0;
                if self.active
                    && (packet.uuid != self.uuid
                        || (packet.flags & CAPTURE != 0 && direct != self.direct))
                {
                    self.disconnect();
                    break;
                }
                self.uuid = packet.uuid;
                self.accepted = packet.flags & CAPTURE != 0;
                self.direct = direct;
                self.paused = packet.fps == 0;
                if !self.paused {
                    self.pacer = Pacer::new(packet.fps).expect("validated peer FPS");
                }
            } else if packet.kind == RELEASE
                && self.retired == Some((packet.generation, packet.sequence))
            {
                self.retired = None;
            } else if packet.kind == RELEASE
                && packet.generation == self.generation
                && self
                    .lease
                    .as_ref()
                    .is_some_and(|lease| lease.published && lease.sequence == packet.sequence)
            {
                if let Some(profile) = &mut self.profile {
                    let elapsed =
                        now.saturating_sub(self.lease.as_ref().expect("checked lease").pts);
                    profile.acknowledged += 1;
                    profile.lease_ns = profile.lease_ns.saturating_add(elapsed);
                    profile.max_lease_ns = profile.max_lease_ns.max(elapsed);
                }
                self.lease = None;
            } else {
                self.disconnect();
                break;
            }
        }
    }
    fn due(&mut self) -> bool {
        if !self.active || !self.accepted || self.paused {
            return false;
        }
        if self.lease.is_some() {
            if let Some(profile) = &mut self.profile {
                profile.held += 1;
            }
            return false;
        }
        let now = now_ns();
        if !self.pacer.admit(now) {
            if let Some(profile) = &mut self.profile {
                profile.paced += 1;
            }
            return false;
        }
        let Some(sequence) = self.sequence.checked_add(1) else {
            self.disconnect();
            return false;
        };
        self.sequence = sequence;
        self.lease = Some(Lease {
            sequence,
            pts: now,
            published: false,
        });
        if let Some(profile) = &mut self.profile {
            profile.due += 1;
        }
        true
    }
    fn complete(&mut self, fence: Option<BorrowedFd<'_>>) -> bool {
        let Some(lease) = &self.lease else {
            return false;
        };
        if lease.published || !self.active || self.socket.is_none() {
            return false;
        }
        let mut packet = Packet::new(FRAME);
        packet.generation = self.generation;
        packet.sequence = lease.sequence;
        packet.pts = lease.pts;
        let mut descriptors = [].as_slice();
        let fenced = [fence];
        let borrowed;
        if let Some(fd) = fenced[0] {
            packet.flags = SYNC_FD;
            packet.nfd = 1;
            borrowed = [fd];
            descriptors = &borrowed;
        }
        let sent = self.send(&packet, descriptors);
        if sent {
            self.lease
                .as_mut()
                .expect("successful send retains lease")
                .published = true;
            if let Some(profile) = &mut self.profile {
                profile.published += 1;
            }
        }
        sent
    }
    fn stop(&mut self) {
        if let Some(lease) = &self.lease
            && lease.published
        {
            self.retired = Some((self.generation, lease.sequence));
        }
        if self.active && self.socket.is_some() {
            let mut packet = Packet::new(STOP);
            packet.generation = self.generation;
            self.send(&packet, &[]);
        }
        self.active = false;
        self.lease = None;
        if let Some(profile) = &self.profile {
            eprintln!(
                "DPE game profile {{\"admitted\":{},\"heldPresents\":{},\"pacedPresents\":{},\"published\":{},\"acknowledged\":{},\"cancelled\":{},\"leaseNs\":{},\"maxLeaseNs\":{}}}",
                profile.due,
                profile.held,
                profile.paced,
                profile.published,
                profile.acknowledged,
                profile.cancelled,
                profile.lease_ns,
                profile.max_lease_ns
            );
        }
    }
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_hook_enter() -> bool {
    PRESENTING
        .compare_exchange(false, true, Ordering::Acquire, Ordering::Relaxed)
        .is_ok()
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_hook_leave() {
    PRESENTING.store(false, Ordering::Release);
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_hook_enabled() -> bool {
    state().enabled
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_hook_direct() -> bool {
    state().direct
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_hook_active() -> bool {
    state().active
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_hook_starting() -> bool {
    let state = state();
    !state.active && state.socket.is_some() && state.accepted && !state.paused
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_hook_stopping() -> bool {
    let state = state();
    state.active && (state.socket.is_none() || !state.accepted)
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_hook_init() {
    let Some(path) = std::env::var_os("DPE_VKCAPTURE_SOCKET") else {
        return;
    };
    let mut state = state();
    state.enabled = true;
    if !path.as_bytes().starts_with(b"/") || path.as_bytes().len() >= 108 {
        return;
    }
    state.path = CString::new(path.as_bytes()).ok();
    if std::env::var_os("DPE_CAPTURE_PROFILE").is_some_and(|v| v == "1") {
        state.profile = Some(Profile::default());
    }
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_hook_update() {
    state().update();
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_hook_stop() {
    state().stop();
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_hook_due() -> bool {
    state().due()
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_hook_cancel() {
    let mut state = state();
    if state.lease.as_ref().is_some_and(|lease| !lease.published) {
        state.lease = None;
        if let Some(profile) = &mut state.profile {
            profile.cancelled += 1;
        }
    }
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_hook_complete(success: bool) {
    let mut state = state();
    if success {
        state.complete(None);
    } else {
        state.disconnect();
    }
}
/// # Safety
/// A nonnegative fd transfers ownership of exactly one live sync_file.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_hook_complete_fenced(fd: i32) {
    if fd < 0 {
        state().disconnect();
        return;
    }
    // SAFETY: caller transferred unique descriptor ownership to this function.
    let fd = unsafe { OwnedFd::from_raw_fd(fd) };
    state().complete(Some(fd.as_fd()));
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_hook_unavailable() {
    let mut state = state();
    if state.active || !state.accepted || !state.direct || state.socket.is_none() {
        return;
    }
    state.accepted = false;
    state.lease = None;
    let mut packet = Packet::new(UNSUPPORTED);
    packet.generation = state.generation;
    state.send(&packet, &[]);
}
/// # Safety
/// UUID points to a live sixteen-byte identifier.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_hook_identity(uuid: *const u8) {
    if uuid.is_null() {
        return;
    }
    let mut state = state();
    if state.socket.is_none() && !state.active {
        // SAFETY: caller supplies sixteen initialized bytes; copy retains none.
        unsafe {
            state
                .identity
                .copy_from_slice(std::slice::from_raw_parts(uuid, 16));
        }
    }
}
/// # Safety
/// UUID points to a live sixteen-byte identifier.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_hook_device(uuid: *const u8) -> bool {
    if uuid.is_null() {
        return false;
    }
    // SAFETY: caller supplies sixteen initialized bytes for this comparison.
    state().uuid == unsafe { std::slice::from_raw_parts(uuid, 16) }
}
/// # Safety
/// For a valid count each array has that many initialized entries. FDs are
/// borrowed until this call returns; the graphics owner retains the allocation.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_hook_texture(
    width: i32,
    height: i32,
    format: i32,
    strides: *const i32,
    offsets: *const i32,
    modifier: u64,
    flip: bool,
    color: u32,
    count: i32,
    fds: *const i32,
    window: u32,
) {
    let mut state = state();
    if !(1..=16384).contains(&width)
        || !(1..=16384).contains(&height)
        || !(1..=4).contains(&count)
        || color != 0
        || strides.is_null()
        || offsets.is_null()
        || fds.is_null()
    {
        state.disconnect();
        return;
    }
    let mut packet = Packet::new(TEXTURE);
    let Some(generation) = state.generation.checked_add(1) else {
        state.disconnect();
        return;
    };
    packet.generation = generation;
    packet.width = width as u32;
    packet.height = height as u32;
    packet.format = format as u32;
    packet.modifier = modifier;
    packet.flags = if flip { FLIP } else { 0 };
    packet.nfd = count as u32;
    packet.name[..4].copy_from_slice(&window.to_ne_bytes());
    let mut borrowed = [std::mem::MaybeUninit::<BorrowedFd>::uninit(); 4];
    for (index, slot) in borrowed.iter_mut().enumerate().take(count as usize) {
        // SAFETY: all three caller arrays have count entries, validated above.
        let (stride, offset, fd) =
            unsafe { (*strides.add(index), *offsets.add(index), *fds.add(index)) };
        if stride <= 0 || offset < 0 || fd < 0 {
            state.disconnect();
            return;
        }
        packet.strides[index] = stride as u32;
        packet.offsets[index] = offset as u32;
        // SAFETY: caller retains each source fd through this synchronous send.
        slot.write(unsafe { BorrowedFd::borrow_raw(fd) });
    }
    // SAFETY: exactly count borrowed entries were initialized in the loop.
    let descriptors = unsafe {
        std::slice::from_raw_parts(borrowed.as_ptr().cast::<BorrowedFd>(), count as usize)
    };
    state.generation = generation;
    state.active = state.send(&packet, descriptors);
    state.lease = None;
}
