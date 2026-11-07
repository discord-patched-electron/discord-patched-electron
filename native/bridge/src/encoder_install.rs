// SPDX-License-Identifier: MIT
//! One serialized installer for encoder policy hooks. Originals and loader
//! owners publish before any redirect; exact installed bytes are revalidated.
use crate::{
    call_patch::{self, Branch, Module, Slot, Slots},
    encoder_guard as guard,
    node::{self, Env, Info, Value},
    software_crop,
};
use dpe_core::admission::lock;
use std::{
    ffi::CStr,
    ptr,
    sync::{
        Mutex, OnceLock,
        atomic::{AtomicBool, Ordering},
    },
};
type Result<T> = std::result::Result<T, &'static CStr>;
pub(super) struct Published {
    pub originals: Vec<usize>,
    pub scope: Vec<u32>,
    pub base: usize,
    pub module: Option<Module>,
}
pub(super) struct Group {
    pub published: OnceLock<Published>,
    installed: AtomicBool,
    checks: Mutex<Vec<(usize, Vec<u8>)>>,
}
impl Group {
    #[cfg(test)]
    pub(super) fn fixture(&self, originals: Vec<usize>) {
        self.publish(Published {
            originals,
            scope: Vec::new(),
            base: 0,
            module: None,
        });
        self.installed.store(true, Ordering::Release);
    }
    const fn new() -> Self {
        Self {
            published: OnceLock::new(),
            installed: AtomicBool::new(false),
            checks: Mutex::new(Vec::new()),
        }
    }
    pub fn original(&self, index: usize) -> usize {
        self.published
            .get()
            .expect("encoder targets published before redirect")
            .originals[index]
    }
    pub fn active(&self) -> bool {
        self.installed.load(Ordering::Acquire)
    }
    fn existing(&self, module: &Module, scope: &[u32]) -> Result<bool> {
        let Some(state) = self.published.get() else {
            return Ok(false);
        };
        if !self.active() || state.base != module.base || state.scope != scope {
            return Err(c"Encoder installation differs from the reviewed scope.");
        }
        for (address, bytes) in lock(&self.checks).iter() {
            if module.protection(*address, bytes.len()).is_none()
                || node::permissions_range(*address, bytes.len())
                    .is_none_or(|flags| flags & libc::PROT_READ == 0)
            {
                return Err(c"Encoder hook mapping changed.");
            }
            // SAFETY: the retained module and current mapping cover this
            // complete previously installed byte range; installation is serialized.
            if unsafe { std::slice::from_raw_parts(*address as *const u8, bytes.len()) } != bytes {
                return Err(c"Encoder hook bytes changed.");
            }
        }
        Ok(true)
    }
    fn publish(&self, state: Published) {
        assert!(
            self.published.set(state).is_ok(),
            "serialized encoder publication"
        );
    }
    fn finish(&self, regions: &[(usize, usize)]) -> Result<()> {
        let owner = self
            .published
            .get()
            .ok_or(c"Encoder targets were not published.")?;
        let module = owner
            .module
            .as_ref()
            .ok_or(c"Encoder loader owner missing.")?;
        let mut checks = lock(&self.checks);
        for &(address, length) in regions {
            if module.protection(address, length).is_none()
                || node::permissions_range(address, length)
                    .is_none_or(|flags| flags & libc::PROT_READ == 0)
            {
                return Err(c"Encoder hook mapping changed.");
            }
            // SAFETY: validated retained readable native extent, copied only
            // after all writes and exact protection restoration succeeded.
            checks.push((
                address,
                unsafe { std::slice::from_raw_parts(address as *const u8, length) }.to_vec(),
            ));
        }
        self.installed.store(true, Ordering::Release);
        Ok(())
    }
}
pub(super) static GUARD: Group = Group::new();
pub(super) static LEVELS: Group = Group::new();
pub(super) static QUALITY: Group = Group::new();
pub(super) static QP: Group = Group::new();
pub(super) static TRANSITIONS: Group = Group::new();
pub(super) static ALLOCATION: Group = Group::new();
pub(super) static STREAM: Group = Group::new();
static INSTALL: Mutex<()> = Mutex::new(());
fn scoped(module: &Module) -> Result<()> {
    if GUARD
        .published
        .get()
        .is_none_or(|state| state.base != module.base)
    {
        return Err(c"Encoder module differs from the reviewed guard.");
    }
    Ok(())
}
fn exact(module: &Module, address: usize, bytes: &[u8]) -> Result<()> {
    if module.bytes(address, bytes.len()) != Some(bytes) {
        return Err(c"Encoder native layout differs from review.");
    }
    Ok(())
}
fn function(module: &Module, offset: u32) -> Result<usize> {
    let address = module.address(offset)?;
    if !module.executable(address, 1) {
        return Err(c"Encoder target lies outside native text.");
    }
    Ok(address)
}
fn slot(
    module: &Module,
    address: usize,
    target: usize,
    lazy: Option<usize>,
    replacement: usize,
) -> Result<Slot> {
    let candidate = Slot {
        address,
        target,
        lazy,
        replacement,
    };
    Slots::prepare(module, std::slice::from_ref(&candidate))?;
    Ok(candidate)
}
fn branch(module: &Module, site: usize, target: usize, replacement: usize) -> Result<Branch> {
    let bytes = module
        .bytes(site, 5)
        .ok_or(c"Encoder call lies outside native text.")?;
    if bytes[0] != 0xe8
        || site.checked_add(5).and_then(|next| {
            next.checked_add_signed(
                i32::from_le_bytes(bytes[1..].try_into().expect("four bytes")) as isize,
            )
        }) != Some(target)
    {
        return Err(c"Encoder call differs from review.");
    }
    Ok(Branch {
        site,
        target,
        replacement,
        opcode: 0xe8,
    })
}
fn install_group(
    group: &Group,
    filename: &CStr,
    module: Module,
    scope: &[u32],
    originals: Vec<usize>,
    branches: &[Branch],
    slots: &[Slot],
) -> Result<()> {
    let regions: Vec<_> = branches
        .iter()
        .map(|entry| (entry.site, 5))
        .chain(
            slots
                .iter()
                .map(|entry| (entry.address, size_of::<usize>())),
        )
        .collect();
    let state = Published {
        base: module.base,
        module: Some(module),
        scope: scope.to_vec(),
        originals,
    };
    if branches.is_empty() {
        let transaction = Slots::prepare(state.module.as_ref().expect("owned module"), slots)?;
        group.publish(state);
        // SAFETY: caller holds startup installation lock; all exact typed
        // originals and module ownership are already published for callbacks.
        unsafe {
            transaction.apply()?;
        }
    } else {
        let patch_module = Module::loaded(filename)?;
        // SAFETY: complete native branches and slots are reviewed; startup is
        // serialized, and publication precedes the combined mutation transaction.
        unsafe {
            call_patch::redirect_group_slots(patch_module, branches, slots, || {
                group.publish(state)
            })?;
        }
    }
    group.finish(&regions)
}
fn install_guard(filename: &CStr, offsets: &[u32]) -> Result<()> {
    if offsets.len() != 10 {
        return Err(c"Invalid encoder guard scope.");
    }
    let _install = lock(&INSTALL);
    let module = Module::loaded(filename)?;
    if GUARD.existing(&module, offsets)? {
        return Ok(());
    }
    let mut targets = Vec::new();
    let mut slots = Vec::new();
    let names = [
        c"cc_encoder_create",
        c"cc_encoder_update",
        c"cc_encoder_destroy",
        c"cc_encoder_vendor",
    ];
    let replacements = [
        guard::create as *const () as usize,
        guard::update as *const () as usize,
        guard::destroy as *const () as usize,
    ];
    for index in 0..4 {
        let target = function(&module, offsets[if index == 3 { 9 } else { index * 3 + 1 }])?;
        if module.symbol(names[index]) != target {
            return Err(c"Encoder ABI symbol differs from review.");
        }
        targets.push(target);
        if index < 3 {
            let address = module.address(offsets[index * 3])?;
            let lazy = module.address(offsets[index * 3 + 2])?;
            let stub = module
                .bytes(
                    lazy.checked_sub(6)
                        .ok_or(c"Invalid encoder lazy resolver.")?,
                    16,
                )
                .ok_or(c"Invalid encoder lazy resolver.")?;
            let resolver = lazy
                .checked_add(10)
                .and_then(|next| {
                    next.checked_add_signed(i32::from_le_bytes(
                        stub[12..16].try_into().expect("four bytes"),
                    ) as isize)
                })
                .ok_or(c"Invalid encoder lazy resolver.")?;
            if stub[..2] != [0xff, 0x25]
                || stub[6] != 0x68
                || stub[11] != 0xe9
                || lazy.checked_add_signed(i32::from_le_bytes(
                    stub[2..6].try_into().expect("four bytes"),
                ) as isize)
                    != Some(address)
                || module.bytes(resolver, 2) != Some(&[0xff, 0x35])
                || module.bytes(resolver + 6, 2) != Some(&[0xff, 0x25])
            {
                return Err(c"Encoder lazy linkage differs from review.");
            }
            slots.push(slot(
                &module,
                address,
                target,
                Some(lazy),
                replacements[index],
            )?);
        }
    }
    install_group(&GUARD, filename, module, offsets, targets, &[], &slots)
}
fn install_levels(filename: &CStr, offsets: &[u32]) -> Result<()> {
    if offsets.len() != 3 {
        return Err(c"Invalid AV1 level scope.");
    }
    let _install = lock(&INSTALL);
    let module = Module::loaded(filename)?;
    scoped(&module)?;
    if LEVELS.existing(&module, offsets)? {
        return Ok(());
    }
    let target = function(&module, offsets[0])?;
    exact(&module, target, &[0x48, 0x81, 0xec, 0xec, 1, 0, 0])?;
    let sites = [module.address(offsets[1])?, module.address(offsets[2])?];
    if sites[1] <= sites[0] || sites[1] - sites[0] > 65536 {
        return Err(c"AV1 constructor calls differ from review.");
    }
    let branches = sites
        .into_iter()
        .map(|site| branch(&module, site, target, guard::sequence as *const () as usize))
        .collect::<Result<Vec<_>>>()?;
    install_group(
        &LEVELS,
        filename,
        module,
        offsets,
        vec![target],
        &branches,
        &[],
    )
}
fn install_quality(filename: &CStr, offsets: &[u32]) -> Result<()> {
    if offsets.len() != 3 {
        return Err(c"Invalid encoder quality scope.");
    }
    let _install = lock(&INSTALL);
    let module = Module::loaded(filename)?;
    scoped(&module)?;
    if QUALITY.existing(&module, offsets)? {
        return Ok(());
    }
    let target = function(&module, offsets[1])?;
    let query = function(&module, offsets[2])?;
    exact(
        &module,
        target,
        &[
            0x55, 0x41, 0x57, 0x41, 0x56, 0x41, 0x55, 0x41, 0x54, 0x53, 0x50, 0x49, 0x89, 0xf6,
        ],
    )?;
    if module.symbol(c"vaQueryVendorString") != query {
        return Err(c"VAAPI vendor query differs from review.");
    }
    let slot = slot(
        &module,
        module.address(offsets[0])?,
        target,
        None,
        guard::tunings as *const () as usize,
    )?;
    install_group(
        &QUALITY,
        filename,
        module,
        offsets,
        vec![target, query],
        &[],
        &[slot],
    )
}
fn install_transitions(filename: &CStr, offsets: &[u32]) -> Result<()> {
    if offsets.len() != 10 {
        return Err(c"Invalid encoder scaler scope.");
    }
    let _install = lock(&INSTALL);
    let module = Module::loaded(filename)?;
    scoped(&module)?;
    if TRANSITIONS.existing(&module, offsets)? {
        return Ok(());
    }
    let targets = vec![
        function(&module, offsets[0])?,
        function(&module, offsets[4])?,
        function(&module, offsets[8])?,
        function(&module, offsets[9])?,
    ];
    let mut branches = Vec::new();
    for index in 0..6 {
        let target = targets[index / 3];
        let site = module.address(offsets[if index < 3 { index + 1 } else { index + 2 }])?;
        branches.push(branch(
            &module,
            site,
            target,
            if index < 3 {
                guard::configure_quality as *const () as usize
            } else {
                guard::configure_bandwidth as *const () as usize
            },
        )?);
    }
    install_group(
        &TRANSITIONS,
        filename,
        module,
        offsets,
        targets,
        &branches,
        &[],
    )
}
fn install_allocation(filename: &CStr, offsets: &[u32]) -> Result<()> {
    if offsets.len() != 8 {
        return Err(c"Invalid encoder allocation scope.");
    }
    let _install = lock(&INSTALL);
    let module = Module::loaded(filename)?;
    scoped(&module)?;
    if ALLOCATION.existing(&module, offsets)? {
        return Ok(());
    }
    let targets = (0..4)
        .map(|index| function(&module, offsets[index * 2 + 1]))
        .collect::<Result<Vec<_>>>()?;
    let replacements = [
        guard::allocation_rate as *const () as usize,
        guard::target_budget as *const () as usize,
        guard::scaler_destroy as *const () as usize,
        guard::scaler_delete as *const () as usize,
    ];
    let mut branches = Vec::new();
    let mut slots = Vec::new();
    for index in 0..4 {
        let site = module.address(offsets[index * 2])?;
        if index < 2 {
            branches.push(branch(&module, site, targets[index], replacements[index])?);
        } else {
            if module.protection(site, 8) != Some(libc::PROT_READ) {
                return Err(c"Encoder scaler destructor slot differs from review.");
            }
            slots.push(slot(
                &module,
                site,
                targets[index],
                None,
                replacements[index],
            )?);
        }
    }
    install_group(
        &ALLOCATION,
        filename,
        module,
        offsets,
        targets,
        &branches,
        &slots,
    )
}
fn install_qp(filename: &CStr, offsets: &[u32]) -> Result<()> {
    if offsets.len() != 4 {
        return Err(c"Invalid encoder QP scope.");
    }
    let _install = lock(&INSTALL);
    let module = Module::loaded(filename)?;
    scoped(&module)?;
    if QP.existing(&module, offsets)? {
        return Ok(());
    }
    let address = module.address(offsets[0])?;
    let target = function(&module, offsets[1])?;
    let site = module.address(offsets[2])?;
    let config = function(&module, offsets[3])?;
    if module.protection(address, 8) != Some(libc::PROT_READ) {
        return Err(c"Encoder QP slot differs from review.");
    }
    exact(
        &module,
        target,
        &[0x41, 0x56, 0x53, 0x50, 0x49, 0x89, 0xf6, 0x48, 0x89, 0xfb],
    )?;
    exact(
        &module,
        site.checked_add(5).ok_or(c"Encoder gate overflow.")?,
        &[0x80, 0xb8, 0xc9, 0, 0, 0, 1, 0x75, 0x15],
    )?;
    let slot = slot(
        &module,
        address,
        target,
        None,
        guard::trusted_info as *const () as usize,
    )?;
    let branch = branch(
        &module,
        site,
        config,
        guard::bandwidth_config as *const () as usize,
    )?;
    install_group(
        &QP,
        filename,
        module,
        offsets,
        vec![target, config],
        &[branch],
        &[slot],
    )
}
fn install_stream(filename: &CStr, offsets: &[u32]) -> Result<()> {
    if offsets.len() != 3 {
        return Err(c"Invalid encoder stream envelope scope.");
    }
    let _install = lock(&INSTALL);
    let module = Module::loaded(filename)?;
    scoped(&module)?;
    if STREAM.existing(&module, offsets)? {
        return Ok(());
    }
    let site = module.address(offsets[0])?;
    let target = function(&module, offsets[1])?;
    let destructor = function(&module, offsets[2])?;
    let context = &[
        0x48, 0x8d, 0xbd, 0x70, 0xea, 0xff, 0xff, 0x48, 0x8d, 0x95, 0xf0, 0xe7, 0xff, 0xff,
    ];
    exact(
        &module,
        site.checked_sub(context.len())
            .ok_or(c"Encoder context underflow.")?,
        context,
    )?;
    branch(
        &module,
        site,
        target,
        guard::stream_limit as *const () as usize,
    )?;
    let patch_module = Module::loaded(filename)?;
    let state = Published {
        base: module.base,
        module: Some(module),
        scope: offsets.to_vec(),
        originals: vec![target, destructor],
    };
    // SAFETY: exact reviewed R12 owner context and native call ABI; startup is
    // serialized, typed target and loader publish before the call is redirected.
    unsafe {
        call_patch::redirect(
            patch_module,
            site,
            target,
            context,
            &[0x4c, 0x89, 0xe1, 0xff, 0x25, 0, 0, 0, 0],
            guard::stream_limit as *const () as usize,
            || STREAM.publish(state),
        )?;
    }
    STREAM.finish(&[(site, 5)])
}
fn undefined(env: Env) -> Value {
    let mut result = ptr::null_mut();
    if let Some(api) = node::api() {
        // SAFETY: caller is a live native Node callback.
        unsafe {
            (api.undefined)(env, &mut result);
        }
    }
    result
}
type Installer = fn(&CStr, &[u32]) -> Result<()>;
unsafe fn invoke<const N: usize>(env: Env, info: Info, install: Installer) -> Value {
    // SAFETY: callback forwards its live Node invocation to bounded conversion.
    match unsafe { node::encoder_arguments::<N>(env, info) }
        .and_then(|(name, offsets)| install(&name, &offsets))
    {
        Ok(()) => undefined(env),
        Err(error) => node::failure(env, error),
    }
}
macro_rules! callback {
    ($name:ident, $length:expr, $install:ident) => {
        unsafe extern "C" fn $name(env: Env, info: Info) -> Value {
            // SAFETY: Node passes live callback arguments; no JS values persist.
            unsafe { invoke::<$length>(env, info, $install) }
        }
    };
}
callback!(guard_js, 10, install_guard);
callback!(levels_js, 3, install_levels);
callback!(quality_js, 3, install_quality);
callback!(transitions_js, 10, install_transitions);
callback!(allocation_js, 8, install_allocation);
callback!(qp_js, 4, install_qp);
callback!(stream_js, 3, install_stream);
fn software(filename: &CStr, offsets: &[u32]) -> Result<()> {
    if offsets.len() != 10 || offsets.contains(&0) {
        return Err(c"Invalid software AV1 scope.");
    }
    // SAFETY: complete bounded offsets and terminated filename are borrowed
    // synchronously; adapter independently validates its loaded module scope.
    if unsafe { software_crop::dpe_software_av1_install(filename.as_ptr(), offsets.as_ptr()) } != 0
    {
        Ok(())
    } else {
        Err(c"Could not install the reviewed software AV1 crop adapter.")
    }
}
callback!(software_js, 10, software);
fn hevc(filename: &CStr, offsets: &[u32]) -> Result<()> {
    if offsets.len() != 19 {
        return Err(c"Invalid private encoder scope.");
    }
    let image = crate::embedded::ENCODER_IMAGE;
    // SAFETY: complete native scope and optional bundled immutable image extent.
    if unsafe {
        crate::encoder_adapter::dpe_hevc_install(
            filename.as_ptr(),
            offsets.as_ptr(),
            image.as_ptr(),
            image.len(),
        )
    } {
        Ok(())
    } else {
        Err(c"Could not install the reviewed private encoder backend.")
    }
}
callback!(hevc_js, 19, hevc);
unsafe extern "C" fn software_stats(env: Env, _: Info) -> Value {
    let mut values = [0; 6];
    // SAFETY: six initialized writable counter outputs, no retained pointer.
    unsafe {
        software_crop::dpe_software_av1_stats(values.as_mut_ptr());
    }
    node::string_value(
        env,
        &format!(
            "{{\"installed\":{},\"contexts\":{},\"submitted\":{},\"cropped\":{},\"rejected\":{},\"unmatched\":{}}}",
            values[0] != 0,
            values[1],
            values[2],
            values[3],
            values[4],
            values[5]
        ),
    )
}
unsafe extern "C" fn stats(env: Env, _: Info) -> Value {
    let mut hevc = [0; 6];
    let mut av1 = [0; 6];
    // SAFETY: complete writable bounded outputs for synchronized backend counters.
    unsafe {
        crate::encoder_adapter::dpe_hevc_stats(hevc.as_mut_ptr());
        crate::encoder_adapter::dpe_av1_stats(av1.as_mut_ptr());
    }
    let state = lock(&guard::STATE);
    let c = state.counters;
    let reason = state
        .reason
        .map_or("null".to_owned(), |reason| format!("\"{reason}\""));
    let json = format!(
        concat!(
            "{{\"installed\":{},\"hardwareScope\":\"{}\",\"reason\":{},\"rejectedCreates\":{},\"rejectedUpdates\":{},\"width\":{},\"height\":{},",
            "\"av1LevelsInstalled\":{},\"correctedSequences\":{},\"levelIndex\":{},\"levelFps\":{},\"levelBitrate\":{},\"amdQualityInstalled\":{},\"amdQualityUpdates\":{},",
            "\"qpTrustInstalled\":{},\"qpTrustUpdates\":{},\"bandwidthContractUpdates\":{},\"scalerTransitionsInstalled\":{},\"retainedScalerTransitions\":{},\"allocationInstalled\":{},\"allocationQueries\":{},\"streamEnvelopeInstalled\":{},\"streamEnvelopesPreserved\":{},",
            "\"hevc\":{{\"installed\":{},\"probes\":{},\"gpuFrames\":{},\"cpuFrames\":{},\"importFallbacks\":{},\"failedFrames\":{}}},",
            "\"av1Alignment\":{{\"hardwareScope\":\"{}\",\"gpuFrames\":{},\"cpuFrames\":{},\"importFallbacks\":{},\"failedFrames\":{}}}}}"
        ),
        GUARD.active(),
        if state.hardware {
            "amd-vcn4"
        } else {
            "unverified"
        },
        reason,
        c[guard::REJECT_CREATE],
        c[guard::REJECT_UPDATE],
        state.rejected.0,
        state.rejected.1,
        LEVELS.active(),
        c[guard::SEQUENCES],
        state.sequence.2,
        state.sequence.0,
        state.sequence.1,
        QUALITY.active(),
        c[guard::QUALITIES],
        QP.active(),
        c[guard::QP_TRUST],
        c[guard::BANDWIDTH],
        TRANSITIONS.active(),
        c[guard::TRANSITION],
        ALLOCATION.active(),
        c[guard::QUERIES],
        STREAM.active(),
        c[guard::ENVELOPES],
        hevc[0] != 0,
        hevc[1],
        hevc[2],
        hevc[3],
        hevc[4],
        hevc[5],
        if av1[1] != 0 {
            "amd-vcn4"
        } else {
            "unverified"
        },
        av1[2],
        av1[3],
        av1[4],
        av1[5]
    );
    drop(state);
    node::string_value(env, &json)
}
pub(crate) unsafe extern "C" fn register(env: Env, exports: Value) -> Value {
    // SAFETY: module initializer borrows live Node exports; functions are static.
    unsafe {
        node::register_functions(
            env,
            exports,
            &[
                (c"installEncoderGuard", guard_js),
                (c"installAv1Levels", levels_js),
                (c"installEncoderQuality", quality_js),
                (c"installScalerTransitions", transitions_js),
                (c"installEncoderAllocation", allocation_js),
                (c"installEncoderQpTrust", qp_js),
                (c"installStreamBitrateEnvelope", stream_js),
                (c"installSoftwareAv1Crop", software_js),
                (c"installHevcEncoder", hevc_js),
                (c"softwareAv1CropStats", software_stats),
                (c"encoderGuardStats", stats),
            ],
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::ffi::{CString, c_void};
    #[test]
    #[ignore = "private compiled ELF fixture supplied by the Python harness"]
    fn lazy_installer_checks_actual_slots_and_idempotence() {
        let filename =
            CString::new(std::env::var("DPE_TEST_ENCODER_MODULE").expect("fixture path")).unwrap();
        let offsets: Vec<u32> = std::env::var("DPE_TEST_ENCODER_SCOPE")
            .expect("fixture scope")
            .split(',')
            .map(|value| value.parse().unwrap())
            .collect();
        // SAFETY: exact compiled fixture is loaded only into this test process;
        // the handle outlives every read and native linkage assertion.
        let library = node::Library(unsafe {
            libc::dlopen(filename.as_ptr(), libc::RTLD_LAZY | libc::RTLD_LOCAL)
        });
        assert!(!library.0.is_null());
        let module = Module::loaded(&filename).unwrap();
        let address = module.address(offsets[0]).unwrap();
        // SAFETY: fixture scope identifies a complete readable native GOT word.
        let before = unsafe { ptr::read_unaligned(address as *const usize) };
        assert_eq!(before, module.address(offsets[2]).unwrap());
        install_guard(&filename, &offsets).unwrap();
        assert_eq!(
            // SAFETY: installed readable word remains retained by its module.
            unsafe { ptr::read_unaligned(address as *const usize) },
            guard::create as *const () as usize
        );
        install_guard(&filename, &offsets).unwrap();
        let mut changed = offsets.clone();
        changed[1] += 1;
        assert!(install_guard(&filename, &changed).is_err());
        let flags = node::permissions(address).unwrap();
        // SAFETY: query the system page size without native object access.
        let page_size = unsafe { libc::sysconf(libc::_SC_PAGESIZE) } as usize;
        let page = address & !(page_size - 1);
        // SAFETY: this process owns its fixture and installation is idle. Restore
        // the exact original word/protection before checking tamper rejection.
        unsafe {
            assert_eq!(
                libc::mprotect(page as *mut c_void, page_size, flags | libc::PROT_WRITE),
                0
            );
            ptr::write_unaligned(address as *mut usize, before);
            assert_eq!(libc::mprotect(page as *mut c_void, page_size, flags), 0);
        }
        assert!(install_guard(&filename, &offsets).is_err());
    }
}
