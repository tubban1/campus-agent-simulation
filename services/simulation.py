"""Daily simulation orchestration."""


def run_ai_day(progress=None, *, connection_factory, logger, json_dumps, current_day, auto_environment, recover_agents, spread_information, get_module_state, perceive, decide, execute, apply_feedback, get_resident, add_event, add_memory, record_log, advance_groups, write_diaries, publish_news):
    def report(event, message, **data):
        logger.info("[simulate-day] %s | %s | %s", event, message, json_dumps(data, ensure_ascii=False, default=str))
        if progress: progress({"event": event, "message": message, **data})
    with connection_factory() as conn:
        old_day, new_day = current_day(conn), current_day(conn) + 1
        report("day_advance", f"模拟日从第 {old_day} 天推进到第 {new_day} 天。", old_day=old_day, day=new_day)
        conn.execute("UPDATE simulation_state SET value = ? WHERE key = 'current_day'", (str(new_day),)); conn.commit()
        report("environment_start", "正在生成校园环境并同步真实时间/天气。", day=new_day); env = auto_environment(conn, new_day); report("environment_done", f"第 {new_day} 天环境已生成：{env.get('weather')}，校园情绪 {env.get('campus_mood')}。", day=new_day)
        report("agent_recovery", "正在恢复全部 Agent 精力并重置每日时间预算。", day=new_day); recover_agents(conn, new_day)
        report("information_spread", "正在沿关系网络传播外部资讯。", day=new_day); spread_count = spread_information(conn); conn.commit()
        agents = conn.execute("SELECT id, name, role FROM residents ORDER BY id").fetchall(); total_agents = len(agents); report("agents_start", f"开始遍历 {total_agents} 个 Agent。", day=new_day, total_agents=total_agents)
        results, fallback_agents = [], []
        for index, agent in enumerate(agents, start=1):
            state_before, resident_label = get_module_state(conn, agent["id"]), f"{agent['name']}（{agent['role']}）"
            try:
                report("agent_perceiving", f"{resident_label} 正在感知校园环境。", day=new_day, resident_id=agent["id"], agent_name=agent["name"], agent_index=index, total_agents=total_agents); perception = perceive(conn, agent["id"])
                report("agent_deciding", f"{resident_label} 正在检索记忆并生成自主决策。", day=new_day, resident_id=agent["id"], agent_name=agent["name"], agent_index=index, total_agents=total_agents); decision_data = decide(conn, agent["id"]); action = decision_data.get("decision", {}).get("action", "observe")
                report("agent_acting", f"{resident_label} 决定执行 {action}。", day=new_day, resident_id=agent["id"], agent_name=agent["name"], agent_index=index, total_agents=total_agents, action=action); execution = execute(conn, agent["id"], decision_data["decision"]); feedback = apply_feedback(conn, agent["id"], execution["action"], execution["result"])
            except Exception as exc:
                logger.exception("Agent %s failed during day %s", agent["id"], new_day); fallback_agents.append(agent["id"]); report("agent_fallback", f"{resident_label} 行动管线异常，降级为观察。", day=new_day, resident_id=agent["id"], agent_name=agent["name"], agent_index=index, total_agents=total_agents, error=type(exc).__name__); conn.rollback(); resident = get_resident(conn, agent["id"])
                try: perception = perceive(conn, agent["id"])
                except Exception: conn.rollback(); perception = {}
                decision_data = {"decision": {"action": "observe", "reason": f"当日行动异常，改为观察并保留状态：{type(exc).__name__}", "tool_input": {"focus": "校园环境"}}}; name = resident["name"] if resident else f"Agent {agent['id']}"; description = f"{name} 当日行动出现异常，改为观察校园环境。"
                execution = {"resident_id": agent["id"], "action": "observe", "reason": decision_data["decision"]["reason"], "result": {"message": "降级观察完成", "description": description, "error": str(exc)}, "success": False}
                try: add_event(conn, new_day, "agent_fallback_observe", description); add_memory(conn, agent["id"], new_day, description, importance=1, source="fallback"); conn.commit()
                except Exception: logger.exception("Fallback record failed for Agent %s", agent["id"]); conn.rollback()
                feedback = {}
            try:
                state_after = get_module_state(conn, agent["id"]); record_log(conn, agent["id"], perception, decision_data, execution, feedback, state_before, state_after); conn.commit(); report("agent_logged", f"{resident_label} 的决策日志已写入。", day=new_day, resident_id=agent["id"], agent_name=agent["name"], agent_index=index, total_agents=total_agents, action=execution["action"], success=execution.get("success", False))
            except Exception: logger.exception("Simulation log failed for Agent %s", agent["id"]); conn.rollback(); report("agent_log_failed", f"{resident_label} 的决策日志写入失败，已继续处理后续 Agent。", day=new_day, resident_id=agent["id"], agent_name=agent["name"], agent_index=index, total_agents=total_agents)
            results.append({"resident_id": agent["id"], "perception": perception, "decision": decision_data, "execution": execution, "environment_feedback": feedback})
        try: report("group_goals", "正在推进群体目标并调整关系紧张度。", day=new_day); group_updates = advance_groups(conn, new_day, [item["execution"] for item in results]); conn.commit()
        except Exception: logger.exception("Group goal update failed for day %s", new_day); conn.rollback(); group_updates = []
        try: report("daily_diaries", "正在为全部 Agent 生成第一人称日记。", day=new_day); daily_diaries = write_diaries(conn, new_day, results); report("campus_news", "正在从当天行动中抽取最多 4 条生成校园新闻。", day=new_day, daily_diaries=len(daily_diaries)); published_news = publish_news(conn, new_day, results); conn.commit()
        except Exception: logger.exception("Daily publishing failed for day %s", new_day); conn.rollback(); daily_diaries, published_news = [], []
        add_event(conn, new_day, "daily_reflect", f"第 {new_day} 天校园自动模拟完成，共产生 {len(results)} 个行动。"); conn.commit(); report("finished", f"第 {new_day} 天模拟完成，共处理 {len(results)} 个 Agent。", day=new_day, actions_count=len(results), fallback_agents=fallback_agents)
        return {"message": "校园一天模拟完成", "day": new_day, "environment": env, "external_information_spread": spread_count, "actions": results, "group_goal_updates": group_updates, "daily_diaries": len(daily_diaries), "published_news": published_news, "fallback_agents": fallback_agents}


def prune_jobs(jobs, lock, now, max_age_seconds=3600):
    cutoff = now() - max_age_seconds
    with lock:
        for job_id in [job_id for job_id, job in jobs.items() if job.get("created_at", 0) < cutoff and job.get("status") != "running"]:
            jobs.pop(job_id, None)


def get_progress(jobs, lock, job_id, after, *, logger, not_found):
    with lock:
        job = jobs.get(job_id)
        if not job: raise not_found()
        events = list(job["events"])
        logger.info("[simulate-day:%s] progress polled | after=%s new=%s status=%s", job_id, after, len(events[after:]), job["status"])
        return {"job_id": job_id, "status": job["status"], "events": events[after:], "next_index": len(events), "result": job["result"], "error": job["error"]}


def start_progress_job(jobs, lock, *, now, new_id, logger, run, thread_factory):
    job_id = new_id(); logger.info("[simulate-day:%s] progress job created", job_id)
    job = {"id": job_id, "status": "running", "events": [{"event": "queued", "message": "模拟任务已启动，正在连接校园世界。"}], "result": None, "error": None, "created_at": now(), "updated_at": now()}
    with lock: jobs[job_id] = job
    def append_event(event):
        with lock:
            current = jobs.get(job_id)
            if not current: return
            current["events"].append(event); current["updated_at"] = now()
        logger.info("[simulate-day:%s] event queued | %s | %s", job_id, event.get("event"), event.get("message"))
    def worker():
        try:
            result = run(append_event)
            with lock:
                current = jobs.get(job_id)
                if current:
                    current["status"] = "complete"; current["result"] = {"message": result["message"], "day": result["day"], "actions_count": len(result["actions"]), "daily_diaries": result["daily_diaries"], "published_news_count": len(result["published_news"]), "fallback_agents": result["fallback_agents"]}; current["events"].append({"event": "complete", "message": result["message"], **current["result"]}); current["updated_at"] = now()
            logger.info("[simulate-day:%s] progress job complete", job_id)
        except Exception as exc:
            logger.exception("Progress simulation failed")
            with lock:
                current = jobs.get(job_id)
                if current:
                    current["status"] = "error"; current["error"] = {"message": str(exc), "type": type(exc).__name__}; current["events"].append({"event": "error", "message": str(exc), "error": type(exc).__name__}); current["updated_at"] = now()
            logger.info("[simulate-day:%s] progress job failed | %s", job_id, type(exc).__name__)
    thread_factory(target=worker, daemon=True).start()
    return {"job_id": job_id, "status": "running", "events": job["events"]}


def start_stream(run, *, queue_factory, thread_factory, logger):
    events = queue_factory()
    def progress(event): events.put(event)
    def worker():
        try:
            result = run(progress); events.put({"event": "complete", "message": result["message"], "day": result["day"], "actions_count": len(result["actions"]), "daily_diaries": result["daily_diaries"], "published_news_count": len(result["published_news"]), "fallback_agents": result["fallback_agents"]})
        except Exception as exc:
            logger.exception("Streaming simulation failed"); events.put({"event": "error", "message": str(exc), "error": type(exc).__name__})
        finally: events.put(None)
    thread_factory(target=worker, daemon=True).start()
    return events
