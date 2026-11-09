# docs

| Path | What |
|---|---|
| `Dialback_System_Design.pdf` | The design document (Oct 31, 2026). The source of truth for every README in this repo. |
| `BBS_x_Ramp_Hackathon_Info.pdf` | The hackathon deck: prompt, rounds, dates, rubric. |
| `state-machine.md` | The case state machine, generated from `app.case_transitions` by `scripts/db/render_state_machine.py`. Never edit by hand. |
| `live-call.md` | The first live test call: setup, pre-flight, what the person answering says, and what each failure looks like. |
| `threat-model.md` | STRIDE and OWASP LLM Top 10 tables, and the prompt-injection design. |
| `adr/` | Architecture decision records in Michael Nygard's format. Numbered once, never reused; a reversed decision is marked superseded, never deleted. |
| `c4/` | The container view: six parts we build, four outside systems. |
| `sequences/` | The main flow, one bank change from detection to release. |
| `design.md` | To add: a Markdown export of the PDF so agents can grep it. |

## Judging rubric (draft weights, subject to Ramp confirmation)

35% technical execution, 25% problem fit, 20% product polish, 10% creativity, 10% presentation.

## The demo (recorded Nov 9, live Nov 19)

1. Open on the vendor risk list: how many vendors' "number on file" can't be traced to any document.
2. A bank-change email arrives for one vendor, and payments to it go on hold.
3. The lineage panel shows which number Dialback will call and why, and why the number in the email is rejected.
4. The live call: a teammate answers on a team phone, the AI discloses itself, and the vendor confirms.
5. Code decides Verified. The person who entered the change can't release it, so a second person does.
6. The evidence log: one row edited by hand, and the verify job points straight at it.
7. The scorecard: 300 attacks with the false-Verified count and its bound, plus one attack run live.

Recorded clips of each step are the backup if the phone network or Wi-Fi fails on stage.
