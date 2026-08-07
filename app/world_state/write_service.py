"""Write orchestration for world snapshots and branches."""


def create_snapshot(
    conn,
    *,
    payload,
    create_record,
    append_event,
):
    snapshot = create_record(
        conn,
        reason=payload.reason,
        snapshot_type=payload.snapshot_type,
        run_id=payload.run_id,
        branch_key=payload.branch_key,
        parent_snapshot_id=payload.parent_snapshot_id,
        external_data_version=payload.external_data_version,
        metadata=payload.metadata,
    )
    event = append_event(
        conn,
        "world_snapshot_created",
        "世界快照已创建",
        f"已创建快照 #{snapshot['id']}，事件游标为 {snapshot['event_cursor']}。",
        payload={"snapshot_id": snapshot["id"], "checksum": snapshot["checksum"], "branch_key": snapshot["branch_key"]},
        source_type="snapshot",
        source_id=snapshot["id"],
        rule_version=snapshot["schema_version"],
    )
    conn.commit()
    return {"snapshot": snapshot, "event": event}


def create_branch(
    conn,
    *,
    payload,
    ensure_tables,
    create_record,
    append_event,
):
    ensure_tables(conn)
    branch = create_record(
        conn,
        payload.branch_key,
        payload.name,
        payload.source_snapshot_id,
        metadata=payload.metadata,
    )
    event = append_event(
        conn,
        "world_branch_created",
        "隔离世界分支已创建",
        f"已从快照 #{payload.source_snapshot_id} 创建分支 {payload.branch_key}。",
        payload={
            "branch_key": payload.branch_key,
            "source_snapshot_id": payload.source_snapshot_id,
            "head_snapshot_id": branch["head_snapshot_id"],
        },
        source_type="world_branch",
        source_id=branch["id"],
        rule_version="world-branch-v1",
    )
    conn.commit()
    return {"branch": branch, "event": event}


def require_paused_runtime(runtime):
    if runtime["status"] != "paused":
        raise ValueError("恢复或切换分支前必须先暂停 world runtime")
    return runtime["active_branch_key"] or "main"


def restore_snapshot(
    conn,
    snapshot_id,
    payload,
    *,
    runtime_id,
    ensure_tables,
    create_record,
    restore_state,
    append_event,
):
    ensure_tables(conn)
    runtime = conn.execute(
        "SELECT * FROM world_runtime WHERE id = ?", (runtime_id,)
    ).fetchone()
    active_branch_key = require_paused_runtime(runtime)
    active_branch = conn.execute(
        "SELECT * FROM world_branches WHERE branch_key = ?", (active_branch_key,)
    ).fetchone()
    backup = None
    if payload.create_backup:
        backup = create_record(
            conn,
            reason=f"恢复快照 #{snapshot_id} 前自动备份：{payload.reason}",
            snapshot_type="pre_restore_backup",
            branch_key=active_branch_key,
            parent_snapshot_id=active_branch["head_snapshot_id"] if active_branch else None,
            metadata={"restore_target_snapshot_id": snapshot_id},
        )
    restored = restore_state(
        conn,
        snapshot_id,
        active_branch_key=active_branch_key,
        active_run_id=runtime["active_run_id"] or "",
    )
    checkpoint = create_record(
        conn,
        reason=f"已恢复快照 #{snapshot_id}：{payload.reason}",
        snapshot_type="restored_checkpoint",
        branch_key=active_branch_key,
        parent_snapshot_id=snapshot_id,
        metadata={
            "restored_from_snapshot_id": snapshot_id,
            "backup_snapshot_id": backup["id"] if backup else None,
        },
    )
    event = append_event(
        conn,
        "world_snapshot_restored",
        "世界快照已恢复",
        f"分支 {active_branch_key} 已恢复到快照 #{snapshot_id}，runtime 保持暂停。",
        payload={
            "snapshot_id": snapshot_id,
            "backup_snapshot_id": backup["id"] if backup else None,
            "checkpoint_snapshot_id": checkpoint["id"],
            "branch_key": active_branch_key,
        },
        source_type="snapshot_restore",
        source_id=snapshot_id,
        rule_version="world-snapshot-restore-v1",
        branch_key=active_branch_key,
    )
    conn.commit()
    return {"restored": restored, "backup_snapshot": backup, "checkpoint_snapshot": checkpoint, "event": event}


def switch_branch(
    conn,
    branch_key,
    payload,
    *,
    runtime_id,
    ensure_tables,
    create_record,
    restore_state,
    append_event,
):
    ensure_tables(conn)
    runtime = conn.execute(
        "SELECT * FROM world_runtime WHERE id = ?", (runtime_id,)
    ).fetchone()
    current_branch_key = require_paused_runtime(runtime)
    if branch_key == current_branch_key:
        return {"switched": False, "reason": "already_active", "branch_key": branch_key}
    target_branch = conn.execute(
        "SELECT * FROM world_branches WHERE branch_key = ?", (branch_key,)
    ).fetchone()
    if not target_branch or not target_branch["head_snapshot_id"]:
        raise ValueError("目标分支不存在或没有可恢复的分支头")
    current_branch = conn.execute(
        "SELECT * FROM world_branches WHERE branch_key = ?", (current_branch_key,)
    ).fetchone()
    outgoing_snapshot = create_record(
        conn,
        reason=f"切换到 {branch_key} 前封存 {current_branch_key}：{payload.reason}",
        snapshot_type="branch_checkpoint",
        branch_key=current_branch_key,
        parent_snapshot_id=current_branch["head_snapshot_id"] if current_branch else None,
        metadata={"switch_target_branch": branch_key},
    )
    restored = restore_state(
        conn,
        target_branch["head_snapshot_id"],
        active_branch_key=branch_key,
        active_run_id=target_branch["run_id"] or "",
    )
    conn.execute(
        """UPDATE world_branches
        SET status = CASE WHEN branch_key = ? THEN 'active' ELSE 'ready' END,
            updated_at = CURRENT_TIMESTAMP
        WHERE branch_key IN (?, ?)""",
        (branch_key, current_branch_key, branch_key),
    )
    conn.execute(
        """UPDATE experiment_runs
        SET status = CASE WHEN branch_key = ? THEN 'running' ELSE 'paused' END,
            updated_at = CURRENT_TIMESTAMP
        WHERE branch_key IN (?, ?)""",
        (branch_key, current_branch_key, branch_key),
    )
    event = append_event(
        conn,
        "world_branch_switched",
        "活动世界分支已切换",
        f"活动世界从 {current_branch_key} 切换到 {branch_key}，runtime 保持暂停。",
        payload={
            "from_branch": current_branch_key,
            "to_branch": branch_key,
            "outgoing_snapshot_id": outgoing_snapshot["id"],
            "restored_snapshot_id": target_branch["head_snapshot_id"],
        },
        source_type="world_branch",
        source_id=target_branch["id"],
        rule_version="world-branch-v1",
        branch_key=branch_key,
    )
    conn.commit()
    return {"switched": True, "from_branch": current_branch_key, "to_branch": branch_key, "outgoing_snapshot": outgoing_snapshot, "restored": restored, "event": event}
