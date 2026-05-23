# OpenRA Game Mechanics — Source Analysis

> Findings from reading the OpenRA engine source (`OpenRA/OpenRA.Mods.Common/Traits/`)
> and the RA mod rules (`OpenRA/mods/ra/rules/`). Focused on the two issues hit during
> the 2026-05-22 session:
>
> 1. Engineers walked past oil derricks without capturing.
> 2. The AI bot kept sending freshly-built units toward the enemy on its own.

---

## 1. Capturing (engineer + oil derrick)

### 1.1 Engineer (`e6`) — `Captures` trait

File: `OpenRA/mods/ra/rules/infantry.yaml` (e6 definition, ~L279-325)

The engineer carries the `Captures` trait (also a `CaptureManager`). Two variants exist:

| Mode | `CaptureDelay` | `ConsumedByCapture` | Trigger condition |
|---|---|---|---|
| Default (`Captures:`) | **200 ticks (~8 s @ 25 Hz)** | `true` — engineer dies on capture | always |
| Reusable (`Captures@REUSABLE:`) | 375 ticks (~15 s) | `false` — survives | `global-reusable-engineers` condition active |

Capture type filter: `CaptureTypes: building` — only matches `Capturable` actors with `Types: building`.

### 1.2 Oil derrick (`oilb`) — `Capturable` trait

File: `OpenRA/mods/ra/rules/civilian.yaml` (oilb definition, ~L518-564)

- `Capturable: { Types: building }` — accepts any captor whose `CaptureTypes` contains `building`.
- **No HP threshold** — capturable at any health.
- `CapturableProgressBar` + `CapturableProgressBlink` give UI feedback during the delay.
- Also has `CashTrickler` — gives the owning player periodic cash once captured.

### 1.3 Capture flow

File: `OpenRA/OpenRA.Mods.Common/Traits/Captures.cs`

Order string is **`"CaptureActor"`** (not `"Capture"`, not `"Enter"`).

```
Player right-click → Captures.IIssueOrder
  → Order("CaptureActor", self, target, queued)
  → Captures.IResolveOrder
  → self.QueueActivity(new CaptureActor(self, order.Target, ...))
```

`CaptureActor` is a subclass of `Enter`. While running:

1. Engineer walks adjacent to the target (`Enter` parent logic).
2. Once positioned, `CaptureManager` (`CaptureManager.cs` ~L180-235) begins
   incrementing `currentTargetDelay` each tick.
3. Both sides receive a condition while the capture is in progress:
   - captor: `CapturingCondition` (default unused in RA)
   - target: `BeingCapturedCondition` (default unused in RA)
4. The target must **not be moving** during the delay (line ~214). Buildings are
   static so this is satisfied; vehicles would interrupt the capture.
5. When `currentTargetDelay >= CaptureDelay`, `StartCapture()` returns true,
   the engineer enters the building, and ownership transfers.

**Net effect:** the engineer must stand next to the derrick for ~8 seconds. The
user's intuition ("capturing requires sitting nearby for a while") matches the
source exactly.

### 1.4 Root cause of the 2026-05-22 bug

`trait_src/McpBridge.cs` `Dispatch()` switch (~L213-250) handles:

| MCP call | Order string issued |
|---|---|
| `attack(unit_ids, target_id)` | `"Attack"` (L445) |
| `move(unit_ids, x, y)` | `"Move"` / `"AttackMove"` (L427) |
| `set_stance` | `"SetUnitStance"` (L472) |
| `deploy` | `"DeployTransform"` |
| `stop` | `"Stop"` |
| `sell` | `"Sell"` |
| `scatter` | `"Scatter"` |

**There is no `"CaptureActor"` / `"Capture"` / `"Enter"` case.** The bridge cannot
issue a capture order today. When we sent `attack(224, 63)` (engineer 224 →
derrick 63), the engineer received an `"Attack"` order. Neutral-building targets
cannot be attacked, so the order was effectively a no-op; the subsequent
`move(224, 82, 48)` walked the engineer to the cell but never queued a capture.

### 1.5 Fix path

Add a new case to `McpBridge.Dispatch()`:

```csharp
case "capture":
    return HandleCapture(root);

string HandleCapture(JsonElement root)
{
    var ids = ReadIntArray(root, "unit_ids");
    var targetId = (uint)root.GetProperty("target_id").GetInt32();
    var target = world.Actors.FirstOrDefault(a => a.ActorID == targetId);
    foreach (var a in ResolveActors(ids))
        world.IssueOrder(new Order("CaptureActor", a, Target.FromActor(target), queued: false));
    return OkJson("issued_orders", ids.Length);
}
```

On the Python side: expose `capture(unit_ids, target_id)` atomic in `server.py`,
and add a `capture` intent (or a `capture_target` field on `attack`) in the DSL.

Acceptance criterion: send `capture([engineer_id], derrick_id)`, engineer walks
adjacent, 8 s delay, ownership flips to `Commander`, cash trickle begins.

---

## 2. Bot autonomous unit movement

### 2.1 Bot module catalog

Directory: `OpenRA/OpenRA.Mods.Common/Traits/BotModules/`

| Module | Purpose |
|---|---|
| `BaseBuilderBotModule` | Place new structures |
| `HarvesterBotModule` | Keep harvesters collecting ore |
| `UnitBuilderBotModule` | Queue units in production buildings |
| `BuildingRepairBotModule` | Auto-repair damaged structures |
| `McvManagerBotModule` | Deploy MCVs into construction yards |
| `CaptureManagerBotModule` | Send engineers to capture buildings (AI side) |
| **`SquadManagerBotModule`** | **Group idle units into squads and attack enemies** |
| `SupportPowerBotModule` | Trigger nukes, paratroopers, airstrikes, etc. |

### 2.2 SquadManagerBotModule — the "send units to enemy" behavior

File: `OpenRA/OpenRA.Mods.Common/Traits/BotModules/SquadManagerBotModule.cs`

Loop (simplified):

1. **Collect idle units** every tick.
2. **Form squads** when ≥ `SquadSize` units accumulate (default 8). Types:
   Assault, Rush, Protection, Air, Naval.
3. **Find closest enemy** via `FindClosestEnemy()` — filtered by weapon range,
   pathability, visibility.
4. **Issue `"Attack"` orders** to every unit in the squad targeting that enemy.
5. **Update each tick** — if target dies, re-target; if squad wiped, dissolve.

Override hook used by our bridge:

- `public CPos? ExternalAttackTarget` (~L129-132) — when set, squads attack
  this cell instead of auto-picked target. `McpBridge.HandleSetBotFocus()` sets
  it; clearing returns control to the autonomous targeter.

### 2.3 Why this fires for the human player

It **shouldn't**, by design. `trait_src/HumanAssistantBot.cs` (~L12-13) intentionally
omits `SquadManagerBotModule` from the human player's PlayerActor. Only **macro**
modules tick for humans (`HarvesterBotModule`, `BaseBuilderBotModule`,
`UnitBuilderBotModule`, `BuildingRepairBotModule`, `McvManagerBotModule`).

But: `OpenRA/mods/ra/rules/strategy_templates.yaml` (generated) attaches
`SquadManagerBotModule@<template>` blocks to *some* actor (likely the player's
PlayerActor when the strategy controller is active). Each block is gated by
`RequiresCondition: enable-strategy-<template>`.

**Hypothesis for the observed bug:** the strategy template patch *does* include
a `SquadManagerBotModule@balanced` (and the same for the other 4 templates) and
those modules are attached to the human player's PlayerActor by mistake. When we
called `set_strategy(template="balanced", ...)`, `StrategyControllerBotModule`
granted `enable-strategy-balanced`, which woke up `SquadManagerBotModule@balanced`
on our PlayerActor — and that module started shipping our units to the enemy.

This needs a source-level audit:

- Inspect the YAML of `strategy_templates.yaml` blocks — does
  `SquadManagerBotModule@balanced` exist? Does it apply to humans?
- Check `HumanAssistantBot.Info` — does it explicitly strip `SquadManagerBotModule`
  from the human PlayerActor before ticking?
- If `StrategyControllerBotModule` grants `enable-strategy-<x>` on humans, the
  human will inherit the entire strategy module bundle.

### 2.4 Fix path

Two options:

**A. Strip combat modules from human PlayerActor.** Edit
`gen_strategy_templates.py` so `SquadManagerBotModule@<template>` blocks emit
`RequiresCondition: enable-strategy-<template> && bot-controlled` (an additional
condition only granted to AI players). Regenerate the YAML. Combat then never
ticks for humans, regardless of template.

**B. Add explicit "macro_only" guard.** Already partially in place via
`HumanAssistantBot.IsEnabled`. Extend `StrategyControllerBotModule.ApplyTemplate()`
to skip the combat-module conditions when running on a human-owned PlayerActor.

Option A is cleaner — the YAML becomes self-documenting and the trait code
doesn't have to know about the human/AI distinction.

### 2.5 Telemetry

To confirm the diagnosis live, add to `get_strategy` response a list of
currently-active conditions on the human's PlayerActor. If
`enable-strategy-balanced` is granted and SquadManagerBotModule is attached,
that proves the leak.

---

## 3. Other relevant traits worth knowing

### 3.1 Production gating

`UnitBuilderBotModule` reads `UnitsToBuild` (unit → priority weight) and
`UnitLimits` (max count). Our `train(e6, count=N)` atomic *bypasses* this and
queues units directly. The 24-engineer-queue surprise in the same session came
from us calling `train(e6, count=5)` and `train(e6, count=4)` multiple times;
the bridge has no rate-limit and no `cancel_production` atomic.

Fix: add `cancel_production(factory_id, queue_item, count)` and expose it as
both an atomic and as a `report.what: queue` accessor so the LLM can see what's
already queued before piling more on.

### 3.2 Capture by the AI

`CaptureManagerBotModule` (different from `CaptureManager` trait) is the AI-side
"send my engineers to capture nearby buildings" routine. It's attached to AI
players via the template YAML, *not* to humans. If we want the human bot helper
to auto-capture for the player, we'd need a new
`HumanAssistantCaptureBotModule` — or just expose `capture` atomic and let
Claude orchestrate it.

### 3.3 Path-blocked moves

The `Move` order silently terminates if the pathfinder can't reach the cell
(water for ground units, etc.). The bridge doesn't surface this; the unit just
stops short. This was on the user's gap list ("water silently blocks ground
units"). Could be fixed by inspecting `Actor.CurrentActivity` after a move order
and reporting "path blocked" in the atomic response.

### 3.4 Stance — what each value actually does

From `OpenRA/OpenRA.Mods.Common/Traits/AttackBase.cs` and `UnitStance` enum:

| Stance | Behavior |
|---|---|
| `HoldFire` | Never attacks, even when shot. Used during retreats. |
| `ReturnFire` | Attacks only attackers, doesn't pursue. Default for many cargo units. |
| `Defend` | Attacks enemies in range, doesn't move out of position. RA "Guard" stance. |
| `AttackAnything` | Pursues any enemy in sight range. Aggressive. |

The DSL `approach: "charge"` maps to `AttackAnything`; `cautious` maps to
`ReturnFire`. `retreat` intent sets `HoldFire`.

---

## 4. Quick-reference: order strings issued by McpBridge today

| MCP atomic | Order string | Notes |
|---|---|---|
| `move(attack_move=false)` | `"Move"` | Stops at first obstacle |
| `move(attack_move=true)` | `"AttackMove"` | Engages enemies en route |
| `attack` | `"Attack"` | Direct-target focus-fire |
| `set_stance` | `"SetUnitStance"` + ExtraData enum | |
| `deploy` | `"DeployTransform"` | MCV → CY; engineer → no-op |
| `stop` | `"Stop"` | Cancels current order |
| `sell` | `"Sell"` | Refunds, removes building |
| `scatter` | `"Scatter"` | Spread out from current cluster |
| `train` | (queues into `ProductionQueue` directly, no order) | |
| `build` | (places building via `PlaceBuilding` event) | |

**Missing:** `CaptureActor`, `Enter`, `EnterTransport`, `Repair`, `Chronoshift`
(deferred for future trait work).

---

## 5. Force-semantics gaps (interpreter, not engine)

Surfaced during the 2026-05-22 "全军出击" test. The DSL translation has three
distinct correctness problems that the engine cannot see — they are pure
interpreter bugs.

### 5.1 `force.name:"all"` includes harvesters and buildings

**Before:** `_force_by_group("all")` returned every owned actor id, so a
charge order moved harvesters off the ore patch and issued no-op attack
moves to immobile structures.

**Fix (2026-05-22):** `_force_by_group` now resolves `all` and `mobile` to
combat-mobile units only (via `_is_combat_mobile` — excludes
`_BUILDING_KINDS ∪ {harv, mcv}`). A new alias `"everything"` preserves the
old "literally every owned actor" semantics for the rare case it's wanted.

LLM-side: when the player says "全军 / 全部 / all units / 全军出击",
use `force.kind:"group", name:"all"` — it now means combat-mobile self
units by default. Harvesters keep mining. Buildings stay put.

### 5.2 No fire concentration on contact

When the interpreter dispatches `attack` with `approach:"frontal"` or
`charge`, it issues:

1. `set_stance` AttackAnything (charge) / Defend (frontal)
2. `attack(unit_ids, target_id)` if target is a unit, or `move(attack_move=true)`
   to the target cell if target is a building.

AttackMove makes each unit auto-engage enemies it sees, but each unit
picks its own nearest target independently. A 30-unit force passing
through a skirmish dribbles fire across many targets and continues
marching even as units in the rear are still engaged.

**Fix path (deferred):** Add a background tactical controller — either a
Python attack-daemon polling `get_state`, or a new C# `AssaultManagerBotModule`
on the human PlayerActor (gated by `enable-human-strategy`). On each tick:

- Compute force centroid + spread.
- Scan enemies within `engage_radius` (~6 cells) of the centroid.
- If any found: issue `Attack(target=closest)` to every force member with
  `queued: true` (a queued order runs after the current move completes).
- When the closest target dies and no enemy is in radius, resume the
  original move-to-target order.

Order queuing requires exposing `queued` in `McpBridge.HandleAttack` /
`HandleMove`. Trivial addition — `Order(orderName, actor, target, queued)`.

### 5.3 No formation cohesion

Fast units (ftrk ~7 c/s, jeep ~10) reach an attack-move target well ahead
of slow units (e1 ~3 c/s). The vanguard arrives alone and gets killed,
then the rear arrives in trickles and gets killed in turn.

**Fix path (deferred):** Same daemon as §5.2. Every K ticks compute the
position spread of the force; if `stddev(positions) > cohesion_max`,
issue `Stop` to the vanguard (units more than `cohesion_max` from the
trailing centroid) until the spread shrinks back. Defaults to start:
K=25 ticks (1 s), cohesion_max=6 cells.

This effectively converts a stampede into a controlled phalanx. It also
makes "frontal" vs "charge" actually differ — `charge` could relax
cohesion (sacrifice cohesion for speed), `frontal` enforces it.

### 5.4 Paper relevance

§5.2 and §5.3 are the cleanest "single-call DSL coordination gap"
narrative for the paper. Ablation experiment:

- Baseline: current interpreter (no concentration, no cohesion).
- +Concentration: engage-on-contact daemon active.
- +Cohesion: cohesion gate active.

Metric: win rate vs OpenRA Normal AI on 5 maps, holding all other strategy
inputs constant. Expected: significant win-rate lift from each
independently and additively from both together. Frames the
single-call DSL approach as "correct in principle but the interpreter
must own the coordination loop, not the LLM."

---

## 6. Action items extracted

1. **`capture` atomic** — add `case "capture"` to `McpBridge.Dispatch()`,
   issuing `Order("CaptureActor", ...)`. Expose Python `capture(unit_ids, target_id)`
   in `server.py`. Add `capture` intent or `attack.mode: "capture"` to the DSL.
2. **Strip `SquadManagerBotModule@*` from human PlayerActors** — either via
   `RequiresCondition` AND-gating with a `bot-controlled` condition, or by
   filtering in `StrategyControllerBotModule.ApplyTemplate()` when the owner
   is human.
3. **`cancel_production` atomic** — for the 24-engineer-queue scenario.
4. **Path-blocked feedback** — surface `Actor.CurrentActivity` state in
   move-order responses.
5. **`report what: queue`** — let the LLM inspect production queues before
   piling on more train orders.
6. **Active-conditions accessor on PlayerActor** — for diagnosing
   strategy/template leaks like the SquadManager bug.
