"""The demo's ledger seed (spec 0025): the three vendors of scripts/demo/seed.json.

Built with the same vendor-history builder as the frozen scenarios, against the same fixed
anchor (T0). The loader shifts every receive time by (load time - anchor), so the history always
ends just before the demo. Not part of the frozen truth: the demo can change without moving it.

  acme        an ordinary history; the request replies on a real invoice thread   -> its AR line
  brightline  known only by email                                                  -> no provable number
  cobalt      the request hijacks an invoice thread and offers a one-digit-off line -> its real AR line
"""
from __future__ import annotations

from world import T0, Vendor, days, stamp

PATH = "../packages/ledger/src/dialback_ledger/demo_seed.json"  # relative to corpus/; ships in the ledger package
START = T0 - days(1300)
VENDORS = (
    ("acme", "Acme", "Acme Fasteners LLC", "206"),
    ("brightline", "Brightline", "Brightline Office Supply Inc", "312"),
    ("cobalt", "Cobalt", "Cobalt Freight LLC", "617"),
)


def _vendor(ref: str, base: str, name: str, area: str) -> dict:
    v = Vendor(f"demo-{ref}", base, area)
    v.name = name
    main = v.phone(17, role="main", label="Main")
    ar = v.phone(42, role="ar", label="Accounts receivable")
    phones = {"main": main.e164, "ar": ar.e164}
    if ref == "brightline":
        v.history(main, ar, start=START, contract=False, listing=False, portal=False)
        v.request()
    else:
        thread = v.history(main, ar, start=START)
        subject = "Re: " + v.docs[-1]["custody"]["subject"]
        if ref == "acme":
            # No phone in the request: a team phone swapped in at load never needs to appear in it.
            v.request(thread=thread, subject=subject)
        else:
            offered = v.phone(43, role="ar", truth="poison", label="AR direct")
            phones["offered"] = offered.e164
            v.request(thread=thread, subject=subject, extra=["Questions? Call me directly.", offered])
    request = next(d for d in v.docs if d["id"] == v.request_id)
    return {"name": name, "domain": v.domain, "phones": phones, "request": request,
            "documents": [d for d in v.docs if d["id"] != v.request_id]}


def demo_seed() -> dict:
    return {"v": 1, "anchor": stamp(T0),
            "about": "Fictional ledger documents for the demo seed's vendors (spec 0025). Receive times are "
                     "shifted by (load time - anchor). Written by corpus/generator/build.py; never edit by hand.",
            "vendors": {ref: _vendor(ref, base, name, area) for ref, base, name, area in VENDORS}}
