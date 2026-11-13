"""Initial DocMind schema with pgvector support."""
import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

from backend.app.config import settings

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table("users", sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("email", sa.String(320), nullable=False, unique=True), sa.Column("name", sa.String(120), nullable=False, server_default=""), sa.Column("password_hash", sa.String(255), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_table("refresh_tokens", sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False), sa.Column("token_hash", sa.String(64), nullable=False, unique=True), sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False), sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
    op.create_table("collections", sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False), sa.Column("title", sa.String(120), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_collections_user_id", "collections", ["user_id"])
    op.create_table("documents", sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False), sa.Column("collection_id", sa.Uuid(), sa.ForeignKey("collections.id", ondelete="SET NULL")), sa.Column("title", sa.String(255), nullable=False), sa.Column("filename", sa.String(255), nullable=False), sa.Column("storage_key", sa.String(500), nullable=False, unique=True), sa.Column("size_bytes", sa.Integer(), nullable=False), sa.Column("sha256", sa.String(64), nullable=False), sa.Column("page_count", sa.Integer(), nullable=False, server_default="0"), sa.Column("status", sa.String(20), nullable=False, server_default="uploaded"), sa.Column("error", sa.Text()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    for col in ("user_id", "sha256", "status"):
        op.create_index(f"ix_documents_{col}", "documents", [col])
    op.create_index("ix_documents_collection_id", "documents", ["collection_id"])
    op.create_table("chunks", sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False), sa.Column("chunk_index", sa.Integer(), nullable=False), sa.Column("page_start", sa.Integer(), nullable=False), sa.Column("page_end", sa.Integer(), nullable=False), sa.Column("content", sa.Text(), nullable=False), sa.Column("token_count", sa.Integer(), nullable=False), sa.Column("embedding_model", sa.String(120), nullable=False), sa.Column("embedding", Vector(settings.embedding_dimensions)), sa.UniqueConstraint("document_id", "chunk_index"))
    op.create_index("ix_chunks_document_id", "chunks", ["document_id"])
    op.create_index("ix_chunks_embedding_hnsw", "chunks", ["embedding"],
      postgresql_using="hnsw", postgresql_ops={"embedding": "vector_cosine_ops"})
    op.create_index("ix_chunks_content_fts", "chunks",
      [sa.text("to_tsvector('simple', content)")], postgresql_using="gin")
    op.create_table("conversations", sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False), sa.Column("title", sa.String(255), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_conversations_user_id", "conversations", ["user_id"])
    op.create_table("messages", sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("conversation_id", sa.Uuid(), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False), sa.Column("role", sa.String(20), nullable=False), sa.Column("content", sa.Text(), nullable=False), sa.Column("citations_json", sa.Text(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])

def downgrade():
    for table in ("messages", "conversations", "chunks", "documents", "collections", "refresh_tokens", "users"):
        op.drop_table(table)
