"""`dialback-ledger demo`: the demo's Trust Ledger (spec 0025, Q36 and Q37).

    dialback-ledger demo load [--dataset ID] [--phone REF=+1...]... [--with-requests] [--pdf [DIR]]
    dialback-ledger demo request --vendor REF [--dataset ID]
    dialback-ledger read-pdf FILE

`--pdf` (spec 0092) reads each vendor's history from real PDFs instead of the JSON: the committed ones, or the
ones in DIR. It writes the same observations, apart from each document's hash and `extractor_version`. A team
phone is rendered into the PDFs (`lab pdf render --demo --phone REF=+1... --out DIR`), so `--pdf` with `--phone`
is refused, and so is a DIR under `DIALBACK_READER=llm`: a team phone never reaches a model (spec 0049). The
change request stays JSON: it's an email.

`read-pdf FILE` (spec 0092) prints each line of a real PDF with its visibility, then the phone numbers code
found in it. It labels and stores nothing.

Run `load` after `dialback-core demo reset`; it writes each seeded vendor's history and own
domain into the current demo dataset. At beat 2, run `request --vendor REF` right before
`dialback-core demo change --vendor REF`, so the change-request email arrives first.

`--phone REF=+1...` swaps that vendor's fictional AR line for a team phone in the stored rows,
so the rule, the lineage panel and the voice service all see the same number. The reader labels
the documents as the seed wrote them, so a team phone never reaches a model (spec 0049). Give it
on the command line only: team numbers are never committed.

Uses DATABASE_URL, like the core, and DIALBACK_READER (ANTHROPIC_API_KEY for `llm`). Every check
runs before anything is written.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import datetime as dt
import json
import os
import sys
from importlib.resources import files
from pathlib import Path
from uuid import UUID

import phonenumbers as pn
import psycopg
from psycopg.rows import dict_row

from .custody import document_sha256
from .flags import ReaderMode, reader_mode
from .ingest import IngestResult, ingest_all
from .reader import Reader, ReaderFailed, ReaderUnavailable, reader_for
from .store import add_vendor_domain, mark_change_request, stored_id, write_ingest


class DemoError(Exception):
    """A refusal, said to the operator; nothing was written."""


def load_seed() -> dict:
    return json.loads(files(__package__).joinpath("demo_seed.json").read_text(encoding="utf-8"))


def committed_pdfs() -> Path:
    """The demo seed's history as PDFs, fictional numbers only (spec 0092); `lab pdf render --check` keeps them current."""
    return Path(str(files(__package__).joinpath("demo_pdfs")))


def _when(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s).astimezone(dt.UTC)


def _formats(e164: str) -> list[str]:
    area, exch, line = e164[2:5], e164[5:8], e164[8:]
    return [f"({area}) {exch}-{line}", f"{area}-{exch}-{line}", f"{area}.{exch}.{line}"]


def _prepare(doc: dict, delta: dt.timedelta) -> dict:
    """Shift a document's receive time by `delta`."""
    d = copy.deepcopy(doc)
    d["custody"]["received_at"] = (_when(d["custody"]["received_at"]) + delta).isoformat()
    return d


def _swap_text(text: str, swap: tuple[str, str]) -> str:
    """Replace the fictional number `swap[0]` with the team phone `swap[1]`, in each format the seed writes."""
    old, new = swap
    pairs = list(zip(_formats(old), _formats(new))) if new.startswith("+1") and len(new) == 12 else \
        [(f, new) for f in _formats(old)]
    for a, b in pairs:
        text = text.replace(a, b)
    return text


def with_phone(doc: dict, swap: tuple[str, str]) -> dict:
    """A copy of `doc` with the team phone in its text: what the stored rows describe."""
    d = copy.deepcopy(doc)
    for page in d["pages"]:
        for span in page["spans"]:
            span["text"] = _swap_text(span["text"], swap)
    return d


def labeled(documents: list[dict], reader: Reader, swap: tuple[str, str] | None = None,
            pdfs: dict[str, bytes] | None = None) -> IngestResult:
    """Ingest documents with `reader`, storing its version as `extractor_version`. The reader sees
    the documents as the seed wrote them, so a team phone never reaches a model (spec 0049, decision
    5). With `swap` = (fictional AR line, team phone), the team phone then goes into the rows: the
    numbers, the quotes and the documents' hashes, exactly as if it had been in the text.

    With `pdfs` (by document id) each document is read from its PDF (spec 0092).

    A document the reader can't label, or a PDF it refuses, stops everything (spec 0049, decision 4):
    the DemoError rolls back the caller's transaction, so nothing is written."""
    failures: tuple[type[Exception], ...] = (ReaderFailed, ReaderUnavailable)
    if pdfs is not None:
        from .pdf import PdfRefused
        failures += (PdfRefused,)
    try:
        result = ingest_all(documents, extractor_version=reader.version, reader=reader, pdfs=pdfs)
    except failures as exc:
        raise DemoError(f"{exc}; nothing was written") from None
    if swap is None:
        return result
    old, new = swap
    hashes = {d["id"]: document_sha256(with_phone(d, swap)) for d in documents}
    for row in result.documents:
        row["sha256"] = hashes[row["id"]]
    for o in result.observations:
        if o["value_normalized"] == old:
            o["value_normalized"] = new
        if o["quote"] is not None:
            o["quote"] = _swap_text(o["quote"], swap)
    return result


def _reader(reader: Reader | None) -> Reader:
    """The given reader, else the one DIALBACK_READER names; `llm` without its key refuses to start."""
    if reader is not None:
        return reader
    try:
        return reader_for(reader_mode())
    except ReaderUnavailable as exc:
        raise DemoError(str(exc)) from None


def team_phones(given: dict[str, str], seed: dict) -> dict[str, str]:
    out = {}
    for ref, raw in given.items():
        if ref not in seed["vendors"]:
            raise DemoError(f"--phone {ref}=…: the demo seed has no vendor {ref!r} (it has {', '.join(seed['vendors'])})")
        try:
            n = pn.parse(raw, None)
        except pn.NumberParseException:
            n = None
        if n is None or not pn.is_valid_number(n):
            raise DemoError(f"--phone {ref}=…: not a valid phone number in E.164 form (+1…)")
        out[ref] = pn.format_number(n, pn.PhoneNumberFormat.E164)
    return out


def _is_committed(pdf: Path) -> bool:
    return pdf.resolve() == committed_pdfs().resolve()


def _check_pdf(pdf: Path, phones: dict[str, str], seed: dict) -> None:
    """Every check `--pdf` makes (spec 0092, D9), before anything is written."""
    from . import legibility
    try:
        legibility.require_ocr()
    except ReaderUnavailable as exc:
        raise DemoError(f"--pdf: {exc}") from None
    if phones:
        raise DemoError("--pdf with --phone: the phone is already in the PDFs "
                        "(render them with `lab pdf render --demo --phone REF=+1... --out DIR`)")
    if reader_mode() is ReaderMode.LLM and not _is_committed(pdf):
        raise DemoError("--pdf DIR under DIALBACK_READER=llm: a folder given at run time may hold a team phone, and a "
                        "team phone never reaches a model (spec 0049); use the stub reader, or the committed PDFs")
    for v in seed["vendors"].values():
        for d in v["documents"]:
            if not (pdf / f"{d['id']}.pdf").is_file():
                raise DemoError(f"--pdf {pdf}: no PDF for document {d['id']}")


def check(phones: dict[str, str], requests: list[str], pdf: Path | None = None) -> dict[str, str]:
    """Every check `load --phone`, `load --pdf` and `request --vendor` make, without touching a database, so a
    caller (`dialback-core demo prepare`) can refuse before it resets anything. Returns the phones in E.164."""
    _reader(None)                    # DIALBACK_READER=llm without its key refuses here too (spec 0049)
    seed = load_seed()
    for ref in requests:
        if ref not in seed["vendors"]:
            raise DemoError(f"the demo seed has no vendor {ref!r} (it has {', '.join(seed['vendors'])})")
    if pdf is not None:
        _check_pdf(pdf, phones, seed)
    return team_phones(phones, seed)


async def _dataset(conn: psycopg.AsyncConnection, dataset_id: UUID | None, seed: dict) -> tuple[UUID, dict[str, UUID]]:
    if dataset_id is None:
        row = await (await conn.execute("select id, demo_refs from app.datasets where kind = 'demo' and retired_at is null "
                                        "order by created_at desc limit 1")).fetchone()
        if row is None:
            raise DemoError("there is no current demo dataset: run `dialback-core demo reset` first")
    else:
        row = await (await conn.execute("select id, demo_refs from app.datasets where id = %s and kind = 'demo' "
                                        "and retired_at is null", (dataset_id,))).fetchone()
        if row is None:
            raise DemoError(f"{dataset_id} is not a current demo dataset")
    refs = (row["demo_refs"] or {}).get("vendors") or {}
    if not refs:
        raise DemoError("this demo dataset has no vendor refs: was it made by `dialback-core demo reset`?")
    vendors = {}
    for ref in seed["vendors"]:
        if ref in refs:
            v = await (await conn.execute("select id from app.vendors where dataset_id = %s and ramp_vendor_id = %s",
                                          (row["id"], refs[ref]))).fetchone()
            if v is None:
                raise DemoError(f"vendor {ref!r} isn't imported into the demo dataset yet")
            vendors[ref] = v["id"]
    if not vendors:
        raise DemoError("none of the demo seed's vendors are in this dataset")
    return row["id"], vendors


async def _request(conn, seed: dict, dataset_id: UUID, ref: str, vendor_id: UUID, now: dt.datetime,
                   reader: Reader) -> bool:
    req = seed["vendors"][ref]["request"]
    exists = await (await conn.execute("select 1 from ledger.change_requests where dataset_id = %s and document_id = %s",
                                       (dataset_id, stored_id(dataset_id, req["id"])))).fetchone()
    if exists:
        return False
    doc = _prepare(req, now - _when(seed["anchor"]))
    await write_ingest(conn, dataset_id=dataset_id, vendor_id=vendor_id, result=labeled([doc], reader))
    await mark_change_request(conn, dataset_id=dataset_id, vendor_id=vendor_id, document_id=req["id"])
    return True


def _url(database_url: str | None) -> str:
    url = database_url or os.environ.get("DATABASE_URL")
    if not url:
        raise DemoError("DATABASE_URL is unset")
    return url


async def load(database_url: str | None = None, *, phones: dict[str, str], dataset_id: UUID | None = None,
               with_requests: bool = False, now: dt.datetime | None = None, reader: Reader | None = None,
               pdf: Path | None = None) -> dict:
    """Write each seeded vendor's history and domain (and, with `with_requests`, its request).
    `reader` defaults to the one DIALBACK_READER names. With `pdf`, the history is read from the PDFs in
    that folder (spec 0092)."""
    reader = _reader(reader)
    seed = load_seed()
    if pdf is not None:
        _check_pdf(pdf, phones, seed)
    swaps = team_phones(phones, seed)
    now = now or dt.datetime.now(dt.UTC)
    delta = now - _when(seed["anchor"])
    summary = {}
    async with await psycopg.AsyncConnection.connect(_url(database_url), row_factory=dict_row) as conn, conn.transaction():
        ds, vendors = await _dataset(conn, dataset_id, seed)
        for ref, vendor_id in vendors.items():
            v = seed["vendors"][ref]
            swap = (v["phones"]["ar"], swaps[ref]) if ref in swaps else None
            loaded = await (await conn.execute("select 1 from ledger.documents where id = %s",
                                               (stored_id(ds, v["documents"][0]["id"]),))).fetchone()
            docs = [_prepare(d, delta) for d in v["documents"]]
            pdfs = {d["id"]: (pdf / f"{d['id']}.pdf").read_bytes() for d in docs} if pdf is not None else None
            n = await write_ingest(conn, dataset_id=ds, vendor_id=vendor_id, result=labeled(docs, reader, swap, pdfs))
            await add_vendor_domain(conn, dataset_id=ds, vendor_id=vendor_id, domain=v["domain"])
            requested = with_requests and await _request(conn, seed, ds, ref, vendor_id, now, reader)
            # A reload adds nothing, so a different --phone can't take effect: say so (reset to change it).
            phone = "already loaded: unchanged" if loaded else ("team" if swap else "fictional")
            if not loaded and pdf is not None and not _is_committed(pdf):
                phone = "from the PDFs"
            summary[ref] = {"observations": n, "request": requested, "phone": phone}
    return {"dataset_id": str(ds), "vendors": summary}


async def request(database_url: str | None = None, *, vendor: str, dataset_id: UUID | None = None,
                  now: dt.datetime | None = None, reader: Reader | None = None) -> bool:
    """Ingest one vendor's change-request email and mark it (beat 2). False if it was already in.
    `reader` defaults to the one DIALBACK_READER names."""
    reader = _reader(reader)
    seed = load_seed()
    if vendor not in seed["vendors"]:
        raise DemoError(f"the demo seed has no vendor {vendor!r} (it has {', '.join(seed['vendors'])})")
    async with await psycopg.AsyncConnection.connect(_url(database_url), row_factory=dict_row) as conn, conn.transaction():
        ds, vendors = await _dataset(conn, dataset_id, seed)
        if vendor not in vendors:
            raise DemoError(f"vendor {vendor!r} isn't in this demo dataset")
        return await _request(conn, seed, ds, vendor, vendors[vendor], now or dt.datetime.now(dt.UTC), reader)


def read_pdf_command(path: Path) -> int:
    """`dialback-ledger read-pdf FILE`: each line with its visibility, then the phone candidates code found.
    Labels nothing and stores nothing."""
    from .candidates import find_phones
    from .pdf import PdfRefused, read_pdf
    from .text import lines
    try:
        pages = read_pdf(path.read_bytes())
    except PdfRefused as exc:
        print(f"refused: {exc.reason}", file=sys.stderr)
        return 2
    except ReaderUnavailable as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"refused: can't read {path} ({exc.strerror})", file=sys.stderr)
        return 2
    document_lines = lines({"pages": pages})
    for ln in document_lines:
        print(f"{ln.page + 1} {ln.visibility:<8} {ln.text}")
    print("phones:")
    for c in find_phones(document_lines):
        print(f"  {c.e164} {c.visibility}")
    return 0


def _pair(text: str) -> tuple[str, str]:
    ref, sep, phone = text.partition("=")
    if not sep:
        raise argparse.ArgumentTypeError("expected REF=+1…")
    return ref.strip(), phone.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dialback-ledger", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="cmd", required=True)
    rp = commands.add_parser("read-pdf", help="print a PDF's lines with their visibility, and its phone numbers")
    rp.add_argument("file", type=Path)
    demo = commands.add_parser("demo").add_subparsers(dest="action", required=True)
    ld = demo.add_parser("load", help="load the seeded vendors' history into the current demo dataset")
    ld.add_argument("--dataset", type=UUID)
    ld.add_argument("--phone", type=_pair, action="append", default=[], metavar="REF=+1...")
    ld.add_argument("--with-requests", action="store_true", help="also load every change request (rehearsals)")
    ld.add_argument("--pdf", nargs="?", const="", default=None, metavar="DIR",
                    help="read the history from real PDFs: the committed ones, or those in DIR (spec 0092)")
    rq = demo.add_parser("request", help="the change-request email for one vendor (beat 2)")
    rq.add_argument("--vendor", required=True)
    rq.add_argument("--dataset", type=UUID)
    args = parser.parse_args(argv)
    if args.cmd == "read-pdf":
        return read_pdf_command(args.file)
    try:
        if args.action == "load":
            pdf = None if args.pdf is None else (Path(args.pdf) if args.pdf else committed_pdfs())
            out = asyncio.run(load(phones=dict(args.phone), dataset_id=args.dataset, with_requests=args.with_requests, pdf=pdf))
            print(f"loaded the demo ledger into {out['dataset_id']}")
            for ref, s in out["vendors"].items():
                print(f"  {ref:<11} {s['observations']:>3} new sightings, AR line: {s['phone']}"
                      f"{', change request in' if s['request'] else ''}")
            print("next, at beat 2: dialback-ledger demo request --vendor REF, then dialback-core demo change --vendor REF")
        else:
            added = asyncio.run(request(vendor=args.vendor, dataset_id=args.dataset))
            print(f"{args.vendor}: change request {'in' if added else 'was already in'}; "
                  f"next: dialback-core demo change --vendor {args.vendor}")
    except DemoError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
