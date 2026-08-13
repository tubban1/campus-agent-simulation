"""Pure normalization rules for external world information."""


def classify_information(text):
    normalized = str(text or "").lower()
    if any(word in normalized for word in ("ai", "人工智能", "科技", "技术")):
        return "technology"
    if any(word in normalized for word in ("就业", "招聘", "创业", "商业", "经济")):
        return "career"
    if any(word in normalized for word in ("教育", "大学", "考试", "课程", "学生")):
        return "education"
    return "general"


def compact_sync_result(result):
    if result.get("delegated_to_external_ingestion"):
        return {
            "skipped": bool(result.get("skipped")),
            "delegated_to_external_ingestion": True,
            "source_sync_count": int(result.get("source_sync_count") or 0),
            "success_count": int(result.get("success_count") or 0),
            "failed_count": int(result.get("failed_count") or 0),
            "sync_results": result.get("sync_results") or [],
        }
    compact = {"skipped": bool(result.get("skipped")), "failed": bool(result.get("failed")), "reason": result.get("reason", ""), "fetched": int(result.get("fetched") or 0), "new_information_count": len(result.get("new_information") or []), "initial_recipients": int(result.get("initial_recipients") or 0), "last_synced_at": result.get("last_synced_at", "")}
    if result.get("event"):
        compact["event_id"] = result["event"].get("id")
        compact["event_type"] = result["event"].get("event_type")
    if result.get("error"):
        compact["error"] = str(result["error"])[:240]
    return compact


def fetch_information(sources, *, adapter_factory, logger, limit=5):
    errors = []
    for source_name, source_url in sources:
        try:
            records = adapter_factory().fetch({"feed_url": source_url, "limit": limit, "timeout_seconds": 5})
            items = [{"title": record["payload"]["title"], "summary": record["payload"]["summary"], "source_name": source_name, "source_url": record["payload"]["link"], "published_at": record["payload"]["published_at_text"], "category": record["payload"]["category"]} for record in records]
            if items:
                return items
            errors.append(f"{source_name}: no RSS items")
        except Exception as exc:
            logger.warning("External information source failed: %s", source_name, exc_info=True)
            errors.append(f"{source_name}: {type(exc).__name__}")
    raise RuntimeError("; ".join(errors))


def deliver_information(conn, information, resident_id, channel, relevance=65, credibility=80, distortion_note="", source_resident_id=None, *, ensure_system, ensure_profile, load_json, json_dumps, current_day, add_memory):
    ensure_system(conn)
    inserted = conn.execute("INSERT OR IGNORE INTO agent_information (information_id, resident_id, channel, relevance, credibility, distortion_note, source_resident_id) VALUES (?, ?, ?, ?, ?, ?, ?)", (information["id"], resident_id, channel, relevance, credibility, distortion_note, source_resident_id)).rowcount
    if not inserted: return False
    profile = ensure_profile(conn, resident_id)
    perception = load_json(profile["perception"], {}) if profile else {}
    feed = perception.get("external_information", [])
    feed.insert(0, {"title": information["title"], "category": information["category"], "channel": channel, "credibility": credibility, "distortion_note": distortion_note})
    perception["external_information"] = feed[:4]
    conn.execute("UPDATE agent_profiles SET perception = ? WHERE resident_id = ?", (json_dumps(perception, ensure_ascii=False), resident_id))
    add_memory(conn, resident_id, current_day(conn), f"我从{channel}得知外部消息：{information['title']}。可信度 {credibility}。{distortion_note}", importance=4, memory_type="working", tags=["外部资讯", information["category"], channel, f"可信度{credibility}"], source="external_information")
    return True


def seed_recipients(conn, information, *, deliver):
    agents = conn.execute("SELECT residents.id, residents.role, residents.goal, residents.personality, agent_profiles.skills, agent_profiles.organization FROM residents LEFT JOIN agent_profiles ON agent_profiles.resident_id = residents.id ORDER BY residents.id").fetchall()
    terms = {"technology": ("AI", "人工智能", "技术", "创业"), "career": ("创业", "商业", "投资", "就业"), "education": ("学生", "教师", "课程", "学习")}.get(information["category"], ())
    ranked = sorted(agents, key=lambda agent: sum(term in f"{agent['role']} {agent['goal']} {agent['personality']} {agent['skills'] or ''} {agent['organization'] or ''}" for term in terms), reverse=True)
    return [agent["id"] for agent in ranked[:4] if deliver(conn, information, agent["id"], "外部资讯订阅", relevance=80, credibility=88)]


def spread_information(conn, limit=12, *, ensure_system, deliver, choice):
    ensure_system(conn); delivered = 0
    rows = conn.execute("SELECT ai.information_id, ai.resident_id AS sender_id, ai.credibility, ei.title, ei.category FROM agent_information ai JOIN external_information ei ON ei.id = ai.information_id ORDER BY ai.received_at DESC LIMIT ?", (limit,)).fetchall()
    for row in rows:
        contacts = conn.execute("SELECT relationships.to_resident_id, relationship_dynamics.trust, relationship_dynamics.affinity FROM relationships LEFT JOIN relationship_dynamics ON relationship_dynamics.from_resident_id = relationships.from_resident_id AND relationship_dynamics.to_resident_id = relationships.to_resident_id WHERE relationships.from_resident_id = ? AND relationships.score >= 55 ORDER BY COALESCE(relationship_dynamics.trust, 50) + COALESCE(relationship_dynamics.affinity, 50) DESC LIMIT 2", (row["sender_id"],)).fetchall()
        info = {"id": row["information_id"], "title": row["title"], "category": row["category"]}
        for contact in contacts:
            distortion = choice(["", "转述时省略了部分背景。", "转述时更强调了与自己相关的部分。"])
            credibility = max(35, int(row["credibility"] or 80) - (8 if distortion else 3)); relevance = min(85, 52 + int(contact["trust"] or 50) // 3)
            delivered += int(deliver(conn, info, contact["to_resident_id"], "熟人转述", relevance=relevance, credibility=credibility, distortion_note=distortion, source_resident_id=row["sender_id"]))
    return delivered


def sync_into_world(conn, event_type="external_information_manual_sync", tick_id=None, day=None, slot=None, *, ensure_system, ensure_runtime, fetch, seed, add_event, current_day, append_event):
    ensure_system(conn); ensure_runtime(conn)
    fetched, created, recipient_ids = fetch(), [], set()
    for item in fetched:
        conn.execute("INSERT OR IGNORE INTO external_information (title, summary, source_name, source_url, category, published_at) VALUES (?, ?, ?, ?, ?, ?)", (item["title"], item["summary"], item["source_name"], item["source_url"], item["category"], item["published_at"]))
        row = conn.execute("SELECT * FROM external_information WHERE title = ?", (item["title"],)).fetchone()
        if row:
            information = dict(row); newly_informed = seed(conn, information)
            if newly_informed: created.append(information); recipient_ids.update(newly_informed)
    content = f"校园接入 {len(created)} 条外部资讯，已有 {len(recipient_ids)} 位 Agent 先行获知。" if created else f"外部资讯已检查，抓取 {len(fetched)} 条，暂无新的 Agent 接收记录。"
    if created: add_event(conn, day or current_day(conn), "external_information", content)
    event = append_event(conn, event_type, "外部世界自动同步" if event_type == "external_information_auto_sync" else "外部世界同步", content, tick_id=tick_id, payload={"fetched": len(fetched), "new_information_count": len(created), "initial_recipients": len(recipient_ids)}, day=day, slot=slot)
    return {"fetched": len(fetched), "new_information": created, "initial_recipients": len(recipient_ids), "event": event}


def maybe_auto_sync(conn, world_time, tick_id=None, day=None, slot=None, *, ensure_runtime, parse_time, interval_seconds, sync, append_event, logger):
    ensure_runtime(conn)
    latest = conn.execute("SELECT created_at FROM world_event_stream WHERE event_type IN ('external_information_auto_sync', 'external_information_auto_sync_failed') ORDER BY id DESC LIMIT 1").fetchone()
    latest_at = parse_time(latest["created_at"]) if latest else None
    if latest_at and (world_time - latest_at).total_seconds() < interval_seconds:
        return {"skipped": True, "reason": "interval_not_elapsed", "last_synced_at": latest_at.isoformat()}
    try:
        result = sync(conn, event_type="external_information_auto_sync", tick_id=tick_id, day=day, slot=slot); result["skipped"] = False; return result
    except Exception as exc:
        logger.warning("Auto external information sync failed", exc_info=True)
        event = append_event(conn, "external_information_auto_sync_failed", "外部世界自动同步失败", f"外部资讯源暂时不可用：{type(exc).__name__}", tick_id=tick_id, payload={"error": str(exc)}, day=day, slot=slot)
        return {"skipped": False, "failed": True, "error": str(exc), "event": event}
