# Mentions-only desktop badges

The existing numeric badge and dock filters did not cover Discord's tray. Both
its legacy `UNREAD` icon key and structured `unread` state could therefore show
ordinary guild unread with **Badge only for mentions** enabled.

Source inspection on 2 November used the retained Canary native `bundle.js`
(module package only) and the unauthenticated public web bundle
[`web.0bf948f7cf781626.js`](https://canary.discord.com/assets/web.0bf948f7cf781626.js).
The web computes a numeric notification total separately from `hasAnyUnread()`.
Its mention total includes private-channel notifications; native notification
extras remain governed by Discord. Ordinary unread becomes the `-1` sentinel
when that total is zero. Linux converts that sentinel to zero before calling
`app.setBadgeCount`; Windows sends it to the overlay, and macOS uses a dot.
The tray boolean includes ordinary unread and must never be used as a count.

The desktop adapter now retains the latest unfiltered native notification count
and filters the shared tray-state accessor before both stock and custom/preset
icon selection. Count changes refresh the tray even if its unread boolean has
not changed. Settings saves also refresh it, including clearing disabled badges
and restoring all-unread mode without waiting for another message. Original
tray state is preserved, and voice/mute/deafen/speaking priority is unchanged.
Dock and Windows overlay updates feed the same count tracking. Unknown counts,
booleans and nonnumeric values are not evidence of mentions.

Verification uses synthetic controller/native fixtures with no account or
message access. Coverage includes guild-only unread, positive notification
counts, clearing the final mention while guild unread persists, legacy and
structured tray updates, stock/custom selection, live preference changes,
voice states and destroyed trays. The full desktop transform and JavaScript
syntax check also passed against the retained native bundle. No candidate files
were modified and no runtime was launched or restarted. This is source and
fixture validation, not a logged-in desktop acceptance test.
