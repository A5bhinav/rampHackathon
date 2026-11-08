-- voice: the call record. Owner: Sami. Partner reviewer: Inesh.
--
-- Applied after app (scripts/db/apply.sh), so it may reference app.cases and app.datasets.
-- It does NOT reference ledger.* yet: the Trust Ledger schema lands separately (spec 0012,
-- Q40), so contact_point_id is a bare uuid for now and gains its FK when ledger is in.
--
-- D9, the invariant this schema exists to keep: the spoken account last four are NEVER stored
-- here. voice.call_answers holds a digit count, or dont_know / refused, and nothing else for
-- that slot. The keyed hash lives only in the evidence chain, because the core holds the key
-- (spec 0002, D7). The voice service writes these tables as service_role; the dashboard reads.

-- voice.calls: one row per placed call. id is the call_id the core pins as the case's current
-- call from call_started, and the id the call report (internal.yaml) comes back on.
create table voice.calls (
  id               uuid primary key default gen_random_uuid(),
  dataset_id       uuid not null references app.datasets (id),
  case_id          uuid not null references app.cases (id),
  attempt          integer not null check (attempt between 1 and 3),   -- internal.yaml PlaceCallRequest
  idempotency_key  text not null unique check (length(idempotency_key) between 1 and 200),
                   -- {case_id}:call:v{version}:{attempt}; a replayed key returns this same call (internal.yaml, D6)
  contact_point_id uuid,                                               -- the Trust Ledger pick (R2); FK added with ledger (Q40)
  direction        text not null default 'outbound' check (direction in ('outbound', 'inbound')),
  provider         text not null default 'text' check (provider in ('text', 'twilio', 'retell')),
  provider_call_id text,                                               -- Twilio CallSid etc.; null on the text line
  answered_by      text check (answered_by in ('human', 'machine')),
  status           text not null default 'placing'
                     check (status in ('placing', 'ringing', 'in_progress', 'ended')),
  result           text check (result in                              -- mirrors CallEndedBody.result; set at end
                     ('answers_recorded', 'no_answer', 'voicemail', 'busy', 'failed', 'machine',
                      'declined', 'callback_requested')),
  reason           text,                                               -- why it ended, for the call log; never bank details
  retry_at         timestamptz,                                        -- callback time, or redial-after on a drop
  recording_ref    text,                                               -- signed-link key in a private bucket; never the audio
  transcript_ref   text,                                               -- redacted-transcript key; never raw text
  agent_version    text not null,
  started_at       timestamptz,
  ended_at         timestamptz,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);
create index calls_case_idx on voice.calls (case_id);
create index calls_dataset_status_idx on voice.calls (dataset_id, status);

-- voice.call_answers: what the callee said, built by code from the agent's record_answer tool
-- calls and keypad events (the model emits no events, invariant I4). The slot list and the
-- capture methods mirror call-answers.schema.json.
create table voice.call_answers (
  id             uuid primary key default gen_random_uuid(),
  call_id        uuid not null references voice.calls (id) on delete cascade,
  slot           text not null check (slot in (
                   'recording_consent', 'company_named', 'department', 'speaker_name', 'speaker_role',
                   'change_requested', 'account_last4', 'offered_number', 'flag')),
  value          text,                                                 -- the text value; null for account_last4 with digits
  digit_count    smallint check (digit_count between 1 and 4),         -- account_last4 only: how many digits, never which
  turn_id        integer not null check (turn_id >= 0),                -- transcript turn the answer cites
  tool_call_id   text not null check (length(tool_call_id) between 1 and 128),
  capture_method text not null check (capture_method in ('speech', 'keypad')),
  captured_at    timestamptz not null,
  created_at     timestamptz not null default now(),

  -- D9: for account_last4, store only a count (digits given) or dont_know / refused (none). There
  -- is no column that could hold the digits, and value is forced null when a count is present.
  constraint call_answers_last4_no_digits check (
    slot <> 'account_last4'
    or (digit_count is not null and value is null)
    or (digit_count is null and value in ('dont_know', 'refused'))
  ),
  -- Every other slot carries its value as text and never a digit count.
  constraint call_answers_text_value check (
    slot = 'account_last4' or (value is not null and digit_count is null)
  )
);
create index call_answers_call_idx on voice.call_answers (call_id);

-- updated_at housekeeping (voice's own, so the schema stands alone).
create function voice.touch_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at := now();
  return new;
end
$$;
create trigger calls_touch before update on voice.calls for each row execute function voice.touch_updated_at();

-- Access: everyone signed in reads (the dashboard); the voice service writes as service_role,
-- which bypasses RLS (_bootstrap). Same shape as the app schema.
alter table voice.calls        enable row level security;
alter table voice.call_answers enable row level security;

create policy calls_read        on voice.calls        for select to authenticated using (true);
create policy call_answers_read on voice.call_answers for select to authenticated using (true);

revoke all on all tables in schema voice from public, anon, authenticated, service_role;
grant select on all tables in schema voice to authenticated, service_role;
grant insert, update on voice.calls, voice.call_answers to service_role;
