// POST /api/match (devpost/spec.md > Match API). Rendered on demand as a
// Vercel function; the rest of the site stays prebuilt. All the logic is in
// src/lib/api.ts (handleMatch); this file only turns HTTP into a call.
import type { APIRoute } from "astro";
import { handleMatch } from "../../lib/api.ts";
import { liveDeps } from "../../lib/claude.ts";

export const prerender = false;

export const POST: APIRoute = async ({ request }) => {
  let body: unknown = null;
  try {
    body = await request.json();
  } catch {
    // Not JSON: handleMatch answers 400 for a missing bill id.
  }
  const result = await handleMatch(body, liveDeps());
  return new Response(JSON.stringify(result.body), {
    status: result.status,
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  });
};
