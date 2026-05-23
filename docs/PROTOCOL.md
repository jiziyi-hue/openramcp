# TCP JSON 协议 — Python ↔ OpenRA MCPBridgeTrait

> 端口默认 `127.0.0.1:7777` (可通过 `OPENRA_BRIDGE_PORT` 环境变量改).
> 编码: UTF-8, newline-delimited JSON.
> 模型: 单 client 同步 request/response.

---

## 通用响应字段

每个响应都至少包含:

```jsonc
{
  "ok": true,           // 必填. false 时附 error
  "error": null,        // 可选, ok=false 时必填
  "issued_orders": 0,   // 这次调用实际 dispatch 到 sim 的 Order 数
  "affected_unit_ids": [],
  // ... 命令特有字段
}
```

---

## 命令清单

### 1. `get_state`

请求:
```json
{"type": "get_state", "include_enemies": true}
```

响应:
```jsonc
{
  "ok": true,
  "state": {
    "tick": 1234,
    "paused": false,
    "self_cash": 5000,
    "self_power": 50,
    "self_units": [
      {"id": 12, "kind": "MCV", "owner": "Multi0", "pos": {"x": 30, "y": 28}, "hp_pct": 1.0, "activity": "Idle"}
    ],
    "enemy_units": [],
    "map_name": "Forest Path",
    "map_size": {"x": 96, "y": 96}
  }
}
```

### 2. `list_units`

请求:
```json
{"type": "list_units", "owner": "self", "kind": "Soldier"}
```

`owner`: `"self"` | `"enemy"` | `null` (全部). `kind`: 可选, null = 全部 kind.

响应: 同 `get_state` 的 `units` 字段 (UnitInfo[]).

### 3. `find_unit`

请求:
```json
{"type": "find_unit", "description": "重型坦克 (东边那个)"}
```

响应: UnitInfo[] (服务端尽力解析, 可能空).

### 4. `build`

请求:
```json
{"type": "build", "structure": "Refinery", "near": {"x": 32, "y": 28}, "count": 2}
```

- `structure`: OpenRA actor type (大小写敏感, e.g. `"Refinery"`, `"Barracks"`, `"WarFactory"`, `"Powr"`, `"ConstructionYard"`)
- `near`: 可选, 偏好放置位置. 引擎找最近合法格
- `count`: 队列数量

响应: `{ok, issued_orders, affected_unit_ids: []}` (建好的 actor id 不在这, 走 event)。

### 5. `train`

请求:
```json
{"type": "train", "unit": "Soldier", "count": 5, "factory_id": null}
```

- `unit`: e.g. `"Soldier"`, `"HeavyTank"`, `"Engineer"`
- `factory_id`: null = 任意合适工厂. 指定 id 必须存在且是有效工厂

### 6. `move`

请求:
```json
{"type": "move", "unit_ids": [12, 13, 14], "target": {"x": 50, "y": 70}, "attack_move": false}
```

`attack_move=true` 走 A-move (路上遇敌交火).

### 7. `attack`

请求:
```json
{"type": "attack", "unit_ids": [12, 13], "target_id": 88}
```

集火指定 enemy actor. 单位会移动到射程内.

### 8. `set_stance`

请求:
```json
{"type": "set_stance", "unit_ids": [12, 13], "stance": "Defend"}
```

stance 枚举: `HoldFire` | `ReturnFire` | `Defend` | `AttackAnything`.

### 9. `pause` / `resume`

请求:
```json
{"type": "pause"}
```

仅 single-player 生效.

### 10. `screenshot`

请求:
```json
{"type": "screenshot"}
```

响应:
```json
{"ok": true, "screenshot_b64": "iVBORw0KG..."}
```

---

## 错误码 (建议)

`error` 字段是人可读字符串. 未来加 `error_code` 枚举:

- `not_connected` — TCP 未建立
- `parse_error` — JSON 解析失败
- `unknown_command`
- `invalid_target` — 目标 id 不存在
- `not_buildable` — structure 不在玩家科技树
- `no_factory` — train 时没匹配工厂
- `game_not_running` — 还在主菜单, 没进 skirmish
- `internal` — 引擎抛异常

---

## 事件推送 (后期, 当前不实现)

未来 trait 可以**主动推**事件 (单位死了 / 建好了 / 敌方现身) 给 Python 端:

```jsonc
{"event": "unit_built", "id": 42, "kind": "Refinery", "pos": {"x":32, "y":28}}
{"event": "unit_killed", "id": 17}
{"event": "enemy_spotted", "id": 88, "kind": "HeavyTank", "pos": {"x":..,"y":..}}
```

需要扩协议成全双工 (current 是 1:1 同步 request/response). 后续版本.

---

## 线程模型

- Python 这端: 单线程 MCP server, 每个 tool 调用 = 一次同步 send_command.
- OpenRA 这端: trait 在专门的 listener 线程上 accept TCP, 但所有 mutation 调用 `world.AddFrameEndTask` 投递到 sim 线程执行.

→ Python 端 send_command 调用阻塞直到 sim tick 处理完 + 响应回 (典型 < 200 ms, 一个 OpenRA tick 是 40 ms = 25 Hz).

---

**End of Protocol v0.1**
