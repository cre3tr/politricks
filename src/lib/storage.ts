// Safe localStorage get/set for the reader's language choice (`pt.lang`).
//
// "Safe" means: never throw. localStorage can be unavailable (private
// browsing, a locked-down browser, some in-app webviews) and reading or
// writing it then throws a real exception, not just returns null. Every call
// here is wrapped in try/catch so a blocked storage falls back to defaults
// instead of breaking the page — see devpost/spec.md > Important Failure
// Modes ("localStorage unavailable").
export type Lang = "en" | "sw" | "sheng";

const KEY = "pt.lang";

function isLang(value: unknown): value is Lang {
  return value === "en" || value === "sw" || value === "sheng";
}

// Returns the saved language, or null if there isn't one / storage is blocked
// / the saved value isn't one of the three languages we know about.
export function getLang(): Lang | null {
  try {
    const value = window.localStorage.getItem(KEY);
    return isLang(value) ? value : null;
  } catch {
    return null;
  }
}

// Saves the language choice. Returns false (never throws) if storage is
// blocked, so the caller can decide whether to tell the reader their choice
// won't be remembered.
export function setLang(lang: Lang): boolean {
  try {
    window.localStorage.setItem(KEY, lang);
    return true;
  } catch {
    return false;
  }
}
