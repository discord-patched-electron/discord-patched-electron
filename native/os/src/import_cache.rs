// SPDX-License-Identifier: MIT
//! Bounded DMA-BUF import leases. Cache closure removes lookup ownership;
//! outstanding frame leases keep their surface, descriptor and device alive.
use dpe_core::handles::Handles;
use std::{
    ffi::c_void,
    os::fd::{AsRawFd, BorrowedFd, OwnedFd},
    ptr,
    sync::{
        Arc, Mutex, MutexGuard, OnceLock,
        atomic::{AtomicBool, AtomicU64, Ordering},
    },
};
const SLOTS: usize = 4;
#[repr(C)]
#[derive(Clone, Copy, PartialEq, Eq)]
pub struct Image {
    width: u32,
    height: u32,
    fd: i32,
    size: u32,
    offset: [u32; 2],
    stride: [u32; 2],
    modifier: u64,
}
const _: () = assert!(size_of::<Image>() == 40 && std::mem::offset_of!(Image, modifier) == 32);
type Create = unsafe extern "C" fn(*mut c_void, *const Image, *mut usize) -> bool;
type Destroy = unsafe extern "C" fn(*mut c_void, usize);
type Finish = unsafe extern "C" fn(*mut c_void);
struct Device {
    context: *mut c_void,
    create: Create,
    destroy: Destroy,
    finish: Finish,
    api: Mutex<()>,
    owned: AtomicBool,
}
// SAFETY: the C contract permits callbacks on arbitrary frame-release threads.
// The opaque context is never dereferenced by Rust; all callbacks serialize on api.
unsafe impl Send for Device {}
// SAFETY: same serialized callback contract; Rust never accesses foreign fields.
unsafe impl Sync for Device {}
fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}
impl Drop for Device {
    fn drop(&mut self) {
        if self.owned.load(Ordering::Acquire) {
            let _api = lock(&self.api);
            // SAFETY: successful cache creation transferred context ownership.
            // The last Arc drops only after every surface callback has finished.
            unsafe {
                (self.finish)(self.context);
            }
        }
    }
}
struct Surface {
    _fd: OwnedFd,
    value: usize,
    device: Arc<Device>,
}
impl Drop for Surface {
    fn drop(&mut self) {
        let _api = lock(&self.device.api);
        // SAFETY: this surface owns one successful create result and its fd;
        // the device remains owned through completion of this serialized callback.
        unsafe {
            (self.device.destroy)(self.device.context, self.value);
        }
        TOTALS[3].fetch_sub(1, Ordering::Relaxed);
    }
}
#[derive(PartialEq, Eq)]
struct Key {
    device: u64,
    inode: u64,
    image: Image,
}
struct Slot {
    key: Key,
    surface: Arc<Surface>,
    serial: u64,
}
#[derive(Default)]
struct State {
    slots: Vec<Slot>,
    serial: u64,
    closing: bool,
}
struct Cache {
    state: Mutex<State>,
    device: Arc<Device>,
}
fn caches() -> &'static Handles<Arc<Cache>> {
    static TABLE: OnceLock<Handles<Arc<Cache>>> = OnceLock::new();
    TABLE.get_or_init(|| Handles::new(64))
}
fn leases() -> &'static Handles<Arc<Surface>> {
    static TABLE: OnceLock<Handles<Arc<Surface>>> = OnceLock::new();
    TABLE.get_or_init(|| Handles::new(4096))
}
static TOTALS: [AtomicU64; 4] = [const { AtomicU64::new(0) }; 4];
fn errno(error: i32) {
    // SAFETY: errno is private to the calling native thread.
    unsafe {
        *libc::__errno_location() = error;
    }
}
/// # Safety
/// Callbacks support serialized use on arbitrary release threads. Context must
/// remain live until finish; only a successful return transfers its ownership.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_import_cache_new(
    context: *mut c_void,
    create: Option<Create>,
    destroy: Option<Destroy>,
    finish: Option<Finish>,
) -> *mut c_void {
    let (Some(create), Some(destroy), Some(finish)) = (create, destroy, finish) else {
        errno(libc::EINVAL);
        return ptr::null_mut();
    };
    let device = Arc::new(Device {
        context,
        create,
        destroy,
        finish,
        api: Mutex::new(()),
        owned: AtomicBool::new(false),
    });
    let cache = Arc::new(Cache {
        state: Mutex::new(State::default()),
        device: device.clone(),
    });
    let Ok(token) = caches().insert(cache) else {
        errno(libc::EAGAIN);
        return ptr::null_mut();
    };
    // Publish ownership before the newly returned token can be used by callers.
    device.owned.store(true, Ordering::Release);
    token.get() as *mut c_void
}
unsafe fn get(cache: *mut c_void, image: *const Image) -> Result<Arc<Surface>, i32> {
    if cache.is_null() || image.is_null() {
        return Err(libc::EINVAL);
    }
    let cache = caches().get(cache as usize).ok_or(libc::ECANCELED)?;
    // SAFETY: caller provides one initialized 40-byte NV12 ABI record without
    // implicit padding. Its descriptor remains borrowed for this entire call.
    let image = unsafe { *image };
    if image.fd < 0 || image.size == 0 {
        return Err(libc::EINVAL);
    }
    // SAFETY: initialized writable stat record and a caller-borrowed descriptor.
    let mut stat: libc::stat = unsafe { std::mem::zeroed() };
    // SAFETY: stat is complete output; fstat retains no pointers or descriptors.
    if unsafe { libc::fstat(image.fd, &mut stat) } != 0
        || stat.st_ino == 0
        || stat.st_size < i64::from(image.size)
    {
        return Err(libc::ENOTSUP);
    }
    let key = Key {
        device: stat.st_dev,
        inode: stat.st_ino,
        image: Image { fd: -1, ..image },
    };
    let mut state = lock(&cache.state);
    if state.closing {
        return Err(libc::ECANCELED);
    }
    state.serial = state.serial.saturating_add(1);
    let serial = state.serial;
    if let Some(slot) = state.slots.iter_mut().find(|slot| slot.key == key) {
        slot.serial = serial;
        TOTALS[1].fetch_add(1, Ordering::Relaxed);
        return Ok(slot.surface.clone());
    }
    if state.slots.len() == SLOTS {
        let (index, _) = state
            .slots
            .iter()
            .enumerate()
            .filter(|(_, slot)| Arc::strong_count(&slot.surface) == 1)
            .min_by_key(|(_, slot)| slot.serial)
            .ok_or(libc::EAGAIN)?;
        // Only cache ownership remains. Frame leases prevent eviction without
        // a raw user count or an opportunity to overwrite a live entry.
        state.slots.swap_remove(index);
        TOTALS[2].fetch_add(1, Ordering::Relaxed);
    }
    // SAFETY: the C caller retains fd throughout this synchronous operation.
    let fd = unsafe { BorrowedFd::borrow_raw(image.fd) }
        .try_clone_to_owned()
        .map_err(|_| libc::EIO)?;
    let retained = Image {
        fd: fd.as_raw_fd(),
        ..image
    };
    let mut value = 0;
    let created = {
        let _api = lock(&cache.device.api);
        // SAFETY: context is owned by this device; the initialized record,
        // duplicated fd and writable surface output remain live through create.
        unsafe { (cache.device.create)(cache.device.context, &retained, &mut value) }
    };
    if !created {
        return Err(libc::EIO);
    }
    let surface = Arc::new(Surface {
        device: cache.device.clone(),
        _fd: fd,
        value,
    });
    TOTALS[0].fetch_add(1, Ordering::Relaxed);
    TOTALS[3].fetch_add(1, Ordering::Relaxed);
    state.slots.push(Slot {
        key,
        surface: surface.clone(),
        serial,
    });
    Ok(surface)
}
/// # Safety
/// Image is a live initialized ABI record; its descriptor is borrowed until return.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_import_get(cache: *mut c_void, image: *const Image) -> *mut c_void {
    // SAFETY: forwarded caller borrow, checked before metadata access in get.
    let resource = match unsafe { get(cache, image) } {
        Ok(resource) => resource,
        Err(error) => {
            errno(error);
            return ptr::null_mut();
        }
    };
    match leases().insert(resource) {
        Ok(token) => token.get() as *mut c_void,
        Err(resource) => {
            drop(resource);
            errno(libc::EAGAIN);
            ptr::null_mut()
        }
    }
}
/// # Safety
/// Output is writable for one uintptr_t. A live lease pins the returned surface
/// until its owner calls put, independently of concurrent cache closure.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_import_surface(lease: *mut c_void, output: *mut usize) -> bool {
    if output.is_null() {
        return false;
    }
    let Some(surface) = leases().get(lease as usize) else {
        return false;
    };
    // SAFETY: caller owns output; the lookup retains the surface during this write.
    unsafe {
        *output = surface.value;
    }
    true
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_import_put(lease: *mut c_void) {
    drop(leases().remove(lease as usize));
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_import_cache_close(cache: *mut c_void) {
    if let Some(cache) = caches().remove(cache as usize) {
        let retired = {
            let mut state = lock(&cache.state);
            state.closing = true;
            std::mem::take(&mut state.slots)
        };
        // GPU destruction and finish never run while an ownership table lock is held.
        drop(retired);
    }
}
/// # Safety
/// Output points to four writable uint64 values.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_import_stats(output: *mut u64) {
    if output.is_null() {
        return;
    }
    let values = TOTALS
        .each_ref()
        .map(|counter| counter.load(Ordering::Relaxed));
    // SAFETY: caller provides four outputs; each local counter is initialized.
    unsafe {
        ptr::copy_nonoverlapping(values.as_ptr(), output, values.len());
    }
}
