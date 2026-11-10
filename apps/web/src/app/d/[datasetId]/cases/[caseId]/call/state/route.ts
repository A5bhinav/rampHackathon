import { core } from "@/lib/core";
import { callStateResponse } from "@/lib/live-call";
import { sessionToken } from "@/lib/session";

// The live call's polling read (spec 0065): server-side, with the signed-in person's own token. The
// browser asks this route, never the core, and gets back only what the page draws (no token).
export async function GET(request: Request, { params }: { params: Promise<{ datasetId: string; caseId: string }> }) {
  const { caseId } = await params;
  const withSelection = new URL(request.url).searchParams.get("selection") === "1";
  const { status, body } = await callStateResponse(
    { token: sessionToken, getCase: core.case, getSelection: core.selection }, caseId, withSelection);
  return Response.json(body, { status, headers: { "Cache-Control": "no-store" } });
}
