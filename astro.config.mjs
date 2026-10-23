// PoliTricks site config.
//
// - `output` is left at Astro's default (static): almost everything is
//   prebuilt from the bill JSON at build time (see devpost/spec.md > How
//   This Works). Only src/pages/api/*.ts (slice 4) opt into on-demand
//   rendering per-route with `export const prerender = false`.
// - The Vercel adapter still needs to be set so those on-demand routes can
//   run as Vercel functions once they exist.
// - Tailwind v4 is wired in as a Vite plugin (its new, non-PostCSS way of
//   integrating), not a legacy Tailwind/PostCSS config file.
import { defineConfig } from "astro/config";
import vercel from "@astrojs/vercel";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  // maxDuration 60: Explain runs Claude at high effort and can take most of
  // a minute; src/lib/claude.ts gives up at 55 s so the function answers
  // 502 itself instead of being killed mid-call.
  adapter: vercel({ maxDuration: 60 }),
  vite: {
    plugins: [tailwindcss()],
  },
});
