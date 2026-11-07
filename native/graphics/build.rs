// SPDX-License-Identifier: GPL-3.0-only
fn main() {
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
    let output = std::path::PathBuf::from(std::env::var_os("OUT_DIR").expect("Cargo output"));
    bindgen::Builder::default()
        .parse_callbacks(Box::new(bindgen::CargoCallbacks::new()))
        .header(root.join("wrapper.h").to_string_lossy())
        .clang_arg("-I/usr/include/libdrm")
        .clang_arg("-I/usr/include/pipewire-0.3")
        .clang_arg("-I/usr/include/spa-0.2")
        .allowlist_type("EGL.*|GL.*|gbm_.*|gsr_kms_response_item|dpe_kms_scene|dpe_vk_packet|PFNEGL.*|PFNGLEGL.*|pw_loop|spa_loop_utils_methods|spa_data|spa_chunk|sync_file_info")
        .allowlist_function("egl.*|gl.*|gbm_.*")
        .allowlist_var("EGL_.*|GL_.*|GBM_.*|DRM_FORMAT_.*|DPE_VK_.*|DPE_GRAPHICS_.*|SPA_IO_.*")
        .derive_default(true)
        .generate_comments(false)
        .layout_tests(false)
        .generate()
        .expect("system EGL/GLES/GBM bindings")
        .write_to_file(output.join("bindings.rs"))
        .expect("write graphics bindings");
    for library in ["EGL", "GLESv2", "gbm"] {
        println!("cargo:rustc-link-lib={library}");
    }
}
