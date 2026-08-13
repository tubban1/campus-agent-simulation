import sqlite3
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import app.main as main
from app.economy.schema import ECONOMY_FOUNDATION_SQL
from app.economy.service import seed_economy_foundation
from app.models import SCHEMA_SQL
from app.supply.schema import SUPPLY_FOUNDATION_SQL
from app.supply.service import seed_supply_foundation


class CausalActionRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA_SQL)
        self.conn.execute("INSERT INTO simulation_state (key, value) VALUES ('current_day', '1')")
        self.conn.execute(
            """
            INSERT INTO residents
            (id, name, role, personality, goal, money, location)
            VALUES (1, '结算测试学生', '学生', '认真', '验证行动因果链', 100, '食堂')
            """
        )
        self.conn.execute(
            """
            INSERT INTO agent_profiles
            (resident_id, gender, avatar_style, energy, mood, current_task,
             skills, strategy, schedule, perception)
            VALUES (1, '女', '测试', 80, '平稳', '测试结算', '{}', '{}', '[]', '{}')
            """
        )
        main.SOCIAL_SCHEMA_READY = False
        main.WORLD_SCHEMA_READY = False
        main.ensure_campus_state_table(self.conn, allow_ddl=True)
        main.ensure_space_system(self.conn, allow_ddl=True)
        main.ensure_world_runtime_tables(self.conn, allow_ddl=True)
        self.conn.executescript(ECONOMY_FOUNDATION_SQL)
        seed_economy_foundation(self.conn)
        self.world_time = datetime.fromisoformat("2026-07-29T12:00:00+08:00")

    def tearDown(self):
        self.conn.close()
        main.SOCIAL_SCHEMA_READY = False
        main.WORLD_SCHEMA_READY = False

    def test_rules_cover_every_autonomous_action(self):
        actions = {
            row["action_type"]
            for row in self.conn.execute(
                "SELECT action_type FROM world_action_rules WHERE status = 'active'"
            )
        }
        self.assertEqual(actions, main.WORLD_AUTONOMOUS_ACTIONS)

    def test_unconfigured_llm_does_not_consume_budget(self):
        before = main.get_world_runtime(self.conn)["auto_model_calls_used"]

        with patch.object(main, "is_llm_configured", return_value=False):
            allowed = main.consume_auto_model_budget(
                self.conn, "autonomous_decision", resident_id=1
            )

        after = main.get_world_runtime(self.conn)["auto_model_calls_used"]
        log = self.conn.execute(
            "SELECT status FROM model_call_logs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertFalse(allowed)
        self.assertEqual(after, before)
        self.assertEqual(log["status"], "skipped:llm_unconfigured")

    def test_unconfigured_llm_uses_explicit_rule_decision(self):
        agent = dict(
            self.conn.execute(
                """
                SELECT r.*, p.strategy
                FROM residents r
                JOIN agent_profiles p ON p.resident_id = r.id
                WHERE r.id = 1
                """
            ).fetchone()
        )
        step = {
            "plan_state": "due",
            "action": "observe",
            "location": "食堂",
            "goal": "观察食堂运行",
        }

        with patch.object(main, "is_llm_configured", return_value=False), patch.object(
            main, "ask_llm"
        ) as ask_llm:
            decision = main.build_autonomous_tick_decision(
                self.conn, agent, {"weather": "晴"}, step
            )

        ask_llm.assert_not_called()
        self.assertEqual(decision["mode"], "rule-unconfigured-v1")
        self.assertNotIn("失败", decision["reason"])

    def test_wellbeing_priority_redirects_hungry_agent_to_consume(self):
        self.conn.execute(
            """
            CREATE TABLE agent_body_states (
                resident_id INTEGER PRIMARY KEY,
                hunger REAL NOT NULL,
                fatigue REAL NOT NULL,
                sleep_debt REAL NOT NULL,
                stress REAL NOT NULL,
                attention REAL NOT NULL,
                social_energy REAL NOT NULL,
                health REAL NOT NULL,
                weather_exposure REAL NOT NULL,
                last_updated_at TEXT,
                last_updated_tick INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT 'test',
                version INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.conn.execute(
            """
            INSERT INTO agent_body_states
            (resident_id, hunger, fatigue, sleep_debt, stress, attention,
             social_energy, health, weather_exposure)
            VALUES (1, 50, 30, 20, 20, 60, 60, 80, 0)
            """
        )
        self.conn.execute(
            """
            UPDATE agent_body_states
            SET hunger = 96, fatigue = 30, health = 80, attention = 60
            WHERE resident_id = 1
            """
        )
        self.conn.execute(
            "UPDATE agent_profiles SET energy = 80, time_budget = 0 WHERE resident_id = 1"
        )
        parent = main.append_world_event(
            self.conn,
            "test_tick",
            "测试 tick",
            "开始测试饥饿恢复兜底",
        )
        tick = self.conn.execute(
            """
            INSERT INTO world_ticks
            (tick_index, world_time, day, slot, reason, status)
            VALUES (1, ?, 1, '08:00-16:00', 'test', 'running')
            """,
            (self.world_time.isoformat(),),
        )
        resident = dict(
            self.conn.execute(
                """
                SELECT r.*, p.strategy
                FROM residents r
                JOIN agent_profiles p ON p.resident_id = r.id
                WHERE r.id = 1
                """
            ).fetchone()
        )
        due_plan = {
            "intent": "测试饥饿恢复",
            "steps": [
                {
                    "time": "11:00",
                    "action": "observe",
                    "location": "图书馆",
                    "goal": "观察图书馆",
                }
            ],
        }
        decision = {
            "action": "observe",
            "location": "图书馆",
            "goal": "观察图书馆",
            "reason": "测试原计划",
            "plan_relation": "continue",
            "mode": "test",
        }

        with patch.object(main, "get_current_agent_plan", return_value=due_plan), patch.object(
            main, "build_autonomous_tick_decision", return_value=decision
        ), patch.object(
            main,
            "apply_realism_constraints_to_decision",
            side_effect=lambda conn, agent, item, perception, world_time: item,
        ):
            result = main.process_world_agent_tick(
                self.conn,
                resident,
                self.world_time,
                tick.lastrowid,
                1,
                "08:00-16:00",
                parent_event_id=parent["id"],
            )

        body = self.conn.execute(
            "SELECT hunger FROM agent_body_states WHERE resident_id = 1"
        ).fetchone()
        payload = main.load_json_text(result["event"]["payload"], {})
        self.assertTrue(result["success"])
        self.assertEqual(payload["action"], "consume")
        self.assertLess(body["hunger"], 96)

    def test_news_classification_ignores_internal_llm_fallback_reason(self):
        payload = {
            "runtime_decision": {
                "mode": "rule-error-fallback-v1",
                "reason": "自主决策失败，按原计划执行。",
            },
            "social_effect": {"effect": "positive", "relationship": {"trust": 48}},
        }

        category, score = main.classify_campus_news_candidate(
            "agent_tick",
            action="chat",
            content="林小夏在宿舍区与同学交流。",
            payload=payload,
        )

        self.assertEqual(category, "关系风向")
        self.assertEqual(score, 86)

    def test_explicit_failed_event_remains_breaking_news(self):
        category, score = main.classify_campus_news_candidate(
            "agent_tick_failed",
            action="observe",
            content="行动因空间关闭而失败。",
            payload={"failure_code": "location_closed"},
        )

        self.assertEqual(category, "突发异常")
        self.assertEqual(score, 100)

    def test_observer_events_do_not_enter_news_or_agent_life_course(self):
        main.ensure_agent_news_system(self.conn, allow_ddl=True)
        main.append_world_event(
            self.conn,
            "observer_session",
            "观察者进入世界",
            "browser-observer 正在观察 Agent 1。",
            resident_id=1,
            day=1,
        )

        candidates = main.collect_campus_news_candidates(
            self.conn,
            day=1,
            source_slot="08:00-16:00",
        )
        timeline = main._life_course_timeline(self.conn, resident_id=1)

        self.assertEqual(candidates, [])
        self.assertEqual(timeline, [])

    def test_insufficient_energy_rejects_action_without_charging_resources(self):
        self.conn.execute(
            "UPDATE agent_profiles SET energy = 1, time_budget = 100 WHERE resident_id = 1"
        )
        action = main.begin_world_action_execution(
            self.conn,
            1,
            "club_activity",
            "操场",
            self.world_time.replace(hour=19),
            tick_id=10,
        )
        settlement = main.finalize_rejected_action_execution(self.conn, action)
        stored = self.conn.execute(
            "SELECT * FROM world_action_executions WHERE id = ?",
            (action["id"],),
        ).fetchone()

        self.assertEqual(action["status"], "rejected")
        self.assertEqual(action["failure_code"], "insufficient_energy")
        self.assertEqual(settlement["before"], settlement["after"])
        self.assertEqual(stored["status"], "rejected")

    def test_critical_hunger_uses_campus_meal_when_food_stock_is_empty(self):
        self.conn.execute(
            """
            INSERT INTO residents
            (id, name, role, personality, goal, money, location)
            VALUES (5, '食堂经营者', '食堂商家', '稳定', '保障餐食供应', 100, '食堂')
            """
        )
        self.conn.execute(
            """
            INSERT INTO residents
            (id, name, role, personality, goal, money, location)
            VALUES (6, '饮品经营者', '奶茶店商家', '稳定', '保障饮品供应', 100, '商业街')
            """
        )
        seed_economy_foundation(self.conn)
        self.conn.executescript(SUPPLY_FOUNDATION_SQL)
        seed_supply_foundation(self.conn)
        self.conn.execute(
            """
            CREATE TABLE agent_body_states (
                resident_id INTEGER PRIMARY KEY,
                hunger REAL NOT NULL,
                fatigue REAL NOT NULL,
                sleep_debt REAL NOT NULL,
                stress REAL NOT NULL,
                attention REAL NOT NULL,
                social_energy REAL NOT NULL,
                health REAL NOT NULL,
                weather_exposure REAL NOT NULL,
                last_updated_at TEXT,
                last_updated_tick INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT 'test',
                version INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.conn.execute(
            """
            INSERT INTO agent_body_states
            (resident_id, hunger, fatigue, sleep_debt, stress, attention,
             social_energy, health, weather_exposure)
            VALUES (1, 96, 30, 20, 20, 60, 60, 80, 0)
            """
        )
        self.conn.execute(
            """
            UPDATE inventory_accounts SET quantity_on_hand = 0
            WHERE owner_actor_key = 'resident:5'
              AND item_id = (SELECT id FROM catalog_items WHERE name = '套餐饭')
            """
        )

        action = main.begin_world_action_execution(
            self.conn, 1, "consume", "食堂", self.world_time, tick_id=12
        )
        settlement = main.settle_world_action_resources(self.conn, action, success=True)

        self.assertEqual(action["status"], "pending")
        self.assertIn("emergency_nutrition", action["resources_before"])
        self.assertEqual(settlement["costs"]["money"], 0)
        self.assertEqual(settlement["costs"]["energy"], 0)

    def test_passive_runtime_poll_does_not_consume_daily_resources(self):
        self.conn.execute(
            "UPDATE agent_profiles SET energy = 1, time_budget = 0 WHERE resident_id = 1"
        )
        action = main.begin_world_action_execution(
            self.conn,
            1,
            "observe",
            "食堂",
            self.world_time,
            tick_id=10,
            settlement_mode="passive",
        )
        settlement = main.settle_world_action_resources(self.conn, action, success=True)

        self.assertEqual(action["status"], "pending")
        self.assertEqual(action["rule"]["rule_version"], "passive-tick-v1")
        self.assertEqual(settlement["costs"], {"energy": 0, "time_budget": 0, "money": 0})
        self.assertEqual(settlement["before"], settlement["after"])

    def test_successful_action_settles_resources_and_applies_delayed_effect(self):
        self.conn.execute(
            """
            UPDATE world_action_rules
            SET success_probability = 1.0
            WHERE action_type = 'consume' AND status = 'active'
            """
        )
        root_event = main.append_world_event(
            self.conn,
            "test_tick",
            "测试 tick",
            "开始测试消费结算",
        )
        action = main.begin_world_action_execution(
            self.conn,
            1,
            "consume",
            "食堂",
            self.world_time,
            tick_id=11,
            parent_event_id=root_event["id"],
        )
        self.assertEqual(action["status"], "pending")

        settlement = main.settle_world_action_resources(self.conn, action, success=True)
        action_event = main.append_world_event(
            self.conn,
            "agent_tick",
            "消费行动完成",
            "测试学生完成消费",
            resident_id=1,
            location="食堂",
            source_type="world_action_execution",
            source_id=action["id"],
            parent_event_id=root_event["id"],
            rule_version=action["rule"]["rule_version"],
        )
        main.link_action_execution_event(self.conn, action["id"], action_event["id"])
        delayed_ids = main.enqueue_world_delayed_effects(
            self.conn,
            action,
            action_event["id"],
            self.world_time,
        )
        service_account = self.conn.execute(
            """
            SELECT balance FROM world_resource_accounts
            WHERE account_key = 'campus-services'
            """
        ).fetchone()
        transfer = self.conn.execute(
            """
            SELECT * FROM world_resource_transfers
            WHERE action_execution_id = ?
            """,
            (action["id"],),
        ).fetchone()

        self.assertEqual(settlement["after"]["energy"], 82)
        self.assertEqual(settlement["after"]["time_budget"], 88)
        self.assertEqual(settlement["after"]["money"], 92)
        self.assertEqual(service_account["balance"], 8)
        self.assertEqual(transfer["amount"], 8)
        self.assertEqual(transfer["to_account_key"], "campus-services")
        ledger_transaction = self.conn.execute(
            """
            SELECT * FROM ledger_transactions
            WHERE action_execution_id = ?
            """,
            (action["id"],),
        ).fetchone()
        ledger_entries = self.conn.execute(
            """
            SELECT entry_side, amount_minor
            FROM ledger_entries
            WHERE transaction_id = ?
            ORDER BY entry_side
            """,
            (ledger_transaction["id"],),
        ).fetchall()
        self.assertEqual(
            [(row["entry_side"], row["amount_minor"]) for row in ledger_entries],
            [("credit", 800), ("debit", 800)],
        )
        self.assertEqual(len(delayed_ids), 1)

        delayed_result = main.process_due_world_delayed_effects(
            self.conn,
            self.world_time + timedelta(minutes=61),
            tick_id=12,
            day=1,
            slot="08:00-16:00",
        )
        environment = main.get_campus_environment(self.conn, 1)
        delayed_row = self.conn.execute(
            "SELECT * FROM world_delayed_effects WHERE id = ?",
            (delayed_ids[0],),
        ).fetchone()
        delayed_event = self.conn.execute(
            "SELECT * FROM world_event_stream WHERE id = ?",
            (delayed_row["applied_event_id"],),
        ).fetchone()

        self.assertEqual(environment["consumption_index"], 1.01)
        self.assertEqual(delayed_result["due_count"], 1)
        self.assertEqual(delayed_row["status"], "applied")
        self.assertEqual(delayed_event["parent_event_id"], action_event["id"])
        self.assertEqual(delayed_event["root_event_id"], root_event["id"])

    def test_runtime_records_precondition_failure_as_action_outcome(self):
        self.conn.execute(
            "UPDATE agent_profiles SET time_budget = 0 WHERE resident_id = 1"
        )
        tick = self.conn.execute(
            """
            INSERT INTO world_ticks
            (tick_index, world_time, day, slot, reason, status)
            VALUES (1, ?, 1, '08:00-16:00', 'test', 'running')
            """,
            (self.world_time.isoformat(),),
        )
        parent = main.append_world_event(
            self.conn,
            "world_tick_started",
            "测试 tick",
            "测试行动被拒绝",
            tick_id=tick.lastrowid,
        )
        resident = dict(
            self.conn.execute(
                """
                SELECT r.*, p.strategy
                FROM residents r
                JOIN agent_profiles p ON p.resident_id = r.id
                WHERE r.id = 1
                """
            ).fetchone()
        )
        decision = {
            "action": "observe",
            "location": "食堂",
            "goal": "观察食堂",
            "reason": "测试时间预算约束",
            "plan_relation": "continue",
            "mode": "test",
        }
        due_plan = {
            "intent": "测试资源约束",
            "steps": [
                {
                    "time": "11:00",
                    "action": "observe",
                    "location": "食堂",
                    "goal": "观察食堂",
                }
            ],
        }
        with patch.object(main, "get_current_agent_plan", return_value=due_plan), patch.object(
            main, "build_autonomous_tick_decision", return_value=decision
        ), patch.object(
            main,
            "apply_realism_constraints_to_decision",
            side_effect=lambda conn, agent, item, perception, world_time: item,
        ):
            result = main.process_world_agent_tick(
                self.conn,
                resident,
                self.world_time,
                tick.lastrowid,
                1,
                "08:00-16:00",
                parent_event_id=parent["id"],
            )

        execution = self.conn.execute(
            "SELECT * FROM world_action_executions ORDER BY id DESC LIMIT 1"
        ).fetchone()
        event = self.conn.execute(
            "SELECT * FROM world_event_stream WHERE id = ?",
            (execution["world_event_id"],),
        ).fetchone()
        self.assertTrue(result["success"])
        self.assertFalse(result["action_success"])
        self.assertEqual(execution["status"], "rejected")
        self.assertEqual(execution["failure_code"], "insufficient_time_budget")
        self.assertEqual(event["event_type"], "agent_action_rejected")
        self.assertEqual(event["root_event_id"], parent["id"])

    def test_runtime_action_rolls_back_move_when_event_persistence_fails(self):
        tick = self.conn.execute(
            """
            INSERT INTO world_ticks
            (tick_index, world_time, day, slot, reason, status)
            VALUES (2, ?, 1, '08:00-16:00', 'test', 'running')
            """,
            (self.world_time.isoformat(),),
        )
        parent = main.append_world_event(
            self.conn,
            "world_tick_started",
            "回滚测试 tick",
            "验证行动原子性",
            tick_id=tick.lastrowid,
        )
        self.conn.commit()
        resident = dict(
            self.conn.execute(
                """
                SELECT r.*, p.strategy
                FROM residents r
                JOIN agent_profiles p ON p.resident_id = r.id
                WHERE r.id = 1
                """
            ).fetchone()
        )
        due_plan = {
            "intent": "移动到图书馆",
            "steps": [
                {
                    "time": "11:00",
                    "action": "move",
                    "location": "图书馆",
                    "goal": "前往图书馆",
                }
            ],
        }
        decision = {
            "action": "move",
            "location": "图书馆",
            "goal": "前往图书馆",
            "reason": "测试事务回滚",
            "plan_relation": "continue",
            "mode": "test",
        }
        original_append = main.append_world_event

        def fail_action_event(conn, event_type, *args, **kwargs):
            if event_type == "agent_tick":
                raise RuntimeError("event persistence failed")
            return original_append(conn, event_type, *args, **kwargs)

        with patch.object(main, "get_current_agent_plan", return_value=due_plan), patch.object(
            main, "build_autonomous_tick_decision", return_value=decision
        ), patch.object(
            main,
            "apply_realism_constraints_to_decision",
            side_effect=lambda conn, agent, item, perception, world_time: item,
        ), patch.object(main, "append_world_event", side_effect=fail_action_event):
            result = main.process_world_agent_tick(
                self.conn,
                resident,
                self.world_time,
                tick.lastrowid,
                1,
                "08:00-16:00",
                parent_event_id=parent["id"],
            )

        location = self.conn.execute(
            "SELECT location FROM residents WHERE id = 1"
        ).fetchone()["location"]
        execution_count = self.conn.execute(
            "SELECT COUNT(*) AS count FROM world_action_executions"
        ).fetchone()["count"]
        self.assertFalse(result["success"])
        self.assertEqual(location, "食堂")
        self.assertEqual(execution_count, 0)


if __name__ == "__main__":
    unittest.main()
