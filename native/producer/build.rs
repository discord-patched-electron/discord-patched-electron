// SPDX-License-Identifier: GPL-3.0-only
fn main() {
    let header = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("wrapper.h");
    let output = std::path::PathBuf::from(std::env::var_os("OUT_DIR").expect("Cargo output"));
    bindgen::Builder::default()
        .parse_callbacks(Box::new(bindgen::CargoCallbacks::new()))
        .header(header.to_string_lossy())
        .allowlist_type("VkApplicationInfo|VkInstanceCreateInfo|VkPhysicalDeviceDrmPropertiesEXT|VkPhysicalDeviceIDProperties|VkPhysicalDeviceProperties2|PFN_vk(CreateInstance|DestroyInstance|EnumeratePhysicalDevices|GetPhysicalDeviceProperties2)|dma_buf_export_sync_file|sync_merge_data")
        .allowlist_var("VK_(SUCCESS|STRUCTURE_TYPE_(APPLICATION_INFO|INSTANCE_CREATE_INFO|PHYSICAL_DEVICE_DRM_PROPERTIES_EXT|PHYSICAL_DEVICE_ID_PROPERTIES|PHYSICAL_DEVICE_PROPERTIES_2))|DMA_BUF_SYNC_READ")
        .derive_default(true)
        .generate_comments(false)
        .layout_tests(false)
        .generate()
        .expect("system Vulkan inventory and Linux fence bindings")
        .write_to_file(output.join("bindings.rs"))
        .expect("write producer bindings");
    println!("cargo:rustc-link-arg-bin=dpe-direct-source=-Wl,-z,relro,-z,now,-z,noexecstack");
}
