// SPDX-License-Identifier: MIT
use super::*;
use std::cell::Cell;
thread_local! {
    pub(super) static VCN4: Cell<bool> = const { Cell::new(false) };
    static TEST: RefCell<Test> = RefCell::new(Test::default());
}
struct Test {
    session: f::encoder_session,
    options: f::encoder_options,
    emit: bool,
    quality: bool,
    vendor: &'static CStr,
    metadata: [u8; 0x110],
    calls: [usize; 6],
    codec: u32,
}
impl Default for Test {
    fn default() -> Self {
        let mut metadata = [0; 0x110];
        metadata[8] = 1;
        metadata[0x18..0x22].copy_from_slice(b"amd: vaapi");
        metadata[0x2f] = 10;
        Self {
            session: f::encoder_session::default(),
            options: f::encoder_options::default(),
            emit: false,
            quality: false,
            vendor: c"Mesa Gallium driver 26 for AMD Radeon",
            metadata,
            calls: [0; 6],
            codec: 4,
        }
    }
}
unsafe extern "C" fn create_stub(
    kind: u32,
    frame: *const c_void,
    context: *mut c_void,
    encoded: *mut c_void,
    dropped: *mut c_void,
    output: *mut *mut c_void,
    session: f::encoder_session,
    options: f::encoder_options,
) -> bool {
    assert!(matches!(kind, 0 | 3 | 5 | 6 | 10));
    assert_eq!(
        (
            frame as usize,
            context as usize,
            encoded as usize,
            dropped as usize
        ),
        (1, 2, 3, 4)
    );
    let (emit, quality) = TEST.with(|test| {
        let mut test = test.borrow_mut();
        test.calls[0] += 1;
        assert_eq!(
            (
                session.fps,
                session.codec,
                session.maximum.width,
                session.maximum.height
            ),
            (
                test.session.fps,
                test.session.codec,
                test.session.maximum.width,
                test.session.maximum.height
            )
        );
        assert_eq!(
            (
                options.fps,
                options.dimensions.width,
                options.dimensions.height,
                options.bitrate_target,
                options.bitrate_maximum,
                options.keyframe_interval
            ),
            (
                test.options.fps,
                test.options.dimensions.width,
                test.options.dimensions.height,
                test.options.bitrate_target,
                test.options.bitrate_maximum,
                test.options.keyframe_interval
            )
        );
        (test.emit, test.quality)
    });
    // SAFETY: the real Rust wrapper forwards this complete writable test slot.
    unsafe {
        output.write(0x123400usize as *mut c_void);
    }
    if emit {
        let mut config = [0u8; 0x50];
        let mut output = [0u8; 0x300];
        // SAFETY: complete local initialized config/output extents; real policy
        // reads the exact reviewed native offsets, including nested construction.
        unsafe {
            write(
                config.as_mut_ptr().cast(),
                0x20,
                u64::from(options.bitrate_target),
            );
            write(config.as_mut_ptr().cast(), 0x30, options.fps);
            sequence(
                output.as_mut_ptr().cast(),
                config.as_ptr().cast(),
                ptr::null(),
            );
        }
    }
    if quality {
        let mut output = [0u8; 0x40];
        let mut native_session = [0u8; 0x38];
        native_session[0x32] = session.codec as u8;
        let display = 0x5678usize;
        // SAFETY: complete local native test records and borrowed display word.
        unsafe {
            tunings(
                output.as_mut_ptr().cast(),
                (&raw const display).cast(),
                native_session.as_ptr().cast(),
                ptr::null(),
            );
        }
    }
    true
}
unsafe extern "C" fn update_stub(handle: *mut c_void, options: f::encoder_options) -> bool {
    assert_eq!(handle as usize, 0x123400);
    TEST.with(|test| {
        let mut test = test.borrow_mut();
        test.calls[1] += 1;
        assert_eq!(options.fps, test.options.fps);
    });
    true
}
unsafe extern "C" fn destroy_stub(handle: *mut c_void) {
    assert_eq!(handle as usize, 0x123400);
    TEST.with(|test| test.borrow_mut().calls[2] += 1);
}
unsafe extern "C" fn vendor_stub(_: *mut c_void) -> u32 {
    1
}
unsafe extern "C" fn sequence_stub(
    output: *mut c_void,
    _: *const c_void,
    _: *const c_void,
) -> *mut c_void {
    let dimensions = TEST.with(|test| test.borrow().options.dimensions);
    // SAFETY: caller provides a complete writable synthetic sequence record.
    unsafe {
        write(
            output,
            0x2d4,
            [
                (dimensions.width - 1) as u16,
                (dimensions.height - 1) as u16,
            ],
        );
    }
    output
}
unsafe extern "C" fn tunings_stub(
    output: *mut c_void,
    _: *const c_void,
    _: *const c_void,
    _: *const c_void,
) -> *mut c_void {
    // SAFETY: caller provides complete writable synthetic Tunings storage.
    unsafe {
        ptr::write_bytes(output.cast::<u8>(), 0, 0x40);
        write(output, 0x10, 1u32);
    }
    output
}
unsafe extern "C" fn vendor_query(_: *mut c_void) -> *const libc::c_char {
    TEST.with(|test| test.borrow().vendor.as_ptr())
}
unsafe extern "C" fn info_stub(output: *mut c_void, _: *const c_void) -> *mut c_void {
    TEST.with(|test| {
        // SAFETY: exact complete local native metadata output extent.
        unsafe {
            ptr::copy_nonoverlapping(test.borrow().metadata.as_ptr(), output.cast(), 0x110);
        }
    });
    output
}
static CONFIG: [u8; 0xca] = [0; 0xca];
unsafe extern "C" fn config_stub(_: *const c_void) -> *const c_void {
    CONFIG.as_ptr().cast()
}
unsafe extern "C" fn configure_stub(_: *mut c_void, _: *const c_void) {
    TEST.with(|test| test.borrow_mut().calls[3] += 1);
}
unsafe extern "C" fn started(_: *const c_void) -> bool {
    true
}
unsafe extern "C" fn codec(_: *const c_void) -> u32 {
    TEST.with(|test| test.borrow().codec)
}
unsafe extern "C" fn rate_stub(_: *const c_void, _: i64) -> OptionalRate {
    OptionalRate {
        value: 7,
        present: 1,
    }
}
unsafe extern "C" fn budget_stub(_: *mut c_void, _: i64) {}
unsafe extern "C" fn scaler_stub(_: *mut c_void) {
    TEST.with(|test| test.borrow_mut().calls[4] += 1);
}
unsafe extern "C" fn limit_stub(output: *mut c_void, _: u64, _: *const c_void) -> *mut c_void {
    // SAFETY: caller owns one complete writable optional output record.
    unsafe {
        write(output, 16, 1u8);
    }
    output
}
unsafe extern "C" fn info_destroy(_: *mut c_void) {
    TEST.with(|test| test.borrow_mut().calls[5] += 1);
}
fn create_test(session: f::encoder_session, options: f::encoder_options) -> Option<*mut c_void> {
    create_kind(6, session, options)
}
fn create_kind(
    kind: u32,
    session: f::encoder_session,
    options: f::encoder_options,
) -> Option<*mut c_void> {
    TEST.with(|test| {
        let mut test = test.borrow_mut();
        test.session = session;
        test.options = options;
    });
    let mut handle = ptr::null_mut();
    // SAFETY: real wrapper receives its exact by-value ABI, complete output
    // slot, and sentinel pointers never dereferenced by the native test stubs.
    let success = unsafe {
        create(
            kind,
            ptr::dangling::<c_void>(),
            2usize as *mut c_void,
            3usize as *mut c_void,
            4usize as *mut c_void,
            &mut handle,
            session,
            options,
        )
    };
    if success {
        Some(handle)
    } else {
        assert!(handle.is_null());
        None
    }
}
#[test]
fn native_policies_preserve_abi_reentry_handle_retirement_and_scaler_ownership() {
    GUARD.fixture(vec![
        create_stub as *const () as usize,
        update_stub as *const () as usize,
        destroy_stub as *const () as usize,
        vendor_stub as *const () as usize,
    ]);
    LEVELS.fixture(vec![sequence_stub as *const () as usize]);
    QUALITY.fixture(vec![
        tunings_stub as *const () as usize,
        vendor_query as *const () as usize,
    ]);
    QP.fixture(vec![
        info_stub as *const () as usize,
        config_stub as *const () as usize,
    ]);
    TRANSITIONS.fixture(vec![
        configure_stub as *const () as usize,
        configure_stub as *const () as usize,
        started as *const () as usize,
        codec as *const () as usize,
    ]);
    ALLOCATION.fixture(vec![
        rate_stub as *const () as usize,
        budget_stub as *const () as usize,
        scaler_stub as *const () as usize,
        scaler_stub as *const () as usize,
    ]);
    STREAM.fixture(vec![
        limit_stub as *const () as usize,
        info_destroy as *const () as usize,
    ]);
    let dimensions = f::encoder_dimensions {
        width: 1280,
        height: 720,
    };
    let mut session = f::encoder_session {
        fps: 60,
        maximum: dimensions,
        codec: 2,
        ..Default::default()
    };
    let mut options = f::encoder_options {
        fps: 60,
        dimensions,
        bitrate_target: 6_000_000,
        bitrate_maximum: 6_000_000,
        keyframe_interval: 120,
    };
    assert!(create_test(session, options).is_none());
    assert_eq!(lock(&STATE).reason, Some("av1-level-unverified"));
    TEST.with(|test| test.borrow_mut().emit = true);
    let handle = create_test(session, options).unwrap();
    assert_eq!(lock(&STATE).guarded[&(handle as usize)].level, 8);
    options.fps = 240;
    TEST.with(|test| test.borrow_mut().options = options);
    // SAFETY: live test-native handle, updated exact by-value options.
    assert!(!unsafe { update(handle, options) });
    options.fps = 0;
    TEST.with(|test| test.borrow_mut().options = options);
    // SAFETY: same live handle; zero-rate suspension keeps the native update.
    assert!(unsafe { update(handle, options) });
    // SAFETY: retire this test-native handle exactly once before reuse.
    unsafe {
        destroy(handle);
    }
    assert!(lock(&STATE).guarded.is_empty());
    options.fps = 60;
    session.codec = 0;
    TEST.with(|test| {
        let mut test = test.borrow_mut();
        test.emit = false;
        test.quality = true;
    });
    let handle = create_test(session, options).unwrap();
    assert!(lock(&STATE).guarded[&(handle as usize)].vbaq);
    let encoder = [0usize, handle as usize];
    let mut info = [0u8; 0x110];
    // SAFETY: complete synthetic native encoder prefix and metadata output.
    unsafe {
        trusted_info(info.as_mut_ptr().cast(), encoder.as_ptr().cast());
    }
    assert_eq!(&info[0xf0..0xf2], &[0, 1]);
    assert_eq!(
        // SAFETY: complete metadata contract and ignored synthetic settings.
        unsafe { bandwidth_config(ptr::null(), info.as_ptr().cast()) },
        EXPLICIT_GATE.as_ptr().cast()
    );
    info[8] = 0;
    assert_eq!(
        // SAFETY: same complete records with explicit eligibility removed.
        unsafe { bandwidth_config(ptr::null(), info.as_ptr().cast()) },
        CONFIG.as_ptr().cast()
    );
    info[8] = 1;
    let mut resource = [0u8; 0x80];
    let mut manager = [0u8; 0xa00];
    let scaler = [0u8; 0x40];
    let manager_ptr = manager.as_mut_ptr().cast();
    let scaler_ptr = scaler.as_ptr().cast::<c_void>();
    // SAFETY: complete writable local manager/resource records; stubs own no
    // allocation and retain no pointers after the synchronous native callbacks.
    unsafe {
        write(resource.as_mut_ptr().cast(), 0x60, scaler_ptr);
        write(manager_ptr, 0x48, resource.as_ptr());
        write(manager_ptr, 0xec, 6_000_000u32);
        write(manager_ptr, 0xf0, 1u8);
        configure_bandwidth(manager_ptr, info.as_ptr().cast());
        assert_eq!(
            allocation_rate(scaler_ptr.byte_add(0x20), 123).value,
            6_000_000
        );
        assert_eq!(allocation_rate(scaler_ptr.byte_add(0x21), 123).value, 7);
        target_budget(manager_ptr, 0);
        assert_eq!(allocation_rate(scaler_ptr.byte_add(0x20), 123).present, 0);
        target_budget(manager_ptr, 12_000_000);
        assert_eq!(
            allocation_rate(scaler_ptr.byte_add(0x20), 123).value,
            12_000_000
        );
        scaler_destroy(scaler_ptr.cast_mut());
        assert_eq!(allocation_rate(scaler_ptr.byte_add(0x20), 123).value, 7);
        write(manager_ptr, 0x78, 1u32);
    }
    let mut unknown = [0u8; 0x110];
    unknown[0x18..0x1f].copy_from_slice(b"unknown");
    unknown[0x2f] = 7;
    let before = TEST.with(|test| test.borrow().calls[3]);
    // SAFETY: complete synthetic manager/metadata records remain borrowed.
    unsafe {
        configure_quality(manager_ptr, unknown.as_ptr().cast());
    }
    assert_eq!(TEST.with(|test| test.borrow().calls[3]), before);
    TEST.with(|test| test.borrow_mut().codec = 2);
    // SAFETY: same records with an unmanaged codec; native path must run.
    unsafe {
        configure_quality(manager_ptr, unknown.as_ptr().cast());
    }
    assert_eq!(TEST.with(|test| test.borrow().calls[3]), before + 1);
    TEST.with(|test| test.borrow_mut().metadata[0xf1] = 1);
    let mut vtable = [0usize; 13];
    vtable[12] = info_stub as *const () as usize;
    let encoder = [vtable.as_ptr() as usize, handle as usize];
    let mut owner = [0u8; 0x200];
    let mut output = [0u8; 24];
    // SAFETY: complete native test owner, vtable, encoder and output lifetimes
    // cover the real wrapper and its nested metadata query/destructor.
    unsafe {
        write(owner.as_mut_ptr().cast(), 0x118, 4u32);
        write(owner.as_mut_ptr().cast(), 0x1a0, 1u32);
        write(owner.as_mut_ptr().cast(), 0x1e8, encoder.as_ptr());
        stream_limit(
            output.as_mut_ptr().cast(),
            0,
            ptr::null(),
            owner.as_ptr().cast(),
        );
    }
    assert_eq!(output[16], 0);
    assert_eq!(TEST.with(|test| test.borrow().calls[5]), 1);
    // SAFETY: retire native handle and its policy before the same address returns.
    unsafe {
        destroy(handle);
        trusted_info(info.as_mut_ptr().cast(), encoder.as_ptr().cast());
    }
    assert!(lock(&STATE).guarded.is_empty());
    TEST.with(|test| {
        let mut test = test.borrow_mut();
        test.vendor = c"Intel iHD driver";
    });
    let handle = create_test(session, options).unwrap();
    assert!(lock(&STATE).guarded.is_empty());
    // SAFETY: final test-native handle retired once.
    unsafe {
        destroy(handle);
    }
    VCN4.with(|value| value.set(true));
    session.codec = 2;
    TEST.with(|test| {
        let mut test = test.borrow_mut();
        test.emit = true;
        test.quality = false;
    });
    let handle = create_test(session, options).unwrap();
    assert!(lock(&STATE).guarded[&(handle as usize)].dimension);
    options.dimensions.height = 722;
    TEST.with(|test| test.borrow_mut().options = options);
    assert!(
        // SAFETY: live fixture handle and an unaligned native resize request.
        !unsafe { update(handle, options) }
    );
    // SAFETY: retire before rejecting a fresh unaligned construction.
    unsafe {
        destroy(handle);
    }
    assert!(create_test(session, options).is_none());
    VCN4.with(|value| value.set(false));
    TEST.with(|test| {
        let mut test = test.borrow_mut();
        test.emit = false;
        test.quality = false;
    });
    for kind in [0, 10] {
        let handle = create_kind(kind, session, options).unwrap();
        assert!(lock(&STATE).guarded.is_empty());
        // SAFETY: non-VAAPI native handle must retire through its original ABI.
        unsafe {
            destroy(handle);
        }
    }
    session.codec = 0;
    options.dimensions.height = 720;
    TEST.with(|test| {
        let mut test = test.borrow_mut();
        test.vendor = c"Mesa Gallium driver 26 for AMD";
        test.quality = true;
    });
    {
        let mut state = lock(&STATE);
        for key in 0..MAXIMUM {
            state.guarded.insert(
                key,
                Guarded {
                    dimension: false,
                    vbaq: true,
                    level: 31,
                },
            );
        }
    }
    assert!(create_test(session, options).is_none());
    lock(&STATE).guarded.clear();
}
#[test]
fn construction_stack_masks_nonhardware_calls_and_restores_nested_observers() {
    let value = Construction {
        fps: 60,
        bitrate: 6_000_000,
        level: 31,
        observed: false,
        vbaq: false,
    };
    let outer = Constructing::begin(Some(value)).unwrap();
    {
        let masked = Constructing::begin(None).unwrap();
        constructing(|_| panic!("masked parent"));
        assert!(masked.observed().is_none());
    }
    {
        let inner = Constructing::begin(Some(value)).unwrap();
        constructing(|value| value.level = 9);
        assert_eq!(inner.observed().unwrap().level, 9);
    }
    assert_eq!(outer.observed().unwrap().level, 31);
    drop(outer);
    assert!(CONSTRUCTION.with(|state| state.borrow().is_empty()));
}

#[test]
#[ignore = "private sysfs tree supplied by the Python harness"]
fn hardware_scope_uses_every_amd_render_device() {
    let root = std::env::var_os("DPE_TEST_DRM_ROOT").expect("fixture root");
    assert_eq!(
        all_amd_vcn4(Path::new(&root)),
        std::env::var("DPE_TEST_EXPECT_VCN4").unwrap() == "1"
    );
}
