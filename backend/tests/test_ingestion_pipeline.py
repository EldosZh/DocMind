from types import SimpleNamespace
from uuid import uuid4

import pymupdf
import pytest

from backend.app import ingestion
from backend.app.config import settings


class FakeSession:
    def __init__(self, document):
        self.document = document
        self.chunks = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def get(self, model, document_id):
        return self.document if str(self.document.id) == str(document_id) else None

    async def commit(self):
        return None

    async def execute(self, statement):
        return None

    async def flush(self):
        return None

    async def rollback(self):
        return None

    def add(self, item):
        self.chunks.append(item)


@pytest.mark.asyncio
async def test_pdf_ingestion_extracts_page_text_and_persists_embedded_chunk(monkeypatch):
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "The renewal date is 14 June 2027.")
    content = pdf.tobytes()
    document = SimpleNamespace(id=uuid4(), storage_key="private.pdf", status="uploaded",
      error=None, page_count=0)
    session = FakeSession(document)

    class FakeStorage:
        async def read(self, key):
            assert key == document.storage_key
            return content

    class FakeProvider:
        async def embed(self, texts):
            assert len(texts) == 1
            return [[0.0] * settings.embedding_dimensions]

    monkeypatch.setattr(ingestion, "SessionLocal", lambda: session)
    monkeypatch.setattr(ingestion, "storage", FakeStorage())
    monkeypatch.setattr(ingestion, "provider", FakeProvider())

    await ingestion.ingest_document(str(document.id))

    assert document.status == "ready"
    assert document.page_count == 1
    assert len(session.chunks) == 1
    assert "14 June 2027" in session.chunks[0].content
    assert session.chunks[0].page_start == session.chunks[0].page_end == 1
