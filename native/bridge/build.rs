// SPDX-License-Identifier: MIT
fn main() {
    println!("cargo:rerun-if-env-changed=DPE_BUNDLED_ENCODER_IMAGE");
    let encoder = if let Some(path) = std::env::var_os("DPE_BUNDLED_ENCODER_IMAGE") {
        let path = std::path::PathBuf::from(path)
            .canonicalize()
            .expect("verified bundled encoder image");
        println!("cargo:rerun-if-changed={}", path.display());
        format!("pub(crate) static ENCODER_IMAGE: &[u8] = include_bytes!({path:?});")
    } else {
        "pub(crate) static ENCODER_IMAGE: &[u8] = &[];".to_owned()
    };
    std::fs::write(
        std::path::PathBuf::from(std::env::var_os("OUT_DIR").expect("Cargo output"))
            .join("embedded.rs"),
        encoder,
    )
    .expect("bundled encoder declaration");
    println!("cargo:rerun-if-env-changed=DPE_NODE_INCLUDE");
    let node_include = std::env::var_os("DPE_NODE_INCLUDE")
        .map(std::path::PathBuf::from)
        .or_else(|| {
            let node = std::process::Command::new("node")
                .args(["-p", "process.execPath"])
                .output()
                .ok()?;
            let executable = std::path::PathBuf::from(String::from_utf8(node.stdout).ok()?.trim())
                .canonicalize()
                .ok()?;
            Some(executable.parent()?.parent()?.join("include/node"))
        })
        .filter(|path| path.join("node_api.h").is_file())
        .expect("Node.js development headers (node_api.h) are required");
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    let header = root.join("native/bridge/wrapper.h");
    println!("cargo:rerun-if-changed={}", header.display());
    println!(
        "cargo:rerun-if-changed={}",
        root.join("integration/video/encoder-abi.h").display()
    );
    let output = std::path::PathBuf::from(std::env::var_os("OUT_DIR").expect("Cargo output"));
    bindgen::Builder::default()
        .parse_callbacks(Box::new(bindgen::CargoCallbacks::new()))
        .header(header.to_string_lossy())
        .clang_arg("-I/usr/include/libdrm")
        .clang_arg(format!("-I{}", node_include.display()))
        .allowlist_type("VA.*|_VA.*|VADRM.*|encoder_.*|dpe_hevc_api|dpe_discord.*|dpe_direct.*|sync_file_info|v4l2_format|v4l2_fmtdesc|napi_.*|link_map")
        .allowlist_function("va.*|dpe_direct_gpu_.*|dpe_direct_frame_.*|dpe_direct_capture_ready")
        .allowlist_var("VA_.*|DRM_FORMAT_.*|DPE_HEVC_ABI")
        .derive_default(true)
        .generate_comments(false)
        .layout_tests(false)
        .generate()
        .expect("system VAAPI bindings")
        .write_to_file(output.join("bindings.rs"))
        .expect("write VAAPI bindings");
    println!("cargo:rustc-link-lib=va");
    println!("cargo:rustc-link-lib=va-drm");
    bindgen::Builder::default()
        .parse_callbacks(Box::new(bindgen::CargoCallbacks::new()))
        .header(root.join("native/bridge/capture.h").to_string_lossy())
        .clang_arg("-I/usr/include/pipewire-0.3")
        .clang_arg("-I/usr/include/spa-0.2")
        .allowlist_type("pw_buffer|pw_stream|spa_meta_header|spa_meta_region|spa_meta_busy|spa_meta_type|spa_data_type")
        .allowlist_var("SPA_.*")
        .prepend_enum_name(false)
        .derive_default(true)
        .generate_comments(false)
        .layout_tests(false)
        .generate()
        .expect("system PipeWire buffer metadata bindings")
        .write_to_file(output.join("capture.rs"))
        .expect("write capture bindings");
}
