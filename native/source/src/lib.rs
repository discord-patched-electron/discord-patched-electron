// SPDX-License-Identifier: GPL-3.0-only
//! Bounded command/signal support for dedicated native source processes.
pub mod hook_input;
use std::{
    io::{self, Write},
    os::fd::{AsRawFd, FromRawFd, OwnedFd},
};
pub fn emit(message: impl std::fmt::Display) {
    let mut output = io::stdout().lock();
    let _ = writeln!(output, "{message}");
    let _ = output.flush();
}
pub fn quoted(text: &str) -> String {
    let mut result = String::with_capacity(text.len() + 2);
    result.push('"');
    for character in text.chars() {
        match character {
            '"' => result.push_str("\\\""),
            '\\' => result.push_str("\\\\"),
            value if value < ' ' => {
                use std::fmt::Write;
                let _ = write!(result, "\\u{:04x}", value as u32);
            }
            value => result.push(value),
        }
    }
    result.push('"');
    result
}
pub struct Signals(OwnedFd);
impl Signals {
    /// Install before creating graphics or PipeWire threads. KMS authorization
    /// startup runs first, retaining its separate bounded cancellation handler.
    pub fn new() -> io::Result<Self> {
        // SAFETY: zero is a valid initial sigset_t; fixed process signal mask.
        let mut mask = unsafe { std::mem::zeroed::<libc::sigset_t>() };
        // SAFETY: initialized local mask and no borrowed signal-handler state.
        let status = unsafe {
            libc::sigemptyset(&mut mask);
            libc::sigaddset(&mut mask, libc::SIGINT);
            libc::sigaddset(&mut mask, libc::SIGTERM);
            libc::sigprocmask(libc::SIG_BLOCK, &mask, std::ptr::null_mut())
        };
        if status != 0 {
            return Err(io::Error::last_os_error());
        }
        // SAFETY: successful creation transfers one private nonblocking fd.
        let raw = unsafe { libc::signalfd(-1, &mask, libc::SFD_CLOEXEC | libc::SFD_NONBLOCK) };
        if raw < 0 {
            return Err(io::Error::last_os_error());
        }
        // SAFETY: signalfd returned a new uniquely owned descriptor.
        Ok(Self(unsafe { OwnedFd::from_raw_fd(raw) }))
    }
    pub fn descriptor(&self) -> i32 {
        self.0.as_raw_fd()
    }
}
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Command {
    Stop,
    Rate(u32),
}
fn token(input: &mut &str, value: &str) -> bool {
    let Some(rest) = input.trim_ascii_start().strip_prefix(value) else {
        return false;
    };
    *input = rest;
    true
}
fn number(input: &mut &str) -> Option<u32> {
    *input = input.trim_ascii_start();
    let end = input.bytes().take_while(u8::is_ascii_digit).count();
    let value = input.get(..end)?.parse::<u32>().ok()?;
    *input = &input[end..];
    (1..=240).contains(&value).then_some(value)
}
pub fn command(mut line: &str) -> Option<Command> {
    line = line.trim_ascii();
    if line == "stop" {
        return Some(Command::Stop);
    }
    if token(&mut line, "rate") {
        if !line.starts_with(|value: char| value.is_ascii_whitespace()) {
            return None;
        }
        let rate = number(&mut line)?;
        return line.trim_ascii().is_empty().then_some(Command::Rate(rate));
    }
    if !token(&mut line, "{") || !token(&mut line, "\"op\"") || !token(&mut line, ":") {
        return None;
    }
    let result = if token(&mut line, "\"stop\"") {
        Command::Stop
    } else {
        if !token(&mut line, "\"rate\"")
            || !token(&mut line, ",")
            || !token(&mut line, "\"fps\"")
            || !token(&mut line, ":")
        {
            return None;
        }
        Command::Rate(number(&mut line)?)
    };
    (token(&mut line, "}") && line.trim_ascii().is_empty()).then_some(result)
}
pub struct Stdin {
    saved: i32,
    bytes: [u8; 128],
    length: usize,
}
impl Stdin {
    pub fn new() -> io::Result<Self> {
        // SAFETY: scalar flags query/update on this dedicated process's stdin.
        let saved = unsafe { libc::fcntl(libc::STDIN_FILENO, libc::F_GETFL) };
        if saved < 0 {
            return Err(io::Error::last_os_error());
        }
        // SAFETY: update only the owned input's nonblocking flag; Drop restores it.
        if unsafe { libc::fcntl(libc::STDIN_FILENO, libc::F_SETFL, saved | libc::O_NONBLOCK) } < 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(Self {
            saved,
            bytes: [0; 128],
            length: 0,
        })
    }
    pub fn read(&mut self, mut apply: impl FnMut(Command)) -> io::Result<bool> {
        let mut bytes = [0; 256];
        // SAFETY: bounded initialized local output and process-owned stdin.
        let size =
            unsafe { libc::read(libc::STDIN_FILENO, bytes.as_mut_ptr().cast(), bytes.len()) };
        if size < 0 {
            let error = io::Error::last_os_error();
            if matches!(
                error.kind(),
                io::ErrorKind::WouldBlock | io::ErrorKind::Interrupted
            ) {
                return Ok(true);
            }
            return Err(error);
        }
        if size == 0 {
            return Ok(false);
        }
        for byte in &bytes[..size as usize] {
            if *byte == b'\n' {
                let text = std::str::from_utf8(&self.bytes[..self.length])
                    .map_err(|_| io::ErrorKind::InvalidData)?;
                apply(command(text).ok_or(io::ErrorKind::InvalidData)?);
                self.length = 0;
            } else {
                if self.length >= self.bytes.len() - 1 {
                    return Err(io::ErrorKind::InvalidData.into());
                }
                self.bytes[self.length] = *byte;
                self.length += 1;
            }
        }
        Ok(true)
    }
}
impl Drop for Stdin {
    fn drop(&mut self) {
        // SAFETY: restore only the original flags of this process input.
        unsafe {
            libc::fcntl(libc::STDIN_FILENO, libc::F_SETFL, self.saved);
        }
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn commands_preserve_quoted_tokens_and_reject_trailing_or_unbounded_input() {
        for text in ["rate 60", " { \"op\" : \"rate\" , \"fps\" : 60 } "] {
            assert_eq!(command(text), Some(Command::Rate(60)));
        }
        for text in ["stop", "{\"op\":\"stop\"}"] {
            assert_eq!(command(text), Some(Command::Stop));
        }
        for text in [
            "rate60",
            "rate 0",
            "rate 241",
            "rate 30x",
            "rate -1",
            "rate 1.5",
            "{\"op\":\"ra te\",\"fps\":60}",
            "{\"op\":\"rate\",\"fps\":60} junk",
            "{\"op\":\"rate\",\"fps\":4294967296}",
        ] {
            assert_eq!(command(text), None, "{text}");
        }
        assert_eq!(quoted("a\n\"\\\u{1}"), "\"a\\u000a\\\"\\\\\\u0001\"");
    }
}
