# Official Tabler SVG sources

These unmodified SVG files are cached from the Tabler Icons `v3.31.0` release:

`https://raw.githubusercontent.com/tabler/tabler-icons/v3.31.0/icons/outline/`

Tabler Icons are MIT licensed; see `LICENSE.txt` in this directory.

Run `node scripts/create-native-icons.cjs --tabbar-only` with `SHARP_MODULE`
pointing to an installed `sharp` package to regenerate only the native tab bar.
The generator keeps the source files unchanged and renders a 60 px SVG with
1.75 stroke width into a 72 px transparent canvas. The native navigation routes
and tab touch targets remain unchanged. In-page UI icons retain their existing
size and stroke weight.
