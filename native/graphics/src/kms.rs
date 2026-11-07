// SPDX-License-Identifier: GPL-3.0-only
use crate::{
    context::Context,
    ffi as f,
    target::{Program, Target},
};
use dpe_core::handles::Handles;
use std::{
    cell::RefCell,
    ffi::{CStr, c_char, c_void},
    os::fd::{AsRawFd, IntoRawFd},
    ptr,
    rc::Rc,
};

struct Gpu {
    program: Rc<Program>,
    preview: Option<Target>,
}
struct Buffer {
    target: Target,
    program: Rc<Program>,
}
thread_local! {
    static GPUS: RefCell<Handles<Rc<RefCell<Gpu>>>> = RefCell::new(Handles::new(8));
    static BUFFERS: RefCell<Handles<Rc<Buffer>>> = RefCell::new(Handles::new(64));
}
fn gpu(token: *mut c_void) -> Option<Rc<RefCell<Gpu>>> {
    GPUS.with(|table| table.borrow().get(token as usize))
}
fn buffer(token: *mut c_void) -> Option<Rc<Buffer>> {
    BUFFERS.with(|table| table.borrow().get(token as usize))
}
pub(crate) fn fit(source: (u32, u32), requested: (u32, u32)) -> Option<(u32, u32)> {
    if source.0 == 0
        || source.1 == 0
        || !(1..=480).contains(&requested.0)
        || !(1..=270).contains(&requested.1)
    {
        return None;
    }
    let (mut width, mut height) = requested;
    if u64::from(width) * u64::from(source.1) > u64::from(height) * u64::from(source.0) {
        width = (u64::from(height) * u64::from(source.0) / u64::from(source.1)) as u32;
    } else {
        height = (u64::from(width) * u64::from(source.1) / u64::from(source.0)) as u32;
    }
    (width != 0 && height != 0).then_some((width, height))
}
pub(crate) fn render(
    target: &Target,
    program: &Program,
    primary: &f::gsr_kms_response_item,
    cursor: Option<&f::gsr_kms_response_item>,
    canvas: (u32, u32),
) -> bool {
    target.bind()
        && program.draw(primary, false, canvas)
        && cursor.is_none_or(|item| program.draw(item, true, canvas))
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_kms_gpu_create(node: *const c_char) -> *mut c_void {
    if node.is_null() {
        return ptr::null_mut();
    }
    // SAFETY: C API supplies a live NUL-terminated device pathname for this call.
    let Some(context) = Context::open(unsafe { CStr::from_ptr(node) }) else {
        return ptr::null_mut();
    };
    let Some(program) = Program::new(&context) else {
        return ptr::null_mut();
    };
    GPUS.with(|table| {
        table
            .borrow()
            .insert(Rc::new(RefCell::new(Gpu {
                program: Rc::new(program),
                preview: None,
            })))
            .map_or(ptr::null_mut(), |token| token.get() as *mut c_void)
    })
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_kms_gpu_destroy(token: *mut c_void) {
    let removed = GPUS.with(|table| table.borrow().remove(token as usize));
    drop(removed);
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_kms_gpu_fd(token: *mut c_void) -> i32 {
    gpu(token)
        .and_then(|gpu| {
            gpu.try_borrow()
                .ok()
                .map(|gpu| gpu.program.context.file.as_raw_fd())
        })
        .unwrap_or(-1)
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_kms_buffer_create(
    token: *mut c_void,
    width: u32,
    height: u32,
    packet: *mut f::dpe_vk_packet,
    descriptors: *mut i32,
) -> *mut c_void {
    if packet.is_null() || descriptors.is_null() {
        return ptr::null_mut();
    }
    let Some(gpu) = gpu(token) else {
        return ptr::null_mut();
    };
    let Ok(gpu) = gpu.try_borrow() else {
        return ptr::null_mut();
    };
    let Some(target) = Target::new(&gpu.program.context, width, height) else {
        return ptr::null_mut();
    };
    let fd = target.export.as_raw_fd();
    let mut description = f::dpe_vk_packet {
        magic: f::DPE_VK_MAGIC,
        version: f::DPE_VK_VERSION as u16,
        type_: f::DPE_GRAPHICS_TEXTURE,
        width,
        height,
        format: f::DPE_GRAPHICS_ARGB,
        modifier: 0,
        nfd: 1,
        ..Default::default()
    };
    description.strides[0] = target.stride;
    let Some(token) = BUFFERS.with(|table| {
        table
            .borrow()
            .insert(Rc::new(Buffer {
                target,
                program: gpu.program.clone(),
            }))
            .ok()
    }) else {
        return ptr::null_mut();
    };
    // SAFETY: caller supplies one writable packet and four descriptor slots;
    // descriptors remain borrowed from the registered buffer until its destroy.
    unsafe {
        packet.write(description);
        ptr::copy_nonoverlapping([fd, -1, -1, -1].as_ptr(), descriptors, 4);
    }
    token.get() as *mut c_void
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_kms_buffer_destroy(token: *mut c_void) {
    let removed = BUFFERS.with(|table| table.borrow().remove(token as usize));
    drop(removed);
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_kms_buffer_render(
    token: *mut c_void,
    scene: *const f::dpe_kms_scene,
) -> i32 {
    let Some(buffer) = buffer(token) else {
        return -1;
    };
    // SAFETY: C scene and its optional item records remain live during this call.
    let Some(scene) = (unsafe { scene.as_ref() }) else {
        return -1;
    };
    // SAFETY: same scoped C scene contract; DMA-BUFs are borrowed until import returns.
    let (Some(primary), cursor) = (unsafe { (scene.primary.as_ref(), scene.cursor.as_ref()) })
    else {
        return -1;
    };
    if !render(
        &buffer.target,
        &buffer.program,
        primary,
        cursor,
        (buffer.target.width, buffer.target.height),
    ) {
        return -1;
    }
    buffer.program.fence().map_or(-1, IntoRawFd::into_raw_fd)
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_kms_gpu_preview(
    token: *mut c_void,
    scene: *const f::dpe_kms_scene,
    source_width: u32,
    source_height: u32,
    width: *mut u32,
    height: *mut u32,
    pixels: *mut u8,
) -> bool {
    if width.is_null() || height.is_null() || pixels.is_null() {
        return false;
    }
    // SAFETY: caller provides two initialized dimension outputs, one live scene
    // and a pixel destination sized to its requested width*height*4.
    let (requested, scene) = unsafe { ((*width, *height), scene.as_ref()) };
    let Some(size) = fit((source_width, source_height), requested) else {
        return false;
    };
    let Some(scene) = scene else {
        return false;
    };
    // SAFETY: caller's scene records and their descriptors remain live until return.
    let (Some(primary), cursor) = (unsafe { (scene.primary.as_ref(), scene.cursor.as_ref()) })
    else {
        return false;
    };
    let Some(gpu) = gpu(token) else {
        return false;
    };
    let Ok(mut gpu) = gpu.try_borrow_mut() else {
        return false;
    };
    if gpu
        .preview
        .as_ref()
        .is_some_and(|preview| (preview.width, preview.height) != size)
    {
        gpu.preview = None;
    }
    if gpu.preview.is_none() {
        gpu.preview = Target::new(&gpu.program.context, size.0, size.1);
    }
    let Some(target) = &gpu.preview else {
        return false;
    };
    // SAFETY: fit only reduces the caller's bounded pixel capacity.
    let pixels =
        unsafe { std::slice::from_raw_parts_mut(pixels, size.0 as usize * size.1 as usize * 4) };
    if !render(
        target,
        &gpu.program,
        primary,
        cursor,
        (source_width, source_height),
    ) || !target.read_bgra(pixels)
    {
        return false;
    }
    // SAFETY: dimension outputs remain writable throughout the operation.
    unsafe {
        *width = size.0;
        *height = size.1;
    }
    true
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn preview_bounds_and_stale_handles_do_not_open_a_gpu() {
        assert_eq!(fit((1920, 1080), (480, 270)), Some((480, 270)));
        assert_eq!(fit((3840, 2160), (320, 270)), Some((320, 180)));
        assert_eq!(fit((1080, 1920), (480, 270)), Some((151, 270)));
        for bad in [(0, 1), (481, 270), (480, 271)] {
            assert_eq!(fit((1920, 1080), bad), None);
        }
        assert_eq!(fit((0, 1080), (480, 270)), None);
        assert_eq!(dpe_kms_gpu_fd(usize::MAX as *mut c_void), -1);
        dpe_kms_gpu_destroy(usize::MAX as *mut c_void);
        dpe_kms_buffer_destroy(usize::MAX as *mut c_void);
        // SAFETY: rejected invalid token/NULL pointers are explicitly accepted.
        unsafe {
            assert!(
                dpe_kms_buffer_create(ptr::null_mut(), 480, 270, ptr::null_mut(), ptr::null_mut())
                    .is_null()
            );
            assert_eq!(dpe_kms_buffer_render(ptr::null_mut(), ptr::null()), -1);
        }
    }
}
