#region Copyright & License Information
/*
 * Strategy Controller Bot Module for OpenRA / openra_mcp.
 *
 * Lives on the human player's PlayerActor. Acts as the single source of truth
 * for the player's current strategy template (tank_rush / infantry_swarm /
 * balanced / turtle / raid_harass) and broader bot policy state.
 *
 * Architecture (3-layer C2):
 *   Player (NL command)
 *     ↓
 *   LLM (Claude Code via MCP) — translates NL to set_strategy(patch)
 *     ↓
 *   StrategyControllerBotModule (this trait) — receives patch, swaps the
 *     active template condition, updates posture state. Sibling bot modules
 *     (BaseBuilderBotModule@X / UnitBuilderBotModule@X / SquadManagerBotModule@X)
 *     gated by `RequiresCondition: enable-strategy-X` switch on/off accordingly.
 *
 * McpBridge writes via the public SetStrategyPatch(...) method on the sim
 * thread (already serialized by Game.RunAfterTick). No order plumbing required
 * for skirmish (replay/multiplayer would need IResolveOrder later).
 *
 * Drop into OpenRA.Mods.Common/Traits/Player/ (the openra_mcp csproj glob
 * picks files from trait_src/ automatically).
 */
#endregion

using System;
using System.Collections.Generic;
using System.Linq;
using OpenRA.Traits;

namespace OpenRA.Mods.Common.Traits
{
	[TraitLocation(SystemActors.Player)]
	[Desc("Holds the human player's current strategy template; grants per-template " +
		"conditions; mediates between McpBridge and sibling bot modules.")]
	public class StrategyControllerBotModuleInfo : TraitInfo
	{
		[Desc("Template name active at game start.")]
		public readonly string DefaultStrategy = "balanced";

		[Desc("All recognised template names. Each must have a matching set of " +
			"BaseBuilderBotModule@<name> / UnitBuilderBotModule@<name> / " +
			"SquadManagerBotModule@<name> entries gated by `RequiresCondition: " +
			"enable-strategy-<name>`.")]
		public readonly string[] Templates =
		{
			// P1 core
			"tank_rush", "infantry_swarm", "balanced", "turtle", "raid_harass",
			// P3 flagship
			"tesla_wall", "chrono_blitz", "siege_arty", "paratroop_rain",
		};

		[Desc("Granted to the PlayerActor while this trait is active (used by " +
			"template-agnostic macro modules like HarvesterBotModule that should run " +
			"under any strategy).")]
		[GrantedConditionReference]
		public readonly string EnabledCondition = "enable-human-strategy";

		[Desc("Default transition mode when a strategy is swapped mid-game.")]
		public readonly StrategyTransitionMode TransitionMode = StrategyTransitionMode.Hybrid;

		[Desc("Damage taken (HP) within DamageWindowTicks above which the auto " +
			"DefenseState escalates one notch (passive → active → full_alert).")]
		public readonly int DamageEscalationThreshold = 800;

		[Desc("Sliding window length (ticks) for damage accumulation.")]
		public readonly int DamageWindowTicks = 250;

		[Desc("How many ticks between IBotTick evaluations (state trim + focus push).")]
		public readonly int EvaluationInterval = 25;

		[Desc("If true, INotifyDamage events auto-escalate DefenseState. Set false to " +
			"keep state purely under LLM control.")]
		public readonly bool AutoEscalateOnDamage = true;

		[Desc("HP fraction below which a unit triggers a retreat order. Tied to " +
			"RetreatThreshold: never=-1, low=0.15, normal=0.35, high=0.55, always=1.0.")]
		public readonly int RetreatScanInterval = 75;     // ticks (~3s @ 25Hz)

		[Desc("Per-tick interval for the counter_pick enemy scan.")]
		public readonly int CounterPickInterval = 250;

		[Desc("Per-tick interval for the harass squad dispatch.")]
		public readonly int HarassDispatchInterval = 200;

		[Desc("Pulse period (ticks) for spend_ratio pacing. Pause kicks in for a " +
			"fraction of every pulse: all_army=0, army_heavy=0.2, balanced=0.4, " +
			"eco_heavy=0.6, all_eco=0.9 — higher = more pause = less army.")]
		public readonly int SpendRatioPulsePeriod = 50;

		public override object Create(ActorInitializer init) { return new StrategyControllerBotModule(this); }
	}

	public enum StrategyTransitionMode { Soft, Hard, Hybrid }
	public enum HumanDefenseState { Passive, Active, FullAlert }

	// Custom marker interface: a sibling trait can implement this to halt
	// BaseBuilderBotModule.BotTick. Mirrors IBotRequestPauseUnitProduction but
	// targets base building specifically (since OpenRA stock has no equivalent).
	// BaseBuilderBotModule.cs is patched to honour this.
	public interface IBotRequestPauseBaseBuilding
	{
		bool PauseBaseBuilding { get; }
	}

	public sealed class StrategyControllerBotModule
		: INotifyCreated, INotifyOwnerChanged, ITick, INotifyDamage,
		  IBotRequestPauseUnitProduction, IBotRequestPauseBaseBuilding
	{
		readonly StrategyControllerBotModuleInfo info;

		Actor self;                          // PlayerActor
		World world;

		// --- Strategy state -----------------------------------------------
		public string CurrentStrategy { get; private set; }
		public HumanDefenseState DefenseState = HumanDefenseState.Passive;
		public CPos? AttackFocus;
		public CPos? HarassFocus;
		public bool MacroPaused;
		public string SpendRatio;            // "all_eco" | "eco_heavy" | "balanced" | "army_heavy" | "all_army"
		public string TechFocus;
		public string ScoutPriority;
		public string RetreatThreshold;
		public string SupportPowersAuto;
		public string PrimaryObjective;
		public bool CounterPick;
		public bool AutoAdapt = true;
		public bool VerboseReports;

		// --- Condition management -----------------------------------------
		int enabledConditionToken = Actor.InvalidConditionToken;
		readonly Dictionary<string, int> templateConditionTokens = new Dictionary<string, int>();

		// --- For test / diag ----------------------------------------------
		public int LastTransitionTick { get; private set; } = -1;
		public StrategyTransitionMode LastTransitionMode { get; private set; }

		// --- Damage window for auto defense escalation --------------------
		readonly Queue<(int Tick, int Damage)> damageEvents = new Queue<(int, int)>();
		int totalDamageInWindow;
		int nextEvaluationTick;

		// Manual override flag — when LLM explicitly sets defense_state we don't
		// override it via auto-escalation until enough damage accumulates.
		bool defenseStateOverridden;

		// --- P2 behavior wiring -------------------------------------------
		// Tracks last-hp snapshot per owned actor for damage delta calc each tick
		// (works around INotifyDamage on PlayerActor not firing for owned actors).
		readonly Dictionary<uint, int> hpSnapshot = new Dictionary<uint, int>();
		int nextRetreatScanTick;
		int nextCounterPickTick;
		int nextHarassDispatchTick;
		readonly HashSet<uint> recentlyRetreatedUnits = new HashSet<uint>();
		// Init to a large-negative value so the first cooldown check trivially passes,
		// avoiding spam-retreat during the first 500 ticks of the game (audit bug H).
		int retreatCooldownTick = int.MinValue / 2;

		// counter_pick / harass dispatch dedup (units already given an order this cycle).
		readonly HashSet<uint> harassedThisCycle = new HashSet<uint>();

		// Cache last known fact location for retreat target
		CPos? selfBaseCachedLocation;

		public StrategyControllerBotModule(StrategyControllerBotModuleInfo info)
		{
			this.info = info;
		}

		void INotifyCreated.Created(Actor self)
		{
			this.self = self;
			this.world = self.World;

			// Grant baseline condition for template-agnostic macros to enable.
			if (enabledConditionToken == Actor.InvalidConditionToken)
				enabledConditionToken = self.GrantCondition(info.EnabledCondition);

			// Apply default strategy. Skip transition logic since we are setting from null.
			ApplyTemplateInner(info.DefaultStrategy, StrategyTransitionMode.Soft, isInitial: true);
		}

		void ITick.Tick(Actor self)
		{
			if (world == null) return;

			// Always-run lightweight tasks (cheap).
			SnapshotOwnedHpAndAccumulateDamage();

			// Periodic evaluation cluster.
			if (world.WorldTick >= nextEvaluationTick)
			{
				nextEvaluationTick = world.WorldTick + info.EvaluationInterval;
				TrimDamageWindow();
				MaybeAutoEscalateDefense();
				PushAttackFocusToSquadManagers();
			}

			// Retreat scan (own units below threshold get pulled home).
			if (world.WorldTick >= nextRetreatScanTick)
			{
				nextRetreatScanTick = world.WorldTick + info.RetreatScanInterval;
				MaybeRetreatLowHpUnits();
			}

			// Counter-pick: based on enemy composition. Gated by macro_paused.
			if (CounterPick && !MacroPaused && world.WorldTick >= nextCounterPickTick)
			{
				nextCounterPickTick = world.WorldTick + info.CounterPickInterval;
				MaybeRequestCounterPick();
			}

			// Harass squad dispatch. Gated by macro_paused (paused → don't bleed units).
			if (HarassFocus.HasValue && !MacroPaused && world.WorldTick >= nextHarassDispatchTick)
			{
				nextHarassDispatchTick = world.WorldTick + info.HarassDispatchInterval;
				MaybeDispatchHarassSquad();
			}
		}

		// ==================================================================
		// IBotRequestPauseUnitProduction
		// ==================================================================

		bool IBotRequestPauseUnitProduction.PauseUnitProduction
		{
			get
			{
				if (MacroPaused) return true;
				// spend_ratio pacing — pulse pause for a fraction of every period.
				var ratio = SpendRatioToPauseFraction(SpendRatio);
				if (ratio <= 0f) return false;
				if (ratio >= 1f) return true;
				var period = Math.Max(1, info.SpendRatioPulsePeriod);
				var phase = (world?.WorldTick ?? 0) % period;
				return phase < (int)(period * ratio);
			}
		}

		static float SpendRatioToPauseFraction(string ratio)
		{
			switch (ratio)
			{
				case "all_army":    return 0.0f;
				case "army_heavy":  return 0.2f;
				case "balanced":    return 0.4f;
				case "eco_heavy":   return 0.65f;
				case "all_eco":     return 0.9f;
				default:            return 0.0f;
			}
		}

		// ==================================================================
		// IBotRequestPauseBaseBuilding — halt BaseBuilderBotModule when paused.
		// ==================================================================

		bool IBotRequestPauseBaseBuilding.PauseBaseBuilding => MacroPaused;

		// ==================================================================
		// Per-tick behaviors
		// ==================================================================

		void SnapshotOwnedHpAndAccumulateDamage()
		{
			if (self == null) return;
			var owner = self.Owner;
			foreach (var a in world.Actors)
			{
				if (a == null || a.IsDead || !a.IsInWorld || a.Owner != owner) continue;
				if (a.OccupiesSpace == null) continue;
				var h = a.TraitOrDefault<Health>();
				if (h == null) continue;
				if (hpSnapshot.TryGetValue(a.ActorID, out var prev))
				{
					var delta = prev - h.HP;
					if (delta > 0)
					{
						damageEvents.Enqueue((world.WorldTick, delta));
						totalDamageInWindow += delta;
					}
				}
				hpSnapshot[a.ActorID] = h.HP;
			}
			// trim stale entries occasionally
			if (world.WorldTick % 250 == 0 && hpSnapshot.Count > 500)
			{
				var stale = hpSnapshot.Keys.Where(id => world.ActorsHavingTrait<Health>()
					.All(a => a.ActorID != id)).ToArray();
				foreach (var s in stale) hpSnapshot.Remove(s);
			}
		}

		float RetreatHpThreshold()
		{
			switch (RetreatThreshold)
			{
				case "never":  return -1f;
				case "low":    return 0.15f;
				case "normal": return 0.35f;
				case "high":   return 0.55f;
				case "always": return 1.0f;
				default:       return 0.35f;
			}
		}

		void MaybeRetreatLowHpUnits()
		{
			var thresh = RetreatHpThreshold();
			if (thresh <= 0f) return;
			if (self == null) return;
			var owner = self.Owner;

			var basePos = ResolveSelfBaseLocation();
			if (basePos == null) return;
			var target = OpenRA.Traits.Target.FromCell(world, basePos.Value);

			foreach (var a in world.Actors)
			{
				if (a == null || a.IsDead || !a.IsInWorld || a.Owner != owner) continue;
				if (a.OccupiesSpace == null) continue;
				// Skip buildings and harvesters (harv runs its own retreat via HarvesterBotModule).
				if (a.Info.HasTraitInfo<BuildingInfo>()) continue;
				var kind = a.Info.Name;
				if (kind == "harv" || kind == "mcv") continue;
				var h = a.TraitOrDefault<Health>();
				if (h == null || h.MaxHP == 0) continue;
				var frac = (float)h.HP / h.MaxHP;
				if (frac > thresh) continue;
				if (recentlyRetreatedUnits.Contains(a.ActorID)) continue;
				// Issue Move home + ReturnFire stance (don't die fighting on the way).
				world.IssueOrder(new Order("Move", a, target, false));
				recentlyRetreatedUnits.Add(a.ActorID);
			}

			// Periodically clear cooldown so units that came back can be re-ordered later.
			if (world.WorldTick - retreatCooldownTick > 500)
			{
				recentlyRetreatedUnits.Clear();
				retreatCooldownTick = world.WorldTick;
			}
		}

		CPos? ResolveSelfBaseLocation()
		{
			if (self == null) return selfBaseCachedLocation;
			var owner = self.Owner;
			foreach (var a in world.Actors)
			{
				if (a == null || a.IsDead || !a.IsInWorld || a.Owner != owner) continue;
				if (a.Info.Name == "fact")
				{
					selfBaseCachedLocation = a.Location;
					return selfBaseCachedLocation;
				}
			}
			return selfBaseCachedLocation;
		}

		void MaybeRequestCounterPick()
		{
			// Scan enemies, count top kind, pick counter unit, queue request.
			if (self == null) return;
			var owner = self.Owner;
			var enemyKindCount = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
			foreach (var a in world.Actors)
			{
				if (a == null || a.IsDead || !a.IsInWorld) continue;
				if (a.Owner == null || a.Owner == owner) continue;
				if (a.Owner.RelationshipWith(owner) != PlayerRelationship.Enemy) continue;
				if (a.OccupiesSpace == null) continue;
				var k = a.Info.Name;
				enemyKindCount[k] = (enemyKindCount.TryGetValue(k, out var c) ? c : 0) + 1;
			}
			if (enemyKindCount.Count == 0) return;
			var topKind = enemyKindCount.OrderByDescending(kv => kv.Value).First().Key;

			// Map enemy kind → our counter (one-shot table; coarse but useful).
			string counter = null;
			switch (topKind.ToLowerInvariant())
			{
				case "yak": case "mig": case "heli": case "hind": case "mh60": case "u2": case "badr":
					counter = "sam";                         // air → SAM
					break;
				case "1tnk": case "2tnk": case "3tnk": case "4tnk": case "ttnk":
					counter = "e3";                          // tanks → rocket inf
					break;
				case "e1": case "e2": case "e3": case "e4": case "dog": case "shok":
					counter = "ftrk";                        // infantry → mobile flak
					break;
				case "ftrk": case "agun":
					counter = "ttnk";                        // AA → tesla tank
					break;
				default:
					return;
			}
			// Route to the currently-enabled sibling UnitBuilderBotModule so its
			// internal buildRequest queue is biased (audit fix A: was previously
			// stored in dead self dict, no consumer).
			foreach (var ub in self.TraitsImplementing<UnitBuilderBotModule>())
			{
				if (!ub.IsTraitEnabled()) continue;
				if (((IBotRequestUnitProduction)ub).RequestedProductionCount(null, counter) == 0)
					((IBotRequestUnitProduction)ub).RequestUnitProduction(null, counter);
				break;
			}
		}

		void MaybeDispatchHarassSquad()
		{
			if (!HarassFocus.HasValue || self == null) return;
			var target = OpenRA.Traits.Target.FromCell(world, HarassFocus.Value);
			var owner = self.Owner;
			// Reset dedup set every 4 dispatch cycles (~32s) so units that returned can be reused.
			if ((world.WorldTick / Math.Max(1, info.HarassDispatchInterval)) % 4 == 0)
				harassedThisCycle.Clear();

			// Pick up to 4 mobile, raid-capable units that we haven't already dispatched.
			var raidKinds = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
				{ "jeep", "ftrk", "apc", "dog", "heli", "mh60", "hind", "1tnk", "spy" };
			var pool = new List<Actor>();
			foreach (var a in world.Actors)
			{
				if (a == null || a.IsDead || !a.IsInWorld || a.Owner != owner) continue;
				if (a.OccupiesSpace == null) continue;
				if (!raidKinds.Contains(a.Info.Name)) continue;
				if (harassedThisCycle.Contains(a.ActorID)) continue;
				pool.Add(a);
				if (pool.Count >= 4) break;
			}
			if (pool.Count == 0) return;
			foreach (var u in pool)
			{
				world.IssueOrder(new Order("AttackMove", u, target, false));
				harassedThisCycle.Add(u.ActorID);
			}
		}

		void INotifyDamage.Damaged(Actor self, AttackInfo e)
		{
			if (!info.AutoEscalateOnDamage) return;
			if (world == null) return;
			if (e.Damage.Value <= 0) return;

			// PlayerActor itself doesn't take damage; this notification fires on actor
			// instances whose damage propagates here only if we attach. We listen via
			// a sibling pattern: the trait sits on PlayerActor; INotifyDamage on
			// PlayerActor only fires for PlayerActor damage (rare). Real damage events
			// come through child actors — we lift the signal by polling the recently-
			// damaged set ourselves in TrimDamageWindow if needed. For now this hook
			// is a passive observer.
			damageEvents.Enqueue((world.WorldTick, e.Damage.Value));
			totalDamageInWindow += e.Damage.Value;
		}

		void TrimDamageWindow()
		{
			while (damageEvents.Count > 0 &&
				damageEvents.Peek().Tick < world.WorldTick - info.DamageWindowTicks)
			{
				totalDamageInWindow -= damageEvents.Dequeue().Damage;
			}
			if (totalDamageInWindow < 0) totalDamageInWindow = 0;
		}

		void MaybeAutoEscalateDefense()
		{
			if (defenseStateOverridden) return;     // honour explicit LLM setting
			var t = info.DamageEscalationThreshold;
			if (t <= 0) return;
			HumanDefenseState target;
			if (totalDamageInWindow >= t * 4) target = HumanDefenseState.FullAlert;
			else if (totalDamageInWindow >= t) target = HumanDefenseState.Active;
			else target = HumanDefenseState.Passive;
			DefenseState = target;
		}

		void PushAttackFocusToSquadManagers()
		{
			// Mirror our AttackFocus to every SquadManagerBotModule attached to this
			// PlayerActor so the *condition-enabled* one picks it up. Disabled ones
			// also receive it but ignore it (they don't tick).
			if (self == null) return;
			foreach (var sm in self.TraitsImplementing<SquadManagerBotModule>())
				sm.ExternalAttackTarget = AttackFocus;
		}

		// Public accumulator usable by future BuildingDamageObserver helpers
		// (Player-level damage doesn't propagate here; an external observer
		//  trait will eventually call this for each child-actor damage event).
		public void ReportDamage(int damage)
		{
			if (!info.AutoEscalateOnDamage || world == null || damage <= 0) return;
			damageEvents.Enqueue((world.WorldTick, damage));
			totalDamageInWindow += damage;
		}

		void INotifyOwnerChanged.OnOwnerChanged(Actor self, Player oldOwner, Player newOwner)
		{
			// PlayerActor owner can't really change mid-game in skirmish; defensive cleanup.
			ReleaseAllConditions(self);
			this.self = self;
			enabledConditionToken = self.GrantCondition(info.EnabledCondition);
			ApplyTemplateInner(CurrentStrategy ?? info.DefaultStrategy, StrategyTransitionMode.Soft, isInitial: true);
		}

		void ReleaseAllConditions(Actor s)
		{
			if (enabledConditionToken != Actor.InvalidConditionToken)
			{
				enabledConditionToken = s.RevokeCondition(enabledConditionToken);
			}
			foreach (var kv in templateConditionTokens.ToArray())
			{
				if (kv.Value != Actor.InvalidConditionToken)
				{
					s.RevokeCondition(kv.Value);
				}
			}
			templateConditionTokens.Clear();
		}

		// ==================================================================
		// Public API (called from McpBridge.HandleSetStrategy on sim thread)
		// ==================================================================

		/// <summary>
		/// Apply a partial strategy patch. Patch is a dict of field name -> value
		/// (from the JSON wire). Unknown fields are recorded in `rejected` but do
		/// not block the patch.
		/// </summary>
		public StrategyApplyResult ApplyPatch(IDictionary<string, object> patch, StrategyTransitionMode mode)
		{
			var applied = new Dictionary<string, object>();
			var rejected = new Dictionary<string, string>();
			int repurposed = 0;

			if (patch == null)
				return new StrategyApplyResult(applied, rejected, repurposed);

			foreach (var kv in patch)
			{
				try
				{
					switch (kv.Key)
					{
						case "template":
							var tpl = ToStr(kv.Value);
							if (string.IsNullOrEmpty(tpl)) { rejected[kv.Key] = "null_or_empty"; break; }
							if (!info.Templates.Contains(tpl)) { rejected[kv.Key] = "unknown_template"; break; }
							var prev = CurrentStrategy;
							ApplyTemplateInner(tpl, mode, isInitial: false);
							applied[kv.Key] = tpl;
							if (prev != null && prev != tpl)
							{
								repurposed += EstimateRepurposedUnits();
							}
							break;
						case "macro_paused":
							MacroPaused = ToBool(kv.Value);
							applied[kv.Key] = MacroPaused;
							break;
						case "defense_state":
							var ds = ToStr(kv.Value)?.ToLowerInvariant();
							if (ds == "passive") DefenseState = HumanDefenseState.Passive;
							else if (ds == "active") DefenseState = HumanDefenseState.Active;
							else if (ds == "full_alert") DefenseState = HumanDefenseState.FullAlert;
							else { rejected[kv.Key] = "unknown_value"; break; }
							defenseStateOverridden = true;     // pin until auto_adapt re-clears
							applied[kv.Key] = ds;
							break;
						case "attack_focus":
							AttackFocus = IsExplicitNull(kv.Value) ? (CPos?)null : ParseTargetCell(kv.Value);
							applied[kv.Key] = AttackFocus.HasValue
								? (object)new Dictionary<string, int> { { "x", AttackFocus.Value.X }, { "y", AttackFocus.Value.Y } }
								: null;
							break;
						case "harass_focus":
							HarassFocus = IsExplicitNull(kv.Value) ? (CPos?)null : ParseTargetCell(kv.Value);
							// Also clear dispatch dedup so future re-targets can re-grab units.
							if (!HarassFocus.HasValue) harassedThisCycle.Clear();
							applied[kv.Key] = HarassFocus.HasValue
								? (object)new Dictionary<string, int> { { "x", HarassFocus.Value.X }, { "y", HarassFocus.Value.Y } }
								: null;
							break;
						case "clear_attack_focus":
							if (ToBool(kv.Value)) { AttackFocus = null; applied[kv.Key] = true; }
							break;
						case "clear_harass_focus":
							if (ToBool(kv.Value))
							{
								HarassFocus = null;
								harassedThisCycle.Clear();
								applied[kv.Key] = true;
							}
							break;
						case "spend_ratio":
							SpendRatio = ToStr(kv.Value);
							applied[kv.Key] = SpendRatio;
							break;
						case "tech_focus":
							TechFocus = ToStr(kv.Value);
							applied[kv.Key] = TechFocus;
							break;
						case "scout_priority":
							ScoutPriority = ToStr(kv.Value);
							applied[kv.Key] = ScoutPriority;
							break;
						case "retreat_threshold":
							RetreatThreshold = ToStr(kv.Value);
							applied[kv.Key] = RetreatThreshold;
							break;
						case "support_powers_auto":
							SupportPowersAuto = ToStr(kv.Value);
							applied[kv.Key] = SupportPowersAuto;
							break;
						case "primary_objective":
							PrimaryObjective = ToStr(kv.Value);
							applied[kv.Key] = PrimaryObjective;
							break;
						case "counter_pick":
							CounterPick = ToBool(kv.Value);
							applied[kv.Key] = CounterPick;
							break;
						case "auto_adapt":
							AutoAdapt = ToBool(kv.Value);
							// auto_adapt=true releases the manual defense_state pin so future
							// damage events can re-escalate.
							if (AutoAdapt) defenseStateOverridden = false;
							applied[kv.Key] = AutoAdapt;
							break;
						case "verbose_reports":
							VerboseReports = ToBool(kv.Value);
							applied[kv.Key] = VerboseReports;
							break;
						default:
							rejected[kv.Key] = "unknown_field";
							break;
					}
				}
				catch (Exception e)
				{
					rejected[kv.Key] = "exception: " + e.Message;
				}
			}

			// Mirror AttackFocus right away so squads redirect this same tick rather
			// than waiting for the next IBotTick evaluation interval.
			if (applied.ContainsKey("attack_focus"))
				PushAttackFocusToSquadManagers();

			return new StrategyApplyResult(applied, rejected, repurposed);
		}

		public IReadOnlyDictionary<string, object> GetStateDict()
		{
			return new Dictionary<string, object>
			{
				{ "template", CurrentStrategy },
				{ "defense_state", DefenseStateToWire(DefenseState) },
				{ "macro_paused", MacroPaused },
				{ "spend_ratio", SpendRatio },
				{ "tech_focus", TechFocus },
				{ "scout_priority", ScoutPriority },
				{ "retreat_threshold", RetreatThreshold },
				{ "support_powers_auto", SupportPowersAuto },
				{ "primary_objective", PrimaryObjective },
				{ "counter_pick", CounterPick },
				{ "auto_adapt", AutoAdapt },
				{ "verbose_reports", VerboseReports },
				{ "attack_focus", AttackFocus.HasValue
					? (object)new Dictionary<string, int> { { "x", AttackFocus.Value.X }, { "y", AttackFocus.Value.Y } }
					: null },
				{ "harass_focus", HarassFocus.HasValue
					? (object)new Dictionary<string, int> { { "x", HarassFocus.Value.X }, { "y", HarassFocus.Value.Y } }
					: null },
				{ "last_transition_tick", LastTransitionTick },
				{ "last_transition_mode", LastTransitionMode.ToString().ToLowerInvariant() },
			};
		}

		// ==================================================================
		// Internal: template + condition flip
		// ==================================================================

		void ApplyTemplateInner(string template, StrategyTransitionMode mode, bool isInitial)
		{
			if (string.IsNullOrEmpty(template)) return;
			if (template == CurrentStrategy && !isInitial) return;

			// Revoke previously active template condition(s).
			foreach (var t in info.Templates)
			{
				var existing = templateConditionTokens.TryGetValue(t, out var tok) ? tok : Actor.InvalidConditionToken;
				var want = t == template;
				if (want && existing == Actor.InvalidConditionToken)
				{
					templateConditionTokens[t] = self.GrantCondition("enable-strategy-" + t);
				}
				else if (!want && existing != Actor.InvalidConditionToken)
				{
					self.RevokeCondition(existing);
					templateConditionTokens[t] = Actor.InvalidConditionToken;
				}
			}

			CurrentStrategy = template;
			LastTransitionMode = mode;
			LastTransitionTick = world != null ? world.WorldTick : -1;

			if (!isInitial)
				HandleTransition(mode);
		}

		void HandleTransition(StrategyTransitionMode mode)
		{
			if (mode == StrategyTransitionMode.Soft) return;
			if (self == null || world == null) return;

			// 1. Disband squads on every SquadManagerBotModule attached to this PlayerActor.
			//    The next AssignRolesToIdleUnits tick will re-grab the units under the
			//    new template's gating (the SquadManager@<old> is now disabled by the
			//    revoked condition; SquadManager@<new> picks them up).
			foreach (var sm in self.TraitsImplementing<SquadManagerBotModule>())
			{
				// Even disabled SMs hold stale squad references — drain them all.
				try { sm.DisbandAllSquads(); }
				catch (Exception) { /* defensive: never let transition crash */ }
			}

			// 2. Production queue cancellation policy varies by mode.
			//    Find queues owned by this player (each production building has its own
			//    ProductionQueue trait on the building actor).
			//    Hard:   cancel every queued item.
			//    Hybrid: keep items that the NEW template's UnitBuilderBotModule@X
			//            still wants (intersection with UnitsToBuild keys).
			HashSet<string> keep = null;
			if (mode == StrategyTransitionMode.Hybrid)
				keep = CollectKeepList();

			foreach (var queueActor in world.ActorsHavingTrait<ProductionQueue>()
				.Where(a => a.Owner == self.Owner && !a.IsDead && a.IsInWorld))
			{
				foreach (var q in queueActor.TraitsImplementing<ProductionQueue>())
				{
					if (!q.Enabled) continue;
					// Snapshot queued names (we mutate via Orders, so don't iterate live).
					var queued = q.AllQueued().Select(i => i.Item).ToArray();
					foreach (var itemName in queued)
					{
						if (keep != null && keep.Contains(itemName))
							continue;
						world.IssueOrder(Order.CancelProduction(queueActor, itemName, 1));
					}
				}
			}
		}

		HashSet<string> CollectKeepList()
		{
			// Read the UnitBuilderBotModule that is currently enabled (the one
			// gated by enable-strategy-<CurrentStrategy>). Its UnitsToBuild keys
			// are what we keep in the queue during a Hybrid transition.
			var keep = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
			foreach (var ub in self.TraitsImplementing<UnitBuilderBotModule>())
			{
				if (!ub.IsTraitEnabled()) continue;
				foreach (var k in ub.Info.UnitsToBuild.Keys)
					keep.Add(k);
			}
			return keep;
		}

		int EstimateRepurposedUnits()
		{
			// Count own combat units NOT currently in any active squad. After a
			// transition these are the units that will get re-grouped under the
			// new template on next AssignRolesToIdleUnits tick.
			if (self == null || world == null) return 0;
			var owner = self.Owner;
			int free = 0;
			foreach (var a in world.Actors)
			{
				if (a == null || a.IsDead || !a.IsInWorld) continue;
				if (a.Owner != owner) continue;
				if (a.OccupiesSpace == null) continue;
				// Skip buildings (no MobileInfo) and harvesters/MCV (excluded from squads).
				if (a.Info.HasTraitInfo<BuildingInfo>()) continue;
				var kind = a.Info.Name;
				if (kind == "harv" || kind == "mcv") continue;
				free++;
			}
			return free;
		}

		// ==================================================================
		// Parsing helpers (patch values come from JSON, may be JsonElement,
		// IDictionary<string,object>, or raw primitives depending on parser).
		// ==================================================================

		static bool ToBool(object v)
		{
			if (v is bool b) return b;
			if (v is string s) return s == "true" || s == "1" || s == "yes";
			if (v is System.Text.Json.JsonElement je)
			{
				if (je.ValueKind == System.Text.Json.JsonValueKind.True) return true;
				if (je.ValueKind == System.Text.Json.JsonValueKind.False) return false;
				if (je.ValueKind == System.Text.Json.JsonValueKind.String)
				{
					var str = je.GetString();
					return str == "true" || str == "1" || str == "yes";
				}
				if (je.ValueKind == System.Text.Json.JsonValueKind.Number)
					return je.GetInt32() != 0;
			}
			return false;
		}

		// Unwrap a patch value to a string (handles JsonElement / raw string / null).
		// Trims whitespace so enum matches like " active " succeed (audit fix J).
		static string ToStr(object v)
		{
			string s;
			if (v == null) s = null;
			else if (v is string str) s = str;
			else if (v is System.Text.Json.JsonElement je)
			{
				if (je.ValueKind == System.Text.Json.JsonValueKind.Null) return null;
				s = je.ValueKind == System.Text.Json.JsonValueKind.String ? je.GetString() : je.ToString();
			}
			else s = v.ToString();
			return s?.Trim();
		}

		// Detect an explicit null value (vs an unset field). Used by attack_focus /
		// harass_focus to allow the LLM to clear a previously-set target by passing
		// null as the value (audit fix C).
		static bool IsExplicitNull(object v)
		{
			if (v == null) return true;
			if (v is System.Text.Json.JsonElement je && je.ValueKind == System.Text.Json.JsonValueKind.Null)
				return true;
			return false;
		}

		// Map HumanDefenseState enum to canonical wire string (snake_case).
		static string DefenseStateToWire(HumanDefenseState s)
		{
			switch (s)
			{
				case HumanDefenseState.Passive: return "passive";
				case HumanDefenseState.Active: return "active";
				case HumanDefenseState.FullAlert: return "full_alert";
				default: return s.ToString().ToLowerInvariant();
			}
		}

		static CPos? ParseTargetCell(object v)
		{
			// Expect either a JsonElement of shape {"actor_id":?, "pos":{"x":?,"y":?}}
			// or a Dictionary equivalent. actor_id alone (without pos) yields null —
			// the Python side should have resolved to pos already.
			if (v == null) return null;

			if (v is System.Text.Json.JsonElement je)
			{
				if (je.ValueKind == System.Text.Json.JsonValueKind.Null) return null;
				if (je.TryGetProperty("pos", out var posElem) && posElem.ValueKind == System.Text.Json.JsonValueKind.Object)
				{
					var x = posElem.GetProperty("x").GetInt32();
					var y = posElem.GetProperty("y").GetInt32();
					return new CPos(x, y);
				}
				return null;
			}

			if (v is IDictionary<string, object> d)
			{
				if (d.TryGetValue("pos", out var posObj) && posObj is IDictionary<string, object> p)
				{
					int x = Convert.ToInt32(p["x"]);
					int y = Convert.ToInt32(p["y"]);
					return new CPos(x, y);
				}
				return null;
			}

			return null;
		}
	}

	public readonly struct StrategyApplyResult
	{
		public readonly IDictionary<string, object> Applied;
		public readonly IDictionary<string, string> Rejected;
		public readonly int RepurposedUnits;

		public StrategyApplyResult(IDictionary<string, object> applied,
			IDictionary<string, string> rejected,
			int repurposed)
		{
			Applied = applied;
			Rejected = rejected;
			RepurposedUnits = repurposed;
		}
	}
}
