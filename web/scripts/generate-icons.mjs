/**
 * Generates the PWA / favicon icon set from a single vector mark.
 *
 * Usage: npm run icons  (writes into public/ — the PNGs are committed)
 */
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import sharp from "sharp";

const publicDir = join(dirname(fileURLToPath(import.meta.url)), "..", "public");

const GRADIENT = `
  <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#6366F1"/>
    <stop offset="1" stop-color="#9333EA"/>
  </linearGradient>
  <radialGradient id="glow" cx="0.3" cy="0.2" r="0.9">
    <stop offset="0" stop-color="#FFFFFF" stop-opacity="0.28"/>
    <stop offset="1" stop-color="#FFFFFF" stop-opacity="0"/>
  </radialGradient>`;

/** The glyph is drawn on a 512 grid; `scale` shrinks it around the centre (maskable safe zone). */
function glyph(scale = 1) {
  const offset = (512 - 512 * scale) / 2;
  return `
  <g transform="translate(${offset} ${offset}) scale(${scale})">
    <path d="M148 360V196l108 104 108-104v164" fill="none" stroke="#FFFFFF"
      stroke-width="46" stroke-linecap="round" stroke-linejoin="round"/>
    <circle cx="256" cy="150" r="30" fill="#FFFFFF"/>
  </g>`;
}

function iconSvg({ radius, scale }) {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="512" height="512">
  <defs>${GRADIENT}</defs>
  <rect width="512" height="512" rx="${radius}" fill="url(#bg)"/>
  <rect width="512" height="512" rx="${radius}" fill="url(#glow)"/>
  ${glyph(scale)}
</svg>`;
}

const targets = [
  // Rounded "any" icons (transparent corners).
  { file: "icons/icon-192.png", size: 192, svg: iconSvg({ radius: 112, scale: 1 }) },
  { file: "icons/icon-512.png", size: 512, svg: iconSvg({ radius: 112, scale: 1 }) },
  // Maskable: full bleed, glyph inside the 80% safe zone.
  { file: "icons/icon-maskable-512.png", size: 512, svg: iconSvg({ radius: 0, scale: 0.72 }) },
  // iOS applies its own mask and dislikes transparency.
  { file: "apple-touch-icon.png", size: 180, svg: iconSvg({ radius: 0, scale: 0.82 }) },
];

async function main() {
  for (const { file, size, svg } of targets) {
    const out = join(publicDir, file);
    await mkdir(dirname(out), { recursive: true });
    await sharp(Buffer.from(svg)).resize(size, size).png({ compressionLevel: 9 }).toFile(out);
    console.log(`wrote ${file} (${size}x${size})`);
  }
  await writeFile(join(publicDir, "favicon.svg"), iconSvg({ radius: 112, scale: 1 }) + "\n");
  console.log("wrote favicon.svg");
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
