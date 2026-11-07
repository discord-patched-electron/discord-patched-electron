// SPDX-License-Identifier: MIT
//! Real libwayland server wire tests. Only private memfds receive synthetic pixels.
use dpe_wayland::capture::{Buffer, Capture, Constraints, Event, Format};
use std::{
    cell::Cell,
    fs::File,
    io::{Read, Write},
    os::{
        fd::{AsFd, AsRawFd, FromRawFd},
        unix::{fs::FileExt, net::UnixStream, process::CommandExt},
    },
    process::{Child, Command},
    rc::Rc,
};
use wayland_client::Connection;
struct Storage {
    file: File,
    drops: Rc<Cell<u32>>,
}
impl Drop for Storage {
    fn drop(&mut self) {
        self.drops.set(self.drops.get() + 1);
    }
}
type Owner = Rc<Storage>;
type Completion = (Buffer<Owner>, dpe_wayland::capture::Frame, i32);
struct Fixture {
    capture: Option<Capture<Owner>>,
    server: Child,
    control: Option<UnixStream>,
    constraints: Constraints,
    frames: Vec<Completion>,
    stopped: Vec<i32>,
    drops: Rc<Cell<u32>>,
}
impl Fixture {
    fn new(filter: fn(u32, bool) -> bool) -> Self {
        let (client, server) = UnixStream::pair().unwrap();
        let (control, commands) = UnixStream::pair().unwrap();
        let fds = [server.as_raw_fd(), commands.as_raw_fd()];
        let mut command = Command::new(
            std::env::var_os("DPE_WAYLAND_TEST_SERVER").expect("explicit private fixture server"),
        );
        command.args(fds.map(|fd| fd.to_string()));
        // SAFETY: the child clears CLOEXEC only on its two private sockets,
        // using async-signal-safe fcntl before exec; no shared Rust state changes.
        unsafe {
            command.pre_exec(move || {
                for fd in fds {
                    if libc::fcntl(fd, libc::F_SETFD, 0) != 0 {
                        return Err(std::io::Error::last_os_error());
                    }
                }
                Ok(())
            });
        }
        let server = command.spawn().unwrap();
        drop(commands);
        let connection = Connection::from_socket(client).unwrap();
        let capture = Capture::from_connection(connection, filter).unwrap();
        Self {
            capture: Some(capture),
            server,
            control: Some(control),
            constraints: Constraints::default(),
            frames: Vec::new(),
            stopped: Vec::new(),
            drops: Rc::new(Cell::new(0)),
        }
    }
    fn capture(&mut self) -> &mut Capture<Owner> {
        self.capture.as_mut().unwrap()
    }
    fn pump(&mut self) {
        self.capture().roundtrip().unwrap();
        while let Some(event) = self.capture().next_event() {
            match event {
                Event::Constraints(value) => self.constraints = value,
                Event::Frame {
                    buffer,
                    frame,
                    status,
                } => self.frames.push((buffer, frame, status)),
                Event::Stopped(error) => self.stopped.push(error),
            }
        }
    }
    fn command(&mut self, byte: u8) {
        let control = self.control.as_mut().unwrap();
        control.write_all(&[byte]).unwrap();
        let mut reply = [0];
        control.read_exact(&mut reply).unwrap();
        assert_eq!(reply, [byte]);
        self.pump();
    }
    fn start(&mut self) {
        assert_eq!(self.capture().start("DPE-1", true), Ok(()));
        self.pump();
    }
    fn allocate(&mut self) -> Buffer<Owner> {
        // SAFETY: fixed name; successful memfd creation transfers one descriptor.
        let raw = unsafe { libc::memfd_create(c"dpe-wayland-fixture".as_ptr(), libc::MFD_CLOEXEC) };
        assert!(raw >= 0);
        // SAFETY: this successful memfd is uniquely owned by the test.
        let file = unsafe { File::from_raw_fd(raw) };
        file.set_len(u64::from(
            self.constraints.width * self.constraints.height * 4,
        ))
        .unwrap();
        let storage = Rc::new(Storage {
            file,
            drops: self.drops.clone(),
        });
        self.capture
            .as_ref()
            .unwrap()
            .allocate_shm(
                &self.constraints,
                Format {
                    format: 0,
                    modifier: 0,
                    dmabuf: false,
                },
                storage.file.as_fd(),
                self.constraints.width * 4,
                storage.clone(),
            )
            .ok()
            .unwrap()
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        drop(self.capture.take());
        drop(self.control.take());
        let status = self.server.wait().unwrap();
        assert!(status.success(), "private server failed: {status}");
    }
}
fn all(_: u32, _: bool) -> bool {
    true
}
fn shm(_: u32, dma: bool) -> bool {
    !dma
}
#[test]
#[ignore = "requires explicitly compiled DPE_WAYLAND_TEST_SERVER, no host display"]
fn selected_output_generations_retry_metadata_hotplug_and_owned_teardown() {
    {
        let mut f = Fixture::new(all);
        assert_eq!(
            f.capture().start("missing-monitor", true),
            Err(-libc::ENOENT)
        );
    }
    {
        let mut f = Fixture::new(shm);
        f.command(b'G');
        f.start();
        assert_eq!(f.constraints.formats.len(), 1);
        assert!(f.stopped.is_empty());
    }
    {
        let mut f = Fixture::new(all);
        f.start();
        assert_eq!(f.constraints.formats.len(), 3);
        let first = f.allocate();
        assert!(f.capture().submit(first.clone()).is_ok());
        assert_eq!(
            f.capture().submit(first.clone()).err().unwrap().0,
            -libc::EBUSY
        );
        f.pump();
        let (_, frame, status) = f.frames.pop().unwrap();
        assert_eq!(status, 0);
        assert_eq!(frame.presentation_ns, 123000000456);
        assert_eq!(frame.transform, 1);
        assert_eq!(frame.damage, Some((0, 0, 16, 16)));
        let mut pixel = [0; 4];
        first.storage().file.read_exact_at(&mut pixel, 0).unwrap();
        assert_eq!(u32::from_ne_bytes(pixel), 0xff000001);
        f.command(b'U');
        assert!(f.capture().submit(first.clone()).is_ok());
        f.pump();
        assert_eq!(f.frames.pop().unwrap().2, -libc::EAGAIN);
        assert!(f.stopped.is_empty());
        f.command(b'O');
        assert!(f.capture().submit(first.clone()).is_ok());
        f.pump();
        assert_eq!(f.frames.pop().unwrap().2, 0);
        f.command(b'P');
        assert_eq!(f.capture().refresh(&first), Err(-libc::EAGAIN));
        assert_eq!(
            f.capture().submit(first.clone()).err().unwrap().0,
            -libc::EAGAIN
        );
        f.command(b'Q');
        let second = f.allocate();
        assert_eq!(f.constraints.generation, 2);
        assert_eq!(
            f.capture().submit(first.clone()).err().unwrap().0,
            -libc::ESTALE
        );
        assert_eq!(f.capture().refresh(&first), Ok(()));
        assert_eq!(first.generation(), 2);
        assert!(f.capture().submit(first.clone()).is_ok());
        f.pump();
        assert_eq!(f.frames.pop().unwrap().2, 0);
        f.command(b'H');
        assert!(f.capture().submit(second.clone()).is_ok());
        assert_eq!(f.capture().refresh(&second), Err(-libc::EBUSY));
        f.pump();
        f.command(b'C');
        assert_eq!(f.constraints.generation, 3);
        assert!(f.frames.is_empty());
        f.command(b'R');
        assert_eq!(f.frames.pop().unwrap().1.generation, 2);
        let third = f.allocate();
        f.command(b'F');
        assert!(f.capture().submit(third.clone()).is_ok());
        f.pump();
        assert_eq!(f.frames.pop().unwrap().2, -libc::ESTALE);
        assert_eq!(
            f.capture().submit(third.clone()).err().unwrap().0,
            -libc::ESTALE
        );
        assert_eq!(f.capture().refresh(&third), Err(-libc::ESTALE));
        f.command(b'C');
        assert_eq!(f.capture().refresh(&third), Ok(()));
        let next = third;
        f.command(b'H');
        assert!(f.capture().submit(next.clone()).is_ok());
        f.pump();
        f.command(b'S');
        assert_eq!(f.stopped, [-libc::ECANCELED]);
        f.command(b'R');
        assert_eq!(f.frames.pop().unwrap().2, -libc::ECANCELED);
        assert_eq!(f.capture().submit(next).err().unwrap().0, -libc::ECANCELED);
    }
    for _ in 0..3 {
        let mut f = Fixture::new(all);
        f.start();
        let buffer = f.allocate();
        f.command(b'H');
        assert!(f.capture().submit(buffer.clone()).is_ok());
        f.pump();
        f.command(b'+');
        f.command(b'-');
        assert!(f.stopped.is_empty());
        f.command(b'D');
        assert_eq!(f.stopped, [-libc::ENODEV]);
        f.command(b'S');
        f.command(b'L');
        assert_eq!(f.stopped.len(), 1);
        f.command(b'R');
        assert_eq!(f.frames.pop().unwrap().2, -libc::ENODEV);
    }
    for mode in b"NTB" {
        let mut f = Fixture::new(all);
        f.start();
        let buffer = f.allocate();
        f.command(*mode);
        if *mode != b'B' {
            assert!(f.capture().submit(buffer).is_ok());
            f.pump();
        }
        assert_eq!(f.stopped, [-libc::EPROTO]);
        assert!(f.frames.iter().all(|value| value.2 != 0));
    }
    {
        let mut f = Fixture::new(all);
        f.start();
        let buffer = f.allocate();
        let drops = f.drops.clone();
        f.command(b'H');
        assert!(f.capture().submit(buffer).is_ok());
        f.pump();
        assert_eq!(drops.get(), 0);
        assert!(f.frames.is_empty());
        drop(f);
        assert_eq!(drops.get(), 1);
    }
    {
        let mut f = Fixture::new(all);
        f.start();
        let buffer = f.allocate();
        let mut foreign = Fixture::new(all);
        foreign.start();
        assert_eq!(
            foreign.capture().submit(buffer).err().unwrap().0,
            -libc::EINVAL
        );
    }
    println!(
        "Rust output selection, bounded requests, retries, generations, metadata, hotplug and lease teardown passed"
    );
}
