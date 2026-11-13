from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_env: str = "development"
    secret_key: str = "unsafe-development-key-change-me"
    database_url: str = "postgresql+asyncpg://docmind:docmind@localhost:5432/docmind"
    redis_url: str = "redis://localhost:6379/0"
    upload_dir: str = "./uploads"
    s3_bucket: str = ""
    s3_endpoint_url: str = ""
    s3_region: str = "us-east-1"
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    max_upload_mb: int = 25
    max_pdf_pages: int = 500
    access_token_minutes: int = 20
    refresh_token_days: int = 14
    cookie_secure: bool = False
    frontend_origins: str = "http://localhost:5173"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    chunk_tokens: int = 650
    chunk_overlap: int = 120

    @model_validator(mode="after")
    def validate_security_and_chunking(self):
        if self.chunk_tokens < 1 or self.chunk_overlap < 0 or self.chunk_overlap >= self.chunk_tokens:
            raise ValueError("Chunk overlap must be non-negative and smaller than chunk size")
        if self.embedding_dimensions < 1:
            raise ValueError("Embedding dimensions must be positive")
        if self.app_env.lower() == "production":
            if len(self.secret_key) < 32 or self.secret_key == "unsafe-development-key-change-me":
                raise ValueError("Production requires a unique SECRET_KEY of at least 32 characters")
            if not self.cookie_secure:
                raise ValueError("COOKIE_SECURE must be enabled in production")
        return self

settings = Settings()
