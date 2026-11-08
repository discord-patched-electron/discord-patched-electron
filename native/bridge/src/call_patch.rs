// SPDX-License-Identifier: MIT
//! Startup-only redirects for reviewed x86-64 calls. A loaded-library guard and
//! executable segment bounds precede every native byte read. Uncommitted code
//! mappings are owned; committed code and its library stay alive with the hook.
use crate::{ffi, node};
use std::{
    ffi::{CStr, c_void},
    ptr,
};

pub(crate) struct Module {
    library: node::Library,
    pub(crate) base: usize,
    segments: Vec<(usize, usize, u32)>,
    relro: Vec<(usize, usize)>,
}
impl Module {
    pub(crate) fn containing(address: usize) -> Result<Self, &'static CStr> {
        // SAFETY: dladdr accepts arbitrary addresses and writes this local info.
        let mut info = unsafe { std::mem::zeroed::<libc::Dl_info>() };
        // SAFETY: the system loader checks membership without dereferencing it.
        if unsafe { libc::dladdr(address as *const c_void, &mut info) } == 0
            || info.dli_fname.is_null()
        {
            return Err(c"Native address is not in a loaded module.");
        }
        // SAFETY: successful dladdr supplies a loader-owned terminated filename.
        Self::loaded(unsafe { CStr::from_ptr(info.dli_fname) })
    }
    /// Accept only this exact canonical lazy PLT entry; unrelated preexisting
    /// hooks never become an accepted original through the lazy exception.
    pub(crate) fn lazy_slot(&self, slot: usize) -> Option<usize> {
        let flags = self.protection(slot, size_of::<usize>())?;
        if flags & libc::PROT_READ == 0 || flags & libc::PROT_EXEC != 0 {
            return None;
        }
        // SAFETY: the retained readable native segment covers this whole word.
        let current = unsafe { ptr::read_unaligned(slot as *const usize) };
        let stub = self.bytes(current.checked_sub(6)?, 16)?;
        let delta = i32::from_le_bytes(stub[2..6].try_into().expect("four bytes"));
        (stub[0..2] == [0xff, 0x25]
            && stub[6] == 0x68
            && stub[11] == 0xe9
            && current.checked_add_signed(delta as isize) == Some(slot))
        .then_some(current)
    }
    pub(crate) fn symbol(&self, name: &CStr) -> usize {
        // SAFETY: this guard retains the library and name is a valid C string.
        unsafe { libc::dlsym(self.library.0, name.as_ptr()) as usize }
    }
    pub(crate) fn named_function(&self, target: usize, fragment: &CStr) -> bool {
        if !self.executable(target, 1) {
            return false;
        }
        // SAFETY: dladdr accepts an address and writes one complete info record.
        let mut symbol = unsafe { std::mem::zeroed::<libc::Dl_info>() };
        // SAFETY: target is in this retained module; symbol is writable.
        if unsafe { libc::dladdr(target as *const c_void, &raw mut symbol) } == 0
            || symbol.dli_sname.is_null()
            || symbol.dli_saddr as usize != target
            || symbol.dli_fbase as usize != self.base
        {
            return false;
        }
        // SAFETY: successful dladdr supplies a loader-owned terminated symbol.
        let name = unsafe { CStr::from_ptr(symbol.dli_sname) }.to_bytes();
        name.windows(fragment.to_bytes().len())
            .any(|part| part == fragment.to_bytes())
    }
    pub(crate) fn loaded(filename: &CStr) -> Result<Self, &'static CStr> {
        // SAFETY: a valid C string names an already loaded library only.
        let raw = unsafe { libc::dlopen(filename.as_ptr(), libc::RTLD_NOW | libc::RTLD_NOLOAD) };
        if raw.is_null() {
            return Err(c"The native capture module is not loaded.");
        }
        let library = node::Library(raw);
        let mut map: *mut ffi::link_map = ptr::null_mut();
        // SAFETY: the loader writes its own link_map for the retained library.
        if unsafe { libc::dlinfo(raw, libc::RTLD_DI_LINKMAP, (&raw mut map).cast()) } != 0
            || map.is_null()
        {
            return Err(c"The native capture module map is unavailable.");
        }
        // SAFETY: successful dlinfo returns a live public link_map.
        let base = unsafe { (*map).l_addr } as usize;
        let mut result = Self {
            library,
            base,
            segments: Vec::new(),
            relro: Vec::new(),
        };
        // SAFETY: dl_iterate_phdr supplies valid headers, and borrows result only
        // during this synchronous call. The callback retains no loader pointers.
        unsafe {
            libc::dl_iterate_phdr(Some(segments), (&raw mut result).cast());
        }
        if result.segments.is_empty() {
            return Err(c"Missing native capture segments.");
        }
        Ok(result)
    }
    pub(crate) fn address(&self, offset: u32) -> Result<usize, &'static CStr> {
        self.base
            .checked_add(offset as usize)
            .ok_or(c"Native capture address overflow.")
    }
    pub(crate) fn executable(&self, address: usize, length: usize) -> bool {
        self.protection(address, length) == Some(libc::PROT_READ | libc::PROT_EXEC)
    }
    fn executable_pages(&self, address: usize, length: usize, page: usize) -> bool {
        let Some(end) = address.checked_add(length) else {
            return false;
        };
        // ELF text ends may lie within their final mapped page. Validate the
        // actual RX mapping as well as membership in this module's rounded
        // text segment, rather than rejecting harmless loader-owned padding.
        self.segments.iter().any(|&(start, last, flags)| {
            flags == (libc::PF_R | libc::PF_X)
                && (start & !(page - 1)) <= address
                && last
                    .checked_add(page - 1)
                    .is_some_and(|last| end <= (last & !(page - 1)))
        }) && node::permissions_range(address, length) == Some(libc::PROT_READ | libc::PROT_EXEC)
    }
    pub(crate) fn protection(&self, address: usize, length: usize) -> Option<i32> {
        let end = address.checked_add(length)?;
        let &(_, _, flags) = self
            .segments
            .iter()
            .find(|&&(start, last, _)| start <= address && end <= last)?;
        let mut protection = 0;
        for (flag, bit) in [
            (libc::PF_R, libc::PROT_READ),
            (libc::PF_W, libc::PROT_WRITE),
            (libc::PF_X, libc::PROT_EXEC),
        ] {
            if flags & flag != 0 {
                protection |= bit;
            }
        }
        if self
            .relro
            .iter()
            .any(|&(start, last)| start <= address && end <= last)
        {
            protection &= !libc::PROT_WRITE;
        }
        Some(protection)
    }
    pub(crate) fn bytes(&self, address: usize, length: usize) -> Option<&[u8]> {
        if !self.executable(address, length) {
            return None;
        }
        // SAFETY: the loader's retained RX segment covers the complete range.
        Some(unsafe { std::slice::from_raw_parts(address as *const u8, length) })
    }
}

pub(crate) struct Slot {
    pub(crate) address: usize,
    pub(crate) target: usize,
    pub(crate) lazy: Option<usize>,
    pub(crate) replacement: usize,
}
/// Startup-only GOT transaction. No native byte is read until its retained
/// module bounds and current nonexecutable page permissions have been checked.
pub(crate) struct Slots {
    values: Vec<(usize, usize, usize)>,
    pages: Vec<(usize, i32)>,
    page_size: usize,
}
impl Slots {
    pub(crate) fn prepare(module: &Module, slots: &[Slot]) -> Result<Self, &'static CStr> {
        use std::sync::atomic::{AtomicUsize, Ordering};
        if slots.is_empty() || slots.len() > 16 {
            return Err(c"Invalid native slot count.");
        }
        let page_size = page_size()?;
        let mut result = Self {
            values: Vec::new(),
            pages: Vec::new(),
            page_size,
        };
        for slot in slots {
            let protection = module
                .protection(slot.address, size_of::<usize>())
                .ok_or(c"Native slot is outside the module.")?;
            if !slot.address.is_multiple_of(align_of::<AtomicUsize>())
                || protection & libc::PROT_READ == 0
                || protection & libc::PROT_EXEC != 0
                || !module.executable(slot.target, 1)
                || result
                    .values
                    .iter()
                    .any(|&(address, _, _)| address == slot.address)
            {
                return Err(c"Invalid native slot.");
            }
            let page = slot.address & !(page_size - 1);
            let actual = node::permissions(slot.address).ok_or(c"Native slot is not mapped.")?;
            if actual & libc::PROT_READ == 0 || actual & libc::PROT_EXEC != 0 {
                return Err(c"Invalid native slot permissions.");
            }
            // SAFETY: the retained readable segment covers this aligned word.
            // Installation is serialized before native encoder/capture startup.
            let before = unsafe { AtomicUsize::from_ptr(slot.address as *mut usize) }
                .load(Ordering::Acquire);
            if before != slot.target && Some(before) != slot.lazy {
                return Err(c"Native slot differs from review.");
            }
            if let Some(&(_, existing)) = result.pages.iter().find(|&&(address, _)| address == page)
            {
                if existing != actual {
                    return Err(c"Inconsistent native page permissions.");
                }
            } else {
                result.pages.push((page, actual));
            }
            result.values.push((slot.address, before, slot.replacement));
        }
        Ok(result)
    }
    fn protect(&self, page: usize, flags: i32) -> bool {
        // SAFETY: every page belongs to a validated, retained native slot.
        unsafe { libc::mprotect(page as *mut c_void, self.page_size, flags) == 0 }
    }
    fn rollback(&self) {
        use std::sync::atomic::{AtomicUsize, Ordering};
        for &(page, protection) in &self.pages {
            if !self.protect(page, protection | libc::PROT_WRITE) {
                continue;
            }
            for &(address, before, _) in &self.values {
                if address & !(self.page_size - 1) != page {
                    continue;
                }
                // SAFETY: this transaction validated the aligned native word
                // and has just restored write permission for its exact page.
                unsafe { AtomicUsize::from_ptr(address as *mut usize) }
                    .store(before, Ordering::Release);
            }
            self.protect(page, protection);
        }
    }
    /// Caller must retain the module and publish all callback targets first.
    /// Even a failed restoration keeps those owners alive: partially installed
    /// native callbacks must never jump into an unloaded library.
    pub(crate) unsafe fn apply(&self) -> Result<(), &'static CStr> {
        use std::sync::atomic::{AtomicUsize, Ordering};
        for (index, &(page, protection)) in self.pages.iter().enumerate() {
            if !self.protect(page, protection | libc::PROT_WRITE) {
                for &(before, flags) in &self.pages[..index] {
                    self.protect(before, flags);
                }
                return Err(c"Cannot make native slots writable.");
            }
        }
        for &(address, _, replacement) in &self.values {
            // SAFETY: preparation validated this aligned word; its page is writable.
            unsafe { AtomicUsize::from_ptr(address as *mut usize) }
                .store(replacement, Ordering::Release);
        }
        let mut restored = true;
        for &(page, flags) in &self.pages {
            restored &= self.protect(page, flags);
        }
        if restored {
            return Ok(());
        }
        for &(page, flags) in &self.pages {
            if self.protect(page, flags | libc::PROT_WRITE) {
                for &(address, before, _) in &self.values {
                    if address & !(self.page_size - 1) == page {
                        // SAFETY: this rollback page was just made writable.
                        unsafe { AtomicUsize::from_ptr(address as *mut usize) }
                            .store(before, Ordering::Release);
                    }
                }
                self.protect(page, flags);
            }
        }
        Err(c"Cannot restore native slot permissions.")
    }
}
unsafe extern "C" fn segments(info: *mut libc::dl_phdr_info, _: usize, data: *mut c_void) -> i32 {
    // SAFETY: dl_iterate_phdr provides info; data is its exclusive Module borrow.
    let (info, module) = unsafe { (&*info, &mut *data.cast::<Module>()) };
    if info.dlpi_addr as usize != module.base {
        return 0;
    }
    if info.dlpi_phnum > 256 {
        return 1;
    }
    // SAFETY: the loader provides exactly dlpi_phnum live program headers.
    for header in unsafe { std::slice::from_raw_parts(info.dlpi_phdr, info.dlpi_phnum as usize) } {
        if header.p_type == libc::PT_LOAD
            && let Some(start) = module.base.checked_add(header.p_vaddr as usize)
            && let Some(end) = start.checked_add(header.p_memsz as usize)
        {
            module.segments.push((start, end, header.p_flags));
        }
        if header.p_type == libc::PT_GNU_RELRO
            && let Some(start) = module.base.checked_add(header.p_vaddr as usize)
            && let Some(end) = start.checked_add(header.p_memsz as usize)
        {
            module.relro.push((start, end));
        }
    }
    1
}
struct Code {
    address: usize,
    length: usize,
}
impl Drop for Code {
    fn drop(&mut self) {
        // SAFETY: this guard exclusively owns this uncommitted mmap allocation.
        unsafe {
            libc::munmap(self.address as *mut c_void, self.length);
        }
    }
}
fn page_size() -> Result<usize, &'static CStr> {
    // SAFETY: scalar sysconf query, with no pointers or retained resources.
    let size = unsafe { libc::sysconf(libc::_SC_PAGESIZE) };
    if size <= 0 || !(size as usize).is_power_of_two() {
        return Err(c"Invalid page size.");
    }
    Ok(size as usize)
}
impl Code {
    fn near(site: usize) -> Result<Self, &'static CStr> {
        let length = page_size()?;
        let center = site & !(length - 1);
        for distance in (0x10000..0x70000000usize).step_by(0x10000) {
            for address in [center.checked_sub(distance), center.checked_add(distance)]
                .into_iter()
                .flatten()
            {
                // SAFETY: MAP_FIXED_NOREPLACE never overwrites existing mappings;
                // this reserves a private, writable, initially nonexecutable page.
                let raw = unsafe {
                    libc::mmap(
                        address as *mut c_void,
                        length,
                        libc::PROT_READ | libc::PROT_WRITE,
                        libc::MAP_PRIVATE | libc::MAP_ANONYMOUS | libc::MAP_FIXED_NOREPLACE,
                        -1,
                        0,
                    )
                };
                if raw as usize == address {
                    return Ok(Self { address, length });
                }
                if raw != libc::MAP_FAILED {
                    // SAFETY: an older kernel may ignore NOREPLACE; discard only
                    // the distinct mapping it just returned, never the requested address.
                    unsafe {
                        libc::munmap(raw, length);
                    }
                }
            }
        }
        Err(c"Could not reserve native capture code.")
    }
}
pub(crate) struct Installed {
    pub(crate) site: usize,
    pub(crate) target: usize,
}

pub(crate) struct Branch {
    pub(crate) site: usize,
    pub(crate) target: usize,
    pub(crate) opcode: u8,
    pub(crate) replacement: usize,
}
/// Install a bounded group of plain call/tail-jump redirects atomically with
/// respect to startup. All sites are validated before any text is writable.
/// Caller serializes startup and publishes original targets before redirection.
pub(crate) unsafe fn redirect_group(
    module: Module,
    branches: &[Branch],
    publish: impl FnOnce(),
) -> Result<(), &'static CStr> {
    // SAFETY: identical startup serialization and target publication contract.
    unsafe { redirect_group_slots(module, branches, &[], publish) }
}
/// Same serialized startup contract as redirect_group. Validate both native
/// calls and nonexecutable slots before changing either; retain published
/// owners even when a protection failure prevents a complete rollback.
pub(crate) unsafe fn redirect_group_slots(
    module: Module,
    branches: &[Branch],
    slots: &[Slot],
    publish: impl FnOnce(),
) -> Result<(), &'static CStr> {
    if branches.is_empty() || branches.len() > 16 {
        return Err(c"Invalid capture branch count.");
    }
    let slots = if slots.is_empty() {
        None
    } else {
        Some(Slots::prepare(&module, slots)?)
    };
    let mut before = Vec::<Vec<u8>>::with_capacity(branches.len());
    let mut low = usize::MAX;
    for branch in branches {
        if !matches!(branch.opcode, 0xe8 | 0xe9 | 0xff) || !module.executable(branch.target, 1) {
            return Err(c"Invalid native capture target.");
        }
        let size = if branch.opcode == 0xff { 6 } else { 5 };
        let bytes = module
            .bytes(branch.site, size)
            .ok_or(c"Invalid native capture branch.")?
            .to_vec();
        let next = branch
            .site
            .checked_add(size)
            .ok_or(c"Capture branch overflow.")?;
        let destination = if branch.opcode == 0xff {
            let slot = next
                .checked_add_signed(
                    i32::from_le_bytes(bytes[2..].try_into().expect("four bytes")) as isize,
                )
                .ok_or(c"Capture slot overflow.")?;
            let protection = module
                .protection(slot, size_of::<usize>())
                .ok_or(c"Invalid capture slot.")?;
            if bytes[1] != 0x15 || protection & libc::PROT_READ == 0 {
                return Err(c"Invalid indirect capture call.");
            }
            // SAFETY: the retained readable native segment covers the complete
            // word; startup serialization precedes voice-engine execution.
            Some(unsafe { ptr::read_unaligned(slot as *const usize) })
        } else {
            next.checked_add_signed(
                i32::from_le_bytes(bytes[1..].try_into().expect("four bytes")) as isize,
            )
        };
        if bytes[0] != branch.opcode || destination != Some(branch.target) {
            return Err(c"Native capture branch differs from review.");
        }
        if branches
            .iter()
            .filter(|other| {
                let other_size = if other.opcode == 0xff { 6 } else { 5 };
                other.site < next && branch.site < other.site.saturating_add(other_size)
            })
            .count()
            != 1
        {
            return Err(c"Overlapping capture branches.");
        }
        low = low.min(branch.site);
        before.push(bytes);
    }
    let code = Code::near(low)?;
    let mut pages = std::collections::BTreeSet::new();
    for branch in branches {
        let size = if branch.opcode == 0xff { 6 } else { 5 };
        pages.insert(branch.site & !(code.length - 1));
        pages.insert((branch.site + size - 1) & !(code.length - 1));
    }
    let pages: Vec<_> = pages.into_iter().collect();
    if pages
        .iter()
        .any(|page| !module.executable_pages(*page, code.length, code.length))
    {
        return Err(c"Capture branch pages are outside executable segments.");
    }
    let protect = |flags| {
        let mut complete = true;
        for page in &pages {
            // SAFETY: every exact page belongs to retained reviewed native text;
            // disjoint call sites never broaden permission changes over gaps.
            complete &= unsafe { libc::mprotect(*page as *mut c_void, code.length, flags) } == 0;
        }
        complete
    };
    let mut deltas = Vec::with_capacity(branches.len());
    for (index, branch) in branches.iter().enumerate() {
        let address = code.address + index * 16;
        deltas.push(
            i32::try_from(address as i128 - (branch.site as i128 + 5))
                .map_err(|_| c"Capture branch dispatch is out of range.")?,
        );
        // SAFETY: at most sixteen 16-byte stubs fit the exclusively owned page;
        // each emits an indirect SysV tail jump and one unaligned target word.
        unsafe {
            ptr::copy_nonoverlapping([0xff, 0x25, 0, 0, 0, 0].as_ptr(), address as *mut u8, 6);
            ptr::write_unaligned((address + 6) as *mut usize, branch.replacement);
        }
    }
    // SAFETY: seal our private code before making the reviewed startup text writable.
    if unsafe {
        libc::mprotect(
            code.address as *mut c_void,
            code.length,
            libc::PROT_READ | libc::PROT_EXEC,
        )
    } != 0
    {
        return Err(c"Cannot prepare capture branches.");
    }
    if !protect(libc::PROT_READ | libc::PROT_WRITE | libc::PROT_EXEC) {
        protect(libc::PROT_READ | libc::PROT_EXEC);
        return Err(c"Cannot prepare capture branch pages.");
    }
    publish();
    if let Some(slots) = &slots {
        // SAFETY: all target state has been published, native startup is
        // serialized, and both text and slot records were prepared above.
        if let Err(error) = unsafe { slots.apply() } {
            protect(libc::PROT_READ | libc::PROT_EXEC);
            std::mem::forget(code);
            std::mem::forget(module.library);
            return Err(error);
        }
    }
    for (branch, delta) in branches.iter().zip(deltas) {
        // SAFETY: four displacement bytes in each disjoint validated RW call.
        unsafe {
            if branch.opcode == 0xff {
                ptr::write(branch.site as *mut u8, 0xe8);
                ptr::write((branch.site + 5) as *mut u8, 0x90);
            }
            ptr::copy_nonoverlapping(
                delta.to_le_bytes().as_ptr(),
                (branch.site + 1) as *mut u8,
                4,
            );
        }
    }
    if !protect(libc::PROT_READ | libc::PROT_EXEC) {
        protect(libc::PROT_READ | libc::PROT_WRITE | libc::PROT_EXEC);
        for (branch, bytes) in branches.iter().zip(before) {
            if node::permissions_range(branch.site, bytes.len())
                != Some(libc::PROT_READ | libc::PROT_WRITE | libc::PROT_EXEC)
            {
                continue;
            }
            // SAFETY: the exact rollback extent was validated and currently has
            // write permission; retained originals stay live if another page fails.
            unsafe {
                ptr::copy_nonoverlapping(bytes.as_ptr(), branch.site as *mut u8, bytes.len());
            }
        }
        protect(libc::PROT_READ | libc::PROT_EXEC);
        if let Some(slots) = &slots {
            slots.rollback();
        }
        std::mem::forget(code);
        std::mem::forget(module.library);
        return Err(c"Cannot restore capture branch protection.");
    }
    std::mem::forget(code);
    std::mem::forget(module.library);
    Ok(())
}

/// Caller must serialize installation and ensure the reviewed call is not yet
/// executing. `publish` installs typed original state before any hook can run.
pub(crate) unsafe fn redirect(
    module: Module,
    site: usize,
    target: usize,
    context: &[u8],
    dispatch: &[u8],
    replacement: usize,
    publish: impl FnOnce(),
) -> Result<Installed, &'static CStr> {
    let context_start = site
        .checked_sub(context.len())
        .ok_or(c"Invalid native capture context.")?;
    if module.bytes(context_start, context.len()) != Some(context) || !module.executable(target, 1)
    {
        return Err(c"Native capture register context differs from review.");
    }
    let before: [u8; 5] = module
        .bytes(site, 5)
        .ok_or(c"Invalid native capture call.")?
        .try_into()
        .expect("five bytes");
    let next = site
        .checked_add(5)
        .ok_or(c"Native capture call overflow.")?;
    let old_target = next.checked_add_signed(i32::from_le_bytes(
        before[1..].try_into().expect("four bytes"),
    ) as isize);
    if before[0] != 0xe8 || old_target != Some(target) {
        return Err(c"Native capture call differs from review.");
    }
    let code = Code::near(site)?;
    if dispatch.len() + size_of::<usize>() > code.length {
        return Err(c"Capture dispatch is too large.");
    }
    let delta = (code.address as i128) - (next as i128);
    let delta = i32::try_from(delta).map_err(|_| c"Capture dispatch is out of range.")?;
    // SAFETY: the owned RW page has checked capacity for code plus pointer bytes.
    unsafe {
        ptr::copy_nonoverlapping(dispatch.as_ptr(), code.address as *mut u8, dispatch.len());
        ptr::write_unaligned((code.address + dispatch.len()) as *mut usize, replacement);
    }
    let page = code.length;
    let first = site & !(page - 1);
    let end = next
        .checked_add(page - 1)
        .ok_or(c"Capture page overflow.")?
        & !(page - 1);
    let length = end - first;
    if !module.executable_pages(first, length, page) {
        return Err(c"Capture patch crosses executable segments.");
    }
    // SAFETY: code is our private mapping. Native pages cover only the reviewed
    // startup call; restore their RX protection immediately after the write.
    if unsafe {
        libc::mprotect(
            code.address as *mut c_void,
            page,
            libc::PROT_READ | libc::PROT_EXEC,
        )
    } != 0
    {
        return Err(c"Could not prepare native capture adapter.");
    }
    // SAFETY: validated complete RX range, startup serialization required above.
    if unsafe {
        libc::mprotect(
            first as *mut c_void,
            length,
            libc::PROT_READ | libc::PROT_WRITE | libc::PROT_EXEC,
        )
    } != 0
    {
        // SAFETY: undo a possible partial protection change; no text was written.
        unsafe {
            libc::mprotect(
                first as *mut c_void,
                length,
                libc::PROT_READ | libc::PROT_EXEC,
            );
        }
        return Err(c"Could not prepare native capture adapter.");
    }
    publish();
    // SAFETY: the validated call's writable displacement is four bytes; the
    // opcode and all surrounding instructions remain unchanged.
    unsafe {
        ptr::copy_nonoverlapping(delta.to_le_bytes().as_ptr(), (site + 1) as *mut u8, 4);
    }
    // SAFETY: restore the exact original RX text protection after patching.
    if unsafe {
        libc::mprotect(
            first as *mut c_void,
            length,
            libc::PROT_READ | libc::PROT_EXEC,
        )
    } != 0
    {
        // SAFETY: reestablish write access rather than assuming a failed
        // multi-page protection operation left the whole native range writable.
        if unsafe {
            libc::mprotect(
                first as *mut c_void,
                length,
                libc::PROT_READ | libc::PROT_WRITE | libc::PROT_EXEC,
            )
        } == 0
        {
            // SAFETY: the complete rollback range is writable again.
            unsafe {
                ptr::copy_nonoverlapping(before.as_ptr(), site as *mut u8, before.len());
            }
        }
        // SAFETY: retry restoring the original permissions on the same range.
        unsafe {
            libc::mprotect(
                first as *mut c_void,
                length,
                libc::PROT_READ | libc::PROT_EXEC,
            );
        }
        std::mem::forget(code);
        std::mem::forget(module.library);
        return Err(c"Could not restore native capture code protection.");
    }
    // These two resources are intentionally process-lived, like the native
    // call site. Keeping the original target loaded is part of the hook's ABI.
    std::mem::forget(code);
    std::mem::forget(module.library);
    Ok(Installed { site, target })
}
