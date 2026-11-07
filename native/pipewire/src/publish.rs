// SPDX-License-Identifier: MIT
//! Owned DRM publication for Rust sources. GPU storage and loop lifetimes stay
//! explicit; only the borrowed native metadata boundary remains unsafe.
use crate::{ffi as f, relay, source::MainLoop};
use std::{
    cell::{Cell, RefCell},
    ffi::c_void,
    os::fd::{AsRawFd, BorrowedFd, OwnedFd},
    ptr::NonNull,
    rc::Rc,
};
#[derive(Clone, Copy)]
pub struct Plane<'a> {
    pub descriptor: BorrowedFd<'a>,
    pub stride: u32,
    pub offset: u32,
}
pub struct Image<'a> {
    pub generation: u64,
    pub width: u32,
    pub height: u32,
    pub format: u32,
    pub modifier: u64,
    pub planes: &'a [Plane<'a>],
}
struct Completion {
    busy: Cell<bool>,
    result: Cell<Option<bool>>,
}
pub struct Publisher {
    token: usize,
    owner: Rc<RefCell<relay::Relay>>,
    completion: Box<Completion>,
    _loop: Rc<MainLoop>,
}
impl Publisher {
    pub fn supported(device: BorrowedFd<'_>) -> bool {
        relay::dpe_vk_relay_supported(device.as_raw_fd())
    }
    /// # Safety
    /// Buffer is a live writable native SPA buffer supplied by add_buffer. Its
    /// complete arrays/chunks are valid for their declared sizes. The creating
    /// thread owns this loop; metadata must be live during each later submit.
    pub unsafe fn new(
        loop_: Rc<MainLoop>,
        device: BorrowedFd<'_>,
        buffer: *mut f::spa_buffer,
        image: Image<'_>,
    ) -> Option<Self> {
        if !(1..=8192).contains(&image.width)
            || !(1..=8192).contains(&image.height)
            || !(1..=4).contains(&image.planes.len())
            || image.generation == 0
            || image.planes.iter().any(|plane| {
                plane.stride == 0 || plane.stride > 131072 || plane.offset > i32::MAX as u32
            })
        {
            return None;
        }
        let mut packet = f::dpe_vk_packet {
            magic: f::DPE_RELAY_MAGIC,
            version: f::DPE_RELAY_VERSION as u16,
            type_: f::DPE_RELAY_TEXTURE as u16,
            generation: image.generation,
            width: image.width,
            height: image.height,
            format: image.format,
            modifier: image.modifier,
            nfd: image.planes.len() as u32,
            ..Default::default()
        };
        let mut descriptors = [-1; 4];
        for (index, plane) in image.planes.iter().enumerate() {
            descriptors[index] = plane.descriptor.as_raw_fd();
            packet.strides[index] = plane.stride;
            packet.offsets[index] = plane.offset;
        }
        // SAFETY: image/descriptor views are borrowed only for this checked
        // constructor. The native boundary duplicates every retained descriptor.
        let token = NonNull::new(unsafe {
            relay::dpe_vk_relay_create(
                device.as_raw_fd(),
                loop_.as_ptr(),
                buffer,
                &packet,
                descriptors.as_ptr(),
            )
        })?;
        let Some(owner) = relay::relay(token.as_ptr() as usize) else {
            relay::dpe_vk_relay_destroy(token.as_ptr());
            return None;
        };
        Some(Self {
            token: token.as_ptr() as usize,
            owner,
            completion: Box::new(Completion {
                busy: Cell::new(false),
                result: Cell::new(None),
            }),
            _loop: loop_,
        })
    }
    pub fn busy(&self) -> bool {
        self.completion.busy.get()
    }
    pub fn take_completion(&self) -> Option<bool> {
        let result = self.completion.result.take()?;
        self.completion.busy.set(false);
        Some(result)
    }
    /// # Safety
    /// The exact native buffer's metadata remains live until return. Submit is
    /// forbidden after native buffer removal; completion itself never accesses
    /// the retired metadata. GPU storage stays retained through release/teardown.
    pub unsafe fn submit(&self, acquire: OwnedFd) -> std::result::Result<(), &'static str> {
        // SAFETY: identical live-metadata/storage contract, with an owned fence.
        unsafe { self.submit_optional(Some(acquire)) }
    }
    /// # Safety
    /// Same live-buffer and completion-storage contract as submit. With no
    /// explicit fence, the caller must independently establish that producer
    /// work completed before submission. This path signals the acquire point;
    /// socket readability or a missing descriptor alone cannot establish it.
    pub unsafe fn submit_optional(
        &self,
        acquire: Option<OwnedFd>,
    ) -> std::result::Result<(), &'static str> {
        if self.completion.busy.replace(true) {
            return Err("GPU buffer is still leased");
        }
        self.completion.result.set(None);
        let data = (&raw const *self.completion).cast_mut().cast();
        // SAFETY: this object pins completion storage and native owners. The
        // callback publishes only a Cell value and never invokes caller code.
        let result = unsafe { relay::submit(&self.owner, acquire, Some(completed), data) };
        if result < 0 {
            self.completion.busy.set(false);
            Err("GPU timeline publication failed")
        } else {
            Ok(())
        }
    }
}
unsafe extern "C" fn completed(data: *mut c_void, success: bool) {
    // SAFETY: the Publisher retains this stable Box until its native relay and
    // watch have been cancelled; callback/owner retirement share one loop thread.
    unsafe { &*data.cast::<Completion>() }
        .result
        .set(Some(success));
}
impl Drop for Publisher {
    fn drop(&mut self) {
        relay::dpe_vk_relay_destroy(self.token as *mut c_void);
        // Field order retires the final relay/watch before completion storage
        // and the loop owner. Dropping a source never writes another image.
    }
}
