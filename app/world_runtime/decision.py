"""Pure decision policies used by the world runtime."""


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
    food_locations = ["食堂", "商业街"]
    open_food = [location for location in food_locations if is_location_open(location, hour)]
    # Start a meal during normal service hours before the body enters the
    # warning band; outside those hours retain a higher emergency threshold.
    hunger_threshold = 60 if open_food else 78
    if hydration >= 70 and action not in {"consume", "hydrate"}:
        return {
            "action": "hydrate",
            "location": current_location if current_location in food_locations else (open_food[0] if open_food else current_location),
            "goal": "先补充饮水，恢复基础注意力与行动能力",
            "reason": "缺水已达到行动风险阈值，优先取得可用饮水。",
        }
    if hunger < hunger_threshold or action == "consume":
        return None
    if destination in food_locations and is_location_open(destination, hour):
        food_location = destination
    elif current_location in food_locations and is_location_open(current_location, hour):
        food_location = current_location
    elif open_food:
        food_location = open_food[0]
    else:
        return {
            "action": "rest",
            "location": "宿舍区",
            "goal": "食堂暂不可用，先降低消耗并等待补给窗口",
            "reason": "饥饿过高但当前无开放补给点，优先回到安全空间降低消耗。",
        }
    return {
        "action": "consume",
        "location": food_location,
        "goal": "优先补充食物，恢复基础行动能力",
        "reason": "饥饿已接近行动风险阈值，暂缓原计划并寻找可用食物。",
    }
