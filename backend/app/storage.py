import asyncio
from pathlib import Path
from typing import Protocol

from backend.app.config import settings


class PrivateStorage(Protocol):
    async def put(self, key: str, content: bytes) -> None: ...
    async def read(self, key: str) -> bytes: ...
    async def delete(self, key: str) -> None: ...


class LocalPrivateStorage:
    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if path.parent != self.root:
            raise ValueError("Invalid storage key")
        return path

    async def put(self, key: str, content: bytes) -> None:
        await asyncio.to_thread(self._path(key).write_bytes, content)

    async def read(self, key: str) -> bytes:
        return await asyncio.to_thread(self._path(key).read_bytes)

    async def delete(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            await asyncio.to_thread(path.unlink)


class S3PrivateStorage:
    def __init__(self):
        import boto3
        self.bucket = settings.s3_bucket
        self.client = boto3.client("s3", endpoint_url=settings.s3_endpoint_url or None,
          region_name=settings.s3_region, aws_access_key_id=settings.s3_access_key_id or None,
          aws_secret_access_key=settings.s3_secret_access_key or None)

    async def put(self, key: str, content: bytes) -> None:
        await asyncio.to_thread(self.client.put_object, Bucket=self.bucket, Key=key,
          Body=content, ContentType="application/pdf", ServerSideEncryption="AES256")

    async def read(self, key: str) -> bytes:
        def fetch():
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            return response["Body"].read()
        return await asyncio.to_thread(fetch)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self.client.delete_object, Bucket=self.bucket, Key=key)


storage: PrivateStorage = S3PrivateStorage() if settings.s3_bucket else LocalPrivateStorage(settings.upload_dir)
