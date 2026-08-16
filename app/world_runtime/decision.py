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
    valid_locations=None,
):
    search_locations = []
    for loc in (destination, current_location):
        if loc and loc not in search_locations:
            search_locations.append(loc)

    if valid_locations:
        for loc in valid_locations:
            if loc and loc not in search_locations and is_food_location(loc):
                search_locations.append(loc)
    else:
        for loc in ("食堂", "商业街", "清晏款识", "清芬园", "观畴园", "桃李园", "紫荆园"):
            if loc not in search_locations:
                search_locations.append(loc)

    food_candidates = [loc for loc in search_locations if loc and is_food_location(loc)]
    open_food = [loc for loc in food_candidates if is_location_open(loc, hour)]

    hunger_threshold = 60 if open_food else 78

    if hydration >= 70 and action not in {"consume", "hydrate"}:
        water_location = (
            current_location
            if is_food_location(current_location)
            else (open_food[0] if open_food else (current_location or "宿舍区"))
        )
        return {
            "action": "hydrate" if current_location == water_location else "move",
            "location": water_location,
            "goal": "补充饮水，恢复基础注意力与行动能力",
            "reason": "缺水达到行动风险阈值，优先前往补给点补充饮水。",
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
            "location": current_location or "宿舍区",
            "goal": "餐饮补给暂不可用，先降低消耗并等待补给窗口",
            "reason": "饥饿过高但当前无开放补给点，优先回到安全空间降低消耗。",
        }

    if current_location == food_location:
        return {
            "action": "consume",
            "location": food_location,
            "goal": "优先在补给点用餐，恢复基础行动能力",
            "reason": "饥饿已接近行动风险阈值，暂缓原计划在当前补给点用餐。",
        }
    else:
        return {
            "action": "move",
            "location": food_location,
            "goal": f"前往{food_location}用餐补给",
            "reason": f"饥饿过高，暂缓原计划并前往{food_location}进行用餐补给。",
        }

