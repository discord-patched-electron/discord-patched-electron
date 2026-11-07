// SPDX-License-Identifier: MIT
fn main() {
    let header = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("wrapper.h");
    let output = std::path::PathBuf::from(std::env::var_os("OUT_DIR").expect("Cargo output"));
    bindgen::Builder::default()
        .parse_callbacks(Box::new(bindgen::CargoCallbacks::new()))
        .header(header.to_string_lossy())
        .allowlist_type("xcb_.*")
        .allowlist_var("XCB_.*")
        .derive_default(true)
        .generate_comments(false)
        .layout_tests(false)
        .generate()
        .expect("system XCB protocol bindings")
        .write_to_file(output.join("bindings.rs"))
        .expect("write XCB bindings");
}
