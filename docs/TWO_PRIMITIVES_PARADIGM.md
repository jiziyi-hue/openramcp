# Two Primitives + LLM Composition

> Phase E7 architectural commitment (2026-05-25).
> Replaces the leader-based + per-type FSM approach explored in Phase D-E6.

---

## TL;DR

Engine-side squad FSMs are pared down to **two primitives**:

- **Assault** — push a unit set toward a target cell or actor.
- **Protection** — defend a target cell; engage threats nearby.

All higher-level tactical behaviors — patrol, escort, harass, exploration,
pincer, feint, combined arms, multi-prong attack — are **composed by the
LLM (or a Python helper standing in for the LLM)** via temporal
sequencing of `spawn_squad_batch` calls.

---

## Why

Earlier phases tried to model each tactic as a dedicated SquadType +
state machine: Patrol, Escort, Harass, Explore. They ran into:

1. **Leader-based FSMs thrash** at scale (Phase D-E3 finding: 17-40 unit
   squads rally-gate ping-pong, see `project-rally-gate-scales-poorly`).
2. **Boids-style FSMs** (Phase E4-E5) fixed Assault but the other types
   were brittle — pre-queued waypoint loops didn't honor `queued=true`
   append, re-issue-on-arrival cycles failed to advance the cursor for
   reasons that resist short debugging.
3. **Strategy in FSM = layering violation** (`project-squad-execution-vs-strategy`).
   Harass picked targets, Explore picked spokes — both are decisions
   that belong at the LLM layer.

Meanwhile **Assault** worked beautifully (T10/T11/T12: 80+ units to 4
corners in 25s, 0 losses). The lesson: the engine is great at
"push these units to this cell"; composition logic should live above it.

---

## The new architecture

```
┌──────────────────────────────────────┐
│  Human (strategic intent)            │
│  "take the north oil derrick"        │
└────────────┬─────────────────────────┘
             │
┌────────────▼─────────────────────────┐
│  LLM (tactical composition)          │
│  Decomposes intent into ordered      │
│  spawn_squad_batch calls. Tracks     │
│  squad cursors, advances on arrival, │
│  issues new batches on events.       │
└────────────┬─────────────────────────┘
             │ spawn_squad_batch
             ▼
┌──────────────────────────────────────┐
│  C# Squad FSM (execution only)       │
│  • Assault: AttackMove to target;    │
│    AutoTarget handles combat.        │
│  • Protection: defend a cell.        │
│  Issue orders only on target/idle    │
│  changes (no per-tick spam).         │
└────────────┬─────────────────────────┘
             │ per-unit Order
             ▼
┌──────────────────────────────────────┐
│  Engine (per-unit autonomy)          │
│  ActivityQueue + AutoTarget +        │
│  pathfinding + collision.            │
└──────────────────────────────────────┘
```

Three responsibility levels, cleanly separated:

| Layer       | Decides                        | Lives in          |
|-------------|--------------------------------|-------------------|
| Human       | Strategy ("what should happen")| Player's brain    |
| LLM         | Tactics ("which squads, when") | Python / MCP loop |
| Squad FSM   | Execution ("how to push")      | C# (≤2 classes)   |
| Unit/Engine | Behavior (path, fire, dodge)   | OpenRA traits     |

---

## Composing higher-level tactics

### Patrol (cycle through N waypoints)

```python
cursor = 0
spawn_squad_batch([{
    "squad_type": "Assault",
    "unit_ids": squad_ids,
    "target_pos": waypoints[cursor],
}])

# Each tick of the LLM-side loop:
while running:
    state = get_state()
    centroid = mean([state.unit_pos(uid) for uid in squad_ids])
    if distance(centroid, waypoints[cursor]) < 4:
        cursor = (cursor + 1) % len(waypoints)
        spawn_squad_batch([{ ... target_pos = waypoints[cursor] }])
```

`mcp_server/tools/compose_patrol.py` is a working demo of this for
4 squads cycling 4 corners.

### Escort

```python
while escortee.alive:
    if escortee.moved_since_last_issue:
        spawn_squad_batch([{
            "squad_type": "Assault",
            "unit_ids": guards,
            "target_pos": escortee.pos,
        }])
```

### Harass (rotating economy targets)

```python
while harassers.alive:
    target = closest_enemy_economy_structure(squad_centroid)
    if target != last_target:
        spawn_squad_batch([{
            "squad_type": "Assault",
            "unit_ids": harassers,
            "target_actor_id": target.id,
        }])
```

### Combined arms (tanks lead, infantry trail)

```python
spawn_squad_batch([
    { "squad_type": "Assault", "unit_ids": tanks,    "target_pos": (target.x - 2, target.y) },
    { "squad_type": "Assault", "unit_ids": infantry, "target_pos": (target.x + 2, target.y) },
])
```

### Pincer

```python
spawn_squad_batch([
    { "squad_type": "Assault", "unit_ids": north_force, "target_pos": enemy_fact_north_approach },
    { "squad_type": "Assault", "unit_ids": south_force, "target_pos": enemy_fact_south_approach },
])
```

### Feint + raid

```python
spawn_squad_batch([
    { "squad_type": "Assault",    "unit_ids": feint, "target_pos": enemy_base },
    { "squad_type": "Assault",    "unit_ids": raid,  "target_pos": enemy_fact },
])
```

---

## What got archived

`archive/squad_fsm_attempts/`:
- `PatrolStates.cs` — pre-queued circuit (queued=true append issues)
- `EscortStates.cs` — re-issue-on-escortee-move
- `ExploreStates.cs` — 8-spoke spiral, cursor-driven

Code preserved for paper write-up and possible future revival; not built.

---

## Paper framing

> "Two engine primitives, LLM-composed tactics."
>
> The OpenRA engine exposes only two squad primitives (Assault, Protection)
> through the MCP protocol. The LLM composes patrol, escort, harass, and
> combined-arms behaviors at the event boundary — re-issuing batched
> spawn_squad commands when a squad arrives, dies, or the player updates
> intent. This produces ~10× fewer LLM API calls per minute than per-tick
> control schemes (e.g. OpenRA-RL) while retaining tactical expressivity.

Contrast OpenRA-RL: per-tick LLM-in-the-loop with 48 atomic engine tools.
Contrast HIVE/HIMA: multi-step plan JSON, but on a toy / text engine.
We sit in between — task-level intent on a real RTS engine.

---

## Where this leaves the project

- C# squad code shrinks to ~2 working primitive FSMs + supporting types.
- Composition demos live in `mcp_server/tools/compose_*.py`.
- Paper-relevant memory: `project-squad-execution-vs-strategy`,
  `project-boids-squad-architecture`, `project-rally-gate-scales-poorly`,
  `project-paper-ab-pause-insight`.
