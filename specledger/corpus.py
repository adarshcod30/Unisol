"""The source corpus.

Datasheets are copyrighted, so we do NOT vendor the PDFs into the repo. We pin a
manifest of URLs + expected SHA-256 and fetch on setup. That keeps the repo clean,
keeps the eval reproducible (hash mismatch = the vendor silently revised the doc,
which is itself a signal a catalog system must handle), and keeps the demo offline
once fetched.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

import httpx

from . import config
from .models import SourceDoc


@dataclass
class ManifestEntry:
    doc_id: str
    url: str
    publisher: str
    authority: str
    title: str
    covers: tuple            # part numbers this document tabulates
    filename: str = ""

    def path(self):
        return config.FIXTURES / (self.filename or f"{self.doc_id}.pdf")


MANIFEST: list[ManifestEntry] = [
    ManifestEntry(
        "vishay-1n4001-4007", "https://www.vishay.com/docs/88503/1n4001.pdf",
        "Vishay", "MFR_DATASHEET", "Vishay 1N4001 thru 1N4007 Rectifier",
        ("1N4001", "1N4002", "1N4003", "1N4004", "1N4005", "1N4006", "1N4007"),
    ),
    ManifestEntry(
        "diodes-1n4001-4007", "https://www.diodes.com/assets/Datasheets/ds28002.pdf",
        "Diodes Incorporated", "MFR_DATASHEET", "Diodes Inc 1N4001-1N4007 Rectifier",
        ("1N4001", "1N4002", "1N4003", "1N4004", "1N4005", "1N4006", "1N4007"),
    ),
    ManifestEntry(
        "vishay-1n5817-5819", "https://www.vishay.com/docs/88525/1n5817.pdf",
        "Vishay", "MFR_DATASHEET", "Vishay 1N5817 thru 1N5819 Schottky",
        ("1N5817", "1N5818", "1N5819"),
    ),
    ManifestEntry(
        "ti-lm317", "https://www.ti.com/lit/ds/symlink/lm317.pdf",
        "Texas Instruments", "MFR_DATASHEET", "TI LM317 Adjustable Regulator",
        ("LM317",),
    ),
    ManifestEntry(
        "ti-lm317l", "https://www.ti.com/lit/ds/symlink/lm317l.pdf",
        "Texas Instruments", "MFR_DATASHEET", "TI LM317L Adjustable Regulator",
        ("LM317L",),
    ),
    ManifestEntry(
        "ti-lm1117", "https://www.ti.com/lit/ds/symlink/lm1117.pdf",
        "Texas Instruments", "MFR_DATASHEET", "TI LM1117 LDO Regulator",
        ("LM1117",),
    ),
    ManifestEntry(
        "ti-lm2940", "https://www.ti.com/lit/ds/symlink/lm2940-n.pdf",
        "Texas Instruments", "MFR_DATASHEET", "TI LM2940 Low Dropout Regulator",
        ("LM2940",),
    ),
]

BY_ID = {m.doc_id: m for m in MANIFEST}
LOCK_PATH = config.DATA / "corpus.lock.json"


def sha256_file(p) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_all(force: bool = False, verbose: bool = True) -> dict[str, str]:
    """Download every manifest entry. Returns {doc_id: sha256}."""
    lock = {}
    if LOCK_PATH.exists():
        lock = json.loads(LOCK_PATH.read_text())

    headers = {"User-Agent": config.USER_AGENT, "Accept": "application/pdf,*/*"}
    out = {}
    with httpx.Client(follow_redirects=True, timeout=60.0, headers=headers) as client:
        for m in MANIFEST:
            dest = m.path()
            if dest.exists() and not force:
                digest = sha256_file(dest)
                out[m.doc_id] = digest
                if verbose:
                    print(f"  cached  {m.doc_id:22s} {dest.stat().st_size:>9,d} B")
                continue
            r = client.get(m.url)
            r.raise_for_status()
            if not r.content.startswith(b"%PDF"):
                raise RuntimeError(f"{m.doc_id}: {m.url} did not return a PDF "
                                   f"(got {r.headers.get('content-type')})")
            dest.write_bytes(r.content)
            digest = sha256_file(dest)
            out[m.doc_id] = digest
            prev = lock.get(m.doc_id, {}).get("sha256")
            flag = ""
            if prev and prev != digest:
                flag = "  ** REVISED UPSTREAM **"
            if verbose:
                print(f"  fetched {m.doc_id:22s} {len(r.content):>9,d} B{flag}")

    LOCK_PATH.write_text(json.dumps(
        {m.doc_id: {"sha256": out[m.doc_id], "url": m.url,
                    "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
         for m in MANIFEST if m.doc_id in out},
        indent=2))
    return out


def load_doc(doc_id: str) -> SourceDoc:
    m = BY_ID[doc_id]
    p = m.path()
    if not p.exists():
        raise FileNotFoundError(f"{doc_id} not fetched -- run `make fetch`")
    lock = json.loads(LOCK_PATH.read_text()) if LOCK_PATH.exists() else {}
    return SourceDoc(
        doc_id=doc_id, url=m.url, publisher=m.publisher, authority=m.authority,
        local_path=str(p), sha256=lock.get(doc_id, {}).get("sha256", sha256_file(p)),
        fetched_at=lock.get(doc_id, {}).get("fetched_at", ""), title=m.title,
    )


def docs_for_part(mpn: str) -> list[str]:
    """Which documents claim to cover this part number."""
    u = mpn.upper().strip()
    return [m.doc_id for m in MANIFEST if u in {c.upper() for c in m.covers}]


if __name__ == "__main__":
    import sys
    fetch_all(force="--force" in sys.argv)
