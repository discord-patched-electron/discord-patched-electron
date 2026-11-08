// SPDX-License-Identifier: MIT
fn main() {
    println!("cargo:rerun-if-env-changed=DPE_FFMPEG_SOURCE");
    if std::env::var_os("CARGO_FEATURE_FFMPEG").is_none() {
        return;
    }
    let source = std::path::PathBuf::from(
        std::env::var_os("DPE_FFMPEG_SOURCE").expect("verified private FFmpeg source required"),
    );
    assert!(source.is_absolute() && source.join("config.h").is_file());
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    let header = root.join("native/encoder/wrapper.h");
    let output = std::path::PathBuf::from(std::env::var_os("OUT_DIR").expect("Cargo output"));
    bindgen::Builder::default()
        .parse_callbacks(Box::new(bindgen::CargoCallbacks::new()))
        .header(header.to_string_lossy())
        .clang_arg(format!("-I{}", source.display()))
        .allowlist_type("AV.*|VA.*|_VA.*|H264LevelDescriptor|encoder_.*|dpe_hevc_api|dpe_import_.*")
        .allowlist_function("av.*|va.*|ff_h264_guess_level|dpe_import_.*")
        .allowlist_var("AV.*|VA_.*|DPE_HEVC_ABI")
        .prepend_enum_name(false)
        .derive_default(true)
        .generate_comments(false)
        .layout_tests(false)
        .generate()
        .expect("private FFmpeg and reviewed encoder ABI bindings")
        .write_to_file(output.join("bindings.rs"))
        .expect("write private encoder bindings");
}
