// The review-status badge label, in exactly one place. ClauseCard uses this
// for clause cards; the bill page uses it again for the summary card — since
// it's needed in a second spot, it lives here rather than being copied
// (Iron Law X: the second occurrence of a claim is the design defect, not
// the first).
//
// The `never` branch is load-bearing, not decoration: if `Review["status"]`
// ever grows a fourth value, the `never` assignment below fails to TYPECHECK
// until a human decides what badge that status gets. That is what makes
// "a sample can never render as AI draft or Reviewed" (devpost/spec.md >
// Clause Card) a compile-time guarantee instead of a hope.
//
// The check is real because `npm run build` runs `astro check` first (also
// `npm run check` on its own). `astro build` alone strips types without
// checking them, so build with `npm run build`, not bare `astro build`.
import type { Review } from "./schema";

export interface Badge {
  label: string;
  className: string;
}

export function badgeFor(status: Review["status"]): Badge {
  switch (status) {
    case "draft":
      return { label: "AI draft — not yet reviewed", className: "badge-draft" };
    case "reviewed":
      return { label: "Reviewed", className: "badge-reviewed" };
    case "sample":
      return { label: "Sample text — placeholder", className: "badge-sample" };
    default: {
      const exhaustive: never = status;
      throw new Error(`badgeFor: unhandled review status ${String(exhaustive)}`);
    }
  }
}
