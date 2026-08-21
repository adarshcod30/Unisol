"""PDF -> text with exact character offsets, plus page rendering for the review UI.

Two jobs, deliberately split:
  * character offsets  -> used to VERIFY an evidence quote (deterministic, exact)
  * PyMuPDF search_for -> used to DRAW the highlight rectangle (visual only)
Hand-mapping offsets to bounding boxes is brittle across ligatures and table
layouts; letting each mechanism do the job it's good at makes both reliable.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict

import pymupdf

from . import config, corpus
from .models import SourceDoc

_WS = re.compile(r"\s+")


def collapse(s: str) -> str:
    return _WS.sub(" ", s).strip()


@dataclass
class PageSpan:
    page: int          # 1-indexed
    char_start: int
    char_end: int
    width: float
    height: float


class IngestedDoc:
    def __init__(self, doc: SourceDoc, text: str, pages: list[PageSpan]):
        self.doc = doc
        self.text = text
        self.pages = pages
        self._flat = collapse(text).lower()
        self._sections = None

    @property
    def sections(self):
        from .sections import SectionMap
        if self._sections is None:
            self._sections = SectionMap(self.text)
        return self._sections

    # ---------- offsets ----------
    def page_for_offset(self, offset: int) -> int:
        for p in self.pages:
            if p.char_start <= offset < p.char_end:
                return p.page
        return self.pages[-1].page if self.pages else 1

    def page_text(self, page: int) -> str:
        for p in self.pages:
            if p.page == page:
                return self.text[p.char_start:p.char_end]
        return ""

    def slice(self, start: int, end: int) -> str:
        return self.text[max(0, start):max(0, end)]

    def window(self, start: int, end: int, pad: int = 240) -> str:
        return self.text[max(0, start - pad): min(len(self.text), end + pad)]

    def find_quote(self, quote: str) -> int:
        """Locate a quote anywhere in the doc, whitespace-insensitively.
        Returns a char offset into self.text, or -1."""
        q = collapse(quote)
        if not q:
            return -1
        idx = self.text.find(quote)
        if idx >= 0:
            return idx
        # whitespace-insensitive: build a regex that lets any run of whitespace match
        pat = r"\s+".join(re.escape(tok) for tok in q.split(" "))
        m = re.search(pat, self.text, flags=re.IGNORECASE)
        return m.start() if m else -1

    def contains(self, quote: str) -> bool:
        return self.find_quote(quote) >= 0

    # ---------- rendering ----------
    def render_page_png(self, page: int, highlight: str = "", dpi: int = 130) -> bytes:
        with pymupdf.open(self.doc.local_path) as pdf:
            pg = pdf[page - 1]
            if highlight:
                rects = pg.search_for(highlight[:180]) or []
                if not rects:
                    # fall back to the longest distinctive fragment
                    frag = max(collapse(highlight).split(" | ")[0].split(", "),
                               key=len, default="")
                    if len(frag) > 6:
                        rects = pg.search_for(frag[:80]) or []
                for r in rects[:12]:
                    a = pg.add_highlight_annot(r)
                    a.set_colors(stroke=(1, 0.85, 0.2))
                    a.update()
            pix = pg.get_pixmap(dpi=dpi, annots=True)
            return pix.tobytes("png")

    def highlight_rects(self, page: int, quote: str) -> list[list[float]]:
        with pymupdf.open(self.doc.local_path) as pdf:
            pg = pdf[page - 1]
            return [[r.x0, r.y0, r.x1, r.y1] for r in (pg.search_for(quote[:180]) or [])]


def _cache_path(doc_id: str):
    return config.CACHE / f"{doc_id}.text.json"


def ingest(doc_id: str, refresh: bool = False) -> IngestedDoc:
    doc = corpus.load_doc(doc_id)
    cp = _cache_path(doc_id)
    if cp.exists() and not refresh:
        blob = json.loads(cp.read_text())
        if blob.get("sha256") == doc.sha256:
            pages = [PageSpan(**p) for p in blob["pages"]]
            doc.page_count = len(pages)
            return IngestedDoc(doc, blob["text"], pages)

    text_parts: list[str] = []
    pages: list[PageSpan] = []
    cursor = 0
    with pymupdf.open(doc.local_path) as pdf:
        for i, pg in enumerate(pdf, start=1):
            t = pg.get_text("text")
            start = cursor
            text_parts.append(t)
            cursor += len(t)
            pages.append(PageSpan(i, start, cursor, pg.rect.width, pg.rect.height))
    text = "".join(text_parts)
    doc.page_count = len(pages)

    cp.write_text(json.dumps(
        {"sha256": doc.sha256, "text": text, "pages": [asdict(p) for p in pages]}))
    return IngestedDoc(doc, text, pages)


def ingest_all(refresh: bool = False) -> dict[str, IngestedDoc]:
    return {m.doc_id: ingest(m.doc_id, refresh) for m in corpus.MANIFEST}


if __name__ == "__main__":
    for did, d in ingest_all().items():
        print(f"{did:22s} pages={d.doc.page_count:3d}  chars={len(d.text):>8,d}  {d.doc.title}")
