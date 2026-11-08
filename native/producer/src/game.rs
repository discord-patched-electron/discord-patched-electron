// SPDX-License-Identifier: GPL-3.0-only
use crate::{
    cursor::Cursor,
    system::{self, Descriptors},
};
use dpe_core::{
    direct::{self, Image, Packet},
    geometry::Rect,
};
use dpe_game::protocol as hook;
use dpe_os::{profiler::Span, wire};
use std::{
    ffi::CStr,
    io,
    os::fd::{AsFd, AsRawFd, BorrowedFd, OwnedFd},
    time::{Duration, Instant},
};
struct Texture {
    packet: hook::Packet,
    descriptors: wire::Message,
}
pub enum Input {
    Idle,
    Dropped,
    Stop,
    ConsumerClosed,
    Delivered { generation: u64, sequence: u64 },
}
pub struct Game {
    pub socket: OwnedFd,
    uuid: [u8; 16],
    pause: bool,
    device: (u32, u32),
    texture: Option<Texture>,
    cursor: Cursor,
    pub stop_deadline: Option<Instant>,
}
impl Game {
    #[cfg(test)]
    pub(crate) fn fixture(socket: OwnedFd) -> Self {
        Self {
            socket,
            uuid: [7; 16],
            pause: false,
            device: (226, 128),
            texture: None,
            cursor: Cursor::default(),
            stop_deadline: None,
        }
    }
    pub fn new(socket: OwnedFd, uuid: [u8; 16], render: &CStr, pause: bool) -> io::Result<Self> {
        if !wire::dpe_direct_peer(socket.as_raw_fd()) {
            return Err(io::ErrorKind::PermissionDenied.into());
        }
        let device = system::render_device(uuid, render)?;
        Ok(Self {
            socket,
            uuid,
            pause,
            device,
            texture: None,
            cursor: Cursor::new(),
            stop_deadline: None,
        })
    }
    pub fn control(&self, fps: u32) -> io::Result<()> {
        let _profile = Span::new(20);
        let packet = hook::Packet {
            flags: if fps == 0 && !self.pause {
                0
            } else {
                hook::CAPTURE | hook::DIRECT
            },
            fps: if self.pause { fps } else { fps.max(1) },
            uuid: self.uuid,
            ..hook::Packet::new(hook::CONTROL)
        };
        wire::send_raw(self.socket.as_fd(), &packet.bytes(), &[])
    }
    pub fn release(&self, generation: u64, sequence: u64) -> io::Result<()> {
        let packet = hook::Packet {
            generation,
            sequence,
            ..hook::Packet::new(hook::RELEASE)
        };
        wire::send_raw(self.socket.as_fd(), &packet.bytes(), &[])
    }
    pub fn input(&mut self, client: BorrowedFd<'_>, fps: u32, held: bool) -> io::Result<Input> {
        let mut bytes = [0; 128];
        let Some(message) = wire::receive(self.socket.as_fd(), &mut bytes, 4)? else {
            return Ok(Input::Idle);
        };
        let packet = hook::Packet::parse(&bytes).ok_or(io::ErrorKind::InvalidData)?;
        match packet.kind {
            hook::TEXTURE
                if packet.generation != 0
                    && message.count > 0
                    && message.count == packet.nfd as usize
                    && packet.flags == 0
                    && packet.width > 0
                    && packet.width <= 8192
                    && packet.height > 0
                    && packet.height <= 4320 =>
            {
                self.texture = Some(Texture {
                    packet,
                    descriptors: message,
                });
                self.stop_deadline = None;
                Ok(Input::Idle)
            }
            hook::STOP if message.count == 0 && packet.nfd == 0 => {
                if fps != 0 {
                    self.stop_deadline = Some(Instant::now() + Duration::from_secs(2));
                }
                Ok(Input::Stop)
            }
            hook::FRAME => {
                let texture = self.texture.as_ref().ok_or(io::ErrorKind::InvalidData)?;
                let explicit = packet.flags == hook::SYNC_FD;
                if packet.generation != texture.packet.generation
                    || packet.sequence == 0
                    || packet.pts == 0
                    || (packet.flags != 0 && !explicit)
                    || message.count != usize::from(explicit)
                    || packet.nfd as usize != message.count
                {
                    return Err(io::ErrorKind::InvalidData.into());
                }
                if held || fps == 0 {
                    self.release(packet.generation, packet.sequence)?;
                    return Ok(Input::Dropped);
                }
                let t = &texture.packet;
                let first = texture.descriptors.descriptors[0]
                    .as_ref()
                    .ok_or(io::ErrorKind::InvalidData)?
                    .as_fd();
                let mut descriptors = Descriptors::new(first);
                let mut image = Image {
                    width: t.width,
                    height: t.height,
                    format: t.format,
                    planes: t.nfd,
                    modifier: t.modifier,
                    source: Rect {
                        x: 0,
                        y: 0,
                        width: t.width,
                        height: t.height,
                    },
                    destination: Rect {
                        x: 0,
                        y: 0,
                        width: t.width,
                        height: t.height,
                    },
                    offset: t.offsets,
                    stride: t.strides,
                    ..Default::default()
                };
                for (i, descriptor) in texture.descriptors.descriptors[..texture.descriptors.count]
                    .iter()
                    .enumerate()
                {
                    image.fd[i] = descriptors.push(
                        descriptor
                            .as_ref()
                            .ok_or(io::ErrorKind::InvalidData)?
                            .as_fd(),
                    )?;
                }
                let mut output = Packet {
                    generation: packet.generation,
                    sequence: packet.sequence,
                    timestamp_ns: packet.pts,
                    width: t.width,
                    height: t.height,
                    device_major: self.device.0,
                    device_minor: self.device.1,
                    image,
                    ..Packet::new(direct::FRAME)
                };
                let window =
                    u32::from_ne_bytes(t.name[..4].try_into().expect("fixed window identity"));
                if let Some((mut cursor, descriptor)) =
                    self.cursor.image(window, t.width, t.height)?
                {
                    cursor.fd[0] = descriptors.push(descriptor)?;
                    output.cursor = cursor;
                }
                if explicit {
                    output.acquire = descriptors.push(
                        message.descriptors[0]
                            .as_ref()
                            .ok_or(io::ErrorKind::InvalidData)?
                            .as_fd(),
                    )? + 1;
                }
                output.descriptors = descriptors.as_slice().len() as u32;
                let _profile = Span::new(19);
                if let Err(error) = wire::send(client, &output.bytes(), descriptors.as_slice()) {
                    if system::consumer_closed(&error) {
                        return Ok(Input::ConsumerClosed);
                    }
                    return Err(error);
                }
                Ok(Input::Delivered {
                    generation: packet.generation,
                    sequence: packet.sequence,
                })
            }
            _ => Err(io::ErrorKind::InvalidData.into()),
        }
    }
}
impl Drop for Game {
    fn drop(&mut self) {
        let _ = self.control(0);
    }
}

#[cfg(test)]
mod pause_tests {
    use super::*;
    use std::os::fd::FromRawFd;
    #[test]
    fn negotiated_pause_retains_capture_contract_and_legacy_controls_still_stop() {
        let mut pair = [-1; 2];
        // SAFETY: one writable descriptor pair for a private synthetic socket.
        let status = unsafe {
            libc::socketpair(
                libc::AF_UNIX,
                libc::SOCK_SEQPACKET | libc::SOCK_CLOEXEC,
                0,
                pair.as_mut_ptr(),
            )
        };
        assert_eq!(status, 0);
        // SAFETY: successful socketpair returns two distinct unique descriptors.
        let (left, right) =
            unsafe { (OwnedFd::from_raw_fd(pair[0]), OwnedFd::from_raw_fd(pair[1])) };
        let mut game = Game::fixture(left);
        for (pause, flags, fps) in [(false, 0, 1), (true, hook::CAPTURE | hook::DIRECT, 0)] {
            game.pause = pause;
            game.control(0).unwrap();
            let mut bytes = [0; 128];
            let message = wire::receive(right.as_fd(), &mut bytes, 0)
                .unwrap()
                .unwrap();
            assert_eq!(message.count, 0);
            let packet = hook::Packet::parse(&bytes).unwrap();
            assert_eq!(
                (packet.kind, packet.flags, packet.fps, packet.uuid),
                (hook::CONTROL, flags, fps, [7; 16])
            );
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn incoming(socket: BorrowedFd<'_>, packet: hook::Packet, descriptors: &[BorrowedFd<'_>]) {
        wire::send_raw(socket, &packet.bytes(), descriptors).unwrap();
    }
    #[test]
    fn consumer_close_during_forwarding_never_releases_the_hook_lease() {
        let (source, hook) = system::pair();
        let (client, consumer) = system::pair();
        let mut game = Game::fixture(source);
        let plane = std::fs::File::open("/dev/null").unwrap();
        incoming(
            hook.as_fd(),
            hook::Packet {
                generation: 1,
                width: 1920,
                height: 1080,
                nfd: 1,
                format: u32::from_le_bytes(*b"XR24"),
                strides: [7680, 0, 0, 0],
                ..hook::Packet::new(hook::TEXTURE)
            },
            &[plane.as_fd()],
        );
        assert!(matches!(
            game.input(client.as_fd(), 60, false).unwrap(),
            Input::Idle
        ));
        incoming(
            hook.as_fd(),
            hook::Packet {
                generation: 1,
                sequence: 1,
                pts: 123,
                flags: hook::SYNC_FD,
                nfd: 1,
                ..hook::Packet::new(hook::FRAME)
            },
            &[plane.as_fd()],
        );
        drop(consumer);
        assert!(matches!(
            game.input(client.as_fd(), 60, false).unwrap(),
            Input::ConsumerClosed
        ));
        let mut reply = [0; 128];
        assert!(
            wire::receive(hook.as_fd(), &mut reply, 0)
                .unwrap()
                .is_none()
        );
        // A malformed hook message remains a failure even after consumer loss.
        incoming(hook.as_fd(), hook::Packet::new(99), &[]);
        assert!(game.input(client.as_fd(), 60, false).is_err());
    }
    #[test]
    fn frame_leases_survive_texture_replacement_pause_and_recovery() {
        let (source, hook) = system::pair();
        let (client, consumer) = system::pair();
        let mut game = Game::fixture(source);
        let plane = std::fs::File::open("/dev/null").unwrap();
        let texture = hook::Packet {
            generation: 1,
            width: 1920,
            height: 1080,
            nfd: 1,
            format: u32::from_le_bytes(*b"XR24"),
            strides: [7680, 0, 0, 0],
            ..hook::Packet::new(hook::TEXTURE)
        };
        incoming(hook.as_fd(), texture.clone(), &[plane.as_fd()]);
        assert!(matches!(
            game.input(client.as_fd(), 60, false).unwrap(),
            Input::Idle
        ));
        let frame = hook::Packet {
            generation: 1,
            sequence: 1,
            pts: 123,
            flags: hook::SYNC_FD,
            nfd: 1,
            ..hook::Packet::new(hook::FRAME)
        };
        incoming(hook.as_fd(), frame.clone(), &[plane.as_fd()]);
        assert!(matches!(
            game.input(client.as_fd(), 60, false).unwrap(),
            Input::Delivered {
                generation: 1,
                sequence: 1
            }
        ));
        let mut bytes = [0; direct::SIZE];
        let received = wire::receive(consumer.as_fd(), &mut bytes, 9)
            .unwrap()
            .unwrap();
        let output = Packet::parse(&bytes).unwrap();
        assert_eq!(
            (output.image.planes, output.acquire, output.descriptors),
            (1, 2, 2)
        );
        assert_eq!(received.count, 2);
        incoming(
            hook.as_fd(),
            hook::Packet {
                sequence: 2,
                ..frame.clone()
            },
            &[plane.as_fd()],
        );
        assert!(matches!(
            game.input(client.as_fd(), 60, true).unwrap(),
            Input::Dropped
        ));
        let mut reply = [0; 128];
        let m = wire::receive(hook.as_fd(), &mut reply, 0).unwrap().unwrap();
        assert_eq!(m.count, 0);
        let release = hook::Packet::parse(&reply).unwrap();
        assert_eq!(
            (release.kind, release.generation, release.sequence),
            (hook::RELEASE, 1, 2)
        );
        incoming(
            hook.as_fd(),
            hook::Packet {
                generation: 2,
                ..texture
            },
            &[plane.as_fd()],
        );
        game.input(client.as_fd(), 60, true).unwrap();
        game.release(1, 1).unwrap();
        wire::receive(hook.as_fd(), &mut reply, 0).unwrap().unwrap();
        assert_eq!(hook::Packet::parse(&reply).unwrap().generation, 1);
        incoming(hook.as_fd(), hook::Packet::new(hook::STOP), &[]);
        game.input(client.as_fd(), 60, false).unwrap();
        assert!(game.stop_deadline.is_some());
        incoming(
            hook.as_fd(),
            hook::Packet {
                generation: 2,
                sequence: 3,
                ..frame
            },
            &[plane.as_fd()],
        );
        assert!(matches!(
            game.input(client.as_fd(), 0, false).unwrap(),
            Input::Dropped
        ));
        wire::receive(hook.as_fd(), &mut reply, 0).unwrap().unwrap();
        // A malformed frame must not be accepted as a texture or GPU lease.
        incoming(
            hook.as_fd(),
            hook::Packet {
                generation: 2,
                sequence: 4,
                pts: 123,
                flags: hook::FLIP,
                ..hook::Packet::new(hook::FRAME)
            },
            &[],
        );
        assert!(game.input(client.as_fd(), 60, false).is_err());
        // The consumer's own copies remain alive across producer replacement.
        for fd in received.descriptors.into_iter().flatten() {
            // SAFETY: read-only descriptor query while this received owner remains live.
            assert!(unsafe { libc::fcntl(fd.as_raw_fd(), libc::F_GETFD) } >= 0);
        }
    }
}
