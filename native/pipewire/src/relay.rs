// SPDX-License-Identifier: MIT
//! One event-driven DRM timeline per immutable imported image. Scoped owners
//! close exports and sync objects on every failure. Completion unpins registry
//! state before calling C, so the callback may retire its own relay safely.
use crate::ffi as f;
use dpe_core::handles::Handles;
use std::{
    cell::RefCell,
    ffi::c_void,
    os::fd::{AsFd, AsRawFd, BorrowedFd, FromRawFd, OwnedFd},
    ptr::{self, NonNull},
    rc::Rc,
};
type Done = unsafe extern "C" fn(*mut c_void, bool);
const _: () = assert!(size_of::<f::dpe_relay_timeline>() == 24);
const _: () = assert!(size_of::<f::dpe_vk_packet>() == 128);
fn duplicate(fd: i32) -> Option<OwnedFd> {
    if fd < 0 {
        return None;
    }
    // SAFETY: fcntl validates the supplied descriptor without adopting it; an
    // invalid/stale input fails before any Rust descriptor owner is created.
    let cloned = unsafe { libc::fcntl(fd, libc::F_DUPFD_CLOEXEC, 3) };
    if cloned < 0 {
        return None;
    }
    // SAFETY: successful F_DUPFD_CLOEXEC returned a new unique descriptor.
    Some(unsafe { OwnedFd::from_raw_fd(cloned) })
}
struct Object {
    device: Rc<OwnedFd>,
    handle: u32,
}
impl Object {
    fn new(device: Rc<OwnedFd>, flags: u32) -> Option<Self> {
        let mut handle = 0;
        // SAFETY: live DRM descriptor, output handle initialized on successful creation.
        if unsafe { f::drmSyncobjCreate(device.as_raw_fd(), flags, &mut handle) } != 0 {
            return None;
        }
        Some(Self { device, handle })
    }
    fn exported(&self, sync_file: bool) -> Option<OwnedFd> {
        let mut raw = -1;
        // SAFETY: object pins its device and live sync handle; success exports a new owned fd.
        let status = unsafe {
            if sync_file {
                f::drmSyncobjExportSyncFile(self.device.as_raw_fd(), self.handle, &mut raw)
            } else {
                f::drmSyncobjHandleToFD(self.device.as_raw_fd(), self.handle, &mut raw)
            }
        };
        if status != 0 || raw < 0 {
            return None;
        }
        // SAFETY: successful libdrm export returned a new unique descriptor.
        Some(unsafe { OwnedFd::from_raw_fd(raw) })
    }
    fn import(&self, fd: BorrowedFd<'_>) -> bool {
        // SAFETY: both borrowed sync file and owned DRM object remain live through the import.
        (unsafe {
            f::drmSyncobjImportSyncFile(self.device.as_raw_fd(), self.handle, fd.as_raw_fd())
        }) == 0
    }
    fn transfer(&self, point: u64, source: &Self) -> bool {
        // SAFETY: both objects belong to the same retained device; transfer references a fence without waiting.
        (unsafe {
            f::drmSyncobjTransfer(
                self.device.as_raw_fd(),
                self.handle,
                point,
                source.handle,
                0,
                0,
            )
        }) == 0
    }
    fn wait(&self, point: u64) -> bool {
        let mut handle = self.handle;
        let mut point = point;
        // SAFETY: one owned timeline/point pair, nonblocking zero timeout and no optional index output.
        (unsafe {
            f::drmSyncobjTimelineWait(
                self.device.as_raw_fd(),
                &mut handle,
                &mut point,
                1,
                0,
                f::DRM_SYNCOBJ_WAIT_FLAGS_WAIT_ALL,
                ptr::null_mut(),
            )
        }) == 0
    }
    fn signal(&self, point: u64) -> bool {
        let mut point = point;
        // SAFETY: one live timeline and one initialized point; no GPU wait.
        (unsafe {
            f::drmSyncobjTimelineSignal(self.device.as_raw_fd(), &self.handle, &mut point, 1)
        }) == 0
    }
}
impl Drop for Object {
    fn drop(&mut self) {
        // SAFETY: this object uniquely owns the handle and retains its device until destruction returns.
        unsafe {
            f::drmSyncobjDestroy(self.device.as_raw_fd(), self.handle);
        }
    }
}
#[repr(C)]
struct Event {
    handle: u32,
    flags: u32,
    point: u64,
    fd: i32,
    padding: u32,
}
fn notify(timeline: &Object, point: u64, fd: BorrowedFd<'_>) -> bool {
    let mut event = Event {
        handle: timeline.handle,
        flags: 0,
        point,
        fd: fd.as_raw_fd(),
        padding: 0,
    };
    let request = libc::_IOWR::<Event>(b'd' as u32, 0xcf);
    // SAFETY: stable Linux 6.6 DRM eventfd UAPI; event and both descriptor owners remain live.
    (unsafe {
        f::drmIoctl(
            timeline.device.as_raw_fd(),
            request,
            (&raw mut event).cast(),
        )
    }) == 0
}
fn eventfd() -> Option<OwnedFd> {
    // SAFETY: creates a separate nonblocking close-on-exec notification descriptor.
    let fd = unsafe { libc::eventfd(0, libc::EFD_NONBLOCK | libc::EFD_CLOEXEC) };
    if fd < 0 {
        return None;
    }
    // SAFETY: successful eventfd transfers one new unique descriptor.
    Some(unsafe { OwnedFd::from_raw_fd(fd) })
}
fn read_event(fd: BorrowedFd<'_>) -> bool {
    let mut value = 0u64;
    // SAFETY: reads exactly one eventfd counter into an initialized u64.
    (unsafe { libc::read(fd.as_raw_fd(), (&raw mut value).cast(), 8) }) == 8 && value != 0
}
fn signaled(fd: BorrowedFd<'_>) -> bool {
    let mut info = f::sync_file_info::default();
    let request = libc::_IOWR::<f::sync_file_info>(b'>' as u32, 4);
    // SAFETY: live sync file and initialized fixed UAPI output; no foreign fence array is requested.
    (unsafe { libc::ioctl(fd.as_raw_fd(), request, &mut info) }) == 0 && info.status > 0
}
#[derive(Clone, Copy)]
struct Loop {
    data: *mut c_void,
    add: unsafe extern "C" fn(
        *mut c_void,
        i32,
        u32,
        bool,
        f::spa_source_io_func_t,
        *mut c_void,
    ) -> *mut f::spa_source,
    destroy: unsafe extern "C" fn(*mut c_void, *mut f::spa_source),
}
impl Loop {
    unsafe fn new(loop_: *mut f::pw_loop) -> Option<Self> {
        // SAFETY: caller borrows a live PipeWire loop and its immutable SPA utilities interface through relay teardown.
        let utils = unsafe { loop_.as_ref()?.utils.as_ref()? };
        let methods = NonNull::new(
            utils
                .iface
                .cb
                .funcs
                .cast_mut()
                .cast::<f::spa_loop_utils_methods>(),
        )?;
        // SAFETY: read only the documented v0 method slots. No reference to a
        // potentially longer future table is formed from the foreign prefix.
        let (add, destroy) = unsafe {
            (
                ptr::addr_of!((*methods.as_ptr()).add_io).read(),
                ptr::addr_of!((*methods.as_ptr()).destroy_source).read(),
            )
        };
        Some(Self {
            data: utils.iface.cb.data,
            add: add?,
            destroy: destroy?,
        })
    }
    fn watch(self, fd: BorrowedFd<'_>, token: usize) -> Option<Watch> {
        // SAFETY: caller-owned loop remains live; the registry pins callback state, and this watch never owns/closes fd.
        let source = unsafe {
            (self.add)(
                self.data,
                fd.as_raw_fd(),
                f::SPA_IO_IN | f::SPA_IO_ERR | f::SPA_IO_HUP,
                false,
                Some(released),
                token as *mut c_void,
            )
        };
        Some(Watch {
            loop_: self,
            source: NonNull::new(source)?,
        })
    }
}
struct Watch {
    loop_: Loop,
    source: NonNull<f::spa_source>,
}
impl Drop for Watch {
    fn drop(&mut self) {
        // SAFETY: watch uniquely owns a source in the creating thread's live loop; removal synchronously revokes future callbacks.
        unsafe {
            (self.loop_.destroy)(self.loop_.data, self.source.as_ptr());
        }
    }
}
pub(crate) struct Relay {
    watch: Option<Watch>,
    _planes: [Option<OwnedFd>; 4],
    timeline_fd: OwnedFd,
    event: OwnedFd,
    acquire_fd: Option<OwnedFd>,
    timeline: Object,
    acquire: Object,
    metadata: NonNull<f::dpe_relay_timeline>,
    generation: u64,
    point: u64,
    done: Option<(Done, usize)>,
}
thread_local! {static RELAYS:Handles<Rc<RefCell<Relay>>>=Handles::new(64);}
pub(crate) fn relay(token: usize) -> Option<Rc<RefCell<Relay>>> {
    RELAYS.with(|table| table.get(token))
}
struct Publication(usize);
impl Drop for Publication {
    fn drop(&mut self) {
        if self.0 != 0 {
            RELAYS.with(|table| table.remove(self.0));
        }
    }
}
unsafe extern "C" fn released(opaque: *mut c_void, _fd: i32, mask: u32) {
    let Some(owner) = relay(opaque as usize) else {
        return;
    };
    let completion = {
        let Ok(mut state) = owner.try_borrow_mut() else {
            return;
        };
        let success = mask & f::SPA_IO_IN != 0
            && mask & (f::SPA_IO_ERR | f::SPA_IO_HUP) == 0
            && read_event(state.event.as_fd())
            && state.timeline.wait(state.point)
            && state
                .acquire_fd
                .as_ref()
                .is_none_or(|fd| signaled(fd.as_fd()));
        state.acquire_fd = None;
        state
            .done
            .take()
            .map(|(callback, data)| (callback, data, success))
    };
    if let Some((callback, data, success)) = completion {
        // SAFETY: the C caller supplied this callback/data and keeps them valid while submission is pending. No registry/state borrow spans reentry.
        unsafe {
            callback(data as *mut c_void, success);
        }
    }
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_vk_relay_supported(fd: i32) -> bool {
    let Some(device) = duplicate(fd).map(Rc::new) else {
        return false;
    };
    let mut capability = 0;
    // SAFETY: live cloned device and fixed capability output.
    if unsafe {
        f::drmGetCap(
            device.as_raw_fd(),
            f::DRM_CAP_SYNCOBJ_TIMELINE as u64,
            &mut capability,
        )
    } != 0
        || capability == 0
    {
        return false;
    }
    let Some(timeline) = Object::new(device.clone(), 0) else {
        return false;
    };
    let Some(binary) = Object::new(device, f::DRM_SYNCOBJ_CREATE_SIGNALED) else {
        return false;
    };
    let Some(sync) = binary.exported(true) else {
        return false;
    };
    if !binary.import(sync.as_fd()) {
        return false;
    }
    let Some(event) = eventfd() else {
        return false;
    };
    notify(&timeline, 1, event.as_fd())
        && timeline.transfer(1, &binary)
        && timeline.wait(1)
        && read_event(event.as_fd())
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_vk_relay_format(format: u32) -> u32 {
    match format {
        f::DPE_RELAY_ARGB => f::spa_video_format_SPA_VIDEO_FORMAT_BGRA,
        f::DPE_RELAY_XRGB => f::spa_video_format_SPA_VIDEO_FORMAT_BGRx,
        f::DPE_RELAY_ABGR => f::spa_video_format_SPA_VIDEO_FORMAT_RGBA,
        f::DPE_RELAY_XBGR => f::spa_video_format_SPA_VIDEO_FORMAT_RGBx,
        _ => f::spa_video_format_SPA_VIDEO_FORMAT_UNKNOWN,
    }
}
/// # Safety
/// All record/array pointers are live for their declared sizes. The creating
/// thread owns loop callbacks and relay calls. The loop outlives the relay;
/// buffer metadata remains live through submit, but may retire before completion.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_vk_relay_create(
    fd: i32,
    loop_: *mut f::pw_loop,
    buffer: *mut f::spa_buffer,
    packet: *const f::dpe_vk_packet,
    planes: *const i32,
) -> *mut c_void {
    let create = || -> Option<usize> {
        // SAFETY: C caller supplies readable packet and a writable live buffer record.
        let (packet, buffer) = unsafe { (packet.as_ref()?, buffer.as_mut()?) };
        if !(1..=4).contains(&packet.nfd)
            || packet.flags != 0
            || dpe_vk_relay_format(packet.format) == 0
            || buffer.n_datas != packet.nfd + 2
            || buffer.datas.is_null()
            || buffer.metas.is_null()
            || buffer.n_metas > 64
            || planes.is_null()
        {
            return None;
        }
        // SAFETY: caller's associated counts were bounded before borrowing these arrays.
        let (metas, data, planes) = unsafe {
            (
                std::slice::from_raw_parts(buffer.metas, buffer.n_metas as usize),
                std::slice::from_raw_parts_mut(buffer.datas, buffer.n_datas as usize),
                std::slice::from_raw_parts(planes, 4),
            )
        };
        let meta = metas.iter().find(|meta| {
            meta.type_ == f::DPE_RELAY_META
                && meta.size as usize >= size_of::<f::dpe_relay_timeline>()
        })?;
        let metadata = NonNull::new(meta.data.cast::<f::dpe_relay_timeline>())
            .filter(|ptr| ptr.as_ptr().is_aligned())?;
        // SAFETY: caller retains the loop/interface through every watch lifetime.
        let loop_ = unsafe { Loop::new(loop_) }?;
        let device = Rc::new(duplicate(fd)?);
        let timeline = Object::new(device.clone(), 0)?;
        let acquire = Object::new(device, 0)?;
        let timeline_fd = timeline.exported(false)?;
        let event = eventfd()?;
        let mut imports: [Option<OwnedFd>; 4] = std::array::from_fn(|_| None);
        for (i, slot) in imports.iter_mut().enumerate().take(packet.nfd as usize) {
            if data[i].chunk.is_null()
                || data[i].type_ & (1 << f::spa_data_type_SPA_DATA_DmaBuf) == 0
            {
                return None;
            }
            *slot = Some(duplicate(planes[i])?);
        }
        let owner = Rc::new(RefCell::new(Relay {
            watch: None,
            _planes: imports,
            timeline_fd,
            event,
            acquire_fd: None,
            timeline,
            acquire,
            metadata,
            generation: packet.generation,
            point: 0,
            done: None,
        }));
        let token = RELAYS.with(|table| table.insert(owner.clone())).ok()?.get();
        let mut publication = Publication(token);
        let mut state = owner.borrow_mut();
        state.watch = Some(loop_.watch(state.event.as_fd(), token)?);
        // Publication occurs only after every owned resource and callback exists.
        for (i, destination) in data.iter_mut().enumerate() {
            destination.flags = f::SPA_DATA_FLAG_READABLE;
            destination.data = ptr::null_mut();
            destination.mapoffset = 0;
            destination.maxsize = 0;
            if i < packet.nfd as usize {
                destination.type_ = f::spa_data_type_SPA_DATA_DmaBuf;
                destination.fd = state._planes[i].as_ref()?.as_raw_fd().into();
                // SAFETY: caller owns a writable chunk for each plane, checked nonnull before publication.
                unsafe {
                    destination.chunk.write(f::spa_chunk {
                        offset: packet.offsets[i],
                        size: 1,
                        stride: packet.strides[i] as i32,
                        ..Default::default()
                    });
                }
            } else {
                destination.type_ = f::DPE_RELAY_DATA;
                destination.fd = state.timeline_fd.as_raw_fd().into();
            }
        }
        // SAFETY: checked aligned metadata record belongs to this caller's live buffer.
        unsafe {
            state
                .metadata
                .as_ptr()
                .write(f::dpe_relay_timeline::default());
        }
        publication.0 = 0;
        Some(token)
    };
    create().map_or(ptr::null_mut(), |token| token as *mut c_void)
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_vk_relay_destroy(token: *mut c_void) {
    RELAYS.with(|table| table.remove(token as usize));
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_vk_relay_generation(token: *const c_void) -> u64 {
    relay(token as usize)
        .and_then(|owner| owner.try_borrow().ok().map(|state| state.generation))
        .unwrap_or(0)
}
/// # Safety
/// An owned nonnegative acquire_fd transfers even on rejection. Callback/data
/// remain valid until completion or destroy, and the buffer metadata is live.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_vk_relay_submit(
    token: *mut c_void,
    acquire_fd: i32,
    done: Option<Done>,
    data: *mut c_void,
) -> i32 {
    let acquire_fd = if acquire_fd >= 0 {
        // SAFETY: C caller transfers unique ownership of this descriptor at entry.
        Some(unsafe { OwnedFd::from_raw_fd(acquire_fd) })
    } else {
        None
    };
    let Some(owner) = relay(token as usize) else {
        return -libc::EINVAL;
    };
    // SAFETY: same metadata/callback contract as this reviewed C entry point.
    unsafe { submit(&owner, acquire_fd, done, data) }
}
pub(crate) unsafe fn submit(
    owner: &Rc<RefCell<Relay>>,
    acquire_fd: Option<OwnedFd>,
    done: Option<Done>,
    data: *mut c_void,
) -> i32 {
    let Ok(mut state) = owner.try_borrow_mut() else {
        return -libc::EBUSY;
    };
    if state.done.is_some() || state.point > u64::MAX - 2 {
        return -libc::EBUSY;
    }
    let Some(done) = done else {
        return -libc::EINVAL;
    };
    state.point += 2;
    let acquire = state.point - 1;
    // SAFETY: caller retains metadata until submit returns. Completion never accesses this pointer.
    unsafe {
        (*state.metadata.as_ptr()).acquire_point = acquire;
        (*state.metadata.as_ptr()).release_point = state.point;
    }
    if !notify(&state.timeline, state.point, state.event.as_fd()) {
        return -libc::EIO;
    }
    let valid = match &acquire_fd {
        Some(fd) => {
            state.acquire.import(fd.as_fd()) && state.timeline.transfer(acquire, &state.acquire)
        }
        None => state.timeline.signal(acquire),
    };
    if !valid {
        return -libc::EIO;
    }
    state.acquire_fd = acquire_fd;
    state.done = Some((done, data as usize));
    0
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::fd::IntoRawFd;
    #[test]
    fn rejected_tokens_close_transferred_fences_without_touching_other_owners() {
        let raw = std::fs::File::open("/dev/null").unwrap().into_raw_fd();
        // SAFETY: this call transfers the unique file descriptor even though
        // the relay token is stale; no callback/data are supplied or retained.
        let result = unsafe { dpe_vk_relay_submit(123usize as *mut _, raw, None, ptr::null_mut()) };
        assert_eq!(result, -libc::EINVAL);
        // SAFETY: read-only descriptor query; rejected submission must have closed this fd.
        assert_eq!(unsafe { libc::fcntl(raw, libc::F_GETFD) }, -1);
        let owner = std::fs::File::open("/dev/null").unwrap();
        dpe_vk_relay_destroy(123usize as *mut _);
        dpe_vk_relay_destroy(123usize as *mut _);
        assert_eq!(dpe_vk_relay_generation(123usize as *const _), 0);
        // SAFETY: stale destroys must leave independently owned descriptors intact, including a potentially reused fd number.
        assert!(unsafe { libc::fcntl(owner.as_raw_fd(), libc::F_GETFD) } >= 0);
        assert!(!dpe_vk_relay_supported(-1));
    }
}
