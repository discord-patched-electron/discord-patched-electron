// SPDX-License-Identifier: MIT
//! Checked clipping/scaling before passing small rectangles to the GPU API.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Rect {
    pub x: i32,
    pub y: i32,
    pub width: u32,
    pub height: u32,
}
impl Rect {
    pub fn contained(self, image: (u32, u32)) -> bool {
        self.x >= 0
            && self.y >= 0
            && valid_size(image)
            && self.width > 0
            && self.height > 0
            && i64::from(self.x) + i64::from(self.width) <= i64::from(image.0)
            && i64::from(self.y) + i64::from(self.height) <= i64::from(image.1)
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Blit {
    pub source: Rect,
    pub destination: Rect,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Invalid {
    Image,
    Source,
    Destination,
    Canvas,
    Target,
}
fn valid_size((width, height): (u32, u32)) -> bool {
    (1..=8192).contains(&width) && (1..=8192).contains(&height)
}
pub fn scaled_blit(
    image: (u32, u32),
    source: Rect,
    destination: Rect,
    canvas: (u32, u32),
    target: (u32, u32),
) -> Result<Option<Blit>, Invalid> {
    if !valid_size(image) {
        return Err(Invalid::Image);
    }
    if !source.contained(image) {
        return Err(Invalid::Source);
    }
    if !valid_size((destination.width, destination.height)) {
        return Err(Invalid::Destination);
    }
    if !valid_size(canvas) {
        return Err(Invalid::Canvas);
    }
    if !valid_size(target) {
        return Err(Invalid::Target);
    }
    let left = i64::from(destination.x).max(0);
    let top = i64::from(destination.y).max(0);
    let right = (i64::from(destination.x) + i64::from(destination.width)).min(i64::from(canvas.0));
    let bottom =
        (i64::from(destination.y) + i64::from(destination.height)).min(i64::from(canvas.1));
    if right <= left || bottom <= top {
        return Ok(None);
    }
    let source = Rect {
        x: source.x
            + ((left - i64::from(destination.x)) * i64::from(source.width)
                / i64::from(destination.width)) as i32,
        y: source.y
            + ((top - i64::from(destination.y)) * i64::from(source.height)
                / i64::from(destination.height)) as i32,
        width: ((right - left) * i64::from(source.width) / i64::from(destination.width)) as u32,
        height: ((bottom - top) * i64::from(source.height) / i64::from(destination.height)) as u32,
    };
    let destination = Rect {
        x: (left * i64::from(target.0) / i64::from(canvas.0)) as i32,
        y: (top * i64::from(target.1) / i64::from(canvas.1)) as i32,
        width: ((right - left) * i64::from(target.0) / i64::from(canvas.0)) as u32,
        height: ((bottom - top) * i64::from(target.1) / i64::from(canvas.1)) as u32,
    };
    // A clipped cursor may become smaller than one target pixel. It disappears
    // rather than failing capture or passing a zero-sized rectangle to VAAPI.
    if source.width == 0 || source.height == 0 || destination.width == 0 || destination.height == 0
    {
        return Ok(None);
    }
    if !source.contained(image) || !destination.contained(target) {
        return Err(Invalid::Destination);
    }
    Ok(Some(Blit {
        source,
        destination,
    }))
}
#[cfg(test)]
mod tests {
    use super::*;
    fn rect(x: i32, y: i32, width: u32, height: u32) -> Rect {
        Rect {
            x,
            y,
            width,
            height,
        }
    }
    #[test]
    fn whole_picture_and_clipped_cursor_preserve_source_coordinates() {
        let full = rect(0, 0, 1920, 1080);
        assert_eq!(
            scaled_blit((1920, 1080), full, full, (1920, 1080), (1280, 720)),
            Ok(Some(Blit {
                source: full,
                destination: rect(0, 0, 1280, 720)
            }))
        );
        assert_eq!(
            scaled_blit(
                (16, 16),
                rect(0, 0, 16, 16),
                rect(-4, -4, 16, 16),
                (640, 480),
                (320, 240)
            ),
            Ok(Some(Blit {
                source: rect(4, 4, 12, 12),
                destination: rect(0, 0, 6, 6)
            }))
        );
    }
    #[test]
    fn invisible_and_subpixel_cursors_are_harmless() {
        let image = (16, 16);
        let source = rect(0, 0, 16, 16);
        for destination in [
            rect(i32::MIN, i32::MIN, 16, 16),
            rect(1920, 1080, 16, 16),
            rect(1919, 1079, 16, 16),
        ] {
            assert_eq!(
                scaled_blit(image, source, destination, (1920, 1080), (320, 180)),
                Ok(None)
            );
        }
    }
    #[test]
    fn invalid_extents_fail_before_integer_conversion_or_gpu_access() {
        let full = rect(0, 0, 8192, 8192);
        assert_eq!(
            scaled_blit(
                (8192, 8192),
                rect(8190, 0, 4, 16),
                full,
                (8192, 8192),
                (8192, 8192)
            ),
            Err(Invalid::Source)
        );
        assert_eq!(
            scaled_blit(
                (8192, 8192),
                full,
                rect(0, 0, u32::MAX, 16),
                (8192, 8192),
                (8192, 8192)
            ),
            Err(Invalid::Destination)
        );
        assert_eq!(
            scaled_blit((8192, 8192), full, full, (0, 8192), (8192, 8192)),
            Err(Invalid::Canvas)
        );
    }
}
