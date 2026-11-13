import re

import pymupdf
from sqlalchemy import delete

from backend.app.config import settings
from backend.app.db import SessionLocal
from backend.app.models import Chunk, Document
from backend.app.providers import provider
from backend.app.storage import storage


def make_chunks(pages: list[tuple[int, str]], max_words: int = 500, overlap: int = 90):
    words: list[str] = []
    page_numbers: list[int] = []
    for page, text in pages:
        cleaned = re.sub(r"\s+", " ", text).strip()
        for word in cleaned.split():
            words.append(word)
            page_numbers.append(page)
    chunks = []
    start = 0
    while start < len(words):
        end = min(start + max_words, len(words))
        if end < len(words):
            floor = max(start + int(max_words * 0.85), start + 1)
            for index in range(end - 1, floor, -1):
                if words[index - 1].endswith((".", "!", "?")):
                    end = index
                    break
        chunks.append((page_numbers[start], page_numbers[end - 1], " ".join(words[start:end]), end-start))
        if end == len(words):
            break
        start = max(start + 1, end - overlap)
    return chunks

async def ingest_document(document_id: str):
    async with SessionLocal() as db:
        doc = await db.get(Document, document_id)
        if doc is None:
            return
        doc.status = "processing"
        doc.error = None
        await db.commit()
        try:
            content = await storage.read(doc.storage_key)
            pdf = pymupdf.open(stream=content, filetype="pdf")
            if pdf.needs_pass:
                raise ValueError("Password-protected PDFs are not supported")
            if len(pdf) > settings.max_pdf_pages:
                raise ValueError("PDF exceeds configured page limit")
            pages = [(i + 1, page.get_text("text")) for i, page in enumerate(pdf)]
            pieces = make_chunks(pages, settings.chunk_tokens, settings.chunk_overlap)
            if not pieces:
                raise ValueError("No selectable text found; OCR is required for scanned PDFs")
            vectors = await provider.embed([piece[2] for piece in pieces])
            await db.execute(delete(Chunk).where(Chunk.document_id == doc.id))
            for index, ((first, last, content, count), vector) in enumerate(zip(pieces, vectors)):
                db.add(Chunk(document_id=doc.id, chunk_index=index, page_start=first,
                  page_end=last, content=content, token_count=count,
                  embedding_model=settings.embedding_model, embedding=vector))
            doc.page_count = len(pdf)
            doc.status = "ready"
            doc.error = None
            await db.commit()
        except Exception as exc:
            await db.rollback()
            doc = await db.get(Document, document_id)
            if doc:
                doc.status = "failed"
                doc.error = str(exc)[:1000]
                await db.commit()
            raise
