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
  adapter: vercel(),
  vite: {
    plugins: [tailwindcss()],
  },
});
