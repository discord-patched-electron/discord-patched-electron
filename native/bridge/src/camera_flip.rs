// SPDX-License-Identifier: MIT
//! Camera-only mirroring after successful native conversion. A bounded row
//! slice makes pixel reversal safe; unsafe code is confined to the native ABI
//! and the startup-only, checksum-scoped call-site installation.
use crate::{
    ffi,
    node::{self, Env, Info, Value},
};
use std::{
    ffi::{CStr, c_void},
    ptr,
    sync::{
        OnceLock,
        atomic::{AtomicBool, AtomicUsize, Ordering},
    },
};
type Convert = unsafe extern "C" fn(
    *const u8,
    usize,
    *mut u8,
    i32,
    *mut u8,
    i32,
    *mut u8,
    i32,
    i32,
    i32,
    i32,
    i32,
    i32,
    i32,
    i32,
    u32,
) -> i32;
static ORIGINAL: OnceLock<Convert> = OnceLock::new();
static ENABLED: AtomicBool = AtomicBool::new(false);
static INSTALLED: AtomicUsize = AtomicUsize::new(0);
unsafe fn mirror(plane: *mut u8, stride: i32, width: i32, height: i32) {
    if plane.is_null()
        || width <= 0
        || height <= 0
        || width > 8192
        || height > 8192
        || stride.unsigned_abs() < width as u32
    {
        return;
    }
    for row in 0..height as isize {
        let Some(offset) = row.checked_mul(stride as isize) else {
            return;
        };
        // SAFETY: successful native I420 conversion guarantees each logical row
        // is writable for width bytes, including negative-stride output. The
        // borrow ends before moving to the next disjoint row; padding is excluded.
        unsafe { std::slice::from_raw_parts_mut(plane.offset(offset), width as usize) }.reverse();
    }
}
unsafe extern "C" fn convert(
    src: *const u8,
    length: usize,
    y: *mut u8,
    ys: i32,
    u: *mut u8,
    us: i32,
    v: *mut u8,
    vs: i32,
    cx: i32,
    cy: i32,
    sw: i32,
    sh: i32,
    cw: i32,
    ch: i32,
    rotation: i32,
    fourcc: u32,
) -> i32 {
    let Some(original) = ORIGINAL.get() else {
        return -1;
    };
    // SAFETY: all arguments retain the exact reviewed native converter ABI.
    let result = unsafe {
        original(
            src, length, y, ys, u, us, v, vs, cx, cy, sw, sh, cw, ch, rotation, fourcc,
        )
    };
    if result == 0 && ENABLED.load(Ordering::Relaxed) {
        let Some(ch) = ch.checked_abs() else {
            return result;
        };
        let (width, height) = if rotation == 90 || rotation == 270 {
            (ch, cw)
        } else {
            (cw, ch)
        };
        if !(1..=8192).contains(&width) || !(1..=8192).contains(&height) {
            return result;
        }
        // SAFETY: successful conversion establishes the I420 plane ownership,
        // geometry and rotation above; each plane is modified independently.
        unsafe {
            mirror(y, ys, width, height);
            mirror(u, us, (width + 1) / 2, (height + 1) / 2);
            mirror(v, vs, (width + 1) / 2, (height + 1) / 2);
        }
    }
    result
}
struct Adapter {
    page: *mut c_void,
    size: usize,
}
impl Drop for Adapter {
    fn drop(&mut self) {
        // SAFETY: this guard exclusively owns its mmap allocation.
        unsafe {
            libc::munmap(self.page, self.size);
        }
    }
}
fn near(site: usize, size: usize) -> Option<Adapter> {
    let center = site / size * size;
    for distance in (0x10000..0x70000000).step_by(0x10000) {
        for address in [center.checked_sub(distance), center.checked_add(distance)]
            .into_iter()
            .flatten()
        {
            // SAFETY: MAP_FIXED_NOREPLACE reserves only unused addresses, never
            // replaces another mapping. The returned allocation is owned below.
            let page = unsafe {
                libc::mmap(
                    address as *mut c_void,
                    size,
                    libc::PROT_READ | libc::PROT_WRITE,
                    libc::MAP_PRIVATE | libc::MAP_ANONYMOUS | libc::MAP_FIXED_NOREPLACE,
                    -1,
                    0,
                )
            };
            if page == libc::MAP_FAILED {
                continue;
            }
            let adapter = Adapter { page, size };
            if page as usize == address {
                return Some(adapter);
            }
        }
    }
    None
}
unsafe fn install(env: Env, info: Info) -> Result<(), &'static CStr> {
    let api = node::api().ok_or(c"Node API unavailable")?;
    let mut args = [ptr::null_mut(); 3];
    let mut count = 3;
    // SAFETY: Node callback supplies live env/info; array holds all three outputs.
    if unsafe {
        (api.info)(
            env,
            info,
            &mut count,
            args.as_mut_ptr(),
            ptr::null_mut(),
            ptr::null_mut(),
        )
    } != 0
        || count != 3
    {
        return Err(c"Invalid camera flip metadata.");
    }
    let mut filename = [0u8; 4096];
    let mut length = 0;
    // SAFETY: filename is a bounded writable buffer for Node's terminated string.
    if unsafe {
        (api.string)(
            env,
            args[0],
            filename.as_mut_ptr().cast(),
            filename.len(),
            &mut length,
        )
    } != 0
        || length == 0
        || length >= filename.len() - 1
        || filename[..length].contains(&0)
    {
        return Err(c"Invalid camera flip metadata.");
    }
    let (mut call, mut target) = (0u32, 0u32);
    // SAFETY: convert the remaining live Node values into local scalar outputs.
    if unsafe {
        (api.uint)(env, args[1], &mut call) != 0 || (api.uint)(env, args[2], &mut target) != 0
    } {
        return Err(c"Invalid camera flip metadata.");
    }
    // SAFETY: Node terminated filename; NOLOAD forbids initializing an unrelated module.
    let handle =
        unsafe { libc::dlopen(filename.as_ptr().cast(), libc::RTLD_NOW | libc::RTLD_NOLOAD) };
    if handle.is_null() {
        return Err(c"The native camera module is not loaded.");
    }
    let module = node::Library(handle);
    let mut map: *mut ffi::link_map = ptr::null_mut();
    // SAFETY: live dlopen reference retains the public ELF link_map during validation.
    if unsafe { libc::dlinfo(handle, libc::RTLD_DI_LINKMAP, (&raw mut map).cast()) } != 0
        || map.is_null()
    {
        return Err(c"The native camera module is not loaded.");
    }
    // SAFETY: successful dlinfo provides the initialized link_map.
    let base = unsafe { (*map).l_addr } as usize;
    let site = base
        .checked_add(call as usize)
        .ok_or(c"Invalid camera offset")?;
    let target = base
        .checked_add(target as usize)
        .ok_or(c"Invalid camera target")?;
    let installed = INSTALLED.load(Ordering::Acquire);
    if installed != 0 {
        return if installed == site {
            Ok(())
        } else {
            Err(c"Camera module changed.")
        };
    }
    // Validate both complete instruction range and target against this module's
    // executable PT_LOAD entries, not just any readable process mapping.
    // SAFETY: this live dlopen module begins with its loader-validated ELF header.
    let header = unsafe { &*(base as *const libc::Elf64_Ehdr) };
    let executable = |address: usize, length: usize| {
        // SAFETY: loader validated this module's program-header table on load.
        let segments = unsafe {
            std::slice::from_raw_parts(
                (base + header.e_phoff as usize) as *const libc::Elf64_Phdr,
                header.e_phnum as usize,
            )
        };
        segments.iter().any(|p| {
            p.p_type == libc::PT_LOAD
                && p.p_flags & libc::PF_X != 0
                && address >= base + p.p_vaddr as usize
                && address
                    .checked_add(length)
                    .is_some_and(|end| end <= base + p.p_vaddr as usize + p.p_memsz as usize)
        })
    };
    if !executable(site, 5) || !executable(target, 1) {
        return Err(c"The native camera conversion call does not match.");
    }
    // SAFETY: full instruction lies in the retained executable module range.
    let before = unsafe { (site as *const u8).add(1).cast::<i32>().read_unaligned() };
    // SAFETY: first byte belongs to the same validated complete instruction.
    if unsafe { (site as *const u8).read() } != 0xe8
        || (site + 5).checked_add_signed(before as isize) != Some(target)
    {
        return Err(c"The native camera conversion call does not match.");
    }
    // SAFETY: sysconf has no pointers and retains no resources.
    let size = unsafe { libc::sysconf(libc::_SC_PAGESIZE) };
    if size <= 0 {
        return Err(c"Invalid page size");
    }
    let size = size as usize;
    let adapter = near(site, size).ok_or(c"Could not reserve the camera conversion adapter.")?;
    let displacement = i32::try_from(adapter.page as isize - (site + 5) as isize)
        .map_err(|_| c"Camera adapter outside call range")?;
    let mut jump = [0u8; 14];
    jump[..6].copy_from_slice(&[0xff, 0x25, 0, 0, 0, 0]);
    jump[6..].copy_from_slice(&(convert as *const () as usize).to_ne_bytes());
    // SAFETY: owned adapter mapping is writable and at least one page long.
    unsafe {
        ptr::copy_nonoverlapping(jump.as_ptr(), adapter.page.cast(), jump.len());
    }
    let first = site / size * size;
    let span = (site + 4) / size * size - first + size;
    let protection = node::permissions(site).ok_or(c"Missing camera code mapping")?;
    // SAFETY: publish immutable adapter executable before patching the reviewed
    // startup-only call. No engine/camera threads exist at this installation stage.
    if unsafe {
        libc::mprotect(adapter.page, size, libc::PROT_READ | libc::PROT_EXEC) != 0
            || libc::mprotect(first as *mut c_void, span, protection | libc::PROT_WRITE) != 0
    } {
        return Err(c"Could not install the camera conversion adapter.");
    }
    // SAFETY: target was validated as the checksum-reviewed converter's code address.
    let original = unsafe { std::mem::transmute::<usize, Convert>(target) };
    if let Some(previous) = ORIGINAL.get() {
        if *previous as usize != target {
            // SAFETY: installation has not modified code; restore the borrowed page.
            unsafe {
                libc::mprotect(first as *mut c_void, span, protection);
            }
            return Err(c"Camera conversion adapter already configured.");
        }
    } else {
        let _ = ORIGINAL.set(original);
    }
    // SAFETY: this startup call-site instruction is writable; original state is
    // initialized first. Roll back before releasing the adapter on any failure.
    unsafe {
        ((site + 1) as *mut i32).write_unaligned(displacement);
        if libc::mprotect(first as *mut c_void, span, protection) != 0 {
            ((site + 1) as *mut i32).write_unaligned(before);
            libc::mprotect(first as *mut c_void, span, protection);
            return Err(c"Could not restore camera code protection.");
        }
    }
    INSTALLED.store(site, Ordering::Release);
    std::mem::forget(adapter);
    std::mem::forget(module);
    Ok(())
}
unsafe extern "C" fn install_callback(env: Env, info: Info) -> Value {
    // SAFETY: Node owns env/info for this callback invocation.
    match std::panic::catch_unwind(|| unsafe { install(env, info) }) {
        Ok(Ok(())) => undefined(env),
        Ok(Err(error)) => node::failure(env, error),
        Err(_) => node::failure(env, c"Camera installation panicked"),
    }
}
fn undefined(env: Env) -> Value {
    let mut out = ptr::null_mut();
    if let Some(api) = node::api() {
        // SAFETY: invoked only within the current Node callback.
        unsafe {
            (api.undefined)(env, &mut out);
        }
    }
    out
}
unsafe extern "C" fn set_enabled(env: Env, info: Info) -> Value {
    let Some(api) = node::api() else {
        return ptr::null_mut();
    };
    let (mut value, mut count, mut enabled) = (ptr::null_mut(), 1, false);
    // SAFETY: Node supplies live values; all local outputs match the public API.
    if unsafe {
        (api.info)(
            env,
            info,
            &mut count,
            &mut value,
            ptr::null_mut(),
            ptr::null_mut(),
        ) != 0
            || count != 1
            || (api.get_bool)(env, value, &mut enabled) != 0
    } || INSTALLED.load(Ordering::Acquire) == 0
    {
        return node::failure(env, c"Camera flip is not initialized.");
    }
    ENABLED.store(enabled, Ordering::Relaxed);
    undefined(env)
}
pub unsafe fn register(env: Env, exports: Value) -> Value {
    let Some(api) = node::api() else {
        return ptr::null_mut();
    };
    for (name, callback) in [
        (
            c"install",
            install_callback as unsafe extern "C" fn(Env, Info) -> Value,
        ),
        (c"setEnabled", set_enabled),
    ] {
        let mut function = ptr::null_mut();
        // SAFETY: module init owns env/exports; static callback is retained by Node.
        if unsafe {
            (api.function)(
                env,
                name.as_ptr(),
                usize::MAX,
                Some(callback),
                ptr::null_mut(),
                &mut function,
            ) != 0
                || (api.property)(env, exports, name.as_ptr(), function) != 0
        } {
            return ptr::null_mut();
        }
    }
    exports
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::AtomicI32;
    static RESULT: AtomicI32 = AtomicI32::new(0);
    unsafe extern "C" fn fake(
        _src: *const u8,
        _len: usize,
        y: *mut u8,
        ys: i32,
        u: *mut u8,
        us: i32,
        v: *mut u8,
        vs: i32,
        _cx: i32,
        _cy: i32,
        _sw: i32,
        _sh: i32,
        cw: i32,
        ch: i32,
        rotation: i32,
        _fourcc: u32,
    ) -> i32 {
        let result = RESULT.load(Ordering::Relaxed);
        if result != 0 {
            return result;
        }
        let (w, h) = if rotation == 90 || rotation == 270 {
            (ch.abs(), cw)
        } else {
            (cw, ch.abs())
        };
        for (plane, stride, width, height, bias) in [
            (y, ys, w, h, 0),
            (u, us, (w + 1) / 2, (h + 1) / 2, 50),
            (v, vs, (w + 1) / 2, (h + 1) / 2, 100),
        ] {
            for row in 0..height {
                for x in 0..width {
                    // SAFETY: the test supplies three independent 16x16 allocations
                    // with either first-row or last-row base for its signed stride.
                    unsafe {
                        plane
                            .offset((row * stride + x) as isize)
                            .write((bias + row * w + x) as u8);
                    }
                }
            }
        }
        0
    }
    #[test]
    fn rotations_odd_planes_signed_strides_padding_and_failed_conversion() {
        let _ = ORIGINAL.set(fake);
        ENABLED.store(true, Ordering::Relaxed);
        RESULT.store(-1, Ordering::Relaxed);
        assert_eq!(
            // SAFETY: failing converter never reads or writes the deliberately null outputs.
            unsafe {
                convert(
                    ptr::null(),
                    0,
                    ptr::null_mut(),
                    0,
                    ptr::null_mut(),
                    0,
                    ptr::null_mut(),
                    0,
                    0,
                    0,
                    640,
                    480,
                    640,
                    480,
                    0,
                    0,
                )
            },
            -1
        );
        RESULT.store(0, Ordering::Relaxed);
        for rotation in [0, 90, 180, 270] {
            for cw in 1..=9 {
                for ch in 1..=9 {
                    for stride in [16, -16] {
                        let mut planes = [[222u8; 256]; 3];
                        let mut pointers = planes.each_mut().map(|p| p.as_mut_ptr());
                        if stride < 0 {
                            for pointer in &mut pointers {
                                // SAFETY: last-row base lies 240 bytes into its own 256-byte array.
                                *pointer = unsafe { pointer.add(240) };
                            }
                        }
                        let [y, u, v] = pointers;
                        ENABLED.store(false, Ordering::Relaxed);
                        assert_eq!(
                            // SAFETY: all buffers accommodate the bounded fake converter dimensions.
                            unsafe {
                                convert(
                                    ptr::null(),
                                    0,
                                    y,
                                    stride,
                                    u,
                                    stride,
                                    v,
                                    stride,
                                    0,
                                    0,
                                    cw,
                                    ch,
                                    cw,
                                    ch,
                                    rotation,
                                    0,
                                )
                            },
                            0
                        );
                        let before = planes;
                        ENABLED.store(true, Ordering::Relaxed);
                        assert_eq!(
                            // SAFETY: same still-live independent buffers, now with mirroring enabled.
                            unsafe {
                                convert(
                                    ptr::null(),
                                    0,
                                    y,
                                    stride,
                                    u,
                                    stride,
                                    v,
                                    stride,
                                    0,
                                    0,
                                    cw,
                                    ch,
                                    cw,
                                    ch,
                                    rotation,
                                    0,
                                )
                            },
                            0
                        );
                        let (w, h) = if rotation == 90 || rotation == 270 {
                            (ch, cw)
                        } else {
                            (cw, ch)
                        };
                        for plane in 0..3 {
                            for row in 0..16 {
                                for x in 0..16 {
                                    let (width, height) = if plane == 0 {
                                        (w, h)
                                    } else {
                                        ((w + 1) / 2, (h + 1) / 2)
                                    };
                                    let logical = if stride < 0 { 15 - row } else { row };
                                    let source = if logical < height && x < width {
                                        width - 1 - x
                                    } else {
                                        x
                                    };
                                    assert_eq!(
                                        planes[plane][(row * 16 + x) as usize],
                                        before[plane][(row * 16 + source) as usize]
                                    );
                                }
                            }
                        }
                        RESULT.store(-1, Ordering::Relaxed);
                        let previous = planes;
                        assert_eq!(
                            // SAFETY: failure must preserve all output and padding bytes.
                            unsafe {
                                convert(
                                    ptr::null(),
                                    0,
                                    y,
                                    stride,
                                    u,
                                    stride,
                                    v,
                                    stride,
                                    0,
                                    0,
                                    cw,
                                    ch,
                                    cw,
                                    ch,
                                    rotation,
                                    0,
                                )
                            },
                            -1
                        );
                        assert_eq!(planes, previous);
                        RESULT.store(0, Ordering::Relaxed);
                    }
                }
            }
        }
    }
}
