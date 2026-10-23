// POST /api/explain (devpost/spec.md > Explain API). Rendered on demand as a
// Vercel function; the rest of the site stays prebuilt. All the logic is in
// src/lib/api.ts (handleExplain); this file only turns HTTP into a call.
import type { APIRoute } from "astro";
import { handleExplain } from "../../lib/api.ts";
import { liveDeps } from "../../lib/claude.ts";

export const prerender = false;

export const POST: APIRoute = async ({ request }) => {
  let body: unknown = null;
  try {
    body = await request.json();
  } catch {
    // Not JSON: handleExplain answers 400 for an unknown clause.
  }
  const result = await handleExplain(body, liveDeps());
  return new Response(JSON.stringify(result.body), {
    status: result.status,
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  });
};
