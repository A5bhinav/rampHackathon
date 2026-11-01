# Threat model

Dialback's adversary controls some channels (a vendor's mailbox, the documents it sends,
sometimes a public listing, and whoever answers a phone) and wants one payment to reach
their account. Every input from outside is hostile; every consequential decision sits
where that input can't reach: in code, in the database, or with a person.

## STRIDE

| Threat | In Dialback | Control |
|---|---|---|
| Spoofing | A spoofed change email, an impostor on the call, a forged webhook, a spoofed incoming call | Never call a number first seen in the request (R2); the vendor must state the last four digits; HMAC-signed webhooks; outbound calls only, an incoming call never counts |
| Tampering | Edited evidence, an altered document, a quietly changed rule | The hash chain with an outside witness; each document's SHA-256 in the log; the rule version stored with every decision |
| Repudiation | "I never released that." "The AI decided." | Approvals carry the user's ID and the case version, inside the chain; decisions store the code version and a hash of their inputs |
| Information disclosure | Bank numbers, transcripts, recordings, team phone numbers | Last four digits plus a keyed fingerprint; private storage behind row-level security; redacted transcripts; secrets kept out of the repo |
| Denial of service | A flood of fake change requests puts every vendor on hold; runaway call or model costs | Rate limits on intake; one case per vendor; one-click, logged duplicate marking; dollar caps |
| Elevation of privilege | A clerk releases their own change; a server key is used for a release; injected text reaches a tool | Four-eyes in SQL; release only through `release_case()` with the user's token; the agent has no tool that changes anything that matters |

## OWASP Top 10 for LLM Applications (2025)

| Risk | Where it could hit | Control |
|---|---|---|
| LLM01 Prompt injection | PDFs, emails and signatures; the callee's words | The reader is quarantined with no tools; the voice tools only record; code decides (R9) |
| LLM02 Sensitive information disclosure | The agent is talked into revealing what it checks | The agent never holds the new details or the expected digits (R4); traces are redacted |
| LLM03 Supply chain | Libraries and model versions change under us | Lockfiles; pinned model versions; no third-party MCP servers in the case path |
| LLM04 Data and model poisoning | Phone numbers planted in a vendor's history | Two independent sources that predate the request, checked in code; near-miss and change-context exclusions |
| LLM05 Improper output handling | Extracted text shown in the dashboard or used in queries | Schema validation; all document text escaped in the UI; parameterized SQL only |
| LLM06 Excessive agency | An agent that could mark a case verified or release it | No such tool exists; holds and releases are code-only and gated by people |
| LLM07 System prompt leakage | A callee asks for the agent's instructions | Assume it leaks: the prompt holds no secrets, expected values or thresholds |
| LLM09 Misinformation | The reader invents or misreads a number | Grounding: a value must appear in the visible text, or it's dropped |
| LLM10 Unbounded consumption | Lab runs, long calls, retry storms | Dollar caps per run; call-length and attempt caps; rate limits on intake |

LLM08 (vector and embedding weaknesses) doesn't apply: no decision uses embeddings.

## Prompt injection, by design

Dialback doesn't depend on any model resisting injection. It applies the patterns from
Beurer-Kellner et al. (2025): **action selector** (the call agent's whole action space is
three tools that only record), **plan then execute** (the script and tools are fixed before
the call), **map-reduce** (the reader sees one document at a time and code combines the
results), **dual LLM made stricter** (the reader is quarantined and the privileged side is
plain code), and **context minimization** (the agent never sees the change thread, the new
account or the number's lineage). Even if a poisoned document convinces the reader that
"the verified number is X", X is tagged as first seen in the change context and code
excludes it: provenance comes from custody metadata, never from a model.

## Data protection

- Bank details: routing number, last four digits and keyed fingerprint only (ADR-8). The key lives in Supabase Vault or a server-only setting.
- Spoken digits are compared in memory; the record keeps the result and a salted hash, never the digits.
- Recordings and raw transcripts live in a private bucket behind short-lived signed links. Each recording's SHA-256 goes into the chain; the dashboard and traces get a redacted transcript.
- Logs keep an allowlist of fields, mask any run of six or more digits, and never include request bodies.
- Secrets stay server-side, with separate keys for the mock, the sandbox and the demo, and the narrowest Ramp scopes. Secret scanning runs on the repo; every key is rotated after Nov 19.
- Team phone numbers and consent records never enter the repo.
