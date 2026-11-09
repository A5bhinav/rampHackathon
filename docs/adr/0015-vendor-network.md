# 0015: A vendor network beside each customer's Trust Ledger

**Status:** Proposed, Nov 9, 2026. Nothing is built. ADR-8's current wording stays in force until a
spec implements this record.
**Needs sign-off from:** Inesh (Trust Ledger), Abhi (core, evidence, ADR-8).

## Context

Inside Ramp, Dialback could know a vendor across every customer that pays it. Today it knows each
vendor once per customer, and cannot connect the copies.

**Each customer is on its own.** Every Trust Ledger table carries `dataset_id`, and a number
selection replays from that one customer's observations
(`packages/ledger/src/dialback_ledger/select.py`). A number can be called only if it was seen in at
least two independent source types at least 90 days before the change request (spec 0021,
`min_age_days = 90`).

**So new relationships start cold.** A customer that joined Ramp last month, or a vendor it has paid
for under 90 days, has no provable number. No call is placed and a person decides (R7). Spec 0041
(PR #91) attacks the same gap for a vendor's first sight, with outside anchors.

**Dialback cannot link a vendor across customers.**

- Ramp's API returns the routing number and last four digits, never the full account number, and
  no tax ID.
- So the bank fingerprint is `HMAC(key, routing|last4|ramp_account_id)`
  (`apps/core/src/dialback_core/detection.py`). Ramp's account ID is the record in one customer's
  Ramp, so the same real account at two customers gets two fingerprints.
- Routing number plus last four alone is not an identity: at one bank, about one account in 10,000
  shares any given last four.
- `app.vendors.tin_hash` exists, but nothing fills it.
- The core never reads a vendor's Ramp contacts, on purpose: the person changing the bank details
  could edit them too (R2).

**Ramp itself has what linking needs:** the full account number, the tax ID, and every customer's
payment history.

**Fraud often hits many customers at once.** When a vendor's mailbox is taken over (the threat
ADR-13 assumes), the same new bank details go to every customer of that vendor in the same week.

**The design doc already points this way.** Its later-features table (page 45) lists a *network
alarm* (a Not verified decision warns other Ramp customers who pay the same vendor) and *Ramp-signed
identity* (a vendor already on Ramp proves its bank account with a Ramp-signed statement).

The question: with Ramp's internal data, should Dialback combine what it knows about a vendor
across customers, and how, without weakening R1 to R9?

## Decision

### 1. Link on fingerprints computed where the plaintext lives

Dialback never stores a full account number or tax ID. It stores keyed fingerprints that match
across customers.

- **Bank account:** `HMAC-SHA256(network_key, routing || full account number)`, computed inside
  Ramp, or at the moment Dialback receives the number, which it then discards. This is ADR-8 as
  first written, with one key for every customer, so one account gives one value everywhere.
- **Tax ID:** `HMAC-SHA256(network_key, normalized tax ID)`, into `app.vendors.tin_hash`.
- **The key:** held in a key-management service and versioned, as `fingerprint_key_version` is
  today. Never in the database, and never in an env file next to it. Account numbers are short (an
  ACH entry allows at most 17 characters), so whoever holds both the key and the table can test
  guesses until one matches.

Example: Customers A and B both pay Acme into routing `021000021`, account `123456780001`. Both
records get the same fingerprint, and neither stores `123456780001`.

Why not the full numbers:

- A routing number plus an account number is enough to debit an account or forge a check. A table
  of every vendor's numbers across every customer would be the most valuable thing in the system to
  steal.
- Dialback feeds text to models: the document reader and the voice agent. With no full number in
  the system, no prompt, log or call can leak one (R4, R9).

### 2. The network is a layer beside each ledger, never merged into it

- **The ledger keeps its guarantee.** A number is callable because it reached this customer
  through the customer's own channels, by our own clock, before the request (R2). The network never
  adds sightings to a customer's ledger, and never changes which number the ledger picks.
- **The network answers its own questions.** It hands the decision engine labeled inputs (a
  `network` block beside the call answers), and code decides from them (R3).
- **Combine evidence, never trust.** This is the same rule as the shared-vendor-profile design
  under discussion: pooling sightings is fine; pooling "this number is trusted" is the attack.

Merged profiles are rejected because a document at one customer would quietly change what another
customer calls. The attack: a sham business on Ramp adds "Acme Fasteners" with the fraudster's
phone on its invoices and waits 90 days. In a merged profile, that number is now old enough for
every customer of the real Acme.

### 3. Network signals can raise risk freely, lower friction only under strict corroboration, and never skip the call or the release

| Signal | Example | Effect |
|---|---|---|
| The new account belongs to a different vendor elsewhere | B's new "Acme" account is Brightline's account at Customer C | Hold stays, case goes to review: a strong sign of a mule account |
| Another customer's case for this vendor ended Not verified recently | Customer C's call to Acme ended NV02 last week | Review. This is the design doc's network alarm |
| Many customers entered the same new account in a short window | 12 of Acme's customers entered an account ending 0001 within 48 hours | Review. This is the mailbox-takeover pattern, not agreement |
| The account is new to the network, or seen only at young customers | First sighting anywhere, or only at customers under 180 days old | Raises risk, nothing else |
| Long-standing, independent customers have paid into it for months with no returns | 5 customers, each over a year on Ramp, have paid Acme into it for 6 months | May lower friction (below) |
| A phone number is proven for this vendor at long-standing, independent customers | Acme's number seen 90+ days before the request at 4 such customers | May be offered as a candidate to a customer with no provable number |

Rules for the table:

- **"On file at N customers" is never evidence.** Entering an account costs an attacker nothing,
  and a takeover makes many customers enter the same one. Only money paid in over time counts.
- **Payments count only from long-standing, independent customers.** A sham customer can pay its
  own mule account for months. A customer counts only if it has been on Ramp for a long time with
  real spend, shares no admins, users, email domains or bank accounts with the other customers
  counted, and was not created near the vendor's first sighting. Thresholds are open question 1.
- **"Lower friction" has a ceiling.** At most, a customer with no provable number gets a candidate
  number to call. That number is labeled network evidence in the selection and still has to pass
  R2: a number seen in this change request or its thread is never called. Nothing auto-verifies.
  The hold stays until a person releases (R1, R5).
- **Without the network, a case is decided exactly as today.** If the network can't be read, the
  case gets no network input. No friction is lowered, and the per-customer rules still apply (R7).

### 4. Customers see derived facts, never another customer's data

- The network shares counts, ages and outcomes, for example: "paid into by 5 long-standing
  customers since March 2025". It never shares which customers they are, their documents,
  recordings or amounts.
- Joining the network is opt-in, in the customer terms.
- A vendor agreed to a recorded call for one customer. So call recordings and answers never leave
  that customer; only the outcome code does.

### 5. Evidence

- Each case's evidence envelope records the network inputs it used: the signal, counts, ages, the
  network rule version and a hash of the network snapshot. It never records another customer's
  identity.
- The network keeps its own hash-chained log (ADR-7) of each snapshot, so Ramp can prove what the
  network said when a case was decided.

### 6. Order of work

1. **Signals that only add holds:** the network alarm, and "this account belongs to another
   vendor". They can only send cases to review.
2. **Fingerprint linking inside Ramp**, with the Ramp-side computation in item 1 of this decision.
3. **Signals that lower friction, last.** Behind a flag (`DIALBACK_NETWORK`: `off`, `warn`,
   `full`; safe default `off`), and run through the attack lab with sham-customer scenarios before
   they ship.

## Consequences

Good:

- Cold start shrinks for any vendor that other customers already know.
- One customer's catch protects the vendor's other customers, which is how mailbox-takeover
  campaigns actually spread.
- A mule account reused across vendors is caught the second time it appears.
- Dialback still holds no full account numbers.

Costs and risks:

- **Sham customers.** The thresholds for "long-standing, independent" set what an attacker must
  spend to fake corroboration. They need the attack lab before step 3 ships.
- **False alarms used as a weapon.** A false fraud report could hold a vendor's payments at every
  customer. Network alarms send cases to review and never block, and they count independent
  reporters.
- **Honest bank moves look like the attack.** A vendor that moves banks and tells all its customers
  the same day matches the takeover pattern, so those cases go to review. Accepted: this fails
  closed (R7).
- **Audits get harder.** B's evidence cites network facts that B's auditor cannot inspect. The
  network-side log covers this, but only Ramp can open it.
- **Facts go stale.** Carriers reassign phone numbers and vendors change banks, so network facts
  need an expiry.
- **The key becomes critical.** If it leaks with the table, account numbers can be guessed. It
  lives in a key-management service and rotates by version.
- **It needs Ramp's internals.** Dialback on Ramp's public API cannot build item 1. This is a
  feature for inside Ramp.
- **It needs customer terms.** Opt-in wording needs legal review.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Store full account numbers and tax IDs in Dialback and merge profiles in the Trust Ledger | Item 1 gives the same linking without readable numbers; item 2 explains why merging breaks R2 |
| Link vendors by name, email domain and phone with today's data | Weak, and the easiest identifiers to fake. Domain and phone stay as signals, not as the link |
| Share only warnings (step 1 of item 6, and stop) | Viable, and the first step here. It leaves cold start unsolved |
| Do nothing | Cold start stays, and every customer has to catch a multi-customer campaign on its own. Spec 0041's anchors cover a vendor's first sight separately |

## Open questions

1. **Thresholds:** how many independent customers, how long on Ramp, and how many months of
   payments without returns.
2. **Independence:** which links between customers disqualify them from counting together (shared
   users, admins, email domains, bank accounts, owners).
3. **Where fingerprints come from:** a Ramp-side source that returns network fingerprints only.
   Ramp's public API has none today.
4. **Rules on stored account data:** whether payment-industry rules on storing account numbers
   (Nacha's) treat keyed fingerprints as unreadable, to be confirmed.
5. **Customer terms:** the opt-in wording, and which derived facts may be shown.
6. **Expiry:** how long a network fact lasts (number recycling, bank changes).
7. **One model for the first trusted number:** how this meets spec 0041's anchors and the shared
   vendor profile and portal under discussion.
8. **Alarm delivery:** whether the network alarm alerts other customers at once, or only affects
   their next case for that vendor.
