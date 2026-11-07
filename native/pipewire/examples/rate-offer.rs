// SPDX-License-Identifier: MIT
//! Standalone SPA negotiation diagnostic. Reads/writes format bytes, not pixels.
use dpe_pipewire::pod::{Format, MAX_BODY};
use std::io::{Read, Write};
fn main() {
    if run().is_none() {
        std::process::exit(2);
    }
}
fn run() -> Option<()> {
    let arguments: Vec<_> = std::env::args().skip(1).collect();
    if arguments.len() != 2 {
        return None;
    }
    let fps = arguments[0].parse::<u32>().ok().filter(|fps| *fps <= 240)?;
    let preference = match arguments[1].as_str() {
        "preference" => true,
        "cap" => false,
        _ => return None,
    };
    let mut bytes = Vec::new();
    std::io::stdin()
        .take((MAX_BODY + 9) as u64)
        .read_to_end(&mut bytes)
        .ok()?;
    let format = Format::parse(&bytes)?;
    let output = format.with_rate(fps, preference);
    std::io::stdout()
        .write_all(output.as_ref().map_or(bytes.as_slice(), |pod| pod.bytes()))
        .ok()?;
    Some(())
}
