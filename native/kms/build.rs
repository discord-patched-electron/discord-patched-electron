// SPDX-License-Identifier: GPL-3.0-only
fn main() {
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    let header = root.join("native/kms/wrapper.h");
    let output = std::path::PathBuf::from(std::env::var_os("OUT_DIR").expect("Cargo output"));
    bindgen::Builder::default()
        .parse_callbacks(Box::new(bindgen::CargoCallbacks::new()))
        .header(header.to_string_lossy())
        .clang_arg("-I/usr/include/libdrm")
        .allowlist_type("gsr_.*|dpe_kms_.*|drm.*|_drm.*")
        .allowlist_function("drm.*")
        .allowlist_var("DRM_.*|DPE_KMS_.*|GSR_KMS_.*|KMS_.*")
        .derive_default(true)
        .generate_comments(false)
        .layout_tests(false)
        .generate()
        .expect("system DRM and reviewed KMS protocol bindings")
        .write_to_file(output.join("bindings.rs"))
        .expect("write KMS bindings");
    println!("cargo:rustc-link-lib=drm");
    println!("cargo:rustc-link-arg-bin=dpe-kms-server=-Wl,-z,relro,-z,now,-z,noexecstack");
}
