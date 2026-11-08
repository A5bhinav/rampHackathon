"""One fictional vendor's document history, with a record of every phone number planted in it.

Deterministic: ids are UUIDv5 of the scenario and a counter, and every choice comes from a
`random.Random` seeded by the scenario's slug. Documents are a page model (spans with position,
size and colour, the facts pdfplumber reports) plus custody. All values are fictional: phones
in 555-0100 to 555-0199, domains under `.example`, bank accounts only as "ending 4821".
"""
from __future__ import annotations

import datetime as dt
import random
import uuid
from dataclasses import dataclass

NAMESPACE = uuid.UUID("5d0c8f2e-3b7a-4c1d-9e6f-0a2b4c6d8e10")
T0 = dt.datetime(2026, 10, 1, 16, 0, tzinfo=dt.UTC)  # every change request arrives at T0 unless moved
WIDTH, HEIGHT = 612, 792
HIDE = {"white": {"color": "#ffffff"}, "tiny": {"size": 1}, "off_page": {"x": WIDTH + 90}}
TRADES = ["Fasteners", "Packaging", "Logistics", "Industrial Supply", "Coatings", "Electrical", "Facilities", "Print Works"]


def days(n: float) -> dt.timedelta:
    return dt.timedelta(days=n)


def stamp(t: dt.datetime) -> str:
    return t.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def near_miss(a: str, b: str) -> bool:
    """One digit off, or two adjacent digits swapped (the ledger-select-v1 near-miss check)."""
    if a == b or len(a) != len(b):
        return False
    diff = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
    if len(diff) == 1:
        return True
    return len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]]


@dataclass(frozen=True)
class Phone:
    """A number to plant: where it shows, what it is, and whether it's the vendor's own."""
    e164: str
    role: str = "main"            # main, ar, billing, fax
    truth: str = "real"           # real or poison
    party: str = "vendor"
    label: str = "Main"

    def text(self, style: int) -> str:
        area, exch, line = self.e164[2:5], self.e164[5:8], self.e164[8:]
        return [f"({area}) {exch}-{line}", f"{area}-{exch}-{line}", f"{area}.{exch}.{line}"][style % 3]


class Vendor:
    def __init__(self, slug: str, name: str, area: str) -> None:
        self.slug, self.area = slug, area
        self.rng = random.Random(f"dialback-corpus:{slug}")
        self.name = f"{name} {self.rng.choice(TRADES)}"
        self.base = name.lower()
        self.domain = f"{self.base}.example"
        self.docs: list[dict] = []
        self.sightings: list[dict] = []
        self._invoice = 1000 + self.rng.randrange(100, 900)

    @property
    def lookalike(self) -> str:
        """A domain a person would misread: one letter swapped for a lookalike, or doubled."""
        for a, b in (("l", "1"), ("o", "0"), ("i", "1"), ("m", "rn")):
            if a in self.base:
                return self.base.replace(a, b, 1) + ".example"
        return self.base + self.base[-1] + ".example"

    def phone(self, last2: int, **kw) -> Phone:
        assert 0 <= last2 <= 99
        return Phone(f"+1{self.area}55501{last2:02d}", **kw)

    def doc(self, kind: str, channel: str, at: dt.datetime, lines: list, *, sender: str | None = None,
            thread: str | None = None, subject: str | None = None, parent: str | None = None,
            claimed: str | None = None, header_date: str | None = None, scan: bool = False) -> str:
        """Add one document. A line is text, a Phone, or (Phone, hide) with hide in HIDE."""
        doc_id = str(uuid.uuid5(NAMESPACE, f"{self.slug}:{len(self.docs) + 1}"))
        spans, y = [], 72
        for line in lines:
            hide = None
            if isinstance(line, tuple):
                line, hide = line
            if isinstance(line, Phone):
                text = f"{line.label}: {line.text(self.rng.randrange(3))}"
                self.sightings.append({"e164": line.e164, "truth": line.truth, "document_id": doc_id, "channel": channel,
                                       "received_at": stamp(at), "visibility": hide or "visible", "role": line.role,
                                       "party": line.party, "quote": text})
            else:
                text = line
            span = {"text": text, "x": 72, "y": y, "size": 10, "color": "#000000"} | HIDE.get(hide, {})
            spans.append(span)
            y += 14
        self.docs.append({
            "id": doc_id, "kind": kind, "scan": scan, "claimed_date": claimed, "header_date": header_date,
            "custody": {"channel": channel, "received_at": stamp(at), "sender": sender, "thread_id": thread,
                        "subject": subject, "parent_document_id": parent},
            "pages": [{"width": WIDTH, "height": HEIGHT, "spans": spans}],
        })
        return doc_id

    def email(self, at: dt.datetime, lines: list, *, sender: str | None = None, thread: str | None = None,
              subject: str = "Hello", attachment: list | None = None, header_date: str | None = None) -> str:
        sender = sender or f"ar@{self.domain}"
        thread = thread or f"thr-{self.slug}-{len(self.docs) + 1}"
        mail = self.doc("email", "email", at, lines, sender=sender, thread=thread, subject=subject,
                        header_date=header_date or stamp(at))
        if attachment is not None:
            self.doc("invoice", "email", at, attachment, sender=sender, thread=thread, subject=subject, parent=mail)
        return thread

    def history(self, main: Phone, ar: Phone, *, start: dt.datetime, contract: bool = True, listing: bool = True,
                portal: bool = True, invoices: int = 6) -> str:
        """Years of ordinary business. Returns the last invoice's thread, for a hijack."""
        if contract:
            self.doc("contract", "contract", start, [f"Master supply agreement: {self.name}", main, ar,
                                                    f"Notices: legal@{self.domain}"])
        if listing:
            for k in (90, 455):
                if start + days(k) < T0 - days(30):  # history is before the request, never after it
                    self.doc("listing", "listing", start + days(k), [f"{self.name}: business listing", main])
        if portal:
            self.doc("portal_entry", "vendor_portal", start + days(300), [f"Vendor profile: {self.name}", ar,
                                                                         Phone(ar.e164, "billing", label="Billing")])
        step = (T0 - days(30) - (start + days(30))) / max(invoices, 1)
        thread = ""
        for n in range(invoices):
            self._invoice += 1
            at = start + days(30) + step * n
            subject = f"Invoice {self._invoice}"
            thread = self.email(at, [f"Hello, please find invoice {self._invoice} attached.", "Thank you", ar, main],
                                subject=subject, attachment=[f"INVOICE {self._invoice}", self.name, "Remit questions to:", ar])
        return thread

    def request(self, at: dt.datetime = T0, *, extra: list = (), sender: str | None = None, thread: str | None = None,
                subject: str = "Updated banking details") -> str:
        """The change request: new remittance details, never a full account number."""
        thread = self.email(at, ["We have moved our banking. Please pay the account ending 4821 from today.",
                                 "Thank you", *extra], sender=sender, thread=thread, subject=subject)
        self.request_id = self.docs[-1]["id"]
        return thread
