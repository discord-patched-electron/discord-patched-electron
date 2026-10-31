# Welcome artwork

Open [welcome-background.kra](welcome-background.kra) in Krita to edit the
onboarding background. Its 1024 × 1280 canvas extends the existing
[project illustration](../discord-patched-electron.png) by 176 pixels above and
80 below. The original 1024 × 1024 pixels remain unchanged on a locked layer.

The separate sky layer continues the sampled edge gradient. The grass and path
layer extends the edge texture, with additional grass strokes drawn using
Krita's `c) Pencil-2` brush. The source was edited and exported with Krita 6.0.4.1.
No generated image is used in this asset.

Export the whole canvas as
[`integration/updater/logos/welcome-background.png`](../integration/updater/logos/welcome-background.png).
The UI applies the top shading and progressive blur, and the translucent footer
blur, in CSS. Keep those effects out of the source illustration.
