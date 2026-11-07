// SPDX-License-Identifier: MIT
//! Choose the bounded SDR export contract before allocating a Vulkan image.
use crate::graphics_ffi as f;

#[unsafe(no_mangle)]
pub extern "C" fn dpe_vk_export_format(format: u32, color_space: u32) -> u32 {
    if color_space != f::VK_COLOR_SPACE_SRGB_NONLINEAR_KHR {
        return f::VK_FORMAT_UNDEFINED;
    }
    match format {
        f::VK_FORMAT_B8G8R8A8_UNORM
        | f::VK_FORMAT_B8G8R8A8_SRGB
        | f::VK_FORMAT_R8G8B8A8_UNORM
        | f::VK_FORMAT_R8G8B8A8_SRGB => format,
        // These noninteger formats can use Vulkan's existing numeric blit.
        // The declared SDR color space needs no HDR transfer or tone mapping.
        f::VK_FORMAT_A2R10G10B10_UNORM_PACK32
        | f::VK_FORMAT_A2B10G10R10_UNORM_PACK32
        | f::VK_FORMAT_R16G16B16A16_UNORM
        | f::VK_FORMAT_R16G16B16A16_SFLOAT => f::VK_FORMAT_B8G8R8A8_UNORM,
        _ => f::VK_FORMAT_UNDEFINED,
    }
}

#[unsafe(no_mangle)]
pub extern "C" fn dpe_vk_blit_supported(source_features: u32, destination_features: u32) -> bool {
    source_features & f::VK_FORMAT_FEATURE_BLIT_SRC_BIT != 0
        && destination_features & f::VK_FORMAT_FEATURE_BLIT_DST_BIT != 0
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn export_preserves_eight_bit_and_converts_only_known_sdr_formats() {
        for format in [
            f::VK_FORMAT_B8G8R8A8_UNORM,
            f::VK_FORMAT_B8G8R8A8_SRGB,
            f::VK_FORMAT_R8G8B8A8_UNORM,
            f::VK_FORMAT_R8G8B8A8_SRGB,
        ] {
            assert_eq!(dpe_vk_export_format(format, 0), format);
        }
        for format in [
            f::VK_FORMAT_A2R10G10B10_UNORM_PACK32,
            f::VK_FORMAT_A2B10G10R10_UNORM_PACK32,
            f::VK_FORMAT_R16G16B16A16_UNORM,
            f::VK_FORMAT_R16G16B16A16_SFLOAT,
        ] {
            assert_eq!(dpe_vk_export_format(format, 0), f::VK_FORMAT_B8G8R8A8_UNORM);
            for color in [1, f::VK_COLOR_SPACE_HDR10_ST2084_EXT, u32::MAX] {
                assert_eq!(dpe_vk_export_format(format, color), f::VK_FORMAT_UNDEFINED);
            }
        }
        for format in [
            0,
            f::VK_FORMAT_R8G8B8A8_UINT,
            f::VK_FORMAT_D32_SFLOAT,
            u32::MAX,
        ] {
            assert_eq!(dpe_vk_export_format(format, 0), f::VK_FORMAT_UNDEFINED);
        }
    }

    #[test]
    fn blit_requires_both_exact_tiling_features() {
        let source = f::VK_FORMAT_FEATURE_BLIT_SRC_BIT;
        let destination = f::VK_FORMAT_FEATURE_BLIT_DST_BIT;
        assert!(dpe_vk_blit_supported(source, destination));
        assert!(dpe_vk_blit_supported(u32::MAX, u32::MAX));
        assert!(!dpe_vk_blit_supported(source, 0));
        assert!(!dpe_vk_blit_supported(0, destination));
        assert!(!dpe_vk_blit_supported(destination, source));
    }
}
