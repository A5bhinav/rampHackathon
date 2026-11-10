# 0015: A vendor network beside each customer's Trust Ledger

**Status:** Proposed, Nov 9, 2026. Nothing is built. It changes ADR-8 in one way: one fingerprint
key for every customer, held in a key-management service, and the fingerprint computed inside Ramp
over the full number. Until a spec implements that, today's fingerprint stays (spec 0002, D4).
**Changed by spec 0045 (PR #93):** shared trust for **phone numbers** is no longer deferred. A shared
vendor profile keeps an ordered list of trusted numbers, which a bank-change case calls in order.
Trust enters that list only through an onboarding anchor or a call to a number already on it, never
by counting customers. Item 4 below says what that answers. Trust in bank accounts and Ramp-signed
identity stay deferred.
**Needs sign-off from:** Inesh (Trust Ledger), Abhi (core, evidence, ADR-8, decision engine), and
every contract owner (decision item 3 adds a `network` facts block).

## Context

Inside Ramp, Dialback could know a vendor across every customer that pays it. Today it knows each
vendor once per customer and cannot connect the copies.

**Each customer is computed on its own.** Every Trust Ledger table carries `dataset_id`, and a
number selection replays from that one customer's observations
(`packages/ledger/src/dialback_ledger/select.py`). A number can be called only if its first clean
sighting reached this customer at least 90 days before the cutoff, and it has been seen in at least
two independent source types (spec 0021). The cutoff is the earliest of the change in Ramp, the
change request and the first message from a lookalike domain.

**But access is not separated yet.** Every `app` and `ledger` read policy is `using (true)` for any
signed-in user (`supabase/migrations/app/0001_app_schema.sql`, the RLS block). There is no tenant
model. Anything below that promises customers privacy depends on building one first.

**New relationships start cold.** A customer that joined Ramp last month, or a vendor it has paid for
under 90 days, has no provable number. No call is placed and a person decides (R7). Spec 0041
(proposed in PR #91, shadow mode only) works on the same gap for a vendor's first sight, with
outside anchors.

**Dialback cannot link a vendor or an account across customers.**

- Ramp's API, as mocked, returns the routing number and last four digits, never the full account
  number, and no tax ID. Whether real Ramp can return a full number is spec 0002's open Q1.
- So the bank fingerprint is `HMAC(key, routing|last4|ramp_account_id)`
  (`apps/core/src/dialback_core/detection.py`). Ramp's account ID belongs to one record in one
  customer's Ramp. The same real account gets a different fingerprint at each customer, and even
  for two vendors at the same customer (spec 0002, D4).
- Routing number plus last four is not an identity. It narrows an account to about one in 10,000
  of that bank's accounts, which at a large bank is thousands of accounts.
- `app.vendors.tin_hash` exists, but nothing fills it.

**Ramp itself has what linking needs:** the full account number, the tax ID, and every customer's
payment history.

**Assumed threat: one takeover, many customers.** When a vendor's mailbox is taken over (the threat
ADR-13 assumes), the attacker can send new bank details to every customer of that vendor at about
the same time. This record assumes that pattern; the repo has no data on how often it happens.

**The design doc points this way.** Its "Future plug-ins" table (pages 45 and 46) lists a *network alarm*
(warn other Ramp customers who pay the same vendor when its mailbox looks compromised) and
*Ramp-signed identity* (a vendor already on Ramp proves its bank account with a Ramp-signed
statement instead of a call). This record adopts the first. It does not adopt the second, because
skipping the call is exactly what item 4 defers.

The question: with Ramp's internal data, should Dialback combine what it knows about a vendor
across customers, and how, without weakening R1 to R9?

## Decision

In short: Ramp links vendors and accounts on keyed fingerprints. A separate network layer shares
**warnings** between customers, which can only send a case to a person. Sharing **trust**, which
could make a case easier, is deferred until the problems in item 4 have answers.

### 1. Link on fingerprints computed inside Ramp

Dialback never receives or stores a full account number or tax ID for the network. Ramp computes
the fingerprints where the plaintext already lives and hands over only the fingerprints.

- **Bank account:** `HMAC-SHA256(network_key, routing || full account number)`.
- **Tax ID:** `HMAC-SHA256(network_key, normalized tax ID)`, into `app.vendors.tin_hash`.
- **One key for every customer**, so one account gives one value everywhere. The key lives in a
  key-management service and is versioned, as `fingerprint_key_version` is today. Only a Ramp-side
  computation can re-fingerprint when the key rotates; Dialback never holds the numbers to redo it.
- **The network stores fingerprints alone.** No routing number or last four sits next to them.
  Today's tables keep routing and last four beside the fingerprint, so a leaked key would leave a
  10 to 12 digit account number only 10^6 to 10^8 guesses away. A tax ID is 9 digits, about 10^9
  guesses, and for a sole proprietor it is a Social Security number.

Example: Customers A and B both pay Acme into routing `021000021`, account `000011112222`. Ramp
gives both records the same fingerprint, and Dialback never sees `000011112222`.

Why Dialback should not hold the full numbers:

- A routing number plus an account number is enough to debit an account or forge a check. A table
  of every vendor's numbers across every customer would be the most valuable thing in the system to
  steal.
- Dialback already handles documents that can carry full numbers. Change requests and invoices are
  stored as documents, and their visible text goes to the document reader. The network should not
  add a second, structured copy of every number, in the same database the models' inputs come from.

### 2. The network is a layer beside each ledger, never merged into it

- **The ledger keeps its rule.** A number is callable because it reached this customer through the
  customer's own channels, by our own clock, before the cutoff (spec 0021). The network never adds
  sightings to a customer's ledger. Spec 0045's shared profile can supply the number a case calls,
  but as its own labeled selection (`profile-select-v1`), with the ledger's own pick kept beside it.
- **The network gives the decision labeled inputs:** a `network` facts block, read by code (R3).
- **Combine evidence, never trust.** Pooling "this account was seen at another customer" is
  evidence. Pooling "another customer trusted this" is the attack.

Merged profiles are rejected because a document at one customer would quietly change what another
customer calls. The attack: a sham business on Ramp adds "Acme Fasteners" with the fraudster's
phone on its invoices and waits 90 days. In a merged profile, that number is now old enough for
every customer of the real Acme.

### 3. Warning signals, and what each one does

Every signal has one defined effect. Three send the case to review through a new Needs review rule
in `decide()`; none can produce Verified or Not verified. One is shown to the approver and changes
nothing.

| Signal | Example | Effect |
|---|---|---|
| **Account linked to another vendor.** The new account is on file for a different vendor (by tax ID fingerprint) at a long-standing customer | B's new "Acme" account is another vendor's account at some other customer | Review |
| **Network alarm.** A case for the same vendor ended without verification at a long-standing customer in the last 30 days | Another customer's call to Acme ended Not verified last week | Review |
| **Burst of bank changes.** Several customers of one vendor changed that vendor's bank details within 72 hours, to any account | 5 of Acme's customers changed Acme's account within two days | Review. Keyed on the vendor, not the account, so a separate mule account per victim doesn't evade it |
| **New to the network.** The account has never been seen at any other customer | A first sighting anywhere | Shown to the approver; no effect on the outcome |

Rules for every signal:

- **Only long-standing customers count.** A customer counts toward a signal if it has been on Ramp
  for at least a year with real spend, shares no admins, users, email domains or bank accounts with
  the customer receiving the signal or with the other customers counted, and was not created
  within 90 days of the vendor's first sighting on the network. This applies to every signal, so a
  sham customer cannot trigger any of them. The exact thresholds are open question 1.
- **Signals are computed only on a real bank-change case.** No API lets anyone ask the network
  about an account. Otherwise a customer, or a Ramp account a fraudster controls, could enter
  guessed account numbers and read which ones are real and whose they are.
- **Approvers see signals; the person who entered the change does not.** An insider who sees which
  signal fired learns how to avoid it next time. This needs the tenant and role model from the
  context section.
- **When the network is on but can't be read, the case goes to review (R7).** The evidence records
  "network unavailable" separately from "no network facts". With the flag `off`, a case is decided
  exactly as today.
- **A signal never places a hold on its own.** R1 already holds every changed account. A network
  alarm affects the next case for that vendor at each customer; it never holds a customer that has
  no case, because there would be nothing to release through `release_case()`.

### 4. Deferred: signals that would make a case easier

These would solve cold start, which is the main reason to share at all. Each has a problem with no
answer yet, so none is part of this decision. Each needs its own record.

- **"Paid into for months with no returns."** An earlier fraud looks exactly like this. Customers
  fooled months ago are still paying the mule account, and mule accounts rarely produce returns.
  It also pools other customers' decisions to trust the account, which item 2 calls the attack.
- **"Phone number proven at long-standing customers", offered to a cold-start customer.** Adopted
  in a different form by spec 0045, which answers the three problems below:
  - Independent customers do not mean independent sources. The design doc's known limit (page 21)
    is that someone who has controlled a vendor's mailbox and a public listing for 90 days gets
    their number picked. Every customer of that vendor sees those same channels, so "proven at 4
    customers" can be one compromise seen 4 times. *Spec 0045 never counts customers.* A shared
    number comes from an onboarding anchor (registry and website, spec 0041) or from a call to a
    number already trusted.
  - Today those customers go straight to a person; this would call instead. *Spec 0045 calls, and a
    profile change or new link in the last 7 days sends the case to review (NR22).*
  - It also can't be dialed without changing V01 ("We dialed the number the Trust Ledger chose")
    and the voice service's dial guard, which dials only the ledger's pick. *Spec 0045 writes the
    pick as a selection in the ledger's table and has the voice service dial the selection the case
    names, so V01 holds as written.*
- **Ramp-signed identity** instead of a call (the design doc's plug-in), for the same reasons.

### 5. Customers see derived facts, never another customer's data

- **Signals are shown coarsely.** No counts below 5 (shown as "fewer than 5"), dates by month,
  never another vendor's or customer's name.
- **Only "ended without verification" is shared.** Never which rule fired: NV01, for example,
  would reveal that the vendor denied the change.
- **Call recordings and answers never leave the customer.** The vendor agreed to a recorded call
  for one customer.
- **Joining the network is opt-in** in the customer terms. Whether vendors must also be told or
  agree is open question 5.

### 6. Evidence

- **Each case's evidence envelope records the network inputs it used:** the signal, counts, ages in
  whole days, the network rule version and a hash of the network snapshot. All are integers or
  strings, since the evidence chain refuses floats. It never records another customer's identity.
- **The network keeps its own hash-chained log (ADR-7) of each snapshot,** so Ramp can prove what
  the network said when a case was decided.

### 7. Order of work

1. **Prerequisites:** a tenant model with per-customer access, and Ramp-side fingerprints (item 1).
2. **The warning signals in item 3,** behind a new flag `DIALBACK_NETWORK` with values `off` and
   `warn`, safe default `off`. They need the `network` facts block in the contracts and the new
   Needs review rule in the decision engine.
3. **Anything from item 4** only through its own record, after attack-lab scenarios with sham
   customers.

## Consequences

Good:

- One customer's catch protects the vendor's other customers when one takeover hits many of them.
- A mule account reused across vendors is caught the second time it appears.
- A burst of bank changes for one vendor is caught even when each victim gets its own account.
- Dialback still holds no full account numbers, and the network adds none.

Costs and risks:

- **Cold start stays.** This record shares warnings only. Spec 0041's anchors remain the path for a
  vendor's first sight.
- **Honest shared accounts look like mule accounts.** Factoring companies, lockboxes, payment
  processors and parent companies all collect for more than one vendor. They trip the linked-account
  signal and go to review. A reviewed list of known shared-account holders is open question 6.
- **Honest bank moves look like the attack.** A vendor that tells all its customers about a new
  bank on the same day trips the burst signal and goes to review. Accepted: this fails closed (R7).
- **False alarms can be used as a weapon.** A long-standing customer, or an attacker inside one,
  could cause reviews at a vendor's other customers. The cost is extra reviews, never a block or a
  release.
- **Audits get harder.** B's evidence cites network facts that B's auditor cannot inspect. The
  network-side log covers this, but only Ramp can open it.
- **Facts go stale.** Vendors change banks, so network facts need an expiry (open question 7).
- **The key becomes critical.** It lives in a key-management service and rotates by version.
- **It needs Ramp's internals and a tenant model.** Dialback on Ramp's public API cannot build
  item 1, and today's read-everything access rules cannot keep item 5's promises.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Give Dialback full account numbers and tax IDs, and merge profiles in the Trust Ledger | Item 1 gives the same linking without readable numbers; item 2 explains why merging breaks the selection rule |
| Fingerprint inside Dialback as each number arrives, then discard it | The full number still passes through Dialback's memory, logs and traces, and Dialback can't re-fingerprint after a key rotation |
| Link vendors by name, email domain and phone with today's data | Weak, and the easiest identifiers to fake |
| Share trust as well as warnings now | Item 4: each friction-lowering signal has a known way to fail |
| Do nothing | Every customer has to catch a takeover that hits many customers on its own |

## Open questions

1. **Thresholds:** how long on Ramp and how much spend make a customer long-standing; the burst
   window and count; the alarm window.
2. **Independence:** which links between customers disqualify them from counting together (shared
   users, admins, email domains, bank accounts, owners).
3. **Where fingerprints come from:** a Ramp-side source that returns network fingerprints only.
   Ramp's public API has none today.
4. **Rules on stored account data:** whether payment-industry rules on storing account numbers
   (Nacha's) treat keyed fingerprints as unreadable, to be confirmed.
5. **Consent:** the customer opt-in wording, and whether vendors must be told or agree.
6. **Shared-account holders:** how factoring companies, lockboxes and processors are recognized,
   and who maintains that list.
7. **Expiry:** how long a network fact lasts.
8. **One model for the first trusted number:** how item 4, if it ever ships, meets spec 0041's
   anchors and any shared vendor profile or portal (none is in the repo yet).
