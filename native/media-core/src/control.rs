// SPDX-License-Identifier: MIT
//! Bounded capture-rate commands: the supervisor's JSONL record and the
//! standalone text form. No allocation, unchecked numbers or general JSON API.
pub fn rate(bytes: &[u8]) -> Option<u32> {
    if bytes.len() > 63 {
        return None;
    }
    let mut input = Input { bytes, at: 0 };
    let value = if input.take(b"rate") {
        if !input
            .bytes
            .get(input.at)
            .is_some_and(u8::is_ascii_whitespace)
        {
            return None;
        }
        input.number(false)?
    } else {
        if !input.take(b"{") {
            return None;
        }
        let mut operation = false;
        let mut value = None;
        for field in 0..2 {
            if field != 0 && !input.take(b",") {
                return None;
            }
            if input.take(b"\"op\"") {
                if operation || !input.take(b":") || !input.take(b"\"rate\"") {
                    return None;
                }
                operation = true;
            } else if input.take(b"\"fps\"") {
                if value.is_some() || !input.take(b":") {
                    return None;
                }
                value = Some(input.number(true)?);
            } else {
                return None;
            }
        }
        if !operation || !input.take(b"}") {
            return None;
        }
        value?
    };
    input.space();
    (input.at == bytes.len() && value <= 240).then_some(value)
}
struct Input<'a> {
    bytes: &'a [u8],
    at: usize,
}
impl Input<'_> {
    fn space(&mut self) {
        while self
            .bytes
            .get(self.at)
            .is_some_and(|byte| matches!(byte, b' ' | b'\t' | b'\r' | b'\n'))
        {
            self.at += 1;
        }
    }
    fn take(&mut self, token: &[u8]) -> bool {
        self.space();
        if self.bytes[self.at..].starts_with(token) {
            self.at += token.len();
            true
        } else {
            false
        }
    }
    fn number(&mut self, json: bool) -> Option<u32> {
        self.space();
        let start = self.at;
        let mut value = 0u32;
        while let Some(byte) = self.bytes.get(self.at).copied().filter(u8::is_ascii_digit) {
            value = value.checked_mul(10)?.checked_add(u32::from(byte - b'0'))?;
            self.at += 1;
        }
        if self.at == start || (json && self.at > start + 1 && self.bytes[start] == b'0') {
            None
        } else {
            Some(value)
        }
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn supervisor_json_and_standalone_rates_share_the_same_bounds() {
        for (bytes, expected) in [
            (b"rate 0".as_slice(), 0),
            (b"rate 240", 240),
            (br#"{"op":"rate","fps":30}"#, 30),
            (br#"{"fps":60,"op":"rate"}"#, 60),
            (b" { \"op\" : \"rate\", \"fps\" : 120 } \r", 120),
        ] {
            assert_eq!(rate(bytes), Some(expected));
        }
        for bytes in [
            b"rate30".as_slice(),
            b"rate -1",
            b"rate 241",
            b"rate 4294967296",
            br#"{"op":"rate","fps":30,"extra":1}"#,
            br#"{"fps":30,"fps":60}"#,
            br#"{"op":"rate","op":"rate"}"#,
            br#"{"op":"rate","fps":030}"#,
            br#"{"op":"rate","fps":30.0}"#,
            br#"{"op":"rate","fps":3e1}"#,
            br#"{"op":"stop","fps":30}"#,
            br#"{"op":"rate","fps":30}garbage"#,
            b"{\"op\"\x0b:\"rate\",\"fps\":30}",
            b"rate 30\0",
            &[b' '; 64],
        ] {
            assert_eq!(rate(bytes), None, "{bytes:?}");
        }
    }
}
