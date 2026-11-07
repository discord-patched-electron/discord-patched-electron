// SPDX-License-Identifier: MIT
//! Initial allocation boundary for completed compositor DMA-BUFs.
use crate::ffi as f;
use std::os::fd::{AsRawFd, BorrowedFd};
pub struct Plane<'a> {
    pub descriptor: BorrowedFd<'a>,
    pub stride: u32,
    pub offset: u32,
}
/// # Safety
/// Same synchronous native allocation and retained storage contract as
/// initialize. One linear BGRA destination has its exact byte extent published;
/// a checked-out buffer's chunk stays empty until GPU completion.
pub unsafe fn initialize_copy(
    buffer: *mut f::spa_buffer,
    plane: Plane<'_>,
    bytes: u32,
) -> Result<(), &'static str> {
    if plane.stride == 0
        || bytes == 0
        || !bytes.is_multiple_of(plane.stride)
        || bytes / plane.stride > 8192
    {
        return Err("Invalid copy destination extent");
    }
    // SAFETY: the caller retains all complete native records and output storage.
    unsafe {
        initialize(buffer, &[plane])?;
    }
    // SAFETY: initialize validated exactly one data/chunk record; no native
    // call or dispatch occurs between validation and this final extent commit.
    unsafe {
        let data = (*buffer).datas;
        (*data).maxsize = bytes;
        (*data).flags = f::SPA_DATA_FLAG_READWRITE;
        (*(*data).chunk).size = 0;
    }
    Ok(())
}
/// # Safety
/// Native supplies a complete writable SPA buffer and data/chunk arrays for
/// this synchronous ALLOC_BUFFERS callback. The factory retains every exported
/// descriptor/storage owner in its resulting Rust buffer, and never dispatches
/// the stream while these borrowed native records are accessed.
pub unsafe fn initialize(
    buffer: *mut f::spa_buffer,
    planes: &[Plane<'_>],
) -> Result<(), &'static str> {
    if buffer.is_null()
        || !(1..=4).contains(&planes.len())
        || planes
            .iter()
            .any(|p| p.stride == 0 || p.stride > i32::MAX as u32 || p.offset > i32::MAX as u32)
    {
        return Err("Invalid DMA-BUF planes");
    }
    // SAFETY: complete native prefix, borrowed through this synchronous factory.
    let (count, data) = unsafe { ((*buffer).n_datas, (*buffer).datas) };
    if count as usize != planes.len() || data.is_null() {
        return Err("DMA-BUF plane count mismatch");
    }
    let mut chunks = [std::ptr::null_mut(); 4];
    for (index, target) in chunks.iter_mut().enumerate().take(planes.len()) {
        // SAFETY: index lies inside the supplied initialized data-array extent.
        let entry = unsafe { data.add(index) };
        // SAFETY: read only initialized type/chunk fields, no foreign references.
        let (kind, chunk) = unsafe {
            (
                std::ptr::addr_of!((*entry).type_).read(),
                std::ptr::addr_of!((*entry).chunk).read(),
            )
        };
        if kind & (1 << f::spa_data_type_SPA_DATA_DmaBuf) == 0
            || chunk.is_null()
            || !chunk.is_aligned()
        {
            return Err("DMA-BUF data type unavailable");
        }
        *target = chunk;
    }
    if chunks[..planes.len()]
        .iter()
        .enumerate()
        .any(|(index, chunk)| chunks[..index].contains(chunk))
    {
        return Err("Aliased DMA-BUF chunks");
    }
    for (index, plane) in planes.iter().enumerate() {
        // SAFETY: the complete bounded data/chunk arrays were validated above;
        // no native call or dispatch can mutate them before this commit.
        let entry = unsafe { data.add(index) };
        let chunk = chunks[index];
        // SAFETY: producer owns both complete writable records until return;
        // the descriptor remains borrowed from the resulting storage owner.
        unsafe {
            chunk.write(f::spa_chunk {
                offset: plane.offset,
                stride: plane.stride as i32,
                size: 1,
                ..Default::default()
            });
            entry.write(f::spa_data {
                type_: f::spa_data_type_SPA_DATA_DmaBuf,
                flags: f::SPA_DATA_FLAG_READABLE,
                fd: i64::from(plane.descriptor.as_raw_fd()),
                chunk,
                ..Default::default()
            });
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::fd::AsFd;
    #[test]
    fn copy_extent_validation_precedes_allocation_publication() {
        let file = std::fs::File::open("/dev/null").unwrap();
        let mut chunk = f::spa_chunk {
            size: 99,
            ..Default::default()
        };
        let mut data = f::spa_data {
            type_: 1 << f::spa_data_type_SPA_DATA_DmaBuf,
            fd: -1,
            chunk: &mut chunk,
            ..Default::default()
        };
        let mut buffer = f::spa_buffer {
            n_datas: 1,
            datas: &mut data,
            ..Default::default()
        };
        for bytes in [0, 257, 256 * 8193] {
            assert!(
                // SAFETY: complete writable mock records and a borrowed
                // descriptor; invalid extents leave every record unchanged.
                unsafe {
                    initialize_copy(
                        &mut buffer,
                        Plane {
                            descriptor: file.as_fd(),
                            stride: 256,
                            offset: 0,
                        },
                        bytes,
                    )
                }
                .is_err()
            );
            assert_eq!((data.fd, chunk.size), (-1, 99));
        }
        assert_eq!(
            // SAFETY: one initialized destination and retained descriptor.
            unsafe {
                initialize_copy(
                    &mut buffer,
                    Plane {
                        descriptor: file.as_fd(),
                        stride: 256,
                        offset: 0,
                    },
                    256 * 32,
                )
            },
            Ok(())
        );
        assert_eq!(
            (data.maxsize, data.flags, chunk.size, chunk.stride),
            (8192, f::SPA_DATA_FLAG_READWRITE, 0, 256)
        );
    }
    #[test]
    fn invalid_later_planes_leave_the_entire_native_allocation_unchanged() {
        let file = std::fs::File::open("/dev/null").unwrap();
        let planes = [
            Plane {
                descriptor: file.as_fd(),
                stride: 256,
                offset: 0,
            },
            Plane {
                descriptor: file.as_fd(),
                stride: 128,
                offset: 4096,
            },
        ];
        let mut chunks = [f::spa_chunk {
            size: 99,
            ..Default::default()
        }; 2];
        let mut data = [f::spa_data {
            type_: 1 << f::spa_data_type_SPA_DATA_DmaBuf,
            fd: -1,
            ..Default::default()
        }; 2];
        data[0].chunk = &mut chunks[0];
        data[1].chunk = std::ptr::null_mut();
        let mut buffer = f::spa_buffer {
            n_datas: 2,
            datas: data.as_mut_ptr(),
            ..Default::default()
        };
        // SAFETY: the initialized mock arrays are complete and writable. The
        // descriptor is borrowed throughout; no native dispatch is performed.
        assert!(unsafe { initialize(&mut buffer, &planes) }.is_err());
        assert_eq!(data[0].fd, -1);
        assert_eq!(chunks[0].size, 99);
        data[1].chunk = &mut chunks[0];
        // SAFETY: complete arrays intentionally alias a chunk; reject before
        // any record is changed, without forming aliased Rust references.
        assert!(unsafe { initialize(&mut buffer, &planes) }.is_err());
        assert_eq!(chunks[0].size, 99);
        data[1].chunk = &mut chunks[1];
        // SAFETY: complete independent native chunk records and live descriptors.
        assert_eq!(unsafe { initialize(&mut buffer, &planes) }, Ok(()));
        assert_eq!(data[0].fd, i64::from(file.as_raw_fd()));
        assert_eq!(
            (chunks[1].offset, chunks[1].stride, chunks[1].size),
            (4096, 128, 1)
        );
    }
}
