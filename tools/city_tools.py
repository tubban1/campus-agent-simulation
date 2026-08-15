import math

from app.economy.service import post_money_transfer
from app.supply.service import settle_goods_trade, supply_runtime_available
from app.market.service import (
    evaluate_market_choice,
    find_market_mechanism,
    fulfill_market_goods_trade,
    market_runtime_available,
)


VALID_LOCATIONS = {
    "宿舍区",
    "教学楼",
    "图书馆",
    "食堂",
    "操场",
    "商业街",
    "校务处",
}


def get_current_day(conn):
    row = conn.execute(
        "SELECT value FROM simulation_state WHERE key = 'current_day'"
    ).fetchone()
    return int(row["value"]) if row else 1


def get_resident(conn, resident_id):
    return conn.execute(
        """
        SELECT id, name, role, personality, goal, money, location
        FROM residents
        WHERE id = ?
        """,
        (resident_id,),
    ).fetchone()


def add_event(conn, day, event_type, description):
    conn.execute(
        """
        INSERT INTO city_events (day, event_type, description)
        VALUES (?, ?, ?)
        """,
        (day, event_type, description),
    )


MEMORY_COLUMN_TYPES = {
    "memory_type": "TEXT NOT NULL DEFAULT 'episodic'",
    "tags": "TEXT NOT NULL DEFAULT ''",
    "source": "TEXT NOT NULL DEFAULT 'action'",
    "last_accessed_at": "TEXT NOT NULL DEFAULT ''",
    "access_count": "INTEGER NOT NULL DEFAULT 0",
}


def ensure_memory_columns(conn):
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(memories)").fetchall()}
    for column, column_type in MEMORY_COLUMN_TYPES.items():
        if column not in columns:
            conn.execute(f"ALTER TABLE memories ADD COLUMN {column} {column_type}")


def infer_memory_metadata(content, memory_type=None, tags=None, source=None):
    text = str(content or "")
    if not memory_type:
        if "日记" in text:
            memory_type = "episodic"
        elif "外部消息" in text or "资讯" in text:
            memory_type = "working"
        elif any(word in text for word in ("信任", "合作", "竞争", "承诺")):
            memory_type = "relationship"
        else:
            memory_type = "episodic"
    if not source:
        source = "diary" if "日记" in text else "action"
    if tags is None:
        tags = []
        for keyword in ("聊天", "交易", "合作", "竞争", "图书馆", "教学楼", "食堂", "宿舍区", "操场", "商业街", "校务处", "外部资讯"):
            if keyword in text:
                tags.append(keyword)
    if isinstance(tags, (list, tuple, set)):
        tags = ",".join(sorted(set(str(tag) for tag in tags if tag)))
    return memory_type, str(tags or ""), source


def add_memory(conn, resident_id, day, content, importance=1, memory_type=None, tags=None, source=None):
    ensure_memory_columns(conn)
    memory_type, tags, source = infer_memory_metadata(content, memory_type, tags, source)
    conn.execute(
        """
        INSERT INTO memories (resident_id, day, content, importance, memory_type, tags, source)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (resident_id, day, content, importance, memory_type, tags, source),
    )


def add_memory_once(conn, resident_id, day, content, importance=1, memory_type=None, tags=None, source=None):
    ensure_memory_columns(conn)
    memory_type, tags, source = infer_memory_metadata(content, memory_type, tags, source)
    existing = conn.execute(
        """
        SELECT id FROM memories
        WHERE resident_id = ? AND day = ? AND content = ? AND source = ?
        LIMIT 1
        """,
        (resident_id, day, content, source),
    ).fetchone()
    if existing:
        return False
    conn.execute(
        """
        INSERT INTO memories (resident_id, day, content, importance, memory_type, tags, source)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (resident_id, day, content, importance, memory_type, tags, source),
    )
    return True


def change_relationship(conn, from_id, to_id, delta, note):
    conn.execute(
        """
        INSERT INTO relationships (from_resident_id, to_resident_id, score, notes)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(from_resident_id, to_resident_id)
        DO UPDATE SET
            score = score + excluded.score,
            notes = CASE
                WHEN relationships.notes = '' THEN excluded.notes
                ELSE relationships.notes || '; ' || excluded.notes
            END
        """,
        (from_id, to_id, delta, note),
    )


def get_inventory_quantity(conn, resident_id, item_name):
    row = conn.execute(
        """
        SELECT quantity FROM inventory
        WHERE resident_id = ? AND item_name = ?
        """,
        (resident_id, item_name),
    ).fetchone()
    return int(row["quantity"]) if row else 0


def add_inventory(conn, resident_id, item_name, quantity):
    conn.execute(
        """
        INSERT INTO inventory (resident_id, item_name, quantity)
        VALUES (?, ?, ?)
        ON CONFLICT(resident_id, item_name)
        DO UPDATE SET quantity = quantity + excluded.quantity
        """,
        (resident_id, item_name, quantity),
    )


def move_resident(conn, resident_id, destination, commit=True):
    resident = get_resident(conn, resident_id)
    if not resident:
        raise ValueError("找不到这个 Agent")
    from app.spatial.location_catalog import is_real_world_location
    if destination not in VALID_LOCATIONS and not is_real_world_location(conn, destination):
        raise ValueError("地点不存在")

    day = get_current_day(conn)
    from app.spatial.runtime import start_spatial_movement

    spatial_result = start_spatial_movement(conn, resident_id, destination)
    if spatial_result is not None:
        description = spatial_result.get(
            "description",
            f"{resident['name']} 正在前往 {destination}。",
        )
        if spatial_result.get("movement_status") == "moving":
            add_event(conn, day, "agent_move_started", description)
            add_memory_once(
                conn,
                resident_id,
                day,
                description,
                importance=2,
                memory_type="episodic",
                tags=["移动", resident["location"], destination],
                source="move",
            )
        if commit:
            conn.commit()
        return {**spatial_result, "description": description}

    conn.execute(
        "UPDATE residents SET location = ? WHERE id = ?",
        (destination, resident_id),
    )
    description = f"{resident['name']} 从 {resident['location']} 移动到 {destination}。"
    add_event(conn, day, "agent_move", description)
    add_memory(conn, resident_id, day, description, importance=2, memory_type="episodic", tags=["移动", resident["location"], destination], source="move")
    if commit:
        conn.commit()
    return {"message": "移动成功", "description": description}


def chat_between(conn, speaker_id, listener_id, message):
    speaker = get_resident(conn, speaker_id)
    listener = get_resident(conn, listener_id)
    if not speaker or not listener:
        raise ValueError("找不到聊天对象")

    day = get_current_day(conn)
    description = f"{speaker['name']} 对 {listener['name']} 说：{message}"
    add_event(conn, day, "agent_chat", description)
    add_memory(conn, speaker_id, day, description, importance=3, memory_type="episodic", tags=["聊天", speaker["name"], listener["name"], speaker["location"]], source="chat")
    add_memory(conn, listener_id, day, description, importance=3, memory_type="episodic", tags=["聊天", speaker["name"], listener["name"], listener["location"]], source="chat")
    change_relationship(conn, speaker_id, listener_id, 2, "校园交流增加熟悉度")
    change_relationship(conn, listener_id, speaker_id, 1, "收到对方交流")
    conn.commit()
    return {"message": "聊天成功", "description": description}


def buy_sell(conn, buyer_id, seller_id, item_name, quantity, unit_price):
    buyer = get_resident(conn, buyer_id)
    seller = get_resident(conn, seller_id)
    if not buyer or not seller:
        raise ValueError("找不到买家或卖家")
    if quantity <= 0 or unit_price <= 0:
        raise ValueError("数量和单价必须大于 0")

    market_evaluation = None
    if market_runtime_available(conn):
        mechanism = find_market_mechanism(
            conn,
            item_name=item_name,
            provider_actor_key=f"resident:{seller_id}",
            location=seller["location"],
        )
        if not mechanism:
            raise ValueError("该商品没有有效市场报价")
        market_evaluation = evaluate_market_choice(
            conn,
            resident_id=buyer_id,
            mechanism_id=int(mechanism["id"]),
            quantity=quantity,
            action_type="buy_sell",
        )
        explicit_max = int(unit_price) * 100
        market_evaluation["maximum_unit_price_minor"] = explicit_max
        if (
            market_evaluation["status"] in {"accepted", "price_rejected"}
            and market_evaluation["total_unit_cost_minor"] <= explicit_max
        ):
            market_evaluation["status"] = "accepted"
            market_evaluation["reason"] = "调用方最高出价覆盖系统报价"
        elif market_evaluation["status"] == "accepted":
            market_evaluation["status"] = "price_rejected"
            market_evaluation["reason"] = "调用方最高出价低于系统报价"
        if market_evaluation["status"] != "accepted":
            raise ValueError(market_evaluation["reason"])
        unit_price = int(
            math.ceil(market_evaluation["total_unit_cost_minor"] / 100)
        )
    total_price = quantity * unit_price
    if int(buyer["money"]) < total_price:
        raise ValueError("买家余额不足")

    stock = get_inventory_quantity(conn, seller_id, item_name)
    if stock < quantity:
        raise ValueError("卖家库存不足")

    day = get_current_day(conn)
    managed_supply = supply_runtime_available(conn)
    if not managed_supply:
        add_inventory(conn, buyer_id, item_name, quantity)
        add_inventory(conn, seller_id, item_name, -quantity)
    transaction_cursor = conn.execute(
        """
        INSERT INTO transactions (buyer_id, seller_id, item_name, quantity, unit_price, total_price)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (buyer_id, seller_id, item_name, quantity, unit_price, total_price),
    )
    legacy_transaction_id = int(transaction_cursor.lastrowid)
    if managed_supply:
        if market_evaluation:
            supply_trade = fulfill_market_goods_trade(
                conn,
                resident_id=buyer_id,
                evaluation=market_evaluation,
                action_execution_id=None,
                source_type="legacy_transaction",
                consume_immediately=False,
            )
        else:
            supply_trade = settle_goods_trade(
                conn,
                transaction_key=f"trade:{legacy_transaction_id}:goods",
                buyer_actor_key=f"resident:{buyer_id}",
                seller_actor_key=f"resident:{seller_id}",
                item_name=item_name,
                quantity=quantity,
                unit_price_minor=unit_price * 100,
                source_type="legacy_transaction",
                source_id=str(legacy_transaction_id),
            )
        ledger_transaction = {"id": supply_trade["ledger_transaction_id"]}
    else:
        ledger_transaction = post_money_transfer(
            conn,
            transaction_key=f"trade:{legacy_transaction_id}:money",
            from_account_key=f"resident:{buyer_id}:cash",
            to_account_key=f"resident:{seller_id}:cash",
            amount_coins=total_price,
            transaction_type="goods_trade",
            source_type="legacy_transaction",
            source_id=str(legacy_transaction_id),
            description=(
                f"{buyer['name']} 向 {seller['name']}购买 "
                f"{quantity} 份 {item_name}"
            ),
            metadata={
                "item_name": item_name,
                "quantity": quantity,
                "unit_price": unit_price,
            },
        )
    description = f"{buyer['name']} 向 {seller['name']} 购买 {quantity} 份 {item_name}，总价 {total_price} 校园币。"
    add_event(conn, day, "trade", description)
    add_memory(conn, buyer_id, day, description, importance=3, memory_type="episodic", tags=["交易", buyer["name"], seller["name"], item_name], source="buy_sell")
    add_memory(conn, seller_id, day, description, importance=3, memory_type="episodic", tags=["交易", buyer["name"], seller["name"], item_name], source="buy_sell")
    conn.commit()
    return {
        "message": "交易成功",
        "description": description,
        "ledger_transaction_id": ledger_transaction["id"],
    }
