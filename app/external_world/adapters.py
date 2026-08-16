from __future__ import annotations

import re
from xml.etree import ElementTree

import requests


WEATHER_CODE_MAP = {
    0: "晴",
    1: "晴",
    2: "多云",
    3: "多云",
    45: "雾",
    48: "雾",
    51: "小雨",
    53: "小雨",
    55: "小雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    71: "小雪",
    73: "小雪",
    75: "大雪",
    80: "小雨",
    81: "中雨",
    82: "大雨",
    95: "雷雨",
    96: "雷雨",
    99: "雷雨",
}


def classify_public_report(text):
    normalized = str(text or "").lower()
    if any(word in normalized for word in ("ai", "人工智能", "科技", "技术")):
        return "technology"
    if any(word in normalized for word in ("就业", "招聘", "创业", "商业", "经济")):
        return "career"
    if any(word in normalized for word in ("教育", "大学", "考试", "课程", "学生")):
        return "education"
    return "general"


class OpenMeteoAdapter:
    adapter_key = "open-meteo-v1"

    def fetch(self, config):
        # Beijing / Tsinghua is the project default.  Per-world source
        # configuration overrides this for other geographic simulations.
        latitude = float(config.get("latitude", 40.0062))
        longitude = float(config.get("longitude", 116.3269))
        response = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": latitude,
                "longitude": longitude,
                "current": (
                    "temperature_2m,precipitation,rain,weather_code,"
                    "wind_speed_10m,relative_humidity_2m"
                ),
                "timezone": config.get("timezone", "Asia/Shanghai"),
                "forecast_days": 1,
            },
            headers={"User-Agent": "world2/1.0"},
            timeout=min(30, int(config.get("timeout_seconds", 12))),
        )
        response.raise_for_status()
        current = response.json().get("current", {})
        weather_code = int(current.get("weather_code", 0))
        precipitation = float(current.get("precipitation", 0) or 0)
        rain = float(current.get("rain", 0) or 0)
        rainfall = max(0, min(100, int(round(max(precipitation, rain) * 20))))
        temperature = int(round(float(current.get("temperature_2m", 24))))
        weather = WEATHER_CODE_MAP.get(weather_code, "多云")
        if temperature >= 32 and weather in {"晴", "多云"}:
            weather = "闷热"
        observed_at = str(current.get("time", ""))
        return [
            {
                "source_record_id": (
                    f"weather:{observed_at}:{weather_code}:{temperature}:{rainfall}"
                ),
                "observed_at": observed_at,
                "payload": {
                    "weather": weather,
                    "temperature": temperature,
                    "rainfall": rainfall,
                    "weather_code": weather_code,
                    "wind_speed_10m": current.get("wind_speed_10m"),
                    "relative_humidity_2m": current.get(
                        "relative_humidity_2m"
                    ),
                    "precipitation": precipitation,
                },
            }
        ]


class FixedRSSAdapter:
    adapter_key = "fixed-rss-v1"

    def fetch(self, config):
        primary_url = config.get("feed_url", "")
        fallbacks = config.get("fallback_urls") or [
            "https://36kr.com/feed",
            "https://www.bing.com/news/search?q=(AI%20OR%20university%20OR%20education)&format=rss&setlang=zh-CN&cc=CN",
            "https://www.ithome.com/rss/",
        ]
        urls = []
        if primary_url:
            urls.append(primary_url)
        for u in fallbacks:
            if u and u not in urls:
                urls.append(u)
        limit = min(20, max(1, int(config.get("limit", 5))))
        records = []

        for source_url in urls:
            try:
                response = requests.get(
                    source_url,
                    timeout=(1.5, 2.5),
                    headers={
                        "User-Agent": "World2/1.0 (+world simulation)"
                    },
                )
                response.raise_for_status()
                root = ElementTree.fromstring(response.content)
                items = root.findall("./channel/item")
                if not items:
                    items = root.findall(".//item")
                for index, node in enumerate(items[:limit]):
                    title = (node.findtext("title") or "").strip()
                    summary = re.sub(
                        r"<[^>]+>", "", node.findtext("description") or ""
                    ).strip()
                    link = (node.findtext("link") or "").strip()
                    published_at = (node.findtext("pubDate") or "").strip()
                    guid = (node.findtext("guid") or link or f"item:{index}").strip()
                    if not title:
                        continue
                    records.append(
                        {
                            "source_record_id": guid,
                            "observed_at": "",
                            "payload": {
                                "title": title[:180],
                                "summary": (summary or title)[:400],
                                "link": link,
                                "published_at_text": published_at,
                                "category": classify_public_report(f"{title} {summary}"),
                            },
                        }
                    )
                if records:
                    return records
            except Exception:
                continue

        import time
        ts = int(time.time() // 900)
        synthetic_pool = [
            (
                "全球多所顶尖高校联合发布 AI 高等教育应用导则",
                "导则强调 AI 技术在科研与教学中的辅助作用，建议建立 AI 伦理审查与学术诚信评价体系。",
                "https://news.campus-simulation.edu/ai-education",
                "education",
            ),
            (
                "前沿科技实验室宣布突破新一代多 Agent 模拟算法",
                "该突破显著降低大规模智能体群体的仿真计算延迟，为社会学与经济学模拟提供高精度支持。",
                "https://news.campus-simulation.edu/agent-tech-breakthrough",
                "technology",
            ),
            (
                "高校毕业生就业市场观察：跨学科复合型人才需求持续上升",
                "最新就业分析显示，兼具人工智能基础与专业背景的复合型人才备受用人单位青睐。",
                "https://news.campus-simulation.edu/career-trends",
                "career",
            ),
            (
                "全国大学生创新创业大赛启动，聚焦前沿大模型与软硬件应用",
                "赛事吸引了百余所高校的项目团队报名，设立专项算力资源与孵化基金资助优秀成果。",
                "https://news.campus-simulation.edu/innovation-contest",
                "career",
            ),
            (
                "大学图书资讯中心推出智慧知识库系统，助力学生跨学科研究",
                "新系统整合了数百万篇学术文献与开放数据集，提供智能推荐与语义检索服务。",
                "https://news.campus-simulation.edu/smart-library",
                "education",
            ),
        ]
        selected = synthetic_pool[:limit]
        for idx, (title, summary, link, cat) in enumerate(selected):
            records.append(
                {
                    "source_record_id": f"synthetic:news:{ts}:{idx}",
                    "observed_at": "",
                    "payload": {
                        "title": title,
                        "summary": summary,
                        "link": link,
                        "published_at_text": "",
                        "category": cat,
                    },
                }
            )
        return records


ADAPTERS = {
    OpenMeteoAdapter.adapter_key: OpenMeteoAdapter(),
    FixedRSSAdapter.adapter_key: FixedRSSAdapter(),
}


def get_adapter(adapter_key):
    adapter = ADAPTERS.get(adapter_key)
    if not adapter:
        raise ValueError(f"未注册外部来源适配器：{adapter_key}")
    return adapter
