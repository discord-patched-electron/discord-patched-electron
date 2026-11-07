// SPDX-License-Identifier: MIT
//! Negotiation state without native pointers or callbacks. A publication owns
//! its offers across reentry; revision tickets prevent an older callback from
//! overwriting replacement caps. No state borrow spans a PipeWire call.
use crate::pod::{Format, Fraction, Owned};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Mode {
    Initial,
    Runtime,
}
#[derive(Clone, Copy, Default, PartialEq, Eq)]
pub struct Totals {
    pub observed: u64,
    pub supported: u64,
    pub changes: u64,
    pub restored: u64,
    pub failures: u64,
    pub bypassed: u64,
    pub timeouts: u64,
    pub asynchronous_errors: u64,
    pub initial_preferences: u64,
    pub initial_without_rate: u64,
}
#[derive(Clone, Copy)]
struct Pending {
    fps: u32,
    generation: u64,
    deadline: u64,
}
pub struct Publication {
    offers: Vec<Owned>,
    fps: u32,
    generation: u64,
    revision: u64,
}
impl Publication {
    pub fn offers(&self) -> &[Owned] {
        &self.offers
    }
}
pub struct Update {
    revision: u64,
}
pub struct State {
    pub mode: Mode,
    original: Vec<Owned>,
    original_maximum: Option<Fraction>,
    pending: Option<Pending>,
    failed_generation: Option<u64>,
    revision: u64,
    busy: usize,
    disabled: bool,
    restore_needed: bool,
    destroyed: bool,
    eligible: bool,
    pub preferred: u32,
    pub applied: u32,
    pub dimensions: (u32, u32),
    pub maximum: Option<Fraction>,
    pub totals: Totals,
}
impl State {
    pub fn new(mode: Mode, original: Vec<Owned>) -> Option<Self> {
        if original.is_empty()
            || original.len() > 32
            || original.iter().any(|pod| !pod.format().is_offer())
        {
            return None;
        }
        Some(Self {
            mode,
            original,
            original_maximum: None,
            pending: None,
            failed_generation: None,
            revision: 0,
            busy: 0,
            disabled: false,
            restore_needed: false,
            destroyed: false,
            eligible: false,
            preferred: 0,
            applied: 0,
            dimensions: (0, 0),
            maximum: None,
            totals: Totals::default(),
        })
    }
    pub fn initial(&mut self, fps: u32) -> Option<Vec<Owned>> {
        if self.mode != Mode::Initial || self.destroyed {
            return None;
        }
        if fps == 0 {
            self.totals.initial_without_rate += 1;
            return None;
        }
        let mut changed = false;
        let offers = self
            .original
            .iter()
            .map(|pod| {
                if let Some(preferred) = pod.format().with_rate(fps, true) {
                    changed = true;
                    preferred
                } else {
                    pod.clone()
                }
            })
            .collect();
        if !changed {
            return None;
        }
        self.preferred = fps;
        self.totals.initial_preferences += 1;
        Some(offers)
    }
    pub fn observe(&mut self, format: Option<Format<'_>>) {
        if self.destroyed {
            return;
        }
        self.totals.observed += 1;
        let raw = format.filter(|format| format.is_raw());
        self.dimensions = raw.and_then(|format| format.dimensions()).unwrap_or((0, 0));
        self.maximum = raw.and_then(|format| format.maximum());
        self.eligible = raw
            .and_then(|format| format.rate())
            .is_some_and(|rate| rate.numerator == 0)
            && self
                .maximum
                .is_some_and(|rate| rate.numerator != 0 && rate.denominator != 0);
        if self.eligible {
            self.totals.supported += 1;
            if self.original_maximum.is_none() {
                self.original_maximum = self.maximum;
            }
        } else {
            self.maximum = None;
        }
        if self.mode == Mode::Initial {
            self.applied = if self.eligible
                && self.preferred != 0
                && self
                    .maximum
                    .is_some_and(|maximum| maximum.equals_rate(self.preferred))
            {
                self.preferred
            } else {
                0
            };
            return;
        }
        if let Some(pending) = self.pending
            && self.eligible
        {
            let acknowledged = self.maximum.is_some_and(|maximum| {
                if pending.fps != 0 {
                    maximum.at_most(pending.fps)
                } else {
                    self.original_maximum
                        .is_some_and(|original| maximum.equivalent(original))
                }
            });
            if acknowledged {
                self.applied = pending.fps;
                self.pending = None;
                if pending.fps == 0 {
                    self.totals.restored += 1;
                } else {
                    self.totals.changes += 1;
                }
            }
        }
    }
    pub fn pending_fps(&self) -> u32 {
        self.pending.map_or(0, |pending| pending.fps)
    }
    pub fn is_pending(&self) -> bool {
        self.pending.is_some()
    }
    fn cancel_pending(&mut self) {
        self.pending = None;
        self.applied = 0;
    }
    pub fn disable_timer(&mut self) {
        self.disabled = true;
        self.totals.bypassed += 1;
    }
    pub fn asynchronous_error(&mut self, generation: u64) {
        if self.destroyed {
            return;
        }
        self.disabled = true;
        self.failed_generation = Some(generation);
        self.restore_needed = self.pending.is_some() || self.applied != 0;
        self.cancel_pending();
        self.dimensions = (0, 0);
        self.maximum = None;
        self.totals.asynchronous_errors += 1;
    }
    pub fn destroy(&mut self) {
        self.destroyed = true;
        self.pending = None;
        self.applied = 0;
        self.preferred = 0;
        self.dimensions = (0, 0);
        self.maximum = None;
    }
    pub fn destroyed(&self) -> bool {
        self.destroyed
    }
    pub fn tick(&mut self, generation: u64, now: u64, stream_error: bool) -> Option<Publication> {
        if self.mode != Mode::Runtime || self.destroyed || self.busy != 0 {
            return None;
        }
        if let Some(pending) = self.pending
            && now >= pending.deadline
        {
            self.failed_generation = Some(pending.generation);
            self.disabled = true;
            self.restore_needed = pending.fps != 0;
            self.cancel_pending();
            self.totals.timeouts += 1;
        }
        let fps = if self.restore_needed {
            self.restore_needed = false;
            if stream_error {
                return None;
            }
            0
        } else if self.pending.is_some() {
            return None;
        } else {
            let fps = if self.eligible && !self.disabled {
                (generation & 255) as u32
            } else {
                0
            };
            if self.applied == fps || self.failed_generation == Some(generation) {
                return None;
            }
            fps
        };
        let offers = if fps == 0 {
            self.original.clone()
        } else {
            let Some(offers) = self
                .original
                .iter()
                .map(|pod| pod.format().with_rate(fps, false))
                .collect::<Option<Vec<_>>>()
            else {
                self.failed_generation = Some(generation);
                self.totals.bypassed += 1;
                return None;
            };
            offers
        };
        self.busy += 1;
        self.pending = Some(Pending {
            fps,
            generation,
            deadline: now.saturating_add(2_000_000_000),
        });
        self.applied = 0;
        Some(Publication {
            offers,
            fps,
            generation,
            revision: self.revision,
        })
    }
    pub fn published(&mut self, publication: Publication, status: i32) {
        if !self.destroyed && self.revision == publication.revision && status < 0 {
            self.cancel_pending();
            self.totals.failures += 1;
            self.failed_generation = Some(publication.generation);
            self.restore_needed = publication.fps != 0;
        }
        self.busy = self.busy.saturating_sub(1);
    }
    pub fn begin_update(&mut self) -> Update {
        self.busy += 1;
        Update {
            revision: self.revision,
        }
    }
    pub fn updated(&mut self, update: Update, offers: Option<Vec<Owned>>, status: i32) {
        if !self.destroyed
            && status >= 0
            && self.revision == update.revision
            && let Some(offers) = offers.filter(|offers| {
                !offers.is_empty()
                    && offers.len() <= 32
                    && offers.iter().all(|pod| pod.format().is_offer())
            })
        {
            self.original = offers;
            self.revision = self.revision.wrapping_add(1);
            self.cancel_pending();
        }
        self.busy = self.busy.saturating_sub(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ffi as f;
    fn offer(maximum: u32, fixed: u32, width: u32) -> Owned {
        let mut bytes = Vec::new();
        for value in [
            8u32,
            f::DPE_POD_OBJECT,
            f::DPE_POD_FORMAT,
            f::DPE_POD_ENUM_FORMAT,
        ] {
            bytes.extend_from_slice(&value.to_ne_bytes());
        }
        for (key, kind, values) in [
            (f::DPE_POD_MEDIA_TYPE, f::DPE_POD_ID, vec![f::DPE_POD_VIDEO]),
            (
                f::DPE_POD_MEDIA_SUBTYPE,
                f::DPE_POD_ID,
                vec![f::DPE_POD_RAW],
            ),
            (f::DPE_POD_SIZE, f::DPE_POD_RECTANGLE, vec![width, 1080]),
            (f::DPE_POD_RATE, f::DPE_POD_FRACTION, vec![fixed, 1]),
        ]
        .into_iter()
        .chain((maximum != 0).then_some((
            f::DPE_POD_MAX_RATE,
            f::DPE_POD_FRACTION,
            vec![maximum, 1],
        ))) {
            for value in [key, 0, (values.len() * 4) as u32, kind]
                .into_iter()
                .chain(values)
            {
                bytes.extend_from_slice(&value.to_ne_bytes());
            }
            bytes.resize(bytes.len().next_multiple_of(8), 0);
        }
        let length = (bytes.len() - 8) as u32;
        bytes[..4].copy_from_slice(&length.to_ne_bytes());
        Format::parse(&bytes).unwrap().owned()
    }
    fn state(mode: Mode) -> State {
        let mut state = State::new(mode, vec![offer(0, 0, 1920)]).unwrap();
        state.observe(Some(offer(240, 0, 1920).format()));
        state
    }
    fn ack(state: &mut State, maximum: u32) {
        state.observe(Some(offer(maximum, 0, 1920).format()));
    }
    #[test]
    fn rates_require_actual_ack_restore_original_limits_and_bound_timeouts() {
        let mut state = state(Mode::Runtime);
        let publication = state.tick(0x100 | 60, 0, false).unwrap();
        assert_eq!(
            publication.offers()[0]
                .format()
                .maximum()
                .unwrap()
                .numerator,
            60
        );
        assert_eq!(state.applied, 0);
        state.published(publication, 0);
        assert_eq!(state.totals.changes, 0);
        ack(&mut state, 60);
        assert_eq!((state.applied, state.totals.changes), (60, 1));
        let publication = state.tick(0x200 | 30, 1, false).unwrap();
        state.published(publication, 0);
        ack(&mut state, 30);
        assert_eq!((state.applied, state.totals.changes), (30, 2));
        let publication = state.tick(0x300, 2, false).unwrap();
        assert!(publication.offers()[0].format().maximum().is_none());
        state.published(publication, 0);
        ack(&mut state, 30);
        assert!(state.is_pending());
        assert_eq!(state.totals.restored, 0);
        ack(&mut state, 240);
        assert!(!state.is_pending());
        assert_eq!(state.totals.restored, 1);
        let generation = 0x400 | 60;
        let publication = state.tick(generation, 3, false).unwrap();
        state.published(publication, -22);
        assert_eq!((state.applied, state.totals.failures), (0, 1));
        let restoration = state.tick(generation, 4, false).unwrap();
        assert!(restoration.offers()[0].format().maximum().is_none());
        state.published(restoration, 0);
        ack(&mut state, 240);
        assert!(state.tick(generation, 5, false).is_none());
        let generation = generation + 256;
        let publication = state.tick(generation, 10, false).unwrap();
        state.published(publication, 0);
        let restoration = state.tick(generation, 2_000_000_010, false).unwrap();
        assert_eq!(state.totals.timeouts, 1);
        state.published(restoration, 0);
        assert!(state.tick(generation, 4_000_000_010, false).is_none());
        assert_eq!(state.totals.timeouts, 2);
        assert!(!state.is_pending());
        assert!(state.tick(generation + 256, 5_000_000_010, false).is_none());
    }
    #[test]
    fn nested_replacements_and_destruction_cannot_retire_inflight_offers() {
        let mut state = state(Mode::Runtime);
        let publication = state.tick(60, 0, false).unwrap();
        let retained = publication.offers().to_vec();
        let update = state.begin_update();
        let nested = state.begin_update();
        state.updated(nested, Some(vec![offer(0, 0, 1024)]), 0);
        state.updated(update, Some(vec![offer(0, 0, 640)]), 0);
        state.published(publication, -22);
        assert_eq!(state.totals.failures, 0);
        let next = state.tick(60, 1, false).unwrap();
        assert_eq!(next.offers()[0].format().dimensions(), Some((1024, 1080)));
        assert_eq!(retained[0].format().dimensions(), Some((1920, 1080)));
        state.destroy();
        state.published(next, -22);
        assert!(state.destroyed());
        assert!(state.tick(30, 2, false).is_none());
        drop(state);
        assert_eq!(retained[0].format().maximum().unwrap().numerator, 60);
    }
    #[test]
    fn initial_mode_never_renegotiates_and_error_streams_are_not_restarted() {
        let mut initial = state(Mode::Initial);
        let supported = crate::ffi::DPE_POD_DROP != 0;
        let offers = initial.initial(60);
        assert_eq!(offers.is_some(), supported);
        if let Some(offers) = offers {
            assert_eq!(initial.preferred, 60);
            initial.observe(Some(offers[0].format()));
            assert_eq!(initial.applied, 60);
        } else {
            assert_eq!(initial.preferred, 0);
        }
        assert!(initial.tick(30, 0, false).is_none());
        assert_eq!(
            (initial.totals.changes, initial.totals.initial_preferences),
            (0, u64::from(supported))
        );
        let mut fixed = State::new(Mode::Initial, vec![offer(0, 60, 1920)]).unwrap();
        assert!(fixed.initial(60).is_none());
        assert_eq!(fixed.preferred, 0);
        let mut bounded = State::new(Mode::Initial, vec![offer(120, 0, 1920)]).unwrap();
        assert!(bounded.initial(60).is_none());
        assert!(bounded.initial(0).is_none());
        assert_eq!(bounded.totals.initial_without_rate, 1);
        let mut runtime = state(Mode::Runtime);
        let publication = runtime.tick(60, 0, false).unwrap();
        runtime.published(publication, 0);
        runtime.asynchronous_error(60);
        assert_eq!(runtime.totals.asynchronous_errors, 1);
        assert!(!runtime.is_pending());
        assert_eq!(runtime.dimensions, (0, 0));
        assert!(runtime.tick(60, 1, true).is_none());
        assert!(runtime.tick(30, 2, true).is_none());
        let mut no_timer = state(Mode::Runtime);
        no_timer.disable_timer();
        assert!(no_timer.tick(60, 0, false).is_none());
        assert_eq!(no_timer.totals.bypassed, 1);
    }
}
