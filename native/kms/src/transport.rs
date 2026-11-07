// SPDX-License-Identifier: GPL-3.0-only
//! Fixed-size GSR protocol 6 transport. Raw bytes become C records only after
//! validating every bool and count; file descriptors always have Rust owners.
use crate::ffi;
use dpe_os::wire;
use std::os::fd::{AsRawFd, BorrowedFd, FromRawFd, IntoRawFd, OwnedFd};
const RESPONSE_SIZE: usize = 1424;
const ITEM_START: usize = 136;
const ITEM_SIZE: usize = 160;
const HDR_PRESENT: usize = std::mem::offset_of!(ffi::gsr_kms_response_item, has_hdr_metadata);
#[derive(Debug, PartialEq, Eq)]
pub(crate) enum Error {
    Invalid,
    Permission,
}
/// Native Rust consumers keep this owner instead of transferring descriptors
/// into a C record and relying on a separate manual cleanup call.
pub(crate) struct Reply {
    response: ffi::gsr_kms_response,
    descriptors: wire::Message,
}
impl Reply {
    pub(crate) fn receive(socket: BorrowedFd<'_>) -> Result<Option<Self>, Error> {
        let mut bytes = [0; RESPONSE_SIZE];
        let Some(descriptors) =
            wire::receive(socket, &mut bytes, 32).map_err(|_| Error::Invalid)?
        else {
            return Ok(None);
        };
        let mut response = decode(&mut bytes, descriptors.count)?;
        let targets = response.items[..response.num_items as usize]
            .iter_mut()
            .flat_map(|item| {
                let count = item.num_dma_bufs as usize;
                item.dma_buf[..count].iter_mut()
            });
        for (plane, owner) in targets.zip(descriptors.descriptors.iter()) {
            // Payload descriptor integers never acquire ownership. Metadata
            // borrows only the already adopted SCM_RIGHTS descriptors here.
            plane.fd = owner.as_ref().map_or(-1, AsRawFd::as_raw_fd);
        }
        Ok(Some(Self {
            response,
            descriptors,
        }))
    }
    pub(crate) fn metadata(&self) -> &ffi::gsr_kms_response {
        &self.response
    }
    pub(crate) fn descriptor(&self, index: usize) -> Option<BorrowedFd<'_>> {
        use std::os::fd::AsFd;
        self.descriptors
            .descriptors
            .get(index)?
            .as_ref()
            .map(AsFd::as_fd)
    }
    fn into_c(mut self) -> ffi::gsr_kms_response {
        for owner in &mut self.descriptors.descriptors {
            if let Some(owner) = owner.take() {
                // The legacy C consumer now owns the exact descriptors already
                // written into response; Rust consumers retain Reply instead.
                let _ = owner.into_raw_fd();
            }
        }
        self.response
    }
}
fn decode(
    bytes: &mut [u8; RESPONSE_SIZE],
    descriptors: usize,
) -> Result<ffi::gsr_kms_response, Error> {
    fn word(bytes: &[u8], at: usize) -> i32 {
        i32::from_ne_bytes(bytes[at..at + 4].try_into().expect("fixed protocol offset"))
    }
    if word(bytes, 0) != 6 || !(0..=8).contains(&word(bytes, 1416)) {
        return Err(Error::Invalid);
    }
    let count = word(bytes, 1416) as usize;
    let mut expected = 0;
    for index in 0..8 {
        let at = ITEM_START + index * ITEM_SIZE;
        if index < count {
            let planes = word(
                bytes,
                at + std::mem::offset_of!(ffi::gsr_kms_response_item, num_dma_bufs),
            );
            if !(1..=4).contains(&planes) || bytes[at + HDR_PRESENT] > 1 {
                return Err(Error::Invalid);
            }
            expected += planes as usize;
        } else {
            // Unused protocol records carry no metadata. Canonicalize their
            // bool before creating a typed Rust value, without inspecting it.
            bytes[at + HDR_PRESENT] = 0;
        }
    }
    if expected != descriptors {
        return Err(Error::Invalid);
    }
    if word(bytes, 4) != 0 {
        let message = bytes[8..136]
            .split(|byte| *byte == 0)
            .next()
            .unwrap_or_default();
        return Err(
            if word(bytes, 4) == 2 && message == b"drmfb handle is NULL" {
                Error::Permission
            } else {
                Error::Invalid
            },
        );
    }
    // SAFETY: exact reviewed C layout; integer/union fields admit every bit
    // pattern. Every bool (including unused records) was checked/canonicalized.
    // The whole input array is initialized, and unaligned reads are permitted.
    Ok(unsafe {
        bytes
            .as_ptr()
            .cast::<ffi::gsr_kms_response>()
            .read_unaligned()
    })
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_kms_receive(fd: i32, response: *mut ffi::gsr_kms_response) -> i32 {
    if fd < 0 || response.is_null() {
        return -1;
    }
    // SAFETY: C caller provides one writable response; no previous fd ownership
    // may remain in it when starting another receive, matching the public ABI.
    unsafe {
        response.write(ffi::gsr_kms_response::default());
    }
    // SAFETY: the caller retains this socket for the entire receive operation.
    let socket = unsafe { BorrowedFd::borrow_raw(fd) };
    let reply = match Reply::receive(socket) {
        Ok(Some(reply)) => reply,
        Ok(None) => return 0,
        Err(Error::Permission) => return -2,
        Err(Error::Invalid) => return -1,
    };
    // SAFETY: C caller owns the output; it now owns each transferred fd until
    // dpe_kms_response_close, with all counts bounded above.
    unsafe {
        response.write(reply.into_c());
    }
    1
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_kms_response_close(response: *mut ffi::gsr_kms_response) {
    if response.is_null() {
        return;
    }
    // SAFETY: caller has exclusive ownership of this initialized C response.
    let response = unsafe { &mut *response };
    let count = response.num_items.clamp(0, 8) as usize;
    for item in &mut response.items[..count] {
        let planes = item.num_dma_bufs.clamp(0, 4) as usize;
        for plane in &mut item.dma_buf[..planes] {
            let raw = std::mem::replace(&mut plane.fd, -1);
            if raw >= 0 {
                // SAFETY: a successful receive transfers each unique fd to
                // this response. Overwrite its slot before releasing ownership.
                drop(unsafe { OwnedFd::from_raw_fd(raw) });
            }
        }
    }
    response.num_items = 0;
}
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_kms_request(client: *mut ffi::dpe_kms_client) -> bool {
    if client.is_null() {
        return false;
    }
    // SAFETY: caller retains the initialized client through this synchronous call.
    let (fd, connector) = unsafe { ((*client).fd, (*client).connector) };
    if fd < 0 {
        return false;
    }
    // SAFETY: caller retains this socket while the request borrows it.
    request(unsafe { BorrowedFd::borrow_raw(fd) }, connector)
}
pub(crate) fn request(socket: BorrowedFd<'_>, connector: u32) -> bool {
    let mut bytes = [0u8; 12];
    bytes[..4].copy_from_slice(&6u32.to_ne_bytes());
    bytes[4..8].copy_from_slice(
        &if connector == 0 {
            1u32
        } else {
            ffi::DPE_KMS_SCOPE_REQUEST
        }
        .to_ne_bytes(),
    );
    bytes[8..].copy_from_slice(&if connector == 0 { u32::MAX } else { connector }.to_ne_bytes());
    wire::send_raw(socket, &bytes, &[]).is_ok()
}
#[cfg(test)]
mod tests {
    use super::*;
    fn response() -> [u8; RESPONSE_SIZE] {
        let mut bytes = [0u8; RESPONSE_SIZE];
        bytes[..4].copy_from_slice(&6i32.to_ne_bytes());
        bytes[1416..1420].copy_from_slice(&1i32.to_ne_bytes());
        bytes[ITEM_START + 48..ITEM_START + 52].copy_from_slice(&1i32.to_ne_bytes());
        bytes
    }
    #[test]
    fn rust_reply_retains_descriptors_and_c_transfer_releases_exactly_once() {
        use std::{
            fs::File,
            os::{fd::AsFd, unix::net::UnixDatagram},
        };
        let _exclusive = crate::FD_TESTS.lock().unwrap();
        let (sender, receiver) = UnixDatagram::pair().unwrap();
        let original = File::open("/dev/null").unwrap();
        let bytes = response();
        for transfer in [false, true] {
            wire::send_raw(sender.as_fd(), &bytes, &[original.as_fd()]).unwrap();
            let reply = Reply::receive(receiver.as_fd()).unwrap().unwrap();
            let received = reply.response.items[0].dma_buf[0].fd;
            assert_ne!(received, original.as_raw_fd());
            // SAFETY: scalar fcntl query observes this owned descriptor only.
            assert!(unsafe { libc::fcntl(received, libc::F_GETFD) } >= 0);
            if transfer {
                let mut response = reply.into_c();
                // SAFETY: ownership just moved to this initialized C response.
                unsafe { dpe_kms_response_close(&raw mut response) };
                // SAFETY: closing an already-cleared response is idempotent.
                unsafe { dpe_kms_response_close(&raw mut response) };
            } else {
                drop(reply);
            }
            // SAFETY: query cannot close/reuse either descriptor.
            assert_eq!(unsafe { libc::fcntl(received, libc::F_GETFD) }, -1);
            // SAFETY: the original sender descriptor remains independently owned.
            assert!(unsafe { libc::fcntl(original.as_raw_fd(), libc::F_GETFD) } >= 0);
        }
    }
    #[test]
    fn untrusted_booleans_counts_and_unused_metadata_are_checked_before_typed_reads() {
        let mut valid = response();
        assert!(decode(&mut valid, 1).is_ok());
        for value in [2, 128, 255] {
            let mut invalid = valid;
            invalid[ITEM_START + HDR_PRESENT] = value;
            assert_eq!(decode(&mut invalid, 1).err(), Some(Error::Invalid));
        }
        let mut unused = valid;
        unused[ITEM_START + ITEM_SIZE + HDR_PRESENT] = 255;
        assert!(decode(&mut unused, 1).is_ok());
        assert_eq!(unused[ITEM_START + ITEM_SIZE + HDR_PRESENT], 0);
        for count in [-1i32, 9, i32::MAX] {
            let mut invalid = valid;
            invalid[1416..1420].copy_from_slice(&count.to_ne_bytes());
            assert_eq!(decode(&mut invalid, 1).err(), Some(Error::Invalid));
        }
        assert_eq!(decode(&mut valid, 0).err(), Some(Error::Invalid));
        assert_eq!(decode(&mut valid, 2).err(), Some(Error::Invalid));
    }
}
