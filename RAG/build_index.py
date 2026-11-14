"""Build the document index for the assistant's RAG tool (docs/PLAN_ASISTENTE.md, M3). Runs offline (CPU is fine).

    RAG/.venv/Scripts/python RAG/build_index.py            # from the repo root

Input : the NASA documents listed in DOCUMENTS (first batch = Drive datos/documentos_ciencia/), read from
        Backend/descargas/<PSI>/... (PSI downloads; FLEX NASA/TP-2015-216046 comes from NTRS).
Output: RAG/index/rag_index.parquet  one row per chunk, with its document metadata and the embedding
        RAG/index/manifest.json      model, settings, per-document counts and a hash of the parquet

Chunking (fixed by the plan): paragraphs packed into ~1,400 characters with ~220 of overlap, never cutting a
paragraph when it fits; page_from/page_to are real PDF page numbers (1-based); empty pages and the NASA STI
Program boilerplate page are skipped. Word files have no pages (page_from/page_to = None).

Embeddings (LC's decision, 2026-11-14): BAAI/bge-small-en-v1.5 through fastembed (ONNX, no torch), normalized,
384 dims. Documents are embedded WITHOUT a prefix; queries get QUERY_INSTRUCTION, the prefix BGE recommends (the API
uses the same constant, Backend/api/rag.py). The model and the reports are English-only.

chunk_kind: 'table' when more than 35 % of the words are numbers, or when at least 4 lines exist and half or more of
them carry 4 numbers or more (LC's rule from assistant/retrieve.py is_table_like, with numbers also counted when
written ".34" or as dates and times); 'text' otherwise.
/rag/search leaves the 'table' chunks out: their numbers are answered by /tests/search.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import docx
import fitz  # PyMuPDF
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DOWNLOADS = ROOT / "Backend" / "descargas"
CATALOG = ROOT / "Backend" / "psi-combustion" / "catalog" / "psi_combustion_catalog.csv"
OUT = ROOT / "RAG" / "index"

EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIM = 384
CHUNK_CHARS, OVERLAP_CHARS, MIN_CHUNK_CHARS = 1400, 220, 200
NTRS = "https://ntrs.nasa.gov/citations/"


@dataclass(frozen=True)
class Doc:
    accession: str
    path: str            # relative to Backend/descargas
    title: str
    category: str
    source_url: str | None = None   # None -> PSI investigation page from the catalog


DOCUMENTS = [
    Doc("PSI-69", "PSI-69/Reports/NASA-TP-2015-216046_FLEX_Detailed_Results.pdf",
        "Detailed Results from the Flame Extinguishment Experiment (FLEX), March 2009 to December 2011 (NASA/TP-2015-216046)",
        "Reports", NTRS + "20150023456"),
    Doc("PSI-159", "PSI-159/Reports/PSI-159_Reports_FinalRpt_ACME.CFI-G.pdf",
        "ACME Cool Flames Investigation with Gases (CFI-G) final report", "Reports"),
    Doc("PSI-25", "PSI-25/Reports/PSI-25_Reports_BASS-II Summary Report.pdf",
        "Burning and Suppression of Solids-II (BASS-II) Summary Report (NASA/TM-20210011385)", "Reports", NTRS + "20210011385"),
    Doc("PSI-25", "PSI-25/Science Documents/PSI-25_Science Documents_BASS-II_SRD.pdf",
        "BASS-II Science Requirements Document", "Science Documents"),
    Doc("PSI-25", "PSI-25/Science Documents/PSI-25_Science Documents_BASS-II_ReadMe.docx",
        "BASS-II data ReadMe", "Science Documents"),
    Doc("PSI-26", "PSI-26/Science Documents/PSI-26_Science Documents_SRD_BASS.pdf",
        "BASS Science Requirements Document", "Science Documents"),
    Doc("PSI-26", "PSI-26/Science Documents/PSI-26_Science Documents_DLD_BASS.pdf",
        "BASS Data Lexicon Document", "Science Documents"),
    Doc("PSI-26", "PSI-26/Presentations/PSI-26_Presentations_BASS_Overview of Recent ISS Results.pdf",
        "BASS: overview of recent ISS results (presentation)", "Presentations"),
    Doc("PSI-26", "PSI-26/Presentations/PSI-26_Presentations_Flammability Aspects of Fabric_USNationalCombustion2013.pdf",
        "Flammability aspects of fabric in microgravity, US National Combustion Meeting 2013 (presentation)", "Presentations"),
    Doc("PSI-26", "PSI-26/Presentations/PSI-26_Presentations_FlammabilityAspectsofCottonFiberglassFabric_ASGSR2012.pdf",
        "Flammability aspects of cotton-fiberglass fabric, ASGSR 2012 (presentation)", "Presentations"),
    Doc("PSI-98", "PSI-98/Science Documents/PSI-98_Science Documents_SAFFIRE Objectives, Development and Status.pdf",
        "Spacecraft Fire Experiment (SAFFIRE): objectives, development and status", "Science Documents"),
    Doc("PSI-98", "PSI-98/Science Documents/PSI-98_Science Documents_SAFFIRE-I and SAFFIRE-III Readme.docx",
        "SAFFIRE-I and SAFFIRE-III data ReadMe", "Science Documents"),
]

STI_BOILERPLATE = re.compile(r"NASA STI Program|STI Program Office|Since its founding, NASA has been dedicated", re.I)
WS = re.compile(r"[ \t ]+")
SENTENCE_END = re.compile(r"(?<=[.!?;:])\s+")


# ----------------------------------------------------------------------------- text extraction
def pdf_paragraphs(path: Path) -> tuple[list[tuple[int, str]], int, int]:
    """(page, paragraph) in reading order; skipped page count; total pages."""
    out, skipped = [], 0
    with fitz.open(path) as pdf:
        for i, page in enumerate(pdf, start=1):
            text = page.get_text("text").strip()
            if len(text) < 40 or STI_BOILERPLATE.search(text):
                skipped += 1
                continue
            for block in page.get_text("blocks", sort=True):
                if block[6] != 0:          # 0 = text block, 1 = image
                    continue
                para = WS.sub(" ", " ".join(line.strip() for line in block[4].splitlines() if line.strip())).strip()
                if para:
                    out.append((i, para))
        return out, skipped, len(pdf)


def docx_paragraphs(path: Path) -> list[tuple[None, str]]:
    d = docx.Document(str(path))
    paras = [p.text for p in d.paragraphs]
    for table in d.tables:
        for row in table.rows:
            paras.append(" | ".join(c.text.strip() for c in row.cells))
    return [(None, WS.sub(" ", p).strip()) for p in paras if p.strip()]


# ----------------------------------------------------------------------------- chunking
def _split_long(page, text: str) -> list[tuple[int | None, str]]:
    """A paragraph longer than a chunk is split at sentence ends (or hard-split as a last resort)."""
    pieces, cur = [], ""
    for sent in SENTENCE_END.split(text):
        while len(sent) > CHUNK_CHARS:
            if cur:
                pieces.append(cur)
                cur = ""
            pieces.append(sent[:CHUNK_CHARS])
            sent = sent[CHUNK_CHARS:]
        if len(cur) + len(sent) + 1 > CHUNK_CHARS and cur:
            pieces.append(cur)
            cur = ""
        cur = f"{cur} {sent}".strip()
    if cur:
        pieces.append(cur)
    return [(page, p) for p in pieces]


def _tail(paras: list[tuple[int | None, str]]) -> list[tuple[int | None, str]]:
    """Last paragraphs (or the end of the last one) adding up to ~OVERLAP_CHARS, to start the next chunk."""
    keep, n = [], 0
    for page, p in reversed(paras):
        if n + len(p) <= OVERLAP_CHARS:
            keep.insert(0, (page, p))
            n += len(p)
            continue
        if not keep:   # last paragraph alone is too long: keep its last sentences
            sents = SENTENCE_END.split(p)
            tail = ""
            for s in reversed(sents):
                if len(tail) + len(s) > OVERLAP_CHARS:
                    break
                tail = f"{s} {tail}".strip()
            if tail:
                keep.insert(0, (page, tail))
        break
    return keep


def chunk(paragraphs: list[tuple[int | None, str]]) -> list[dict]:
    units = [u for page, p in paragraphs for u in (_split_long(page, p) if len(p) > CHUNK_CHARS else [(page, p)])]
    chunks, cur = [], []

    def emit(parts):
        text = "\n".join(p for _, p in parts)
        pages = [pg for pg, _ in parts if pg is not None]
        chunks.append({"content": text, "page_from": min(pages) if pages else None, "page_to": max(pages) if pages else None})

    for unit in units:
        size = sum(len(p) + 1 for _, p in cur)
        if cur and size + len(unit[1]) > CHUNK_CHARS:
            emit(cur)
            cur = _tail(cur)
        cur.append(unit)
    if cur:
        emit(cur)
    # fold a tiny trailing chunk into the previous one
    if len(chunks) > 1 and len(chunks[-1]["content"]) < MIN_CHUNK_CHARS:
        last = chunks.pop()
        chunks[-1]["content"] += "\n" + last["content"]
        if last["page_to"] is not None:
            chunks[-1]["page_to"] = max(chunks[-1]["page_to"] or 0, last["page_to"])
    return chunks


# ----------------------------------------------------------------------------- chunk kind
# a word is a number also when written ".34", or as a date / time ("10/24/09", "15:15:24"): the FLEX test tables are
# made of them and LC's original pattern missed 16 of those table chunks
NUMBER_WORD = re.compile(r"[~<>±–-]?(?:\d+(?:[.,]\d+)?|[.,]\d+|\d+[/:]\d+(?:[/:]\d+)?)%?")
NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def chunk_kind(content: str) -> str:
    words = content.split()
    if not words:
        return "table"
    num_share = sum(bool(NUMBER_WORD.fullmatch(w)) for w in words) / len(words)
    lines = [ln for ln in content.splitlines() if ln.strip()]
    many_nums = sum(len(NUMBER.findall(ln)) >= 4 for ln in lines) / max(len(lines), 1)
    return "table" if num_share > 0.35 or (len(lines) >= 4 and many_nums >= 0.5) else "text"


# ----------------------------------------------------------------------------- main
def catalog() -> dict[str, dict]:
    with CATALOG.open(encoding="utf-8") as f:
        return {r["accession"]: r for r in csv.DictReader(f)}


def main() -> None:
    from fastembed import TextEmbedding   # imported late: the chunking can be checked without the model

    cat = catalog()
    rows, docs_meta = [], []
    for d in DOCUMENTS:
        path = DOWNLOADS / d.path
        if not path.exists():
            sys.exit(f"missing document: {path}")
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        if path.suffix.lower() == ".pdf":
            paras, skipped, n_pages = pdf_paragraphs(path)
        else:
            paras, skipped, n_pages = docx_paragraphs(path), 0, None
        chunks = chunk(paras)
        info = cat.get(d.accession, {})
        for i, c in enumerate(chunks):
            rows.append({"accession": d.accession, "doi": info.get("doi_url") or None, "file_name": path.name,
                         "title": d.title, "category": d.category, "sha256": sha, "n_pages": n_pages,
                         "source_url": d.source_url or info.get("page_url") or None, "s3_key": f"psi-data/{d.path}",
                         "chunk_index": i, **c})
        docs_meta.append({"accession": d.accession, "file": path.name, "pages": n_pages, "pages_skipped": skipped,
                          "chunks": len(chunks)})
        print(f"{d.accession:8} {len(chunks):4} chunks  {n_pages or '-':>4} pages ({skipped} skipped)  {path.name}")

    df = pd.DataFrame(rows)
    df["chunk_kind"] = [chunk_kind(t) for t in df["content"]]
    model = TextEmbedding(EMBEDDING_MODEL)
    tok = model.model.tokenizer
    df["tokens"] = [len(tok.encode(t, add_special_tokens=False).ids) for t in df["content"]]
    t0 = time.time()
    emb = np.array(list(model.embed(df["content"].tolist(), batch_size=32)), dtype=np.float32)
    emb /= np.linalg.norm(emb, axis=1, keepdims=True)
    assert emb.shape == (len(df), EMBEDDING_DIM), emb.shape
    df["embedding"] = list(emb)
    print(f"embedded {len(df)} chunks in {time.time() - t0:.0f} s; chunk_kind: {df['chunk_kind'].value_counts().to_dict()}")

    OUT.mkdir(parents=True, exist_ok=True)
    parquet = OUT / "rag_index.parquet"
    df.to_parquet(parquet, index=False)
    manifest = {"embedding_model": EMBEDDING_MODEL, "embedding_dim": EMBEDDING_DIM, "normalized": True,
                "document_instruction": None, "n_table_chunks": int((df["chunk_kind"] == "table").sum()), "chunk_chars": CHUNK_CHARS, "overlap_chars": OVERLAP_CHARS,
                "n_chunks": len(df), "documents": docs_meta,
                "index_sha256": hashlib.sha256(parquet.read_bytes()).hexdigest()}
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"wrote {parquet.relative_to(ROOT)} ({parquet.stat().st_size / 1e6:.1f} MB) and manifest.json")


if __name__ == "__main__":
    main()
