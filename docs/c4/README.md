# Container view (C4)

Six parts we build, four outside systems. The Trust Ledger and the decision engine are
Python packages inside the core service, which is what lets the attack lab import and test
the exact same code.

| Part | Runs on | Owner | Talks to | How |
|---|---|---|---|---|
| Dashboard (`apps/web`) | Next.js on Vercel | all three, one area each | Core service; Supabase Realtime | REST with the signed-in user's token; reads only |
| Core service (`apps/core`) | FastAPI on Railway, one always-on container | Abhi | Ramp, Supabase, Trust Ledger, voice service | Signed webhooks in; polling, holds and releases out; SQL functions for every state change |
| Trust Ledger (`packages/ledger`) | Python package inside the core service | Inesh | LLM API, Supabase `ledger` schema | Function calls; the extractor's output must match a JSON schema |
| Voice service (`apps/voice`) | FastAPI WebSocket server on Railway | Sami | Twilio, LLM API, Supabase | Twilio sends the caller's words as text; we send back text to speak |
| Attack lab (`evals`) | Python CLI in CI and on laptops | Sami | Ramp mock, core service, text phone line | Imports the same Python packages; writes scorecards to Supabase |
| Ramp mock (`apps/ramp-mock`) | Small FastAPI service on Railway | Abhi | Core service | Same paths and field names as Ramp's API, with signed webhooks |

Outside systems: **Ramp** (vendors, bank-detail changes, bills, the hold), **Supabase**
(Postgres as the referee, Auth, Realtime, private Storage, Vault), **Twilio** (speech to
text and text to speech), and the **LLM APIs** (Claude for the reader and the call agent
with pinned versions; a second model family for attackers and personas; a third for the
judge, so no model attacks or grades itself).

Why this shape: Railway because the live call needs a WebSocket open for minutes; Python
because parsing, eval tools and Ramp's SDK are Python-first and one language lets the lab run
the real pipeline in process; no workflow engine because Postgres tables for timers and
commands are enough at this size (Temporal is the production path).
