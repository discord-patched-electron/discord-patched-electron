# Presentation assets at client startup

The picker reads native icons from the shared page-world presentation cache.
Previously the settings window was the only caller that populated that cache,
so the picker retained fallback drawings until Settings opened.

The main Discord renderer now invokes the same collector before installing its
UI adapters. It retries during the first thirty seconds for late loaded modules,
then stops scanning. Reinjection cannot create duplicate timers, and navigation
cancels pending work. It only reads already-loaded native icon exports and styles,
without fetching account data or evaluating arbitrary module factories.

Tests verify the real collector populates the picker’s icon cache without a
settings window, bounded retry after missing assets, reinjection and navigation.
Unsupported or unavailable native drawings still use the shared fallback set.
