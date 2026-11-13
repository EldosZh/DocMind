from backend.app.ingestion import make_chunks


def test_chunking_preserves_page_boundaries_and_text():
    chunks = make_chunks([(1, "Alpha fact. " + "word " * 30), (2, "Beta clause." )], max_words=18, overlap=4)
    assert chunks
    assert all(1 <= start <= end <= 2 for start, end, _, _ in chunks)
    assert "Alpha" in chunks[0][2]
    assert any(end == 2 for _, end, _, _ in chunks)

def test_empty_pdf_text_does_not_create_chunks():
    assert make_chunks([(1, ""), (2, "   ")]) == []
