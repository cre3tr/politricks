// Safe localStorage get/set for everything the site remembers in this
// browser: the language (`pt.lang`), the reader's profile (`pt.profile`),
// and cached answers from the two live helpers (`pt.match.*`, `pt.draft.*`).
// See devpost/spec.md > Data Model.
//
// "Safe" means: never throw. localStorage can be unavailable (private
// browsing, a locked-down browser, some in-app webviews) and reading or
// writing it then throws a real exception, not just returns null. Every call
// here is wrapped in try/catch so a blocked storage falls back to defaults
// instead of breaking the page — see devpost/spec.md > Important Failure
// Modes ("localStorage unavailable").
//
// The key names "pt.lang" and "pt.profile" are also written by hand in
// src/components/HeadScript.astro, which can't import modules. Rename one,
// rename both.
import { ProfileSchema, type Profile } from "./schema";

export type Lang = "en" | "sw" | "sheng";

const LANG_KEY = "pt.lang";
const PROFILE_KEY = "pt.profile";

function isLang(value: unknown): value is Lang {
  return value === "en" || value === "sw" || value === "sheng";
}

function read(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string): boolean {
  try {
    window.localStorage.setItem(key, value);
    return true;
  } catch {
    return false;
  }
}

// Returns the saved language, or null if there isn't one / storage is blocked
// / the saved value isn't one of the three languages we know about.
export function getLang(): Lang | null {
  const value = read(LANG_KEY);
  return isLang(value) ? value : null;
}

// Saves the language choice. Returns false (never throws) if storage is
// blocked, so the caller can decide whether to tell the reader their choice
// won't be remembered.
export function setLang(lang: Lang): boolean {
  return write(LANG_KEY, lang);
}

// The saved profile, or null when there is none or it doesn't validate (an
// old shape, a hand-edited value): null sends the reader to /welcome.
export function getProfile(): Profile | null {
  const raw = read(PROFILE_KEY);
  if (raw === null) return null;
  try {
    const result = ProfileSchema.safeParse(JSON.parse(raw));
    return result.success ? result.data : null;
  } catch {
    return null;
  }
}

export function setProfile(profile: Profile): boolean {
  return write(PROFILE_KEY, JSON.stringify(ProfileSchema.parse(profile)));
}

// Cached answers from the live helpers. A value that isn't valid JSON is
// treated as missing, so a corrupted entry costs one repeat call, nothing more.
export function getCached<T>(key: string): T | null {
  const raw = read(key);
  if (raw === null) return null;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

export function setCached(key: string, value: unknown): boolean {
  return write(key, JSON.stringify(value));
}
