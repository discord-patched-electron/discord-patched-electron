// SPDX-License-Identifier: GPL-3.0-only
//! One same-user hook connection. Fragmented OBS records retain every adopted
//! descriptor in bounded ownership; rejected records drop the complete batch.
use dpe_game::protocol::Packet;
use dpe_os::wire;
use std::{
    io,
    os::fd::{AsFd, AsRawFd, BorrowedFd, OwnedFd},
};
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Flavor {
    Dpe,
    Obs,
}
pub struct Received {
    pub packet: Packet,
    pub descriptors: wire::Message,
}
pub struct Peer {
    socket: OwnedFd,
    flavor: Flavor,
    bytes: [u8; 128],
    length: usize,
    descriptors: [Option<OwnedFd>; 4],
    count: usize,
    generation: u64,
}
impl Peer {
    pub fn new(socket: OwnedFd, flavor: Flavor) -> io::Result<Self> {
        let mut kind = 0;
        let mut size = size_of_val(&kind) as libc::socklen_t;
        // SAFETY: one owned live socket, scalar output and exact socklen extent.
        if unsafe {
            libc::getsockopt(
                socket.as_raw_fd(),
                libc::SOL_SOCKET,
                libc::SO_TYPE,
                (&raw mut kind).cast(),
                &mut size,
            )
        } != 0
            || size as usize != size_of_val(&kind)
            || kind
                != if flavor == Flavor::Obs {
                    libc::SOCK_STREAM
                } else {
                    libc::SOCK_SEQPACKET
                }
            || !wire::dpe_direct_peer(socket.as_raw_fd())
        {
            return Err(io::ErrorKind::PermissionDenied.into());
        }
        // SAFETY: only this owned descriptor is changed; adopt before creating
        // driver threads. Preserve existing flags while adding nonblocking/CLOEXEC.
        let valid = unsafe {
            let flags = libc::fcntl(socket.as_raw_fd(), libc::F_GETFL);
            let fd_flags = libc::fcntl(socket.as_raw_fd(), libc::F_GETFD);
            flags >= 0
                && fd_flags >= 0
                && libc::fcntl(socket.as_raw_fd(), libc::F_SETFL, flags | libc::O_NONBLOCK) == 0
                && libc::fcntl(
                    socket.as_raw_fd(),
                    libc::F_SETFD,
                    fd_flags | libc::FD_CLOEXEC,
                ) == 0
        };
        if !valid {
            return Err(io::Error::last_os_error());
        }
        Ok(Self {
            socket,
            flavor,
            bytes: [0; 128],
            length: 0,
            descriptors: std::array::from_fn(|_| None),
            count: 0,
            generation: 0,
        })
    }
    pub fn descriptor(&self) -> BorrowedFd<'_> {
        self.socket.as_fd()
    }
    pub fn receive(&mut self) -> io::Result<Option<Received>> {
        let result = self.receive_inner();
        if result.is_err() {
            self.length = 0;
            self.count = 0;
            self.descriptors.fill_with(|| None);
        }
        result
    }
    fn receive_inner(&mut self) -> io::Result<Option<Received>> {
        if self.flavor == Flavor::Dpe {
            let mut bytes = [0; 128];
            let Some(descriptors) = wire::receive(self.socket.as_fd(), &mut bytes, 4)? else {
                return Ok(None);
            };
            let packet = Packet::parse(&bytes).ok_or(io::ErrorKind::InvalidData)?;
            return Ok(Some(Received {
                packet,
                descriptors,
            }));
        }
        let Some((length, message)) = wire::receive_chunk(
            self.socket.as_fd(),
            &mut self.bytes[self.length..],
            4 - self.count,
        )?
        else {
            return Ok(None);
        };
        self.length += length;
        for descriptor in message.descriptors.into_iter().flatten() {
            self.descriptors[self.count] = Some(descriptor);
            self.count += 1;
        }
        if self.length != 128 {
            return Ok(None);
        }
        let generation = self
            .generation
            .checked_add(1)
            .ok_or(io::ErrorKind::InvalidData)?;
        let packet = Packet::parse_obs(&self.bytes, self.count, generation)
            .ok_or(io::ErrorKind::InvalidData)?;
        let mut descriptors = wire::Message {
            descriptors: std::array::from_fn(|_| None),
            count: self.count,
        };
        for (target, owner) in descriptors
            .descriptors
            .iter_mut()
            .zip(&mut self.descriptors)
        {
            *target = owner.take();
        }
        self.length = 0;
        self.count = 0;
        self.generation = generation;
        Ok(Some(Received {
            packet,
            descriptors,
        }))
    }
    pub fn send(&self, bytes: &[u8]) -> io::Result<()> {
        wire::send_raw(self.socket.as_fd(), bytes, &[])
    }
    pub fn disconnect(&self) {
        // SAFETY: shutdown affects only this owned hook connection, before
        // pending GPU imports or downstream leases are released during teardown.
        unsafe {
            libc::shutdown(self.socket.as_raw_fd(), libc::SHUT_RDWR);
        }
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    use std::{io::Write, os::unix::net::UnixStream};
    #[test]
    fn partial_obs_records_retain_descriptors_and_rejection_drops_all_owners() {
        let (mut writer, reader) = UnixStream::pair().unwrap();
        let mut peer = Peer::new(reader.into(), Flavor::Obs).unwrap();
        let file = std::fs::File::open("/dev/null").unwrap();
        let mut bytes = [0u8; 128];
        bytes[0] = 11;
        bytes[1] = 1;
        for (offset, value) in [(2, 64u32), (6, 32), (10, 0x34325241), (14, 256)] {
            bytes[offset..offset + 4].copy_from_slice(&value.to_ne_bytes());
        }
        wire::send_raw(writer.as_fd(), &bytes[..1], &[file.as_fd()]).unwrap();
        assert!(peer.receive().unwrap().is_none());
        assert_eq!(peer.count, 1);
        let partial = peer.descriptors[0].as_ref().unwrap().as_raw_fd();
        writer.write_all(&bytes[1..]).unwrap();
        let received = peer.receive().unwrap().unwrap();
        assert_eq!(
            (
                received.packet.generation,
                received.packet.width,
                received.descriptors.count
            ),
            (1, 64, 1)
        );
        assert_eq!(peer.count, 0);
        assert!(peer.descriptors.iter().all(Option::is_none));
        drop(received);
        // SAFETY: descriptor query only; dropping the received record retires
        // its exact adopted owner before any new receive may reuse that number.
        assert_eq!(unsafe { libc::fcntl(partial, libc::F_GETFD) }, -1);
        bytes[59..63].copy_from_slice(&1u32.to_ne_bytes());
        wire::send_raw(writer.as_fd(), &bytes[..1], &[file.as_fd()]).unwrap();
        assert!(peer.receive().unwrap().is_none());
        let rejected = peer.descriptors[0].as_ref().unwrap().as_raw_fd();
        writer.write_all(&bytes[1..]).unwrap();
        assert!(peer.receive().is_err());
        assert_eq!((peer.count, peer.length), (0, 0));
        // SAFETY: no fd-allocation call occurs between rejection and this query.
        assert_eq!(unsafe { libc::fcntl(rejected, libc::F_GETFD) }, -1);
    }
}
