"""Verification script for 30 agents integration in World2."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.db import get_connection

def verify_all():
    print("=== 开始 World2 30位 Agent 完整性验收测试 ===")
    
    with get_connection() as conn:
        # 1. Check total resident count
        count = list(conn.execute("SELECT COUNT(*) AS c FROM residents").fetchone().values())[0]
        print(f"1. 居民总数: {count} (预期: 30)")
        assert count == 30, f"Expected 30 residents, got {count}"
        
        # 2. Check persona figures (21-30) details
        personas = conn.execute(
            """
            SELECT r.id, r.name, r.role, r.location, p.avatar_image, p.energy, p.mood, b.health, b.hunger, b.fatigue
            FROM residents r
            JOIN agent_profiles p ON p.resident_id = r.id
            LEFT JOIN agent_body_states b ON b.resident_id = r.id
            WHERE r.id >= 21 AND r.id <= 30
            ORDER BY r.id
            """
        ).fetchall()
        
        print("\n2. 10 位历史名人 (IDs 21-30) 状态抽查:")
        for p in personas:
            p_dict = dict(p)
            rid = p_dict["id"]
            name = p_dict["name"]
            role = p_dict["role"]
            loc = p_dict["location"]
            avatar = p_dict["avatar_image"]
            energy = p_dict["energy"]
            mood = p_dict["mood"]
            health = p_dict["health"]
            hunger = p_dict["hunger"]
            fatigue = p_dict["fatigue"]
            print(f"  - Agent #{rid:02d} [{name}]: 角色='{role}', 位置='{loc}', 头像='{avatar}', 能量={energy}, 情绪='{mood}', 健康={health:.1f}, 饥饿={hunger:.1f}")
            assert avatar is not None and len(avatar) > 0, f"Avatar missing for agent {rid}"
            assert health > 50, f"Health low for agent {rid}"
            
        # 3. Check model call budget
        runtime = dict(conn.execute("SELECT daily_auto_model_budget, auto_model_calls_used FROM world_runtime WHERE id = 1").fetchone())
        budget = runtime["daily_auto_model_budget"]
        used = runtime["auto_model_calls_used"]
        print(f"\n3. 世界运行模型调用预算: {budget} (已使用: {used})")
        assert budget == 1000, f"Expected model budget 1000, got {budget}"
        
        # 4. Check relationships count & cross-agent links
        rel_count = list(conn.execute("SELECT COUNT(*) AS c FROM relationships").fetchone().values())[0]
        print(f"\n4. 社交关系链条总数: {rel_count}")
        historical_rels = conn.execute(
            """
            SELECT f.name AS f_name, t.name AS t_name, r.score, r.notes
            FROM relationships r
            JOIN residents f ON f.id = r.from_resident_id
            JOIN residents t ON t.id = r.to_resident_id
            WHERE r.from_resident_id >= 21 OR r.to_resident_id >= 21
            """
        ).fetchall()
        print("  - 历史名人跨时空社交关系抽样:")
        for rel in historical_rels[:6]:
            rel_dict = dict(rel)
            print(f"    * {rel_dict['f_name']} -> {rel_dict['t_name']} (亲密度: {rel_dict['score']}): {rel_dict['notes']}")
            
        # 5. Spatial graph integration check
        spatial_states_count = list(conn.execute("SELECT COUNT(*) AS c FROM agent_spatial_states").fetchone().values())[0]
        print(f"\n5. 空间地图定位代理总数: {spatial_states_count} (预期: 30)")
        assert spatial_states_count == 30, f"Expected 30 spatial states, got {spatial_states_count}"

    print("\n✅ 所有 30 位 Agent 协同生活与系统配置验收项全部通过！")

if __name__ == "__main__":
    verify_all()
