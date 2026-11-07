// SPDX-License-Identifier: MIT
//! Owned ancillary-descriptor transport. Rejected packets drop every received fd.
use std::{
    io, mem,
    os::fd::{AsRawFd, BorrowedFd, FromRawFd, IntoRawFd, OwnedFd},
};
const MAX_FDS: usize = 32;
const PACKET_SIZE: usize = dpe_core::direct::SIZE;
const HEADER_SIZE: usize = mem::size_of::<libc::cmsghdr>();
const fn align(n: usize) -> usize {
    let a = mem::size_of::<usize>();
    (n + a - 1) & !(a - 1)
}
pub fn header(bytes: &[u8]) -> bool {
    dpe_core::direct::header(bytes)
}
pub struct Message {
    pub descriptors: [Option<OwnedFd>; MAX_FDS],
    pub count: usize,
}
/// Receives one complete message without ever publishing partially owned fds.
pub fn receive(
    socket: BorrowedFd<'_>,
    payload: &mut [u8],
    maximum: usize,
) -> io::Result<Option<Message>> {
    let Some((length, message)) = receive_chunk(socket, payload, maximum)? else {
        return Ok(None);
    };
    if length != payload.len() {
        // Drop every received descriptor before rejecting a partial fixed-size
        // packet. Stream callers explicitly accumulate through receive_chunk.
        return Err(io::Error::from_raw_os_error(libc::EPROTO));
    }
    Ok(Some(message))
}
/// Receive a bounded stream fragment and adopt its descriptors immediately.
/// Seqpacket truncation remains an error; fixed-size protocols use receive.
pub fn receive_chunk(
    socket: BorrowedFd<'_>,
    payload: &mut [u8],
    maximum: usize,
) -> io::Result<Option<(usize, Message)>> {
    if maximum > MAX_FDS || payload.is_empty() || payload.len() > 65536 {
        return Err(io::Error::from_raw_os_error(libc::EINVAL));
    }
    let mut control = [0usize;
        (align(HEADER_SIZE) + MAX_FDS * mem::size_of::<i32>()).div_ceil(mem::size_of::<usize>())];
    let mut iov = libc::iovec {
        iov_base: payload.as_mut_ptr().cast(),
        iov_len: payload.len(),
    };
    // SAFETY: msghdr is a plain C record; zero initializes every optional pointer.
    let mut msg: libc::msghdr = unsafe { mem::zeroed() };
    msg.msg_iov = &raw mut iov;
    msg.msg_iovlen = 1;
    msg.msg_control = control.as_mut_ptr().cast();
    msg.msg_controllen = mem::size_of_val(control.as_slice());
    // SAFETY: all payload/ancillary buffers are live and exactly their declared lengths.
    let size = unsafe {
        libc::recvmsg(
            socket.as_raw_fd(),
            &mut msg,
            libc::MSG_DONTWAIT | libc::MSG_CMSG_CLOEXEC | libc::MSG_TRUNC,
        )
    };
    if size < 0 {
        let e = io::Error::last_os_error();
        return if matches!(e.raw_os_error(), Some(libc::EAGAIN | libc::EINTR)) {
            Ok(None)
        } else {
            Err(e)
        };
    }
    let mut valid = size > 0
        && size as usize <= payload.len()
        && msg.msg_flags & (libc::MSG_TRUNC | libc::MSG_CTRUNC) == 0;
    let available = msg.msg_controllen.min(mem::size_of_val(control.as_slice()));
    let mut offset = 0;
    let mut descriptors: [Option<OwnedFd>; MAX_FDS] = std::array::from_fn(|_| None);
    let mut count = 0;
    while offset + HEADER_SIZE <= available {
        // SAFETY: offset is aligned and the complete cmsghdr lies inside the initialized control allocation.
        let c = unsafe {
            control
                .as_ptr()
                .cast::<u8>()
                .add(offset)
                .cast::<libc::cmsghdr>()
                .read()
        };
        if c.cmsg_len < align(HEADER_SIZE) || c.cmsg_len > available - offset {
            valid = false;
            break;
        }
        let bytes = c.cmsg_len - align(HEADER_SIZE);
        if c.cmsg_level != libc::SOL_SOCKET
            || c.cmsg_type != libc::SCM_RIGHTS
            || !bytes.is_multiple_of(mem::size_of::<i32>())
        {
            valid = false;
            offset += align(c.cmsg_len);
            continue;
        }
        for index in 0..bytes / mem::size_of::<i32>() {
            // SAFETY: each descriptor slot lies within this checked ancillary record; kernel owns these new descriptors.
            let raw = unsafe {
                control
                    .as_ptr()
                    .cast::<u8>()
                    .add(offset + align(HEADER_SIZE) + index * mem::size_of::<i32>())
                    .cast::<i32>()
                    .read()
            };
            if raw < 0 {
                valid = false;
                continue;
            }
            // SAFETY: SCM_RIGHTS creates a unique descriptor owned by this receive operation.
            let fd = unsafe { OwnedFd::from_raw_fd(raw) };
            if count < maximum {
                descriptors[count] = Some(fd);
                count += 1;
            } else {
                drop(fd);
                valid = false;
            }
        }
        offset += align(c.cmsg_len);
    }
    if !valid {
        if size == 0 && count == 0 {
            return Err(io::ErrorKind::UnexpectedEof.into());
        }
        return Err(io::Error::from_raw_os_error(libc::EPROTO));
    }
    Ok(Some((size as usize, Message { descriptors, count })))
}
pub fn send(
    socket: BorrowedFd<'_>,
    payload: &[u8],
    descriptors: &[BorrowedFd<'_>],
) -> io::Result<()> {
    if !header(payload) || descriptors.len() > 9 {
        return Err(io::Error::from_raw_os_error(libc::EINVAL));
    }
    send_raw(socket, payload, descriptors)
}
/// Sends a bounded fixed-size foreign protocol message. No ownership transfers
/// until the kernel has accepted the complete message and duplicated its fds.
pub fn send_raw(
    socket: BorrowedFd<'_>,
    payload: &[u8],
    descriptors: &[BorrowedFd<'_>],
) -> io::Result<()> {
    if payload.is_empty() || payload.len() > 65536 || descriptors.len() > MAX_FDS {
        return Err(io::Error::from_raw_os_error(libc::EINVAL));
    }
    let space = align(HEADER_SIZE) + align(mem::size_of_val(descriptors));
    let mut control = [0usize;
        (align(HEADER_SIZE) + align(MAX_FDS * mem::size_of::<i32>()))
            .div_ceil(mem::size_of::<usize>())];
    let mut iov = libc::iovec {
        iov_base: payload.as_ptr().cast_mut().cast(),
        iov_len: payload.len(),
    };
    // SAFETY: plain C record with optional fields initially absent.
    let mut msg: libc::msghdr = unsafe { mem::zeroed() };
    msg.msg_iov = &raw mut iov;
    msg.msg_iovlen = 1;
    if !descriptors.is_empty() {
        let record = libc::cmsghdr {
            cmsg_len: align(HEADER_SIZE) + descriptors.len() * mem::size_of::<i32>(),
            cmsg_level: libc::SOL_SOCKET,
            cmsg_type: libc::SCM_RIGHTS,
        };
        // SAFETY: usize-aligned storage has at least cmsghdr bytes followed by every descriptor.
        unsafe {
            control.as_mut_ptr().cast::<libc::cmsghdr>().write(record);
        }
        for (i, fd) in descriptors.iter().enumerate() {
            // SAFETY: each initialized fd slot is inside the allocated, aligned ancillary record.
            unsafe {
                control
                    .as_mut_ptr()
                    .cast::<u8>()
                    .add(align(HEADER_SIZE) + i * mem::size_of::<i32>())
                    .cast::<i32>()
                    .write(fd.as_raw_fd());
            }
        }
        msg.msg_control = control.as_mut_ptr().cast();
        msg.msg_controllen = space;
    }
    // SAFETY: payload and borrowed fds remain live through sendmsg; kernel duplicates the fds.
    let size = unsafe {
        libc::sendmsg(
            socket.as_raw_fd(),
            &msg,
            libc::MSG_DONTWAIT | libc::MSG_NOSIGNAL,
        )
    };
    if size == payload.len() as isize {
        Ok(())
    } else if size < 0 {
        Err(io::Error::last_os_error())
    } else {
        Err(io::Error::from_raw_os_error(libc::EPROTO))
    }
}

/// # Safety
/// `fds` points to `count` entries, each transferring ownership of one unique fd.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_direct_close(fds: *mut i32, count: u32) {
    if fds.is_null() {
        return;
    }
    // SAFETY: caller owns all entries and descriptors; overwrite before dropping each owner.
    let fds = unsafe { std::slice::from_raw_parts_mut(fds, count as usize) };
    for fd in fds {
        let raw = mem::replace(fd, -1);
        if raw >= 0 {
            // SAFETY: caller transferred ownership of this still-live descriptor.
            drop(unsafe { OwnedFd::from_raw_fd(raw) });
        }
    }
}
#[unsafe(no_mangle)]
pub extern "C" fn dpe_direct_peer(fd: i32) -> bool {
    // SAFETY: zero initializes the C output record, without invalid Rust enum values.
    let mut peer: libc::ucred = unsafe { mem::zeroed() };
    let mut size = mem::size_of_val(&peer) as libc::socklen_t;
    // SAFETY: getsockopt borrows fd and writes only to the initialized credential output.
    unsafe {
        libc::getsockopt(
            fd,
            libc::SOL_SOCKET,
            libc::SO_PEERCRED,
            (&raw mut peer).cast(),
            &mut size,
        ) == 0
            && size as usize == mem::size_of_val(&peer)
            && peer.uid == libc::geteuid()
    }
}
/// # Safety
/// Packet points to 296 initialized bytes; fd arrays contain `count` borrowed fds.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_direct_send(
    fd: i32,
    packet: *const libc::c_void,
    fds: *const i32,
    count: u32,
) -> bool {
    if fd < 0 || packet.is_null() || count > 9 || (count > 0 && fds.is_null()) {
        return false;
    }
    // SAFETY: caller supplies the complete fixed-size packet described by the C ABI.
    let bytes = unsafe { std::slice::from_raw_parts(packet.cast::<u8>(), PACKET_SIZE) };
    if !header(bytes)
        || u32::from_ne_bytes(bytes[52..56].try_into().expect("packet field")) != count
    {
        return false;
    }
    let mut borrowed = [mem::MaybeUninit::<BorrowedFd>::uninit(); 9];
    for (i, slot) in borrowed.iter_mut().enumerate().take(count as usize) {
        // SAFETY: caller keeps this count-sized borrowed descriptor array alive.
        let raw = unsafe { fds.add(i).read() };
        if raw < 0 {
            return false;
        }
        // SAFETY: the caller holds each descriptor until this synchronous send returns.
        slot.write(unsafe { BorrowedFd::borrow_raw(raw) });
    }
    // SAFETY: precisely count borrowed descriptor entries were initialized above.
    let borrowed = unsafe {
        std::slice::from_raw_parts(borrowed.as_ptr().cast::<BorrowedFd>(), count as usize)
    };
    // SAFETY: caller borrows this socket for the duration of the synchronous operation.
    send(unsafe { BorrowedFd::borrow_raw(fd) }, bytes, borrowed).is_ok()
}
/// # Safety
/// Payload and descriptor/count outputs are writable for their declared capacities.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_direct_receive_raw(
    fd: i32,
    payload: *mut libc::c_void,
    expected: usize,
    fds: *mut i32,
    maximum: u32,
    count: *mut u32,
) -> i32 {
    if count.is_null() {
        return -1;
    }
    // SAFETY: count is a valid output and is reset before any possible failure.
    unsafe {
        count.write(0);
    }
    if fd < 0
        || payload.is_null()
        || (maximum != 0 && fds.is_null())
        || maximum as usize > MAX_FDS
        || expected == 0
        || expected > 65536
    {
        return -1;
    }
    // SAFETY: caller provides each declared writable output range.
    let (bytes, output) = unsafe {
        (
            std::slice::from_raw_parts_mut(payload.cast::<u8>(), expected),
            if maximum == 0 {
                &mut []
            } else {
                std::slice::from_raw_parts_mut(fds, maximum as usize)
            },
        )
    };
    output.fill(-1);
    // SAFETY: fd is borrowed for this call only; receive never closes the socket.
    let socket = unsafe { BorrowedFd::borrow_raw(fd) };
    match receive(socket, bytes, maximum as usize) {
        Ok(Some(message)) => {
            let length = message.count;
            for (output, owned) in output
                .iter_mut()
                .zip(message.descriptors.into_iter().flatten())
            {
                *output = owned.into_raw_fd();
            }
            // SAFETY: output count describes exactly the descriptors whose ownership just transferred.
            unsafe {
                count.write(length as u32);
            }
            1
        }
        Ok(None) => 0,
        Err(_) => -1,
    }
}
/// # Safety
/// Same output contract as receive_raw, with the fixed direct-packet capacities.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dpe_direct_receive(
    fd: i32,
    packet: *mut libc::c_void,
    fds: *mut i32,
    count: *mut u32,
) -> i32 {
    // SAFETY: caller provides the complete direct-packet output buffers.
    let result = unsafe { dpe_direct_receive_raw(fd, packet, PACKET_SIZE, fds, 9, count) };
    if result != 1 {
        return result;
    }
    // SAFETY: successful receive initialized every packet byte and the descriptor count.
    let (bytes, received) = unsafe {
        (
            std::slice::from_raw_parts(packet.cast::<u8>(), PACKET_SIZE),
            count.read(),
        )
    };
    if !header(bytes)
        || u32::from_ne_bytes(bytes[52..56].try_into().expect("packet field")) != received
    {
        // SAFETY: this success path owns all received descriptors, now rejected before publication.
        unsafe {
            dpe_direct_close(fds, received);
            count.write(0);
        }
        -1
    } else {
        1
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        io::Write,
        os::{
            fd::AsFd,
            unix::net::{UnixDatagram, UnixStream},
        },
    };
    #[test]
    fn stream_fragments_adopt_rights_without_accepting_short_fixed_packets() {
        let (sender, receiver) = UnixStream::pair().unwrap();
        send_raw(sender.as_fd(), b"first", &[sender.as_fd()]).unwrap();
        let mut bytes = [0u8; 128];
        let (size, owners) = receive_chunk(receiver.as_fd(), &mut bytes, 4)
            .unwrap()
            .unwrap();
        assert_eq!(size, 5);
        assert_eq!(&bytes[..size], b"first");
        assert_eq!(owners.count, 1);
        let mut writer = &sender;
        writer.write_all(b"second").unwrap();
        let (next, next_owners) = receive_chunk(receiver.as_fd(), &mut bytes[size..], 3)
            .unwrap()
            .unwrap();
        assert_eq!(next, 6);
        assert_eq!(&bytes[..size + next], b"firstsecond");
        assert_eq!(next_owners.count, 0);
        assert!(owners.descriptors[0].is_some());
        writer.write_all(b"short").unwrap();
        assert!(receive(receiver.as_fd(), &mut bytes, 4).is_err());
        drop(owners);
        drop(sender);
        assert_eq!(
            receive_chunk(receiver.as_fd(), &mut bytes, 4)
                .err()
                .unwrap()
                .kind(),
            io::ErrorKind::UnexpectedEof
        );
        let (sender, receiver) = UnixDatagram::pair().unwrap();
        sender.send(&[7; 129]).unwrap();
        assert!(receive_chunk(receiver.as_fd(), &mut bytes, 4).is_err());
    }
    #[test]
    fn foreign_protocol_transfers_the_full_descriptor_bound_without_weakening_direct_headers() {
        let (sender, receiver) = UnixDatagram::pair().unwrap();
        let payload = [7u8; 12];
        let descriptors = [sender.as_fd(); 32];
        assert!(send(sender.as_fd(), &payload, &[]).is_err());
        assert!(send_raw(sender.as_fd(), &payload, &descriptors).is_ok());
        let mut received = [0u8; 12];
        let message = receive(receiver.as_fd(), &mut received, 32)
            .unwrap()
            .unwrap();
        assert_eq!(received, payload);
        assert_eq!(message.count, 32);
        for owner in message.descriptors.into_iter().flatten() {
            // SAFETY: query the close-on-exec flag on this owned received fd.
            let flags = unsafe { libc::fcntl(owner.as_raw_fd(), libc::F_GETFD) };
            assert_ne!(flags & libc::FD_CLOEXEC, 0);
        }
        assert!(send_raw(sender.as_fd(), &payload, &[sender.as_fd(); 33]).is_err());
    }
}
