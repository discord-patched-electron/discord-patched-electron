// SPDX-License-Identifier: MIT
use super::*;
use std::collections::VecDeque;

struct Frame {
    pw: f::pw_buffer,
    spa: f::spa_buffer,
    data: f::spa_data,
    chunk: f::spa_chunk,
    meta: f::spa_meta,
    header: f::spa_meta_header,
}
impl Frame {
    fn new(index: usize) -> Box<Self> {
        let mut frame = Box::new(Self {
            pw: Default::default(),
            spa: Default::default(),
            data: Default::default(),
            chunk: Default::default(),
            meta: Default::default(),
            header: Default::default(),
        });
        frame.header.pts = -1;
        frame.header.seq = index as u64;
        frame.meta = f::spa_meta {
            type_: f::SPA_META_Header,
            size: size_of::<f::spa_meta_header>() as u32,
            data: (&raw mut frame.header).cast(),
        };
        frame.chunk.size = 256;
        frame.chunk.stride = 16;
        frame.data.type_ = if index.is_multiple_of(2) {
            f::SPA_DATA_MemFd
        } else {
            f::SPA_DATA_DmaBuf
        };
        frame.data.chunk = &raw mut frame.chunk;
        frame.spa = f::spa_buffer {
            n_metas: 1,
            n_datas: 1,
            metas: &raw mut frame.meta,
            datas: &raw mut frame.data,
        };
        frame.pw.buffer = &raw mut frame.spa;
        frame
    }
}
#[derive(Default)]
struct Fake {
    // Each allocation pins its C records, regardless of vector reallocation.
    #[expect(
        clippy::vec_box,
        reason = "Each native metadata record must keep its address when the inventory moves"
    )]
    frames: Vec<Box<Frame>>,
    queue: VecDeque<Captured>,
    released: usize,
    restored: usize,
    errors: usize,
    fail_restore: bool,
}
thread_local! { static FAKE: RefCell<Fake> = RefCell::new(Fake::default()); }
unsafe extern "C" fn original(_: *mut f::pw_stream) -> Captured {
    FAKE.with(|state| {
        state.borrow_mut().queue.pop_front().unwrap_or(Captured {
            buffer: ptr::null_mut(),
            stream: std::mem::MaybeUninit::uninit(),
        })
    })
}
unsafe extern "C" fn returned(_: *mut Captured) {
    FAKE.with(|state| state.borrow_mut().released += 1);
}
unsafe extern "C" fn restored(stream: *mut f::pw_stream, buffer: *mut f::pw_buffer) -> i32 {
    FAKE.with(|state| {
        let mut state = state.borrow_mut();
        state.restored += 1;
        if state.fail_restore {
            -libc::EINVAL
        } else {
            state.queue.push_front(Captured {
                buffer,
                stream: std::mem::MaybeUninit::new(stream),
            });
            0
        }
    })
}
// Rust cannot define a variadic callback. A fixed three-argument callee
// matches the fixture's no-varargs error invocation under the SysV ABI.
unsafe extern "C" fn error(_: *mut f::pw_stream, code: i32, _: *const libc::c_char) -> i32 {
    assert_eq!(code, -libc::EIO);
    FAKE.with(|state| state.borrow_mut().errors += 1);
    0
}
fn count(counter: &AtomicU64) -> u64 {
    counter.load(Ordering::Relaxed)
}
fn reset(size: usize) {
    for counter in [
        &COUNTS.callbacks,
        &COUNTS.frames,
        &COUNTS.dmabuf,
        &COUNTS.memfd,
        &COUNTS.other,
        &COUNTS.empty,
        &COUNTS.skipped,
        &COUNTS.conservative,
        &COUNTS.return_errors,
        &COUNTS.maximum,
        &COUNTS.known_pts,
        &COUNTS.unknown_pts,
        &COUNTS.metadata,
        &COUNTS.paced,
        &COUNTS.bypass,
        &COUNTS.timeline,
        &COUNTS.invalid_timeline,
        &COUNTS.absent_timeline,
    ] {
        counter.store(0, Ordering::Relaxed);
    }
    for counter in &COUNTS.histogram {
        counter.store(0, Ordering::Relaxed);
    }
    LATEST.store(true, Ordering::Relaxed);
    configure(0);
    LOCAL.with(|local| *local.borrow_mut() = Local::default());
    FAKE.with(|state| {
        let frames: Vec<_> = (0..size).map(Frame::new).collect();
        let queue = frames
            .iter()
            .map(|frame| Captured {
                buffer: (&raw const frame.pw).cast_mut(),
                stream: std::mem::MaybeUninit::new(ptr::dangling_mut::<f::pw_stream>()),
            })
            .collect();
        *state.borrow_mut() = Fake {
            frames,
            queue,
            ..Default::default()
        };
    });
}
fn invoke(time: u64) -> Captured {
    // SAFETY: the fixture pins every native record and uses its own callbacks.
    unsafe { dequeue_with_clock(ptr::dangling_mut::<f::pw_stream>(), || time) }
}
fn frame(index: usize) -> *mut f::pw_buffer {
    FAKE.with(|state| (&raw const state.borrow().frames[index].pw).cast_mut())
}
#[test]
fn bounded_queue_ownership_metadata_and_dynamic_pacing() {
    // SAFETY: no variadic arguments are sent; Linux SysV keeps the same
    // three declared integer/pointer argument locations for this fixture.
    let error = unsafe {
        std::mem::transmute::<
            unsafe extern "C" fn(*mut f::pw_stream, i32, *const libc::c_char) -> i32,
            StreamError,
        >(error)
    };
    assert!(
        NATIVE
            .set(Native {
                dequeue: original,
                release: returned,
                restore: Some(restored),
                error: Some(error)
            })
            .is_ok()
    );
    for malformed in [1, 65] {
        reset(1);
        FAKE.with(|state| {
            let mut state = state.borrow_mut();
            state.frames[0].spa.metas = ptr::null_mut();
            state.frames[0].spa.n_metas = malformed;
        });
        assert_eq!(invoke(0).buffer, frame(0));
        assert_eq!(
            (count(&COUNTS.unknown_pts), count(&COUNTS.conservative)),
            (1, 1)
        );
    }
    reset(4);
    let mut chosen = invoke(0);
    assert_eq!(chosen.buffer, frame(3));
    assert_eq!(
        (
            count(&COUNTS.skipped),
            count(&COUNTS.dmabuf),
            count(&COUNTS.unknown_pts)
        ),
        (3, 1, 1)
    );
    // SAFETY: chosen is the exact still-owned fixture lease.
    unsafe {
        release(&mut chosen);
    }
    FAKE.with(|state| assert_eq!(state.borrow().released, 4));
    LOCAL.with(|local| assert_eq!(local.borrow().processing, 0));

    // Every unfamiliar metadata/chunk case preserves FIFO and does not
    // release a potentially unsignaled synchronization/control frame.
    for kind in 0..8 {
        reset(4);
        FAKE.with(|state| {
            let mut state = state.borrow_mut();
            let frame = &mut state.frames[1];
            match kind {
                0 => frame.meta.type_ = f::SPA_META_Cursor,
                1 => frame.meta.type_ = u32::MAX,
                2 => frame.header.flags = 1,
                3 => {
                    frame.data.type_ = f::SPA_DATA_MemFd;
                    frame.chunk.size = 0;
                }
                4 => frame.chunk.flags = 1,
                5 => frame.meta.size = 1,
                6 => frame.data.type_ = f::SPA_DATA_MemPtr,
                _ => {
                    frame.meta.type_ = 9;
                    frame.meta.size = 24;
                }
            }
        });
        assert_eq!(invoke(0).buffer, frame(0));
        FAKE.with(|state| {
            let state = state.borrow();
            assert_eq!(
                (state.queue.len(), state.released, state.restored),
                (3, 0, 3)
            );
        });
        for index in 1..4 {
            // SAFETY: directly drain the fixture's original FIFO after restoration.
            let returned = unsafe { original(ptr::dangling_mut::<f::pw_stream>()) };
            assert_eq!(returned.buffer, frame(index));
        }
    }
    reset(LIMIT + 1);
    assert_eq!(invoke(0).buffer, frame(0));
    FAKE.with(|state| {
        let state = state.borrow();
        assert_eq!((state.queue.len(), state.released), (LIMIT, 0));
    });
    for index in 1..=LIMIT {
        // SAFETY: remaining leases belong to this fixture's original FIFO.
        let returned = unsafe { original(ptr::dangling_mut::<f::pw_stream>()) };
        assert_eq!(returned.buffer, frame(index));
    }
    reset(3);
    FAKE.with(|state| {
        let mut state = state.borrow_mut();
        state.frames[1].meta.type_ = f::SPA_META_Cursor;
        state.fail_restore = true;
    });
    assert_eq!(invoke(0).buffer, frame(0));
    FAKE.with(|state| assert_eq!((state.borrow().errors, state.borrow().released), (2, 0)));
    assert_eq!(count(&COUNTS.return_errors), 2);
    assert!(!LATEST.load(Ordering::Relaxed));
    reset(4);
    LATEST.store(false, Ordering::Relaxed);
    assert_eq!(invoke(0).buffer, frame(0));
    FAKE.with(|state| {
        assert_eq!(
            (state.borrow().queue.len(), state.borrow().restored),
            (3, 0)
        )
    });
    reset(0);
    assert!(invoke(0).buffer.is_null());
    assert_eq!((count(&COUNTS.callbacks), count(&COUNTS.frames)), (1, 0));

    for fps in [30, 60] {
        reset(1);
        configure(fps);
        FAKE.with(|state| state.borrow_mut().queue.clear());
        for index in 0..240u64 {
            let buffer = frame(0);
            FAKE.with(|state| {
                state.borrow_mut().queue.push_back(Captured {
                    buffer,
                    stream: std::mem::MaybeUninit::new(ptr::dangling_mut::<f::pw_stream>()),
                })
            });
            let mut chosen = invoke(index * 1_000_000_000 / 240);
            if !chosen.buffer.is_null() {
                // SAFETY: accepted fixture lease is returned exactly once.
                unsafe {
                    release(&mut chosen);
                }
            }
        }
        assert_eq!(
            (count(&COUNTS.frames), count(&COUNTS.paced)),
            (u64::from(fps), 240 - u64::from(fps))
        );
        FAKE.with(|state| assert_eq!(state.borrow().released, 240));
    }
    for fps in (5..=60).step_by(5) {
        configure(0);
        configure(fps);
        for index in 0..120u64 {
            let now =
                1_000_000_000 + index * 1_000_000_000 / u64::from(fps) + (index % 3) * 200_000;
            assert!(due(1, PACING.load(Ordering::Relaxed), now));
            assert!(!due(1, PACING.load(Ordering::Relaxed), now));
        }
    }
    configure(0);
    configure(30);
    assert!(due(1, PACING.load(Ordering::Relaxed), 9_000_000_000));
    configure(60);
    assert!(due(1, PACING.load(Ordering::Relaxed), 9_000_000_001));
    assert!(due(2, PACING.load(Ordering::Relaxed), 9_000_000_002));
    assert!(due(2, PACING.load(Ordering::Relaxed), 1));
    reset(1);
    configure(30);
    assert!(due(1, PACING.load(Ordering::Relaxed), 100));
    FAKE.with(|state| state.borrow_mut().frames[0].meta.type_ = f::SPA_META_Cursor);
    assert_eq!(invoke(101).buffer, frame(0));
    assert_eq!(count(&COUNTS.bypass), 1);
}
#[test]
fn sync_observation_never_reads_points_or_adopts_unvalidated_fds() {
    fn observe(raw: &f::spa_buffer, meta: &f::spa_meta, data: &[f::spa_data], counts: &Counters) {
        std::hint::black_box((meta, data));
        Buffer(raw).observe_sync(counts);
    }
    let counts = Counters::new();
    let mut points = [0u64, 17, 18];
    let mut meta = f::spa_meta {
        type_: 9,
        size: 24,
        data: points.as_mut_ptr().cast(),
    };
    let mut data = [f::spa_data::default(); 3];
    data[0].type_ = f::SPA_DATA_DmaBuf;
    for (index, data) in data[1..].iter_mut().enumerate() {
        data.type_ = 5;
        data.fd = 12 + index as i64;
    }
    let mut raw = f::spa_buffer {
        n_metas: 1,
        n_datas: 3,
        metas: &raw mut meta,
        datas: data.as_mut_ptr(),
    };
    observe(&raw, &meta, &data, &counts);
    assert_eq!(count(&counts.timeline), 1);
    data[2].fd = -1;
    observe(&raw, &meta, &data, &counts);
    data[2].fd = 13;
    raw.n_datas = 2;
    observe(&raw, &meta, &data, &counts);
    raw.n_datas = 3;
    data[2].type_ = f::SPA_DATA_DmaBuf;
    observe(&raw, &meta, &data, &counts);
    data[2].type_ = 5;
    meta.size = 23;
    observe(&raw, &meta, &data, &counts);
    meta.size = 24;
    meta.data = ptr::null_mut();
    observe(&raw, &meta, &data, &counts);
    assert_eq!(count(&counts.invalid_timeline), 5);
    raw.n_metas = 0;
    observe(&raw, &meta, &data, &counts);
    data[0].type_ = f::SPA_DATA_MemFd;
    observe(&raw, &meta, &data, &counts);
    assert_eq!(count(&counts.absent_timeline), 1);
    assert_eq!(points, [0, 17, 18]);
}
