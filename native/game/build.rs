// SPDX-License-Identifier: MIT
fn main() {
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
    let output = std::path::PathBuf::from(std::env::var_os("OUT_DIR").expect("Cargo output"));
    bindgen::Builder::default()
        .parse_callbacks(Box::new(bindgen::CargoCallbacks::new()))
        .header(root.join("wrapper.h").to_string_lossy())
        .allowlist_type("dpe_gl_sync_egl|Vk(ImageCreateInfo|ExportSemaphoreCreateInfo|SemaphoreGetFdInfoKHR|FenceCreateInfo|SubmitInfo|PhysicalDeviceImageDrmFormatModifierInfoEXT|PhysicalDeviceExternalImageFormatInfo|PhysicalDeviceImageFormatInfo2|ExternalImageFormatProperties|ImageFormatProperties2|PhysicalDeviceIDProperties|PhysicalDeviceProperties2|PhysicalDeviceExternalSemaphoreInfo|ExternalSemaphoreProperties|QueueFamilyProperties)|PFN_vk(GetInstanceProcAddr|GetDeviceProcAddr|GetDeviceQueue|CreateSemaphore|DestroySemaphore|GetSemaphoreFdKHR|CreateFence|DestroyFence|GetFenceStatus|ResetFences|QueueSubmit|GetPhysicalDeviceImageFormatProperties2KHR|GetPhysicalDeviceProperties2KHR|GetPhysicalDeviceExternalSemaphorePropertiesKHR|GetPhysicalDeviceQueueFamilyProperties)")
        .allowlist_var("VK_.*|GL_.*")
        .allowlist_type("Vk(Format|ColorSpaceKHR|FormatFeatureFlagBits|PipelineStageFlagBits|ExternalMemoryFeatureFlagBits|ExternalSemaphoreFeatureFlagBits|QueueFlagBits)")
        .prepend_enum_name(false)
        .derive_default(true)
        .generate_comments(false)
        .layout_tests(false)
        .generate()
        .expect("system Vulkan/GL synchronization bindings")
        .write_to_file(output.join("graphics.rs"))
        .expect("write graphics bindings");
}
