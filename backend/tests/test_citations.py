from backend.app.rag import history_context, validate_citations


def test_only_retrieved_source_ids_are_returned():
    citations = [
        {"source_id": "S1", "page": 2},
        {"source_id": "S2", "page": 5},
    ]
    answer, verified = validate_citations("Evidence [S2]. Unsupported [S99].", citations)
    assert answer == "Evidence [S2]. Unsupported ."
    assert verified == [citations[1]]


def test_recent_conversation_history_is_bounded():
    history = [{"role": "user", "content": str(index)} for index in range(8)]
    context = history_context(history)
    assert "0" not in context
    assert "2" in context and "7" in context
