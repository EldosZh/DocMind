import pytest
from pydantic import ValidationError

from backend.app.config import Settings


def test_production_requires_secure_cookie_and_long_secret():
    with pytest.raises(ValidationError):
        Settings(app_env="production", secret_key="x" * 32, cookie_secure=False)
    config = Settings(app_env="production", secret_key="x" * 32, cookie_secure=True)
    assert config.app_env == "production"


def test_chunk_configuration_rejects_invalid_overlap():
    with pytest.raises(ValidationError):
        Settings(chunk_tokens=100, chunk_overlap=100)
