"""Seed the canonical residents for a freshly created world.

This script owns people and non-spatial state only. Geography is imported by
``import_tsinghua_world.py`` and the spatial seed binds residents to real POI.
"""

from pathlib import Path
import json
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.db import get_connection
from app.main import DEFAULT_AGENT_PERSONALITY_TRAITS
from tools.city_tools import add_event, add_inventory

CAMPUS_AGENTS = [
    (1, "林小夏", "女", "大一学生", "好奇、外向、喜欢参加活动", "适应校园生活并结交朋友", 120, "宿舍区", "短发圆脸、蓝色卫衣、背双肩包的卡通女生", "/avatars/01_lin_xiaoxia.png", 88, "期待", "参加新生破冰活动", ["08:00 早餐", "10:00 高数课", "15:00 社团招新", "20:00 宿舍复盘"], {"seeing": "宿舍楼下有社团招新海报"}),
    (2, "陈宇航", "男", "大二学生", "自律、理性、重视成绩", "保持绩点并完成课程项目", 110, "教学楼", "戴眼镜、白衬衫、抱着电脑的卡通男生", "/avatars/02_chen_yuhang.png", 76, "专注", "完成课程项目代码", ["08:30 专业课", "13:30 实验室", "19:00 图书馆自习"], {"seeing": "教学楼今天人流稳定"}),
    (3, "赵一鸣", "男", "大三学生", "务实、压力较大、关注就业", "找到实习机会", 130, "图书馆", "黑色夹克、拿简历文件夹的卡通男生", "/avatars/03_zhao_yiming.png", 68, "焦虑", "修改实习简历", ["09:00 查招聘", "14:00 模拟面试", "21:00 投递简历"], {"seeing": "图书馆自习座位紧张"}),
    (4, "苏晴", "女", "学生会干部", "负责、组织力强、关心同学", "办好校园活动", 100, "校务处", "马尾辫、绿色外套、拿活动板夹的卡通女生", "/avatars/04_su_qing.png", 82, "忙碌", "协调校园活动审批", ["09:30 校务处沟通", "12:30 志愿者群通知", "18:00 活动彩排"], {"seeing": "校务处公告栏更新了活动规则"}),
    (5, "周老板", "男", "食堂商家", "精打细算、重视口碑", "提高窗口销量和评分", 280, "食堂", "围裙大叔、笑眯眯端餐盘的卡通男商家", "/avatars/05_zhou_boss.png", 80, "务实", "准备午餐高峰", ["06:30 备菜", "11:30 午餐高峰", "16:00 补货", "20:00 盘点"], {"seeing": "食堂入口排队人数增加"}),
    (6, "李姐", "女", "奶茶店商家", "热情、会营销、反应快", "吸引学生消费", 260, "商业街", "卷发、粉色围裙、拿奶茶杯的卡通女商家", "/avatars/06_li_jie.png", 86, "兴奋", "设计第二杯半价活动", ["10:00 开店", "14:00 上新海报", "18:00 晚高峰促销"], {"seeing": "商业街学生客流变多"}),
    (7, "王老师", "男", "辅导员", "稳重、耐心、关注安全", "维护学生秩序和心理状态", 200, "校务处", "灰色夹克、拿笔记本的卡通男老师", "/avatars/07_wang_teacher.png", 72, "关切", "关注学生压力状态", ["09:00 班会通知", "15:00 个别谈话", "20:00 宿舍走访"], {"seeing": "部分学生临近考试压力升高"}),
    (8, "何管理员", "女", "图书馆管理员", "安静、规则意识强", "保持图书馆有序", 180, "图书馆", "短发、深蓝制服、推书车的卡通女生", "/avatars/08_he_admin.png", 78, "平静", "维护自习秩序", ["08:00 开馆", "12:00 巡查座位", "19:00 安静提醒"], {"seeing": "图书馆二楼座位快满了"}),
    (9, "张晨", "男", "运动社团负责人", "积极、合群、行动力强", "组织训练和比赛", 90, "操场", "运动短发、红色队服、拿篮球的卡通男生", "/avatars/09_zhang_chen.png", 90, "活跃", "组织社团训练", ["07:00 晨跑", "16:30 社团训练", "21:00 发训练通知"], {"seeing": "操场天气适合训练"}),
    (10, "校园后勤", "男", "学校组织", "谨慎、服务导向、关注资源", "保障设施和校园运行", 500, "校务处", "戴安全帽、拿维修工具箱的卡通男工作人员", "/avatars/10_logistics.png", 84, "稳定", "处理设施维修工单", ["08:30 巡检", "13:30 维修", "17:30 反馈处理结果"], {"seeing": "宿舍区有新的维修需求"}),
    (11, "顾南星", "女", "大一学生", "文静、细心、喜欢绘画", "找到适合自己的学习节奏", 115, "图书馆", "长发、米色开衫、抱速写本的卡通女生", "/avatars/11_gu_nanxing.png", 74, "安静", "完成英语阅读", ["09:00 英语课", "14:00 图书馆", "19:30 绘画社"], {"seeing": "图书馆窗边座位很安静"}),
    (12, "许嘉言", "男", "大一学生", "开朗、爱社交、容易分心", "扩大朋友圈", 105, "商业街", "棒球帽、橙色卫衣、挥手的卡通男生", "/avatars/12_xu_jiayan.png", 92, "开心", "约同学一起吃饭", ["10:00 选修课", "12:00 食堂", "16:00 商业街", "22:00 宿舍聊天"], {"seeing": "商业街有新店开业"}),
    (13, "孟雨桐", "女", "大二学生", "理性、独立、计划性强", "准备奖学金申请", 125, "教学楼", "高马尾、黑框眼镜、拿计划本的卡通女生", "/avatars/13_meng_yutong.png", 70, "认真", "整理课程笔记", ["08:00 专业课", "13:00 小组讨论", "20:00 复习"], {"seeing": "教学楼讨论区有空位"}),
    (14, "沈亦舟", "男", "大二学生", "内向、技术宅、喜欢研究工具", "完成一个校园小程序", 118, "宿舍区", "连帽衫、抱笔记本电脑的卡通男生", "/avatars/14_shen_yizhou.png", 66, "沉浸", "调试校园小程序", ["09:30 编程", "15:00 需求调研", "23:00 提交代码"], {"seeing": "宿舍网络状态一般"}),
    (15, "唐晓棠", "女", "大三学生", "外向、表达力强、关注实践", "找到社团和实习机会", 135, "操场", "短发、黄色外套、拿麦克风的卡通女生", "/avatars/15_tang_xiaotang.png", 83, "积极", "采访社团活动", ["10:00 新闻采写", "16:00 操场采访", "21:00 写稿"], {"seeing": "操场社团训练很热闹"}),
    (16, "陆子昂", "男", "大三学生", "稳重、竞争心强、目标明确", "准备考研", 100, "图书馆", "深色卫衣、拿厚书的卡通男生", "/avatars/16_lu_ziang.png", 62, "紧张", "完成考研单词计划", ["07:30 背单词", "10:00 自习", "19:00 刷题"], {"seeing": "图书馆考研区很拥挤"}),
    (17, "乔安然", "女", "研究生", "温和、靠谱、善于协调", "推进课题项目", 160, "教学楼", "白色实验服、拿资料夹的卡通女生", "/avatars/17_qiao_anran.png", 71, "专注", "整理实验数据", ["09:00 课题组会", "14:00 实验", "20:00 数据分析"], {"seeing": "实验室预约时间紧张"}),
    (18, "韩墨", "男", "研究生", "冷静、逻辑强、喜欢独处", "完成论文初稿", 150, "图书馆", "黑色毛衣、端咖啡的卡通男生", "/avatars/18_han_mo.png", 58, "疲惫", "修改论文结构", ["10:00 查文献", "15:00 写论文", "22:00 整理引用"], {"seeing": "安静区适合写作"}),
    (19, "白露", "女", "心理委员", "敏感、共情力强、善于倾听", "帮助同学缓解压力", 95, "宿舍区", "浅蓝外套、拿便签纸的卡通女生", "/avatars/19_bai_lu.png", 79, "关心", "收集同学情绪反馈", ["09:00 上课", "18:00 宿舍交流", "21:00 情绪记录"], {"seeing": "宿舍群里有人抱怨考试压力"}),
    (20, "秦越", "男", "校园创业者", "大胆、灵活、喜欢尝试", "验证校园跑腿服务", 180, "商业街", "黑色背包、拿手机订单的卡通男生", "/avatars/20_qin_yue.png", 87, "兴奋", "测试跑腿订单流程", ["11:00 商业街调研", "17:00 试运行", "22:00 复盘数据"], {"seeing": "商业街订单需求变多"}),
]


INITIAL_RELATIONSHIPS = [
    (1, 11, 35, "同为新生，常在图书馆和宿舍区碰面"),
    (11, 1, 32, "觉得林小夏很热情，愿意一起参加活动"),
    (1, 12, 28, "新生群认识，偶尔一起吃饭"),
    (12, 1, 30, "觉得林小夏适合一起参加社团招新"),
    (2, 13, 40, "同专业课程搭档，一起做课程项目"),
    (13, 2, 42, "认可陈宇航的自律和代码能力"),
    (2, 14, 26, "技术交流伙伴，偶尔一起调试小程序"),
    (14, 2, 30, "遇到技术问题会找陈宇航讨论"),
    (3, 16, 38, "同在图书馆备考和找机会，互相提醒"),
    (16, 3, 34, "理解赵一鸣的就业压力"),
    (3, 20, 22, "关注秦越的校园创业项目，考虑实习合作"),
    (20, 3, 24, "希望赵一鸣帮忙整理商业计划"),
    (4, 7, 45, "学生会活动经常需要辅导员审批"),
    (7, 4, 46, "信任苏晴的组织能力"),
    (4, 9, 34, "准备联合运动社团办校园活动"),
    (9, 4, 35, "希望学生会帮助宣传训练赛"),
    (5, 6, 30, "午晚高峰客流相近，可能合作套餐"),
    (6, 5, 32, "想和食堂窗口做奶茶套餐联动"),
    (5, 10, 25, "食堂设备需要后勤支持"),
    (10, 5, 24, "食堂维修需求稳定"),
    (8, 16, 28, "经常提醒陆子昂注意休息和座位规则"),
    (16, 8, 22, "尊重图书馆管理员的规则"),
    (8, 18, 30, "熟悉韩墨长期在安静区写论文"),
    (18, 8, 26, "感谢图书馆提供稳定写作环境"),
    (17, 18, 36, "研究生同伴，经常交流论文和课题"),
    (18, 17, 34, "愿意和乔安然讨论研究方法"),
    (7, 19, 40, "辅导员和心理委员共同关注学生情绪"),
    (19, 7, 42, "会把宿舍区情绪反馈给王老师"),
    (19, 1, 20, "关注新生适应情况"),
    (1, 19, 18, "觉得白露很会倾听"),
]

INITIAL_MEMORIES = [
    (1, "开学第一天进入宿舍区，看到社团招新海报，想主动认识新朋友。", 2),
    (2, "完成了课程项目分工，计划和孟雨桐一起推进代码。", 2),
    (3, "在图书馆看到实习宣讲信息，意识到需要尽快修改简历。", 3),
    (4, "学生会收到活动审批要求，需要协调辅导员和社团负责人。", 3),
    (5, "食堂午餐高峰临近，套餐饭库存充足但排队压力上升。", 2),
    (6, "商业街客流变多，准备推出第二杯半价吸引学生。", 2),
    (7, "发现部分学生考试压力升高，准备安排班会和个别谈话。", 3),
    (8, "图书馆二楼座位紧张，需要维护自习秩序。", 2),
    (9, "操场天气适合训练，计划组织社团训练赛。", 2),
    (10, "宿舍区出现维修需求，需要安排后勤巡检。", 2),
    (11, "在图书馆找到安静位置，想稳定自己的学习节奏。", 2),
    (12, "商业街有新店开业，想约新同学一起吃饭。", 1),
    (13, "奖学金申请临近，需要整理课程笔记和项目成果。", 3),
    (14, "宿舍网络状态一般，影响校园小程序调试。", 2),
    (15, "操场社团训练热闹，适合写一篇校园活动报道。", 2),
    (16, "考研区很拥挤，背单词计划不能再拖。", 3),
    (17, "实验室预约紧张，需要协调课题组实验时间。", 2),
    (18, "论文初稿结构还不稳定，需要在安静区集中修改。", 3),
    (19, "宿舍群里有人抱怨考试压力，准备收集情绪反馈。", 3),
    (20, "商业街订单需求变多，适合测试校园跑腿服务。", 2),
]
INVENTORY = [
    (5, "套餐饭", 120),
    (5, "早餐券", 80),
    (6, "奶茶", 100),
    (6, "咖啡", 60),
    (10, "维修工单", 40),
    (8, "自习座位", 160),
    (20, "跑腿券", 30),
    (9, "训练名额", 25),
]


def main():
    with get_connection() as conn:
        existing_resident = conn.execute("SELECT 1 FROM residents LIMIT 1").fetchone()
        if existing_resident:
            raise RuntimeError(
                "Fresh resident seed refuses to overwrite existing residents. "
                "Use reset_fresh_world.py for an intentional rebuild."
            )
        conn.execute(
            """
            INSERT INTO simulation_state (key, value) VALUES ('current_day', '1')
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO campus_state (day, weather, semester_stage)
            VALUES (1, '晴', '开学适应期')
            """
        )

        for agent in CAMPUS_AGENTS:
            (
                resident_id,
                name,
                gender,
                role,
                personality,
                goal,
                money,
                location,
                avatar_style,
                avatar_image,
                energy,
                mood,
                current_task,
                schedule,
                perception,
            ) = agent
            conn.execute(
                """
                INSERT INTO residents (id, name, role, personality, goal, money, location)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (resident_id, name, role, personality, goal, money, location),
            )
            conn.execute(
                """
                INSERT INTO agent_profiles (
                    resident_id, gender, avatar_style, avatar_image, energy, mood,
                    current_task, schedule, perception, strategy
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    resident_id,
                    gender,
                    avatar_style,
                    avatar_image,
                    energy,
                    mood,
                    current_task,
                    json.dumps(schedule, ensure_ascii=False),
                    json.dumps(perception, ensure_ascii=False),
                    json.dumps(
                        {
                            "personality_traits": DEFAULT_AGENT_PERSONALITY_TRAITS.get(resident_id, {}),
                            "personality_version": "structured-v1",
                        },
                        ensure_ascii=False,
                    ),
                ),
            )


        for from_id, to_id, score, note in INITIAL_RELATIONSHIPS:
            conn.execute(
                """
                INSERT INTO relationships (from_resident_id, to_resident_id, score, notes)
                VALUES (?, ?, ?, ?)
                """,
                (from_id, to_id, score, note),
            )

        for resident_id, content, importance in INITIAL_MEMORIES:
            conn.execute(
                """
                INSERT INTO memories (resident_id, day, content, importance)
                VALUES (?, 1, ?, ?)
                """,
                (resident_id, content, importance),
            )
        for resident_id, item_name, quantity in INVENTORY:
            add_inventory(conn, resident_id, item_name, quantity)

        add_event(conn, 1, "system", "新世界居民种子已创建；空间位置将在真实地理导入后绑定。")
        conn.commit()

    print("新世界居民种子已创建：20 名 Agent 待绑定至真实地理")


if __name__ == "__main__":
    main()
