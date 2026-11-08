# Portable Wayland monitor ordering

The optimized picker previously numbered monitors in DRM/registry discovery
order. That order is not the desktop layout: the current machine exposed KMS
connectors in a different order from their left-to-right positions.

`dpe-wayland-source --list` now reports standard xdg-output logical positions,
falling back to core wl_output geometry when xdg-output is unavailable. It reads
metadata only and does not capture pixels or request authorization. The imported
xdg-output XML is pinned from the same installed wayland-protocols 1.50 package
as the existing protocol files, with its checksum and upstream path recorded in
`integration/video/protocols/provenance.json`.

The main capture catalog reuses the capability-probe inventory. KMS connector
names are matched exactly and uniquely to that layout. If every displayed
monitor has an unambiguous position, numbering is left-to-right, then
top-to-bottom, with a stable natural connector-name tie break. Missing or
ambiguous names/positions preserve the original order. Source tokens continue
to identify the original connector, independent of numbering.

Wayland has no cross-desktop canonical monitor number or primary-output order.
This is spatial ordering, not a claim to reproduce a particular desktop’s
private numbering convention. No compositor-specific IPC or configuration is
read. The standard protocol explicitly prefers its logical position over
wl_output geometry:
[wayland-protocols xdg-output specification](https://gitlab.freedesktop.org/wayland/wayland-protocols/-/blob/1.50/unstable/xdg-output/xdg-output-unstable-v1.xml).

Validation: 33 controller tests passed, including negative coordinates, stacked
outputs, stable selections after renumbering, unavailable direct capture, and
ambiguous/missing layout fallback. The Wayland producer fixture passed with
ASan/UBSan and exercises both geometry event paths. The helper rebuilt with
warnings as errors at `out/monitor-order/wayland`; its real metadata-only listing
confirmed positions x=0, 1920, 3840 for DP-2, HDMI-A-1 and DP-3 respectively.
No desktop frames were captured and no displays were reconfigured or unplugged.
