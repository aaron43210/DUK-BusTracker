#!/usr/bin/env bash
# Generate the PWA app-icon set from public/duk-logo.png.
#
# The manifest (vite.config.js) and index.html reference these files:
#   public/icon-192.png            (any)      launcher / favicon
#   public/icon-512.png            (any)      hi-res launcher
#   public/icon-maskable-512.png   (maskable) padded for Android's circle crop
#   public/apple-touch-icon.png    (180x180)  iOS home-screen
#
# Run once from the duk_pwa1 directory:  bash scripts/make-icons.sh
# (Must be run OUTSIDE the sandbox — image tools need the system temp dir.)
set -euo pipefail
cd "$(dirname "$0")/.."

SRC="public/duk-logo.png"
[ -f "$SRC" ] || { echo "Missing $SRC"; exit 1; }

if command -v sips >/dev/null 2>&1; then
  # macOS: scale the wide logo, then pad onto a white square.
  tmp="$(mktemp -d)"
  sips -Z 440 "$SRC" --out "$tmp/any.png" >/dev/null
  sips -p 512 512 --padColor FFFFFF "$tmp/any.png" --out public/icon-512.png >/dev/null
  sips -z 192 192 public/icon-512.png --out public/icon-192.png >/dev/null
  sips -z 180 180 public/icon-512.png --out public/apple-touch-icon.png >/dev/null
  # Maskable: smaller logo (~62% of frame) so nothing is clipped by the crop.
  sips -Z 320 "$SRC" --out "$tmp/mask.png" >/dev/null
  sips -p 512 512 --padColor FFFFFF "$tmp/mask.png" --out public/icon-maskable-512.png >/dev/null
  rm -rf "$tmp"
elif command -v magick >/dev/null 2>&1 || command -v convert >/dev/null 2>&1; then
  IM="$(command -v magick || command -v convert)"
  "$IM" "$SRC" -resize 440x440 -background white -gravity center -extent 512x512 public/icon-512.png
  "$IM" public/icon-512.png -resize 192x192 public/icon-192.png
  "$IM" public/icon-512.png -resize 180x180 public/apple-touch-icon.png
  "$IM" "$SRC" -resize 320x320 -background white -gravity center -extent 512x512 public/icon-maskable-512.png
else
  echo "Need sips (macOS) or ImageMagick (magick/convert)."; exit 1
fi

echo "Generated: icon-192.png icon-512.png icon-maskable-512.png apple-touch-icon.png"
