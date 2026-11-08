// SPDX-License-Identifier: GPL-3.0-only
//! Thread-confined opaque GPU owners. Buffers retain their context; completion
//! tokens are never reused and no Rust borrow spans a consumer callback.
use crate::{
    context::Context,
    ffi as f,
    game_render::{CursorOwner, Input, Renderer, Thumbnail},
    target::Target,
};
use dpe_core::handles::Handles;
use std::{
    cell::{Cell, RefCell},
    ffi::{CStr, c_char, c_void},
    os::fd::{AsRawFd, OwnedFd},
    ptr::{self, NonNull},
    rc::Rc,
};
type Done = unsafe extern "C" fn(*mut c_void, bool);
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
    unsafe fn borrowed(raw: *mut f::pw_loop) -> Option<Self> {
        // SAFETY: caller retains the serialized loop until every buffer/GPU
        // owner is destroyed. Read only the documented v0 method prefix.
        let utils = unsafe { raw.as_ref()?.utils.as_ref()? };
        let methods = NonNull::new(
            utils
                .iface
                .cb
                .funcs
                .cast_mut()
                .cast::<f::spa_loop_utils_methods>(),
        )?;
        // SAFETY: the native SPA v0 methods table includes both selected slots.
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
    fn watch(self, fd: i32, token: usize) -> Option<Watch> {
        // SAFETY: registered token pins the buffer, caller retains fd through
        // registration, and the live loop borrows it (close=false).
        let source = unsafe {
            (self.add)(
                self.data,
                fd,
                f::SPA_IO_IN | f::SPA_IO_ERR | f::SPA_IO_HUP,
                false,
                Some(ready),
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
        // SAFETY: creating thread/serialized loop retains this uniquely owned
        // source; synchronous removal revokes callbacks before descriptor close.
        unsafe {
            (self.loop_.destroy)(self.loop_.data, self.source.as_ptr());
        }
    }
}
struct State {
    renderer: Renderer,
    cursor: Option<CursorOwner>,
    input: Option<Input>,
    preview: Option<Thumbnail>,
    size: (u32, u32),
    flip: bool,
    window: u32,
}
struct Gpu {
    state: RefCell<State>,
    uuid: [u8; 16],
    loop_: Option<Loop>,
    pending: Cell<Option<usize>>,
    retired: Cell<bool>,
}
struct Completion {
    token: usize,
    watch: Option<Watch>,
    // While add_io reenters, the registering stack retains this descriptor;
    // ownership moves here only after registration returns for this generation.
    fd: Option<OwnedFd>,
    raw_fd: i32,
    done: Done,
    data: usize,
}
struct Buffer {
    gpu: Rc<Gpu>,
    target: Target,
    completion: RefCell<Option<Completion>>,
    retired: Cell<bool>,
}
thread_local! {
    static GPUS: Handles<Rc<Gpu>> = Handles::new(8);
    static BUFFERS: Handles<Rc<Buffer>> = Handles::new(64);
    // Entries clone existing owners. After bounded warm-up, frame completion
    // reuses this table's allocation and creates no per-frame Rust heap object.
    static PENDING: Handles<Rc<Buffer>> = Handles::new(8);
}
fn gpu(token: *mut c_void) -> Option<Rc<Gpu>> {
    GPUS.with(|t| t.get(token as usize))
}
fn buffer(token: *mut c_void) -> Option<Rc<Buffer>> {
    BUFFERS.with(|t| t.get(token as usize))
}
fn cancel(token: usize) -> Option<Completion> {
    let buffer = PENDING.with(|t| t.remove(token))?;
    if buffer.gpu.pending.get() == Some(token) {
        buffer.gpu.pending.set(None);
    }
    let completion = buffer.completion.borrow_mut().take();
    completion.filter(|c| c.token == token)
}
fn signaled(fd: i32) -> bool {
    let mut info = f::sync_file_info::default();
    // SAFETY: the completion/registration owns this sync-file, and the fixed
    // initialized UAPI output requests no foreign fence array.
    unsafe {
        libc::ioctl(
            fd,
            libc::_IOWR::<f::sync_file_info>(b'>' as u32, 4),
            &mut info,
        ) == 0
            && info.status > 0
    }
}
unsafe extern "C" fn ready(data: *mut c_void, fd: i32, mask: u32) {
    let Some(mut completion) = cancel(data as usize) else {
        return;
    };
    let success = fd == completion.raw_fd
        && mask & f::SPA_IO_IN != 0
        && mask & (f::SPA_IO_ERR | f::SPA_IO_HUP) == 0
        && signaled(fd);
    drop(completion.watch.take());
    drop(completion.fd.take());
    // SAFETY: callback owner remains registered until this exact completion or
    // cancellation. All sources/borrows are retired before consumer reentry.
    unsafe {
        (completion.done)(completion.data as *mut c_void, success);
    }
}
/// # Safety
/// node is terminated; an optional loop stays serialized on this thread and
/// outlives every GPU/buffer handle. NULL loop supports preview/import only.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_vk_gpu_create(
    node: *const c_char,
    loop_: *mut f::pw_loop,
    width: u32,
    height: u32,
) -> *mut c_void {
    if node.is_null() || !(1..=8192).contains(&width) || !(1..=8192).contains(&height) {
        return ptr::null_mut();
    }
    // SAFETY: terminated node path is borrowed only by synchronous creation.
    let Some(context) = Context::open(unsafe { CStr::from_ptr(node) }) else {
        return ptr::null_mut();
    };
    let Some(renderer) = Renderer::new(&context) else {
        return ptr::null_mut();
    };
    let Some(uuid) = crate::game_render::device_uuid(&context) else {
        return ptr::null_mut();
    };
    // SAFETY: optional externally retained serialized loop contract above.
    let loop_ = unsafe { Loop::borrowed(loop_) };
    GPUS.with(|table| {
        table
            .insert(Rc::new(Gpu {
                state: RefCell::new(State {
                    renderer,
                    cursor: None,
                    input: None,
                    preview: None,
                    size: (width, height),
                    flip: false,
                    window: 0,
                }),
                uuid,
                loop_,
                pending: Cell::new(None),
                retired: Cell::new(false),
            }))
            .map_or(ptr::null_mut(), |token| token.get() as *mut c_void)
    })
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_vk_gpu_destroy(token: *mut c_void) {
    if let Some(owner) = GPUS.with(|t| t.remove(token as usize)) {
        owner.retired.set(true);
        if let Some(token) = owner.pending.get() {
            drop(cancel(token));
        }
    }
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_vk_gpu_uuid(token: *mut c_void) -> *const u8 {
    gpu(token).map_or(ptr::null(), |owner| owner.uuid.as_ptr())
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_vk_gpu_fd(token: *mut c_void) -> i32 {
    gpu(token)
        .and_then(|owner| {
            owner
                .state
                .try_borrow()
                .ok()
                .map(|s| s.renderer.context.file.as_raw_fd())
        })
        .unwrap_or(-1)
}
/// # Safety
/// Caller retains one packet and four live DMA-BUF descriptor slots through
/// this synchronous import. EGL acquires storage; the caller keeps FD ownership.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_vk_gpu_import(
    token: *mut c_void,
    packet: *const f::dpe_vk_packet,
    fds: *const i32,
) -> i32 {
    let Some(owner) = gpu(token) else {
        return -1;
    };
    if packet.is_null() || fds.is_null() || owner.pending.get().is_some() {
        return -1;
    }
    let Ok(mut state) = owner.state.try_borrow_mut() else {
        return -1;
    };
    // SAFETY: caller supplies complete initialized packet and four descriptors.
    let (packet, fds) = unsafe { (&*packet, &*fds.cast::<[i32; 4]>()) };
    if !state.renderer.context.current() {
        return -1;
    }
    let Some(input) = Input::new(&state.renderer.context, packet, fds) else {
        return -1;
    };
    state.input = Some(input);
    state.size = (packet.width, packet.height);
    state.flip = packet.flags & f::DPE_VK_FLIP != 0;
    state.window = u32::from_ne_bytes(std::array::from_fn(|i| packet.name[i] as u8));
    0
}
/// # Safety
/// Caller supplies writable SPA data and chunk records, borrowed only here.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_vk_buffer_create(
    token: *mut c_void,
    data: *mut f::spa_data,
    width: u32,
    height: u32,
) -> *mut c_void {
    let Some(owner) = gpu(token) else {
        return ptr::null_mut();
    };
    // SAFETY: caller retains optional writable descriptor/chunk during creation.
    let Some(data) = (unsafe { data.as_mut() }) else {
        return ptr::null_mut();
    };
    if data.chunk.is_null() {
        return ptr::null_mut();
    }
    let Ok(state) = owner.state.try_borrow() else {
        return ptr::null_mut();
    };
    let Some(target) = Target::new(&state.renderer.context, width, height) else {
        return ptr::null_mut();
    };
    if owner.retired.get() {
        return ptr::null_mut();
    }
    let (fd, stride) = (target.export.as_raw_fd(), target.stride);
    let Ok(id) = BUFFERS.with(|table| {
        table.insert(Rc::new(Buffer {
            gpu: owner.clone(),
            target,
            completion: RefCell::new(None),
            retired: Cell::new(false),
        }))
    }) else {
        return ptr::null_mut();
    };
    data.type_ = f::DPE_GRAPHICS_DATA_DMABUF;
    data.flags = f::DPE_GRAPHICS_DATA_RW;
    data.fd = i64::from(fd);
    data.mapoffset = 0;
    data.maxsize = stride * height;
    data.data = ptr::null_mut();
    // SAFETY: checked nonnull chunk is caller-owned initialized writable storage.
    unsafe {
        data.chunk.write(f::spa_chunk {
            size: data.maxsize,
            stride: stride as i32,
            ..Default::default()
        });
    }
    id.get() as *mut c_void
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_vk_buffer_destroy(token: *mut c_void) {
    if let Some(owner) = BUFFERS.with(|t| t.remove(token as usize)) {
        owner.retired.set(true);
        let pending = owner.completion.borrow().as_ref().map(|c| c.token);
        if let Some(token) = pending {
            drop(cancel(token));
        }
    }
}
/// # Safety
/// Callback/data remain valid until exact completion or buffer/GPU retirement.
/// Calls and native loop callbacks are serialized on the creating thread.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_vk_buffer_copy(
    token: *mut c_void,
    done: Option<Done>,
    data: *mut c_void,
) -> i32 {
    let (Some(buffer), Some(done)) = (buffer(token), done) else {
        return -libc::EINVAL;
    };
    let owner = &buffer.gpu;
    if buffer.retired.get() || owner.retired.get() || owner.pending.get().is_some() {
        return -libc::EINVAL;
    }
    let Some(loop_) = owner.loop_ else {
        return -libc::EINVAL;
    };
    let fence = {
        let Ok(mut state) = owner.state.try_borrow_mut() else {
            return -libc::EINVAL;
        };
        if (buffer.target.width, buffer.target.height) != state.size {
            return -libc::EAGAIN;
        }
        let Some(input) = &state.input else {
            return -libc::EINVAL;
        };
        if !buffer.target.bind() || !state.renderer.draw(input, state.flip, state.size) {
            return -1;
        }
        let State {
            renderer,
            cursor,
            window,
            size,
            ..
        } = &mut *state;
        cursor
            .get_or_insert_with(CursorOwner::new)
            .draw(renderer, *window, *size);
        // SAFETY: scalar error query on the retained current GL context.
        if unsafe { f::glGetError() } != f::GL_NO_ERROR {
            return -1;
        }
        let Some(fence) = state.renderer.fence() else {
            return -1;
        };
        fence
    };
    if buffer.retired.get() || owner.retired.get() {
        return -libc::ECANCELED;
    }
    let Ok(id) = PENDING.with(|t| t.insert(buffer.clone())) else {
        return -libc::EBUSY;
    };
    let id = id.get();
    owner.pending.set(Some(id));
    *buffer.completion.borrow_mut() = Some(Completion {
        token: id,
        watch: None,
        fd: None,
        raw_fd: fence.as_raw_fd(),
        done,
        data: data as usize,
    });
    let watch = loop_.watch(fence.as_raw_fd(), id);
    if let Some(watch) = watch {
        let mut completion = buffer.completion.borrow_mut();
        if let Some(c) = completion.as_mut().filter(|c| c.token == id) {
            c.watch = Some(watch);
            c.fd = Some(fence);
        } else {
            drop(completion);
            drop(watch);
        } // synchronous completion/retirement during add_io
        0
    } else {
        drop(cancel(id));
        -1
    }
}
/// # Safety
/// Caller grants requested_width*requested_height*4 writable bytes (480x270
/// maximum) and writable dimension outputs. Readback follows GPU downscaling.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_vk_gpu_preview(
    token: *mut c_void,
    width: *mut u32,
    height: *mut u32,
    pixels: *mut u8,
) -> i32 {
    let Some(owner) = gpu(token) else {
        return -1;
    };
    if width.is_null() || height.is_null() || pixels.is_null() || owner.pending.get().is_some() {
        return -1;
    }
    let Ok(mut state) = owner.state.try_borrow_mut() else {
        return -1;
    };
    // SAFETY: caller provides both live scalar outputs.
    let Some(size) = crate::kms::fit(state.size, unsafe { (*width, *height) }) else {
        return -1;
    };
    if state.input.is_none() || !state.renderer.context.current() {
        return -1;
    }
    if state
        .preview
        .as_ref()
        .is_none_or(|target| target.size != size)
    {
        state.preview = Thumbnail::new(&state.renderer.context, size);
    }
    let Some(target) = &state.preview else {
        return -1;
    };
    target.bind();
    if !state.renderer.draw(
        state.input.as_ref().expect("checked input"),
        state.flip,
        size,
    ) {
        return -1;
    }
    // SAFETY: fit only reduces the caller's bounded allocation.
    let pixels =
        unsafe { std::slice::from_raw_parts_mut(pixels, size.0 as usize * size.1 as usize * 4) };
    if !target.read(pixels) {
        return -1;
    }
    // SAFETY: dimensions remain writable throughout the call.
    unsafe {
        *width = size.0;
        *height = size.1;
    }
    0
}
