from app.spatial.location_catalog import _categories


def is_food_location(location):
    cats = _categories(location, set())
    return bool({"consume", "business"} & cats)


def hunger_recovery_instruction(
    *,
    action,
    destination,
    current_location,
    hunger,
    hydration=0,
    hour,
    is_location_open,
):
    candidates = [loc for loc in (destination, current_location) if loc and is_food_location(loc)]
    open_food = [location for location in candidates if is_location_open(location, hour)]
    hunger_threshold = 60 if open_food else 78
    if hydration >= 70 and action not in {"consume", "hydrate"}:
        return {
            "action": "hydrate",
            "location": current_location if is_food_location(current_location) else (open_food[0] if open_food else current_location),
            "goal": "先补充饮水，恢复基础注意力与行动能力",
            "reason": "缺水已达到行动风险阈值，优先取得可用饮水。",
        }
    if hunger < hunger_threshold or action == "consume":
        return None
    if destination and is_food_location(destination) and is_location_open(destination, hour):
        food_location = destination
    elif current_location and is_food_location(current_location) and is_location_open(current_location, hour):
        food_location = current_location
    elif open_food:
        food_location = open_food[0]
    else:
        return {
            "action": "rest",
            "location": current_location or "校园",
            "goal": "餐饮补给暂不可用，先降低消耗并等待补给窗口",
            "reason": "饥饿过高但当前无开放补给点，优先回到安全空间降低消耗。",
        }
    return {
        "action": "consume",
        "location": food_location,
        "goal": "优先补充食物，恢复基础行动能力",
        "reason": "饥饿已接近行动风险阈值，暂缓原计划并寻找可用食物。",
    }
