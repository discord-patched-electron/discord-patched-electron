// SPDX-License-Identifier: MIT
//! Bounded GPU ownership across GL context changes. Retiring work never waits
//! for the GPU or treats an error as completion. Driver calls are confined to
//! current GL context; recursive driver callbacks fail closed at the registry.
use crate::graphics_ffi as f;
use std::{
    cell::RefCell,
    ffi::{CStr, c_char, c_void},
    os::fd::{AsRawFd, FromRawFd, IntoRawFd, OwnedFd},
    ptr,
};

type Current = unsafe extern "C" fn() -> *mut c_void;
type GlProc = unsafe extern "C" fn(*const c_char) -> *mut c_void;
#[derive(Clone, Copy)]
struct Gl {
    context: Current,
    error: unsafe extern "C" fn() -> u32,
    generate: unsafe extern "C" fn(i32, *mut u32),
    destroy: unsafe extern "C" fn(i32, *const u32),
    import: unsafe extern "C" fn(u32, u32, i32),
    wait: unsafe extern "C" fn(u32, u32, *const u32, u32, *const u32, *const u32),
    signal: unsafe extern "C" fn(u32, u32, *const u32, u32, *const u32, *const u32),
}
macro_rules! proc {
    ($get:expr, $name:literal, $kind:ty) => {{
        let address = $get($name.as_ptr());
        if address.is_null() {
            return None;
        }
        std::mem::transmute::<*mut c_void, $kind>(address)
    }};
}
impl Gl {
    unsafe fn load(get: GlProc, context: Current) -> Option<Self> {
        // SAFETY: caller supplies the current context's GL procedure resolver;
        // each nonnull entry has its Khronos signature, checked before use.
        unsafe {
            Some(Self {
                context,
                error: proc!(get, c"glGetError", unsafe extern "C" fn() -> u32),
                generate: proc!(
                    get,
                    c"glGenSemaphoresEXT",
                    unsafe extern "C" fn(i32, *mut u32)
                ),
                destroy: proc!(
                    get,
                    c"glDeleteSemaphoresEXT",
                    unsafe extern "C" fn(i32, *const u32)
                ),
                import: proc!(
                    get,
                    c"glImportSemaphoreFdEXT",
                    unsafe extern "C" fn(u32, u32, i32)
                ),
                wait: proc!(
                    get,
                    c"glWaitSemaphoreEXT",
                    unsafe extern "C" fn(u32, u32, *const u32, u32, *const u32, *const u32)
                ),
                signal: proc!(
                    get,
                    c"glSignalSemaphoreEXT",
                    unsafe extern "C" fn(u32, u32, *const u32, u32, *const u32, *const u32)
                ),
            })
        }
    }
}
struct Device {
    handle: f::VkDevice,
    queue: f::VkQueue,
    create_semaphore: f::PFN_vkCreateSemaphore,
    destroy_semaphore: f::PFN_vkDestroySemaphore,
    export: f::PFN_vkGetSemaphoreFdKHR,
    create_fence: f::PFN_vkCreateFence,
    destroy_fence: f::PFN_vkDestroyFence,
    status: f::PFN_vkGetFenceStatus,
    reset: f::PFN_vkResetFences,
    submit: f::PFN_vkQueueSubmit,
}
enum Export {
    Descriptor(OwnedFd),
    NoDescriptor,
}
impl Device {
    unsafe fn load(
        get: f::PFN_vkGetDeviceProcAddr,
        handle: f::VkDevice,
        family: u32,
    ) -> Option<Self> {
        let get = get?;
        macro_rules! load {
            ($name:literal, $kind:ty) => {{
                // SAFETY: Vulkan resolver belongs to the caller-retained device.
                let pointer = unsafe { get(handle, $name.as_ptr()) }?;
                // SAFETY: exact Khronos name/signature; the address is nonnull.
                Some(unsafe { std::mem::transmute::<unsafe extern "C" fn(), $kind>(pointer) })
            }};
        }
        let queue_fn: f::PFN_vkGetDeviceQueue = load!(
            c"vkGetDeviceQueue",
            unsafe extern "C" fn(f::VkDevice, u32, u32, *mut f::VkQueue)
        );
        let mut queue = ptr::null_mut();
        // SAFETY: caller selected an existing graphics family and retains device.
        unsafe {
            queue_fn?(handle, family, 0, &mut queue);
        }
        if queue.is_null() {
            return None;
        }
        Some(Self {
            handle,
            queue,
            create_semaphore: load!(
                c"vkCreateSemaphore",
                unsafe extern "C" fn(
                    f::VkDevice,
                    *const f::VkSemaphoreCreateInfo,
                    *const f::VkAllocationCallbacks,
                    *mut f::VkSemaphore,
                ) -> f::VkResult
            ),
            destroy_semaphore: load!(
                c"vkDestroySemaphore",
                unsafe extern "C" fn(f::VkDevice, f::VkSemaphore, *const f::VkAllocationCallbacks)
            ),
            export: load!(
                c"vkGetSemaphoreFdKHR",
                unsafe extern "C" fn(
                    f::VkDevice,
                    *const f::VkSemaphoreGetFdInfoKHR,
                    *mut i32,
                ) -> f::VkResult
            ),
            create_fence: load!(
                c"vkCreateFence",
                unsafe extern "C" fn(
                    f::VkDevice,
                    *const f::VkFenceCreateInfo,
                    *const f::VkAllocationCallbacks,
                    *mut f::VkFence,
                ) -> f::VkResult
            ),
            destroy_fence: load!(
                c"vkDestroyFence",
                unsafe extern "C" fn(f::VkDevice, f::VkFence, *const f::VkAllocationCallbacks)
            ),
            status: load!(
                c"vkGetFenceStatus",
                unsafe extern "C" fn(f::VkDevice, f::VkFence) -> f::VkResult
            ),
            reset: load!(
                c"vkResetFences",
                unsafe extern "C" fn(f::VkDevice, u32, *const f::VkFence) -> f::VkResult
            ),
            submit: load!(
                c"vkQueueSubmit",
                unsafe extern "C" fn(
                    f::VkQueue,
                    u32,
                    *const f::VkSubmitInfo,
                    f::VkFence,
                ) -> f::VkResult
            ),
        })
    }
    fn exported(&self, semaphore: f::VkSemaphore, kind: u32) -> Option<Export> {
        let info = f::VkSemaphoreGetFdInfoKHR {
            sType: f::VK_STRUCTURE_TYPE_SEMAPHORE_GET_FD_INFO_KHR,
            semaphore,
            handleType: kind,
            ..Default::default()
        };
        let mut fd = -1;
        // SAFETY: retained device, live semaphore and initialized FD output.
        let result = unsafe { self.export.expect("loaded device")(self.handle, &info, &mut fd) };
        if result == f::VK_SUCCESS
            && fd == -1
            && kind == f::VK_EXTERNAL_SEMAPHORE_HANDLE_TYPE_SYNC_FD_BIT
        {
            return Some(Export::NoDescriptor);
        }
        if fd < 0 {
            return None;
        }
        // SAFETY: Vulkan returned one new owned descriptor. Even an erroneous
        // failing driver that supplied it cannot leak it through this boundary.
        let fd = unsafe { OwnedFd::from_raw_fd(fd) };
        (result == f::VK_SUCCESS).then_some(Export::Descriptor(fd))
    }
}
#[derive(Clone, Copy)]
enum Phase {
    Idle,
    Begun,
}
struct Bridge {
    gl: Gl,
    context: *mut c_void,
    device: Device,
    acquire: f::VkSemaphore,
    complete: f::VkSemaphore,
    output: f::VkSemaphore,
    before: f::VkFence,
    after: f::VkFence,
    gl_acquire: u32,
    gl_complete: u32,
    before_busy: bool,
    after_busy: bool,
    phase: Phase,
    primed: bool,
    untracked: bool,
    egl: Option<f::dpe_gl_sync_egl>,
    egl_fence: *mut c_void,
}
impl Bridge {
    fn new(gl: Gl, context: *mut c_void, device: Device, egl: Option<f::dpe_gl_sync_egl>) -> Self {
        Self {
            gl,
            context,
            device,
            egl,
            acquire: ptr::null_mut(),
            complete: ptr::null_mut(),
            output: ptr::null_mut(),
            before: ptr::null_mut(),
            after: ptr::null_mut(),
            gl_acquire: 0,
            gl_complete: 0,
            before_busy: false,
            after_busy: false,
            phase: Phase::Idle,
            primed: false,
            untracked: false,
            egl_fence: ptr::null_mut(),
        }
    }
    fn finished(&mut self) -> i32 {
        if self.untracked && !self.context.is_null() {
            return 0;
        }
        // SAFETY: all device/GL function addresses and optional EGL callbacks
        // were checked at creation; object storage stays pinned by this borrow.
        unsafe {
            if let Some(egl) = self.egl {
                if self.egl_fence.is_null() {
                    return 1;
                }
                let mut status = 0;
                if egl.get.expect("validated EGL")(egl.display, self.egl_fence, 0x30f1, &mut status)
                    == 0
                {
                    return -1;
                }
                return i32::from(status == 0x30f2);
            }
            let before = if self.before_busy {
                self.device.status.expect("loaded device")(self.device.handle, self.before)
            } else {
                f::VK_SUCCESS
            };
            let after = if self.after_busy {
                self.device.status.expect("loaded device")(self.device.handle, self.after)
            } else {
                f::VK_SUCCESS
            };
            if ![f::VK_SUCCESS, f::VK_NOT_READY].contains(&before)
                || ![f::VK_SUCCESS, f::VK_NOT_READY].contains(&after)
            {
                return -1;
            }
            if before == f::VK_SUCCESS {
                self.before_busy = false;
            }
            if after == f::VK_SUCCESS {
                self.after_busy = false;
            }
            i32::from(before == f::VK_SUCCESS && after == f::VK_SUCCESS)
        }
    }
    fn semaphore(&self, kind: u32) -> Option<f::VkSemaphore> {
        let export = f::VkExportSemaphoreCreateInfo {
            sType: f::VK_STRUCTURE_TYPE_EXPORT_SEMAPHORE_CREATE_INFO,
            handleTypes: kind,
            ..Default::default()
        };
        let info = f::VkSemaphoreCreateInfo {
            sType: f::VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO,
            pNext: (&raw const export).cast(),
            ..Default::default()
        };
        let mut handle = ptr::null_mut();
        // SAFETY: live device and initialized create-chain/output, no allocator.
        let status = unsafe {
            self.device.create_semaphore.expect("loaded device")(
                self.device.handle,
                &info,
                ptr::null(),
                &mut handle,
            )
        };
        (status == f::VK_SUCCESS && !handle.is_null()).then_some(handle)
    }
    fn import(&self, semaphore: f::VkSemaphore, name: &mut u32) -> bool {
        let Some(Export::Descriptor(fd)) = self.device.exported(
            semaphore,
            f::VK_EXTERNAL_SEMAPHORE_HANDLE_TYPE_OPAQUE_FD_BIT,
        ) else {
            return false;
        };
        // SAFETY: current owning context, initialized GL output; only successful
        // GL import takes fd ownership. Failure retains the Rust descriptor.
        unsafe {
            (self.gl.generate)(1, name);
            if *name == 0 || (self.gl.error)() != f::GL_NO_ERROR {
                return false;
            }
            (self.gl.import)(*name, f::GL_HANDLE_TYPE_OPAQUE_FD_EXT, fd.as_raw_fd());
            if (self.gl.error)() != f::GL_NO_ERROR {
                return false;
            }
        }
        let _ = fd.into_raw_fd();
        true
    }
    fn setup(&mut self) -> bool {
        let Some(acquire) = self.semaphore(f::VK_EXTERNAL_SEMAPHORE_HANDLE_TYPE_OPAQUE_FD_BIT)
        else {
            return false;
        };
        self.acquire = acquire;
        let mut name = 0;
        let imported = self.import(acquire, &mut name);
        self.gl_acquire = name;
        if !imported {
            return false;
        }
        if self.egl.is_some() {
            return true;
        }
        let Some(complete) = self.semaphore(f::VK_EXTERNAL_SEMAPHORE_HANDLE_TYPE_OPAQUE_FD_BIT)
        else {
            return false;
        };
        self.complete = complete;
        let mut name = 0;
        let imported = self.import(complete, &mut name);
        self.gl_complete = name;
        if !imported {
            return false;
        }
        let Some(output) = self.semaphore(f::VK_EXTERNAL_SEMAPHORE_HANDLE_TYPE_SYNC_FD_BIT) else {
            return false;
        };
        self.output = output;
        let info = f::VkFenceCreateInfo {
            sType: f::VK_STRUCTURE_TYPE_FENCE_CREATE_INFO,
            ..Default::default()
        };
        // SAFETY: live device and two distinct initialized output slots.
        unsafe {
            self.device.create_fence.expect("loaded device")(
                self.device.handle,
                &info,
                ptr::null(),
                &mut self.before,
            ) == f::VK_SUCCESS
                && self.device.create_fence.expect("loaded device")(
                    self.device.handle,
                    &info,
                    ptr::null(),
                    &mut self.after,
                ) == f::VK_SUCCESS
        }
    }
    fn begin(&mut self, texture: u32) -> i32 {
        if matches!(self.phase, Phase::Begun) || !self.is_current() {
            return -1;
        }
        let ready = self.finished();
        if ready != 1 {
            return ready;
        }
        // SAFETY: this context is current, every optional EGL entry validated,
        // and all Vulkan submission pointer arrays outlive their synchronous call.
        unsafe {
            if let Some(egl) = self.egl
                && !self.egl_fence.is_null()
            {
                egl.destroy.expect("validated EGL")(egl.display, self.egl_fence);
                self.egl_fence = ptr::null_mut();
            }
            if !self.primed {
                if self.egl.is_some() {
                    self.untracked = true;
                    (self.gl.signal)(self.gl_acquire, 0, ptr::null(), 0, ptr::null(), ptr::null());
                    if (self.gl.error)() != f::GL_NO_ERROR {
                        return -1;
                    }
                } else {
                    if self.device.reset.expect("loaded device")(
                        self.device.handle,
                        1,
                        &self.before,
                    ) != f::VK_SUCCESS
                    {
                        return -1;
                    }
                    let submit = f::VkSubmitInfo {
                        sType: f::VK_STRUCTURE_TYPE_SUBMIT_INFO,
                        signalSemaphoreCount: 1,
                        pSignalSemaphores: &self.acquire,
                        ..Default::default()
                    };
                    if self.device.submit.expect("loaded device")(
                        self.device.queue,
                        1,
                        &submit,
                        self.before,
                    ) != f::VK_SUCCESS
                    {
                        return -1;
                    }
                    self.before_busy = true;
                }
                self.primed = true;
            }
            // No fence yet covers the queued GL wait/copy/signal cycle. A
            // failed end must retain these objects until context teardown.
            self.untracked = true;
            let layout = f::GL_NONE;
            (self.gl.wait)(self.gl_acquire, 0, ptr::null(), 1, &texture, &layout);
            if (self.gl.error)() != f::GL_NO_ERROR {
                return -1;
            }
            self.phase = Phase::Begun;
            1
        }
    }
    fn end(&mut self, texture: u32) -> i32 {
        if !matches!(self.phase, Phase::Begun) || !self.is_current() {
            return -2;
        }
        // SAFETY: current owning context; retained semaphores/fences and fixed
        // local submission arrays remain valid for each driver operation.
        unsafe {
            let layout = f::GL_LAYOUT_GENERAL_EXT;
            (self.gl.signal)(
                if self.egl.is_some() {
                    self.gl_acquire
                } else {
                    self.gl_complete
                },
                0,
                ptr::null(),
                1,
                &texture,
                &layout,
            );
            if (self.gl.error)() != f::GL_NO_ERROR {
                return -2;
            }
            if let Some(egl) = self.egl {
                self.egl_fence =
                    egl.create.expect("validated EGL")(egl.display, 0x3144, ptr::null());
                if self.egl_fence.is_null() {
                    return -2;
                }
                egl.flush.expect("validated EGL")();
                self.untracked = false;
                self.phase = Phase::Idle;
                let fd = egl.dup_fd.expect("validated EGL")(egl.display, self.egl_fence);
                return if fd < 0 { -2 } else { fd };
            }
            if self.device.reset.expect("loaded device")(self.device.handle, 1, &self.after)
                != f::VK_SUCCESS
            {
                return -2;
            }
            let stage = f::VK_PIPELINE_STAGE_ALL_COMMANDS_BIT;
            let signals = [self.output, self.acquire];
            let submit = f::VkSubmitInfo {
                sType: f::VK_STRUCTURE_TYPE_SUBMIT_INFO,
                waitSemaphoreCount: 1,
                pWaitSemaphores: &self.complete,
                pWaitDstStageMask: &stage,
                signalSemaphoreCount: 2,
                pSignalSemaphores: signals.as_ptr(),
                ..Default::default()
            };
            if self.device.submit.expect("loaded device")(self.device.queue, 1, &submit, self.after)
                != f::VK_SUCCESS
            {
                return -2;
            }
            self.after_busy = true;
            self.untracked = false;
            self.phase = Phase::Idle;
            match self.device.exported(
                self.output,
                f::VK_EXTERNAL_SEMAPHORE_HANDLE_TYPE_SYNC_FD_BIT,
            ) {
                Some(Export::Descriptor(fd)) => fd.into_raw_fd(),
                // Keep the legacy -1 completion result only when our actual
                // submission fences prove completion. A missing descriptor
                // alone never authorizes reuse of the captured image.
                Some(Export::NoDescriptor) if self.finished() == 1 => -1,
                Some(Export::NoDescriptor) => -2,
                None => -2,
            }
        }
    }
    fn is_current(&self) -> bool {
        // SAFETY: supplied GL context-query function remains live with the hook.
        !self.context.is_null() && unsafe { (self.gl.context)() == self.context }
    }
}
// A Bridge is dropped only by collect after confirmed completion in its owning
// context, or after that context's destruction has retired its GL references.
impl Drop for Bridge {
    fn drop(&mut self) {
        // SAFETY: the state machine above establishes no outstanding GPU use;
        // all nonnull handles remain owned until these matching destructors.
        unsafe {
            if !self.context.is_null() {
                for name in [self.gl_acquire, self.gl_complete] {
                    if name != 0 {
                        (self.gl.destroy)(1, &name);
                    }
                }
            }
            if let Some(egl) = self.egl
                && !self.egl_fence.is_null()
            {
                egl.destroy.expect("validated EGL")(egl.display, self.egl_fence);
            }
            for semaphore in [self.acquire, self.complete, self.output] {
                if !semaphore.is_null() {
                    self.device.destroy_semaphore.expect("loaded device")(
                        self.device.handle,
                        semaphore,
                        ptr::null(),
                    );
                }
            }
            for fence in [self.before, self.after] {
                if !fence.is_null() {
                    self.device.destroy_fence.expect("loaded device")(
                        self.device.handle,
                        fence,
                        ptr::null(),
                    );
                }
            }
        }
    }
}
const LIMIT: usize = 4;
#[derive(Default)]
struct State {
    gl: Option<Gl>,
    current: Option<Bridge>,
    retired: Vec<Bridge>,
}
impl State {
    fn collect(&mut self) {
        let mut index = 0;
        while index < self.retired.len() {
            let bridge = &mut self.retired[index];
            if bridge.finished() == 1 && (bridge.context.is_null() || bridge.is_current()) {
                drop(self.retired.remove(index));
            } else {
                index += 1;
            }
        }
    }
    fn retire(&mut self) {
        if let Some(bridge) = self.current.take() {
            self.retired.push(bridge);
        }
        self.collect();
    }
}
// SAFETY: raw pointers are opaque driver handles/procedure addresses, not Rust
// allocations. The mutex serializes host access to all Vulkan objects. GL calls
// require the owning context to be current on the calling thread. EGL sync
// queries and Vulkan fence queries may run on any thread; destruction of GL
// objects additionally requires that current context or its explicit teardown.
// No references into State or borrowed driver records escape a locked operation.
unsafe impl Send for State {}
static STATE: std::sync::Mutex<State> = std::sync::Mutex::new(State {
    gl: None,
    current: None,
    retired: Vec::new(),
});
thread_local! { static ENTERED: RefCell<bool> = const { RefCell::new(false) }; }
fn with_state<T>(fallback: T, access: impl FnOnce(&mut State) -> T) -> T {
    ENTERED.with(|entered| {
        if entered.replace(true) {
            return fallback;
        }
        struct Guard<'a>(&'a RefCell<bool>);
        impl Drop for Guard<'_> {
            fn drop(&mut self) {
                self.0.replace(false);
            }
        }
        let _entered = Guard(entered);
        let mut state = STATE
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        access(&mut state)
    })
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_gl_sync_requested() -> bool {
    std::env::var_os("DPE_GL_SEMAPHORE_SYNC").is_none_or(|value| value != "0")
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_gl_sync_active() -> bool {
    with_state(false, |state| state.current.is_some())
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_gl_sync_uses_egl() -> bool {
    with_state(false, |state| {
        state
            .current
            .as_ref()
            .is_some_and(|bridge| bridge.egl.is_some())
    })
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_gl_sync_collect() {
    with_state((), State::collect);
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_gl_sync_retire() {
    with_state((), State::retire);
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_gl_sync_context_destroyed(context: *mut c_void) {
    with_state((), |state| {
        for bridge in &mut state.retired {
            if bridge.context == context {
                bridge.context = ptr::null_mut();
                bridge.gl_acquire = 0;
                bridge.gl_complete = 0;
            }
        }
        state.collect();
    });
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_gl_sync_begin(texture: u32) -> i32 {
    with_state(-1, |state| {
        state
            .current
            .as_mut()
            .map_or(-1, |bridge| bridge.begin(texture))
    })
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_gl_sync_end(texture: u32) -> i32 {
    with_state(-2, |state| {
        state
            .current
            .as_mut()
            .map_or(-2, |bridge| bridge.end(texture))
    })
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_gl_sync_create(
    get: f::PFN_vkGetDeviceProcAddr,
    device: f::VkDevice,
    family: u32,
    egl: *const f::dpe_gl_sync_egl,
) -> bool {
    with_state(false, |state| {
        state.collect();
        if state.current.is_some() || state.retired.len() >= LIMIT {
            return false;
        }
        let Some(gl) = state.gl else {
            return false;
        };
        // SAFETY: caller retains procedure tables/device/optional EGL record;
        // copied callbacks remain live through retirement of their GPU work.
        unsafe {
            let context = (gl.context)();
            if context.is_null() {
                return false;
            }
            let egl = egl.as_ref().copied();
            if egl.is_some_and(|egl| {
                egl.display.is_null()
                    || egl.create.is_none()
                    || egl.destroy.is_none()
                    || egl.dup_fd.is_none()
                    || egl.get.is_none()
                    || egl.flush.is_none()
            }) {
                return false;
            }
            let Some(device) = Device::load(get, device, family) else {
                return false;
            };
            let mut bridge = Bridge::new(gl, context, device, egl);
            let okay = bridge.setup();
            state.current = Some(bridge);
            if !okay {
                state.retire();
            }
            okay
        }
    })
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_gl_image_exportable(
    query: f::PFN_vkGetPhysicalDeviceImageFormatProperties2KHR,
    physical: f::VkPhysicalDevice,
    image: *const f::VkImageCreateInfo,
    modifier: u64,
) -> bool {
    let Some(query) = query else {
        return false;
    };
    // SAFETY: caller supplies a live immutable Vulkan create record for this call.
    let Some(image) = (unsafe { image.as_ref() }) else {
        return false;
    };
    if ![
        f::VK_IMAGE_TILING_LINEAR,
        f::VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT,
    ]
    .contains(&image.tiling)
    {
        return false;
    }
    let both = f::VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT
        | f::VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT;
    for kind in [
        f::VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT,
        f::VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT,
    ] {
        let drm = f::VkPhysicalDeviceImageDrmFormatModifierInfoEXT {
            sType: f::VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGE_DRM_FORMAT_MODIFIER_INFO_EXT,
            drmFormatModifier: modifier,
            sharingMode: image.sharingMode,
            queueFamilyIndexCount: image.queueFamilyIndexCount,
            pQueueFamilyIndices: image.pQueueFamilyIndices,
            ..Default::default()
        };
        let external = f::VkPhysicalDeviceExternalImageFormatInfo {
            sType: f::VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_IMAGE_FORMAT_INFO,
            pNext: if image.tiling == f::VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT {
                (&raw const drm).cast()
            } else {
                ptr::null()
            },
            handleType: kind,
        };
        let info = f::VkPhysicalDeviceImageFormatInfo2 {
            sType: f::VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGE_FORMAT_INFO_2,
            pNext: (&raw const external).cast(),
            format: image.format,
            type_: image.imageType,
            tiling: image.tiling,
            usage: image.usage,
            flags: image.flags,
        };
        let mut export = f::VkExternalImageFormatProperties {
            sType: f::VK_STRUCTURE_TYPE_EXTERNAL_IMAGE_FORMAT_PROPERTIES,
            ..Default::default()
        };
        let mut properties = f::VkImageFormatProperties2 {
            sType: f::VK_STRUCTURE_TYPE_IMAGE_FORMAT_PROPERTIES_2,
            pNext: (&raw mut export).cast(),
            ..Default::default()
        };
        // SAFETY: exact Vulkan query chain and initialized outputs remain live;
        // image queue indices and physical device are borrowed from the caller.
        if unsafe { query(physical, &info, &mut properties) } != f::VK_SUCCESS {
            return false;
        }
        let memory = export.externalMemoryProperties;
        let limits = properties.imageFormatProperties;
        if memory.externalMemoryFeatures & f::VK_EXTERNAL_MEMORY_FEATURE_EXPORTABLE_BIT == 0
            || memory.compatibleHandleTypes & both != both
            || limits.maxExtent.width < image.extent.width
            || limits.maxExtent.height < image.extent.height
            || limits.maxExtent.depth < image.extent.depth
            || limits.maxMipLevels < image.mipLevels
            || limits.maxArrayLayers < image.arrayLayers
            || limits.sampleCounts & image.samples == 0
        {
            return false;
        }
    }
    true
}
unsafe fn probe(
    get: GlProc,
    current: Current,
    vk: f::PFN_vkGetInstanceProcAddr,
    instance: f::VkInstance,
    physical: f::VkPhysicalDevice,
    native_fence: bool,
) -> Option<(Gl, u32)> {
    let vk = vk?;
    // SAFETY: caller's resolvers return Khronos entry points for live GL/Vulkan
    // instances; each address is checked and every query has bounded outputs.
    unsafe {
        if current().is_null() {
            return None;
        }
        let integer = proc!(get, c"glGetIntegerv", unsafe extern "C" fn(u32, *mut i32));
        let string = proc!(
            get,
            c"glGetStringi",
            unsafe extern "C" fn(u32, u32) -> *const u8
        );
        let uuid = proc!(
            get,
            c"glGetUnsignedBytevEXT",
            unsafe extern "C" fn(u32, *mut u8)
        );
        let device_uuid = proc!(
            get,
            c"glGetUnsignedBytei_vEXT",
            unsafe extern "C" fn(u32, u32, *mut u8)
        );
        macro_rules! load {
            ($name:literal, $kind:ty) => {{
                let pointer = vk(instance, $name.as_ptr())?;
                std::mem::transmute::<unsafe extern "C" fn(), $kind>(pointer)
            }};
        }
        let properties = load!(
            c"vkGetPhysicalDeviceProperties2KHR",
            unsafe extern "C" fn(f::VkPhysicalDevice, *mut f::VkPhysicalDeviceProperties2)
        );
        let semaphore_properties = load!(
            c"vkGetPhysicalDeviceExternalSemaphorePropertiesKHR",
            unsafe extern "C" fn(
                f::VkPhysicalDevice,
                *const f::VkPhysicalDeviceExternalSemaphoreInfo,
                *mut f::VkExternalSemaphoreProperties,
            )
        );
        let queues = load!(
            c"vkGetPhysicalDeviceQueueFamilyProperties",
            unsafe extern "C" fn(f::VkPhysicalDevice, *mut u32, *mut f::VkQueueFamilyProperties)
        );
        let mut count = 0;
        integer(f::GL_NUM_EXTENSIONS, &mut count);
        if !(1..=65536).contains(&count) {
            return None;
        }
        let mut semaphore = false;
        let mut fd = false;
        for index in 0..count as u32 {
            let pointer = string(f::GL_EXTENSIONS, index);
            if pointer.is_null() {
                continue;
            }
            match CStr::from_ptr(pointer.cast()).to_bytes() {
                b"GL_EXT_semaphore" => semaphore = true,
                b"GL_EXT_semaphore_fd" => fd = true,
                _ => {}
            }
        }
        if !semaphore || !fd {
            return None;
        }
        let gl = Gl::load(get, current)?;
        let mut id = f::VkPhysicalDeviceIDProperties {
            sType: f::VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ID_PROPERTIES,
            ..Default::default()
        };
        let mut props = f::VkPhysicalDeviceProperties2 {
            sType: f::VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2,
            pNext: (&raw mut id).cast(),
            ..Default::default()
        };
        properties(physical, &mut props);
        let mut driver = [0u8; 16];
        let mut device = [0u8; 16];
        uuid(f::GL_DRIVER_UUID_EXT, driver.as_mut_ptr());
        device_uuid(f::GL_DEVICE_UUID_EXT, 0, device.as_mut_ptr());
        if (gl.error)() != f::GL_NO_ERROR || driver != id.driverUUID || device != id.deviceUUID {
            return None;
        }
        for kind in [
            f::VK_EXTERNAL_SEMAPHORE_HANDLE_TYPE_OPAQUE_FD_BIT,
            f::VK_EXTERNAL_SEMAPHORE_HANDLE_TYPE_SYNC_FD_BIT,
        ]
        .into_iter()
        .take(if native_fence { 1 } else { 2 })
        {
            let info = f::VkPhysicalDeviceExternalSemaphoreInfo {
                sType: f::VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_SEMAPHORE_INFO,
                handleType: kind,
                ..Default::default()
            };
            let mut support = f::VkExternalSemaphoreProperties {
                sType: f::VK_STRUCTURE_TYPE_EXTERNAL_SEMAPHORE_PROPERTIES,
                ..Default::default()
            };
            semaphore_properties(physical, &info, &mut support);
            if support.externalSemaphoreFeatures & f::VK_EXTERNAL_SEMAPHORE_FEATURE_EXPORTABLE_BIT
                == 0
            {
                return None;
            }
        }
        let mut count = 0;
        queues(physical, &mut count, ptr::null_mut());
        if !(1..=64).contains(&count) {
            return None;
        }
        let mut families = [f::VkQueueFamilyProperties::default(); 64];
        queues(physical, &mut count, families.as_mut_ptr());
        let family = families.get(..count as usize)?.iter().position(|family| {
            family.queueCount != 0 && family.queueFlags & f::VK_QUEUE_GRAPHICS_BIT != 0
        })?;
        Some((gl, family as u32))
    }
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_gl_sync_probe(
    get: Option<GlProc>,
    current: Option<Current>,
    vk: f::PFN_vkGetInstanceProcAddr,
    instance: f::VkInstance,
    physical: f::VkPhysicalDevice,
    native_fence: bool,
) -> i32 {
    let (Some(get), Some(current)) = (get, current) else {
        return -1;
    };
    // SAFETY: caller retains the GL context, Vulkan instance/physical device and
    // their procedure libraries throughout this probe and admitted bridge use.
    let Some((gl, family)) = (unsafe { probe(get, current, vk, instance, physical, native_fence) })
    else {
        return -1;
    };
    with_state(-1, |state| {
        state.gl = Some(gl);
        family as i32
    })
}
