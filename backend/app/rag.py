import re

from sqlalchemy import func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import settings
from backend.app.models import Chunk, Document
from backend.app.providers import provider


def validate_citations(draft: str, citations: list[dict]):
    allowed = {citation["source_id"] for citation in citations}
    used = set(re.findall(r"\[(S\d+)\]", draft)) & allowed
    verified = [citation for citation in citations if citation["source_id"] in used]
    safe_draft = re.sub(r"\[S[^\]\r\n]{0,32}\]",
      lambda match: match[0] if match[0] in {f"[{source}]" for source in allowed} else "", draft)
    return safe_draft, verified

def history_context(history: list[dict] | None) -> str:
    if not history:
        return ""
    return "\n".join(f"{item['role']}: {item['content'][:1500]}" for item in history[-6:])


async def retrieve(db: AsyncSession, user_id, question: str, document_ids: list[str],
                   collection_id: str | None = None, limit: int = 8):
    vector = (await provider.embed([question]))[0]
    base = select(Chunk, Document).join(Document, Chunk.document_id == Document.id).where(
      Document.user_id == user_id, Document.status == "ready",
      Chunk.embedding_model == settings.embedding_model)
    if document_ids:
        base = base.where(Document.id.in_(document_ids))
    if collection_id:
        base = base.where(Document.collection_id == collection_id)
    vector_rows = (await db.execute(base.order_by(Chunk.embedding.cosine_distance(vector)).limit(limit * 3))).all()
    dictionary = literal_column("'simple'")
    tsquery = func.plainto_tsquery(dictionary, question)
    tsvector = func.to_tsvector(dictionary, Chunk.content)
    lexical = tsvector.op("@@")(tsquery)
    text_rows = (await db.execute(base.where(lexical).order_by(func.ts_rank(tsvector, tsquery).desc()).limit(limit * 3))).all()
    scores, refs = {}, {}
    for rows in (vector_rows, text_rows):
        for rank, (chunk, doc) in enumerate(rows, 1):
            key = str(chunk.id)
            scores[key] = scores.get(key, 0) + 1 / (60 + rank)
            refs[key] = (chunk, doc)
    return [refs[key] for key in sorted(scores, key=scores.get, reverse=True)[:limit]]

async def answer(db: AsyncSession, user_id, question: str, document_ids: list[str],
                 collection_id: str | None = None, history: list[dict] | None = None):
    history_text = history_context(history)
    search_query = f"{history_text}\nCurrent question: {question}" if history_text else question
    rows = await retrieve(db, user_id, search_query, document_ids, collection_id)
    if not rows:
        return "I couldn't find supporting evidence in the selected documents.", []
    citations, excerpts = [], []
    for index, (chunk, doc) in enumerate(rows, 1):
        source = f"S{index}"
        citations.append({"source_id": source, "document_id": str(doc.id), "title": doc.title,
          "page": chunk.page_start, "chunk_id": str(chunk.id), "passage": chunk.content})
        excerpts.append(f"[{source}] {doc.title}, page {chunk.page_start}: {chunk.content}")
    draft = await provider.answer(question, "\n\n".join(excerpts), history_text)
    return validate_citations(draft, citations)

async def stream_answer(db: AsyncSession, user_id, question: str, document_ids: list[str],
                        collection_id: str | None = None, history: list[dict] | None = None):
    history_text = history_context(history)
    search_query = f"{history_text}\nCurrent question: {question}" if history_text else question
    rows = await retrieve(db, user_id, search_query, document_ids, collection_id)
    if not rows:
        yield "I couldn't find supporting evidence in the selected documents.", None
        yield "", []
        return
    citations, excerpts = [], []
    for index, (chunk, doc) in enumerate(rows, 1):
        source = f"S{index}"
        citations.append({"source_id": source, "document_id": str(doc.id), "title": doc.title,
          "page": chunk.page_start, "chunk_id": str(chunk.id), "passage": chunk.content})
        excerpts.append(f"[{source}] {doc.title}, page {chunk.page_start}: {chunk.content}")
    answer_parts = []
    pending = ""
    async for token in provider.stream_answer(question, "\n\n".join(excerpts), history_text):
        answer_parts.append(token)
        pending += token
        while True:
            match = re.search(r"\[S[^\]\r\n]{0,32}\]", pending)
            if match:
                if match.start():
                    yield pending[:match.start()], None
                pending = pending[match.end():]
                continue
            partial = re.search(r"\[S[^\]\r\n]{0,32}$", pending)
            if partial:
                if partial.start():
                    yield pending[:partial.start()], None
                pending = pending[partial.start():]
            else:
                if pending:
                    yield pending, None
                pending = ""
            break
    if pending and not re.fullmatch(r"\[S[^\]\r\n]{0,32}", pending):
        yield pending, None
    draft = "".join(answer_parts)
    yield "", validate_citations(draft, citations)[1]
