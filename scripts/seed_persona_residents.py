"""Seed historical persona agents (IDs 21-30) as Visiting Scholars into World2.

This script parses all 10 persona JSON definitions from ``docs/Persona/*.md``,
creates residents 21 to 30 alongside existing campus residents (1-20),
populates their detailed ``persona_engine_spec``, initial relationships,
memories, spatial locations, and healthy body states.
"""

from pathlib import Path
import json
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.db import get_connection, create_database_engine
from app.spatial.seed import seed_spatial_foundation
from tools.city_tools import add_event, add_inventory

PERSONA_FILES = [
    (21, "Kongzi.md", "男", "图书馆", "古代冠服、长须端庄的文雅学者", "/avatars/21_confucius.svg"),
    (22, "Socrates.md", "男", "教学楼", "希腊托加长袍、卷发白须的哲学智者", "/avatars/22_socrates.svg"),
    (23, "Buddha.md", "男", "宿舍区", "金色袈裟、神情宁静祥和的觉者", "/avatars/23_buddha.svg"),
    (24, "DaVinci.md", "男", "教学楼", "文艺复兴长袍、手持草图的博物学家", "/avatars/24_da_vinci.svg"),
    (25, "Shakespeare.md", "男", "操场", "伊丽莎白拉夫领、手握手稿的剧作家", "/avatars/25_shakespeare.svg"),
    (26, "Newton.md", "男", "图书馆", "17世纪外套、拿三棱镜的物理学家", "/avatars/26_newton.svg"),
    (27, "Cixi.md", "女", "校务处", "清代宫廷华服、高髻饰花卉的学者", "/avatars/27_cixi.svg"),
    (28, "Einstein.md", "男", "教学楼", "蓬松白发、灰毛衣拉提琴的理论物理学家", "/avatars/28_einstein.svg"),
    (29, "Hepburn.md", "女", "商业街", "黑裙珍珠项链、举止优雅的人文学者", "/avatars/29_hepburn.svg"),
    (30, "SteveJobs.md", "男", "商业街", "黑色高领衫、圆框眼镜的创客先驱", "/avatars/30_steve_jobs.svg"),
]

INITIAL_HISTORICAL_RELATIONSHIPS = [
    (28, 26, 48, "爱因斯坦对牛顿建立的力学体系表达深厚敬意，常讨论引力与时空"),
    (26, 28, 45, "牛顿对爱因斯坦的广义相对论假设保持审慎关注，并核对数学证明"),
    (21, 22, 45, "孔子与苏格拉底常在学堂与讨论区就“仁”与“美德”开展跨文化对话"),
    (22, 21, 46, "苏格拉底通过诘问法与孔子探讨礼乐与道德修养的本质"),
    (30, 24, 50, "乔布斯与达芬奇在商业街创客空间交流科技与艺术人文的完美交叉"),
    (24, 30, 48, "达芬奇欣赏乔布斯对端到端产品工业美学与极致细节的执念"),
    (25, 29, 42, "莎士比亚与赫本在校园剧场探讨戏剧表演与人性情感的共鸣"),
    (29, 25, 44, "赫本赞赏莎士比亚剧作对人类心灵困境的深刻洞察"),
    (7, 21, 40, "辅导员王老师就现代学生育人理念与孔子交流“因材施教”"),
    (14, 30, 38, "技术宅沈亦舟向乔布斯展示校园小程序并请教产品极简设计"),
    (17, 28, 42, "研究生乔安然就课题研究方法向爱因斯坦请教思维实验范式"),
    (8, 26, 35, "图书馆管理员何管理对牛顿长期在安静区专注查阅文献印象深刻"),
]

def load_persona_data(filename: str) -> dict:
    filepath = PROJECT_ROOT / "docs" / "Persona" / filename
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read().strip()
        if content.startswith("```"):
            lines = content.splitlines()
            content = "\n".join(lines[1:-1])
        return json.loads(content)


def main():
    print("开始导入 10 位历史名人 Persona Agent (IDs 21~30)...")
    
    with get_connection() as conn:
        for resident_id, filename, gender, default_loc, avatar_style, avatar_image in PERSONA_FILES:
            persona_json = load_persona_data(filename)
            
            name = persona_json.get("cn_name") or persona_json.get("name")
            basic = persona_json.get("basic_profile", {})
            cutoff = persona_json.get("knowledge_cutoff", {})
            
            # Construct summary personality & goal
            title_role = "访问学者"
            roles = basic.get("roles", [])
            role_desc = f"访问学者 ({roles[0]})" if roles else "访问学者"
            
            personality_traits = basic.get("trait_interpretation", {})
            if isinstance(personality_traits, dict):
                personality_summary = "、".join(list(personality_traits.keys())[:3])
            else:
                personality_summary = str(personality_traits)
            if not personality_summary:
                personality_summary = "深邃、专注、独立"
            
            raw_goal = basic.get("current_goal")
            if isinstance(raw_goal, dict):
                goal = str(raw_goal.get("description") or raw_goal.get("goal") or list(raw_goal.values())[0])
            elif isinstance(raw_goal, list):
                goal = "；".join(str(g) for g in raw_goal)
            elif raw_goal:
                goal = str(raw_goal)
            else:
                goal = f"在清华大学作为访问学者交流，探索{name}的经典思想与平行宇宙的结合"
                
            money = 400
            
            current_task = f"在清华大学{default_loc}作为访问学者开展研学与交流"
            
            schedule = [
                "08:30 晨间思考与文献阅读",
                f"10:00 在{default_loc}开展研学与思想交流",
                "12:30 校园食堂或近春园用餐",
                "14:30 跨学科研讨与探索",
                "19:00 复盘与私人研究"
            ]
            
            perception = {
                "seeing": f"清华校园环境宜人，{default_loc}学术氛围浓厚",
                "environment_focus": "学术与人文交流",
                "persona_id": persona_json.get("person_id")
            }
            
            # Insert or replace resident record for IDs 21..30
            conn.execute(
                """
                INSERT INTO residents (id, name, role, personality, goal, money, location)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    role=excluded.role,
                    personality=excluded.personality,
                    goal=excluded.goal,
                    money=excluded.money,
                    location=excluded.location
                """,
                (resident_id, name, role_desc, personality_summary, goal, money, default_loc),
            )
            
            strategy_data = {
                "persona_engine_spec": persona_json,
                "persona_version": "persona-engine-v3",
                "knowledge_cutoff": cutoff.get("end") or cutoff.get("scope") or "historical",
                "mental_models_count": len(persona_json.get("mental_models", [])),
                "evidence_count": len(persona_json.get("life_evidence_ledger", [])),
            }
            
            conn.execute(
                """
                INSERT INTO agent_profiles (
                    resident_id, gender, avatar_style, avatar_image, energy, mood,
                    current_task, schedule, perception, strategy
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(resident_id) DO UPDATE SET
                    gender=excluded.gender,
                    avatar_style=excluded.avatar_style,
                    avatar_image=excluded.avatar_image,
                    energy=excluded.energy,
                    mood=excluded.mood,
                    current_task=excluded.current_task,
                    schedule=excluded.schedule,
                    perception=excluded.perception,
                    strategy=excluded.strategy
                """,
                (
                    resident_id,
                    gender,
                    avatar_style,
                    avatar_image,
                    90,
                    "笃定",
                    current_task,
                    json.dumps(schedule, ensure_ascii=False),
                    json.dumps(perception, ensure_ascii=False),
                    json.dumps(strategy_data, ensure_ascii=False),
                ),
            )
            print(f"  [+] 成功注入 Resident {resident_id}: {name} ({role_desc})")
        
        # Seed cross-persona & campus relationships
        for from_id, to_id, score, note in INITIAL_HISTORICAL_RELATIONSHIPS:
            conn.execute(
                """
                INSERT INTO relationships (from_resident_id, to_resident_id, score, notes)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(from_resident_id, to_resident_id) DO UPDATE SET
                    score=excluded.score, notes=excluded.notes
                """,
                (from_id, to_id, score, note),
            )

        # Seed initial memories for the 10 visiting scholars
        for resident_id, filename, _, default_loc, _, _ in PERSONA_FILES:
            persona_json = load_persona_data(filename)
            name = persona_json.get("cn_name") or persona_json.get("name")
            mem_content = f"作为访问学者受邀来到清华大学，在{default_loc}开启交流研学，期待与各学科学者产生深度思想碰撞。"
            conn.execute(
                """
                INSERT INTO memories (resident_id, day, content, importance)
                VALUES (?, 1, ?, 3)
                """,
                (resident_id, mem_content),
            )

        add_event(conn, 1, "system", "10 位历史名人访问学者已成功随机投放到清华大学校园。")
        conn.commit()

    print("开始调用 seed_spatial_foundation 自动补齐 30 位 Agent 的空间图谱与正常身体状态...")
    engine = create_database_engine()
    try:
        with engine.begin() as connection:
            res = seed_spatial_foundation(connection)
            print(f"空间图谱与身体状态同步完成: states={res.get('states_created')}, body_states={res.get('body_states_created')}")
    finally:
        engine.dispose()
        
    print("✅ 10 位历史名人 Persona Agents 适配导入成功！共计 30 位居民现已共同在清华大学生活。")


if __name__ == "__main__":
    main()
