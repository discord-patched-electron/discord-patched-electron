// SPDX-License-Identifier: MIT
fn main() {
    let header = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("wrapper.h");
    let output = std::path::PathBuf::from(std::env::var_os("OUT_DIR").expect("Cargo output"));
    bindgen::Builder::default()
        .parse_callbacks(Box::new(bindgen::CargoCallbacks::new()))
        .header(header.to_string_lossy())
        .clang_arg("-I/usr/include/pipewire-0.3")
        .clang_arg("-I/usr/include/spa-0.2")
        .clang_arg("-I/usr/include/libdrm")
        .allowlist_type("dpe_vk_packet|dpe_relay_timeline|spa_buffer|spa_data|spa_data_type|spa_data_flags|spa_video_format|spa_meta|spa_meta_header|spa_chunk|spa_pod|spa_hook|pw_stream|pw_stream_events|pw_core|pw_core_events|pw_core_methods|spa_interface|pw_context|pw_loop|spa_loop_utils_methods|spa_loop_control_methods|sync_file_info")
        .allowlist_function("drm(GetCap|Ioctl|Syncobj.*)")
        .allowlist_function("pw_(init|main_loop_(new|get_loop|destroy)|context_(new|connect|destroy)|core_disconnect|stream_(new|destroy|add_listener|connect|update_params|get_node_id|get_state|dequeue_buffer|queue_buffer|return_buffer|trigger_process|is_driving|is_lazy)|properties_(new_string|free))")
        .allowlist_var("DRM_.*|SPA_(IO|DATA|VIDEO_FORMAT)_.*|DPE_RELAY_.*|DPE_POD_.*|DPE_STREAM_.*|DPE_PARAM_.*")
        .derive_default(true)
        .generate_comments(false)
        .layout_tests(false)
        .generate()
        .expect("system PipeWire loop ABI and DRM synchronization bindings")
        .write_to_file(output.join("bindings.rs"))
        .expect("write PipeWire bindings");
    if std::env::var_os("CARGO_FEATURE_RELAY").is_some() {
        println!("cargo:rustc-link-lib=drm");
    }
    if std::env::var_os("CARGO_FEATURE_SOURCE").is_some() {
        println!("cargo:rustc-link-lib=pipewire-0.3");
    }
}
