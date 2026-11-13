import hashlib
import json
import logging
import secrets
from pathlib import Path
from urllib.parse import quote
from uuid import UUID

import jwt
from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel, EmailStr, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import settings
from backend.app.db import get_db
from backend.app.models import Collection, Conversation, Document, Message, RefreshToken, User, now
from backend.app.security import (
    decode_access,
    hash_password,
    issue_access,
    new_refresh,
    refresh_digest,
    verify_password,
)
from backend.app.storage import storage
from backend.app.tasks import process_document

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
app = FastAPI(title="DocMind API", version="0.1.0")
limiter = Limiter(key_func=get_remote_address, storage_uri=settings.redis_url)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(CORSMiddleware, allow_origins=settings.frontend_origins.split(","),
  allow_credentials=True, allow_methods=["GET", "POST", "PATCH", "DELETE"],
  allow_headers=["Authorization", "Content-Type"])
oauth = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

class Register(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    name: str = Field(default="", max_length=120)
class Login(BaseModel):
    email: EmailStr
    password: str
class Ask(BaseModel):
    question: str = Field(min_length=1, max_length=8000)
    document_ids: list[UUID] = Field(default_factory=list)
    collection_id: UUID | None = None
    conversation_id: UUID | None = None
class Rename(BaseModel):
    title: str = Field(min_length=1, max_length=255)
class DocumentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    collection_id: UUID | None = None
class CollectionName(BaseModel):
    title: str = Field(min_length=1, max_length=120)

async def current_user(token: str = Depends(oauth), db: AsyncSession = Depends(get_db)):
    try:
        user_id = decode_access(token)
    except (jwt.PyJWTError, ValueError, KeyError):
        raise HTTPException(401, "Invalid access token")
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(401, "User not found")
    return user

def set_refresh_cookie(response: Response, token: str):
    response.set_cookie("refresh_token", token, httponly=True, secure=settings.cookie_secure,
      samesite="lax", path="/api/auth", max_age=settings.refresh_token_days * 86400)

def auth_result(user):
    return {"access_token": issue_access(user.id), "token_type": "bearer",
      "user": {"id": str(user.id), "email": user.email, "name": user.name}}

@app.get("/api/health")
async def health():
    return {"status": "ok"}

@app.post("/api/auth/register", status_code=201)
@limiter.limit("5/minute")
async def register(request: Request, body: Register, response: Response, db: AsyncSession = Depends(get_db)):
    email = body.email.lower()
    if await db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "Email already registered")
    user = User(email=email, name=body.name, password_hash=hash_password(body.password))
    db.add(user)
    await db.flush()
    raw, digest, expires = new_refresh()
    db.add(RefreshToken(user_id=user.id, token_hash=digest, expires_at=expires))
    await db.commit()
    set_refresh_cookie(response, raw)
    return auth_result(user)

@app.post("/api/auth/login")
@limiter.limit("8/minute")
async def login(request: Request, body: Login, response: Response, db: AsyncSession = Depends(get_db)):
    user = await db.scalar(select(User).where(User.email == body.email.lower()))
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Incorrect email or password")
    raw, digest, expires = new_refresh()
    db.add(RefreshToken(user_id=user.id, token_hash=digest, expires_at=expires))
    await db.commit()
    set_refresh_cookie(response, raw)
    return auth_result(user)

@app.post("/api/auth/refresh")
async def refresh(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    raw = request.cookies.get("refresh_token")
    if not raw:
        raise HTTPException(401, "Refresh token required")
    stored = await db.scalar(select(RefreshToken).where(RefreshToken.token_hash == refresh_digest(raw)))
    if not stored or stored.revoked or stored.expires_at <= now():
        response.delete_cookie("refresh_token", path="/api/auth")
        raise HTTPException(401, "Refresh token is invalid or expired")
    # Rotation revokes the old credential before issuing its replacement.
    stored.revoked = True
    user = await db.get(User, stored.user_id)
    if not user:
        await db.commit()
        raise HTTPException(401, "User not found")
    new_raw, digest, expires = new_refresh()
    db.add(RefreshToken(user_id=user.id, token_hash=digest, expires_at=expires))
    await db.commit()
    set_refresh_cookie(response, new_raw)
    return auth_result(user)

@app.post("/api/auth/logout", status_code=204)
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    raw = request.cookies.get("refresh_token")
    if raw:
        stored = await db.scalar(select(RefreshToken).where(RefreshToken.token_hash == refresh_digest(raw)))
        if stored:
            stored.revoked = True
            await db.commit()
    response.delete_cookie("refresh_token", path="/api/auth", httponly=True,
      secure=settings.cookie_secure, samesite="lax")
    return response

@app.get("/api/me")
async def me(user: User = Depends(current_user)):
    return {"id": str(user.id), "email": user.email, "name": user.name}

def document_out(doc):
    return {"id": str(doc.id), "title": doc.title, "filename": doc.filename,
      "size_bytes": doc.size_bytes, "page_count": doc.page_count, "status": doc.status,
      "error": doc.error, "collection_id": str(doc.collection_id) if doc.collection_id else None,
      "created_at": doc.created_at.isoformat()}

async def prior_messages(db: AsyncSession, conversation_id: UUID):
    rows = (await db.scalars(select(Message).where(Message.conversation_id == conversation_id)
      .order_by(Message.created_at.desc()).limit(6))).all()
    return [{"role": item.role, "content": item.content} for item in reversed(rows)]

@app.post("/api/documents", status_code=202)
@limiter.limit("12/minute")
async def upload(request: Request, files: list[UploadFile] = File(...),
                 user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    results, queued = [], []
    for file in files:
        if not file.filename or not file.filename.lower().endswith(".pdf"):
            raise HTTPException(400, "Only PDF files are accepted")
        content = await file.read(settings.max_upload_mb * 1024 * 1024 + 1)
        if len(content) > settings.max_upload_mb * 1024 * 1024:
            raise HTTPException(413, "PDF exceeds upload limit")
        if not content.startswith(b"%PDF-"):
            raise HTTPException(400, "Invalid PDF signature")
        digest = hashlib.sha256(content).hexdigest()
        duplicate = await db.scalar(select(Document).where(Document.user_id == user.id, Document.sha256 == digest))
        if duplicate:
            results.append(document_out(duplicate))
            continue
        key = f"{secrets.token_hex(24)}.pdf"
        await storage.put(key, content)
        doc = Document(user_id=user.id, title=Path(file.filename).stem[:255],
          filename=Path(file.filename).name[:255], storage_key=key, size_bytes=len(content), sha256=digest)
        db.add(doc)
        await db.flush()
        queued.append(str(doc.id))
        results.append(document_out(doc))
    await db.commit()
    for doc_id in queued:
        process_document.delay(doc_id)
    return results

@app.get("/api/documents")
async def list_documents(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    docs = (await db.scalars(select(Document).where(Document.user_id == user.id)
      .order_by(Document.created_at.desc()))).all()
    return [document_out(doc) for doc in docs]

@app.patch("/api/documents/{doc_id}")
async def rename_document(doc_id: UUID, body: DocumentUpdate, user: User = Depends(current_user),
                         db: AsyncSession = Depends(get_db)):
    doc = await db.scalar(select(Document).where(Document.id == doc_id, Document.user_id == user.id))
    if not doc:
        raise HTTPException(404, "Document not found")
    if "title" in body.model_fields_set:
        if body.title is None:
            raise HTTPException(422, "Title cannot be empty")
        doc.title = body.title
    if "collection_id" in body.model_fields_set:
        if body.collection_id is not None:
            collection = await db.scalar(select(Collection).where(Collection.id == body.collection_id,
              Collection.user_id == user.id))
            if not collection:
                raise HTTPException(404, "Collection not found")
        doc.collection_id = body.collection_id
    await db.commit()
    return document_out(doc)

@app.get("/api/collections")
async def list_collections(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.scalars(select(Collection).where(Collection.user_id == user.id)
      .order_by(Collection.created_at.desc()))).all()
    return [{"id": str(item.id), "title": item.title, "created_at": item.created_at.isoformat()}
      for item in rows]

@app.post("/api/collections", status_code=201)
async def create_collection(body: CollectionName, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    collection = Collection(user_id=user.id, title=body.title)
    db.add(collection)
    await db.commit()
    return {"id": str(collection.id), "title": collection.title,
      "created_at": collection.created_at.isoformat()}

@app.patch("/api/collections/{collection_id}")
async def rename_collection(collection_id: UUID, body: CollectionName, user: User = Depends(current_user),
                            db: AsyncSession = Depends(get_db)):
    collection = await db.scalar(select(Collection).where(Collection.id == collection_id,
      Collection.user_id == user.id))
    if not collection:
        raise HTTPException(404, "Collection not found")
    collection.title = body.title
    await db.commit()
    return {"id": str(collection.id), "title": collection.title}

@app.delete("/api/collections/{collection_id}", status_code=204)
async def delete_collection(collection_id: UUID, user: User = Depends(current_user),
                            db: AsyncSession = Depends(get_db)):
    collection = await db.scalar(select(Collection).where(Collection.id == collection_id,
      Collection.user_id == user.id))
    if not collection:
        raise HTTPException(404, "Collection not found")
    await db.delete(collection)
    await db.commit()

@app.post("/api/documents/{doc_id}/retry", status_code=202)
async def retry_document(doc_id: UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    doc = await db.scalar(select(Document).where(Document.id == doc_id, Document.user_id == user.id))
    if not doc:
        raise HTTPException(404, "Document not found")
    if doc.status not in {"uploaded", "failed"}:
        raise HTTPException(409, "Only uploaded or failed documents can be retried")
    doc.status = "uploaded"
    doc.error = None
    await db.commit()
    try:
        process_document.delay(str(doc.id))
    except Exception as exc:
        logging.exception("Could not queue document processing")
        raise HTTPException(503, "Processing queue is temporarily unavailable") from exc
    return document_out(doc)

@app.get("/api/documents/{doc_id}/file")
async def document_file(doc_id: UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    doc = await db.scalar(select(Document).where(Document.id == doc_id, Document.user_id == user.id))
    if not doc:
        raise HTTPException(404, "Document not found")
    try:
        content = await storage.read(doc.storage_key)
    except (FileNotFoundError, OSError):
        raise HTTPException(404, "File not found")
    return Response(content=content, media_type="application/pdf",
      headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(doc.filename)}"})

@app.delete("/api/documents/{doc_id}", status_code=204)
async def delete_document(doc_id: UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    doc = await db.scalar(select(Document).where(Document.id == doc_id, Document.user_id == user.id))
    if not doc:
        raise HTTPException(404, "Document not found")
    await storage.delete(doc.storage_key)
    await db.delete(doc)
    await db.commit()

@app.post("/api/chat/ask")
async def ask(body: Ask, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    from backend.app.rag import answer
    ids = list(dict.fromkeys(body.document_ids))
    if body.collection_id and not await db.scalar(select(Collection.id).where(
      Collection.id == body.collection_id, Collection.user_id == user.id)):
        raise HTTPException(404, "Collection not found")
    if ids:
        owned = (await db.scalars(select(Document.id).where(Document.user_id == user.id,
          Document.id.in_(ids)))).all()
        if len(owned) != len(ids):
            raise HTTPException(404, "One or more documents not found")
    if body.conversation_id:
        conv = await db.scalar(select(Conversation).where(Conversation.id == body.conversation_id,
          Conversation.user_id == user.id))
        if not conv:
            raise HTTPException(404, "Conversation not found")
    else:
        conv = Conversation(user_id=user.id, title=body.question[:70])
        db.add(conv)
        await db.flush()
    history = await prior_messages(db, conv.id)
    db.add(Message(conversation_id=conv.id, role="user", content=body.question))
    try:
        text, citations = await answer(db, user.id, body.question, [str(item) for item in ids],
          str(body.collection_id) if body.collection_id else None, history)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    db.add(Message(conversation_id=conv.id, role="assistant", content=text,
      citations_json=json.dumps(citations)))
    await db.commit()
    return {"conversation_id": str(conv.id), "answer": text, "citations": citations}

@app.post("/api/chat/stream")
async def chat_stream(body: Ask, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    from backend.app.rag import stream_answer
    ids = list(dict.fromkeys(body.document_ids))
    if body.collection_id and not await db.scalar(select(Collection.id).where(
      Collection.id == body.collection_id, Collection.user_id == user.id)):
        raise HTTPException(404, "Collection not found")
    if ids:
        owned = (await db.scalars(select(Document.id).where(Document.user_id == user.id,
          Document.id.in_(ids)))).all()
        if len(owned) != len(ids):
            raise HTTPException(404, "One or more documents not found")
    if body.conversation_id:
        conv = await db.scalar(select(Conversation).where(Conversation.id == body.conversation_id,
          Conversation.user_id == user.id))
        if not conv:
            raise HTTPException(404, "Conversation not found")
    else:
        conv = Conversation(user_id=user.id, title=body.question[:70])
        db.add(conv)
        await db.flush()
    conversation_id = conv.id
    history = await prior_messages(db, conversation_id)
    db.add(Message(conversation_id=conversation_id, role="user", content=body.question))
    await db.commit()

    async def events():
        yield f"event: meta\ndata: {json.dumps({'conversation_id': str(conversation_id)})}\n\n"
        pieces = []
        citations = []
        try:
            async for token, final_citations in stream_answer(db, user.id, body.question,
              [str(item) for item in ids], str(body.collection_id) if body.collection_id else None,
              history):
                if final_citations is not None:
                    citations = final_citations
                    continue
                pieces.append(token)
                yield f"event: token\ndata: {json.dumps({'text': token})}\n\n"
            final_text = "".join(pieces)
            db.add(Message(conversation_id=conversation_id, role="assistant", content=final_text,
              citations_json=json.dumps(citations)))
            await db.commit()
            yield f"event: done\ndata: {json.dumps({'citations': citations})}\n\n"
        except Exception as exc:
            logging.exception("Streaming answer failed")
            yield f"event: error\ndata: {json.dumps({'detail': str(exc) if isinstance(exc, RuntimeError) else 'Answer generation failed'})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
      headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.get("/api/conversations")
async def conversations(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.scalars(select(Conversation).where(Conversation.user_id == user.id)
      .order_by(Conversation.created_at.desc()))).all()
    return [{"id": str(item.id), "title": item.title, "created_at": item.created_at.isoformat()} for item in rows]

@app.patch("/api/conversations/{conversation_id}")
async def rename_conversation(conversation_id: UUID, body: Rename, user: User = Depends(current_user),
                              db: AsyncSession = Depends(get_db)):
    conv = await db.scalar(select(Conversation).where(Conversation.id == conversation_id,
      Conversation.user_id == user.id))
    if not conv:
        raise HTTPException(404, "Conversation not found")
    conv.title = body.title
    await db.commit()
    return {"id": str(conv.id), "title": conv.title}

@app.get("/api/conversations/{conversation_id}/messages")
async def messages(conversation_id: UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    conv = await db.scalar(select(Conversation).where(Conversation.id == conversation_id,
      Conversation.user_id == user.id))
    if not conv:
        raise HTTPException(404, "Conversation not found")
    rows = (await db.scalars(select(Message).where(Message.conversation_id == conv.id)
      .order_by(Message.created_at))).all()
    return [{"id": str(item.id), "role": item.role, "content": item.content,
      "citations": json.loads(item.citations_json)} for item in rows]
