import pytest

from backend.app.storage import LocalPrivateStorage


@pytest.mark.asyncio
async def test_private_local_storage_round_trip(tmp_path):
    storage = LocalPrivateStorage(str(tmp_path))
    await storage.put("random-key.pdf", b"%PDF-test")
    assert await storage.read("random-key.pdf") == b"%PDF-test"
    await storage.delete("random-key.pdf")
    with pytest.raises(FileNotFoundError):
        await storage.read("random-key.pdf")


@pytest.mark.asyncio
async def test_local_storage_rejects_path_traversal(tmp_path):
    storage = LocalPrivateStorage(str(tmp_path))
    with pytest.raises(ValueError):
        await storage.put("../outside.pdf", b"private")
