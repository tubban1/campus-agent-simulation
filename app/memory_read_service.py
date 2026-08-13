"""Read models for agent memories and recent world context."""

from app.memory_repository import memory_page, recent_events


def recent_context(conn, resident_id, limit=6, query_terms=None, *, retrieve_memories, current_day, rows_to_dicts):
    memories = retrieve_memories(conn, resident_id, query_terms=query_terms, limit=limit)
    events = recent_events(conn, current_day(conn), limit)
    return {"memories": rows_to_dicts(memories), "memory_retrieval_terms": query_terms or [], "events": rows_to_dicts(events)}


def paginated_memories(conn, resident_id, limit=20, offset=0, *, ensure_columns, current_day, rows_to_dicts):
    ensure_columns(conn)
    day, page_limit, page_offset = current_day(conn), min(max(limit, 1), 100), max(offset, 0)
    total, rows = memory_page(conn, resident_id, day, page_limit, page_offset)
    memories = rows_to_dicts(rows)
    return {"resident_id": resident_id, "total": total, "offset": page_offset, "limit": page_limit, "has_more": page_offset + len(memories) < total, "memories": memories}
