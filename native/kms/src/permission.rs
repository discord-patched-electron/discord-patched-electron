// SPDX-License-Identifier: GPL-3.0-only
//! Root-owned executable grants and a live, kernel-authenticated Discord peer.
use std::{
    fs::{File, OpenOptions},
    io::{Read, Write},
    os::{
        fd::AsRawFd,
        unix::{
            fs::{MetadataExt, OpenOptionsExt},
            net::UnixStream,
        },
    },
    path::{Path, PathBuf},
    time::Duration,
};
#[derive(Debug, PartialEq, Eq)]
pub(crate) struct Grant {
    identity: [u64; 5],
    path: PathBuf,
}
fn identity(m: &std::fs::Metadata) -> Option<[u64; 5]> {
    Some([
        m.dev(),
        m.ino(),
        m.size(),
        u64::try_from(i128::from(m.mtime()) * 1_000_000_000 + i128::from(m.mtime_nsec())).ok()?,
        u64::try_from(i128::from(m.ctime()) * 1_000_000_000 + i128::from(m.ctime_nsec())).ok()?,
    ])
}
// Linux fsuid is per-thread: allow read-only identity checks on a user's
// AppImage FUSE mount without changing the server's process credentials.
struct FsUid(libc::uid_t);
impl FsUid {
    fn user(uid: libc::uid_t) -> Self {
        // SAFETY: scalar per-thread filesystem credential change, restored by Drop.
        Self(unsafe { libc::setfsuid(uid) } as libc::uid_t)
    }
}
impl Drop for FsUid {
    fn drop(&mut self) {
        // SAFETY: restore the exact previous per-thread filesystem credential.
        unsafe { libc::setfsuid(self.0) };
    }
}
impl Grant {
    pub(crate) fn parse(bytes: &[u8]) -> Option<Self> {
        let mut lines = std::str::from_utf8(bytes).ok()?.split('\n');
        if lines.next()? != "DPE-KMS-3" {
            return None;
        }
        let mut values = [0; 5];
        for value in &mut values {
            let text = lines.next()?;
            if text.is_empty() || !text.bytes().all(|b| b.is_ascii_digit()) {
                return None;
            }
            *value = text.parse().ok()?;
        }
        let path = PathBuf::from(lines.next()?);
        if !path.is_absolute()
            || path.as_os_str().len() > 3072
            || !lines.next()?.is_empty()
            || lines.next().is_some()
        {
            return None;
        }
        Some(Self {
            identity: values,
            path,
        })
    }
    pub(crate) fn read(path: &Path) -> Option<Self> {
        let file = OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
            .open(path)
            .ok()?;
        let m = file.metadata().ok()?;
        if !m.is_file() || m.uid() != 0 || m.mode() & 0o022 != 0 || m.len() > 4096 {
            return None;
        }
        let mut bytes = Vec::new();
        file.take(4097).read_to_end(&mut bytes).ok()?;
        Self::parse(&bytes)
    }
    fn process(&self, pid: i32, uid: u32) -> bool {
        let proc = PathBuf::from(format!("/proc/{pid}"));
        let check = || -> Option<()> {
            if proc.metadata().ok()?.uid() != uid {
                return None;
            }
            let executable = proc.join("exe");
            if std::fs::read_link(&executable).ok()? != self.path {
                return None;
            }
            // procfs opens the actual running inode, including across path swaps.
            let _access = FsUid::user(uid);
            let live = File::open(&executable).ok()?;
            let current = File::open(&self.path).ok()?;
            if identity(&live.metadata().ok()?)? != self.identity
                || identity(&current.metadata().ok()?)? != self.identity
            {
                return None;
            }
            Some(())
        };
        check().is_some()
    }
}
/// The endpoint is an unprivileged per-worker gate owned by Discord. SO_PEERCRED
/// authenticates Discord even when systemd owns the producer's process tree.
/// The capability is random, per worker, and retired when that worker stops.
pub(crate) fn authorize(
    grant: &Grant,
    uid: u32,
    parent: i32,
    endpoint: &Path,
    token: &str,
) -> Option<UnixStream> {
    if token.len() != 64
        || !token.bytes().all(|b| b.is_ascii_hexdigit())
        || endpoint.as_os_str().len() > 107
    {
        return None;
    }
    let mut stream = UnixStream::connect(endpoint).ok()?;
    stream.set_read_timeout(Some(Duration::from_secs(2))).ok()?;
    stream
        .set_write_timeout(Some(Duration::from_secs(2)))
        .ok()?;
    let mut credentials = libc::ucred {
        pid: 0,
        uid: 0,
        gid: 0,
    };
    let mut length = size_of::<libc::ucred>() as libc::socklen_t;
    // SAFETY: retained socket and exactly sized initialized Linux ucred output.
    if unsafe {
        libc::getsockopt(
            stream.as_raw_fd(),
            libc::SOL_SOCKET,
            libc::SO_PEERCRED,
            (&raw mut credentials).cast(),
            &mut length,
        )
    } != 0
        || length as usize != size_of::<libc::ucred>()
        || credentials.uid != uid
        || !grant.process(credentials.pid, uid)
    {
        return None;
    }
    stream.write_all(format!("{token}\n").as_bytes()).ok()?;
    let mut bytes = Vec::new();
    // Read only the bounded initialization reply; keep the stream alive for
    // revocation on Discord exit. No extra frame-by-frame authorization cost.
    while bytes.len() < 4096 {
        let mut byte = [0];
        stream.read_exact(&mut byte).ok()?;
        bytes.push(byte[0]);
        if byte[0] == b'\n' {
            break;
        }
    }
    let expected = std::str::from_utf8(&bytes).ok()?.strip_suffix('\n')?;
    if !expected.starts_with('/')
        || expected.contains('\n')
        || std::fs::read_link(format!("/proc/{parent}/exe")).ok()? != Path::new(expected)
    {
        return None;
    }
    Some(stream)
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn strict_versioned_executable_grants() {
        assert!(Grant::parse(b"DPE-KMS-2\n").is_none());
        assert!(Grant::parse(b"DPE-KMS-3\n1\n2\n3\n4\n5\n/bin/discord\n").is_some());
        for bytes in [
            b"DPE-KMS-3\n1\n2\n3\n4\n5\nrelative\n".as_slice(),
            b"DPE-KMS-3\n1\n2\n3\n4\n5\n/bin/a\nextra\n",
            b"DPE-KMS-3\n-1\n2\n3\n4\n5\n/bin/a\n",
        ] {
            assert!(Grant::parse(bytes).is_none());
        }
    }
    #[test]
    fn live_process_requires_exact_path_inode_and_change_metadata() {
        let _exclusive = crate::FD_TESTS.lock().unwrap();
        let pid = std::process::id() as i32;
        // SAFETY: getuid reads the current process identity.
        let uid = unsafe { libc::getuid() };
        let path = std::fs::read_link("/proc/self/exe").unwrap();
        let mut grant = Grant {
            identity: identity(&path.metadata().unwrap()).unwrap(),
            path,
        };
        assert!(grant.process(pid, uid));
        assert!(!grant.process(pid, uid.wrapping_add(1)));
        grant.identity[4] ^= 1;
        assert!(!grant.process(pid, uid));
        grant.identity[4] ^= 1;
        grant.path = PathBuf::from("/bin/false");
        assert!(!grant.process(pid, uid));
    }
    #[test]
    fn socket_credentials_bind_the_gate_and_worker_to_the_approved_executable() {
        let _exclusive = crate::FD_TESTS.lock().unwrap();
        let pid = std::process::id() as i32;
        // SAFETY: getuid has no pointer parameters.
        let uid = unsafe { libc::getuid() };
        let path = std::fs::read_link("/proc/self/exe").unwrap();
        let grant = Grant {
            identity: identity(&path.metadata().unwrap()).unwrap(),
            path: path.clone(),
        };
        let mut template = b"/tmp/dpe-kms-gate-XXXXXX\0".to_vec();
        // SAFETY: writable NUL-terminated template with six trailing X bytes.
        assert!(!unsafe { libc::mkdtemp(template.as_mut_ptr().cast()) }.is_null());
        let directory = std::ffi::CString::from_vec_with_nul(template).unwrap();
        use std::os::unix::ffi::OsStrExt;
        let directory = Path::new(std::ffi::OsStr::from_bytes(directory.to_bytes()));
        let endpoint = directory.join("socket");
        let token = "a".repeat(64);
        for (wrong_identity, wrong_worker, success) in [
            (false, false, true),
            (true, false, false),
            (false, true, false),
        ] {
            let listener = std::os::unix::net::UnixListener::bind(&endpoint).unwrap();
            let reply = if wrong_worker {
                "/bin/false".into()
            } else {
                path.to_string_lossy().into_owned()
            };
            let server = std::thread::spawn(move || {
                let (mut peer, _) = listener.accept().unwrap();
                peer.set_read_timeout(Some(Duration::from_secs(2))).unwrap();
                let mut request = [0; 65];
                if peer.read_exact(&mut request).is_ok() {
                    assert_eq!(&request[..64], &[b'a'; 64]);
                    peer.write_all(format!("{reply}\n").as_bytes()).unwrap();
                }
                peer
            });
            let mut candidate = Grant {
                identity: grant.identity,
                path: grant.path.clone(),
            };
            if wrong_identity {
                candidate.identity[1] ^= 1;
            }
            let authorized = authorize(&candidate, uid, pid, &endpoint, &token);
            assert_eq!(authorized.is_some(), success);
            drop(authorized);
            drop(server.join().unwrap());
            std::fs::remove_file(&endpoint).unwrap();
        }
        std::fs::remove_dir(directory).unwrap();
    }
}
