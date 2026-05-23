#region Copyright & License Information
/*
 * MCP Bridge Trait for OpenRA.
 * Listens on a TCP port and accepts newline-delimited JSON commands from
 * an external MCP server (see ../mcp_server/). Commands are dispatched to
 * the OpenRA sim thread via Game.RunAfterTick.
 *
 * Drop this file into OpenRA.Mods.Common/Traits/World/ — the csproj uses an
 * implicit glob so no project edit is needed. Enable per-mod in world.yaml:
 *
 *     World:
 *         ...
 *         McpBridge:
 *             Port: 7777
 */
#endregion

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;
using System.Threading;
using OpenRA.Graphics;
using OpenRA.Mods.Common.Traits;
using OpenRA.Primitives;
using OpenRA.Traits;

namespace OpenRA.Mods.Common.Traits
{
	[TraitLocation(SystemActors.World)]
	[Desc("Opens a TCP server on localhost that accepts JSON commands from an external MCP server. " +
		"All actor mutations are dispatched onto the sim thread via Game.RunAfterTick. " +
		"Add to the world.yaml of any mod (e.g. RA) to enable.")]
	public class McpBridgeInfo : TraitInfo
	{
		[Desc("TCP port to bind. Default 7777.")]
		public readonly int Port = 7777;

		[Desc("Bind address. Default loopback (127.0.0.1).")]
		public readonly string Host = "127.0.0.1";

		[Desc("Maximum accepted clients. Only one is processed at a time.")]
		public readonly int Backlog = 4;

		[Desc("Verbose log to console.")]
		public readonly bool Verbose = true;

		public override object Create(ActorInitializer init) { return new McpBridge(init.Self, this); }
	}

	public sealed class McpBridge : IWorldLoaded, INotifyActorDisposing
	{
		readonly McpBridgeInfo info;
		readonly Actor selfActor;
		World world;
		WorldRenderer worldRenderer;

		Thread listenerThread;
		TcpListener listener;
		volatile bool stopping;

		// ---- Group state -------------------------------------------------
		// Player units bucketed into named cohorts (e.g. north / center / south).
		// First lookup auto-initializes by splitting along Y (or X) into N buckets.
		readonly object groupLock = new object();
		readonly Dictionary<string, HashSet<uint>> groups = new Dictionary<string, HashSet<uint>>();
		bool groupsInitialized;
		int groupCount = 3;
		int groupAxis = 1;  // 0 = X, 1 = Y
		static readonly string[] Names3Y = { "north", "center", "south" };
		static readonly string[] Names3X = { "west", "center", "east" };
		static readonly string[] Names2Y = { "north", "south" };
		static readonly string[] Names2X = { "west", "east" };

		public McpBridge(Actor self, McpBridgeInfo info)
		{
			this.selfActor = self;
			this.info = info;
		}

		public void WorldLoaded(World w, WorldRenderer wr)
		{
			world = w;
			worldRenderer = wr;

			try
			{
				var ip = IPAddress.Parse(info.Host);
				listener = new TcpListener(ip, info.Port);
				listener.Start(info.Backlog);
				listenerThread = new Thread(AcceptLoop)
				{
					IsBackground = true,
					Name = "McpBridge listener"
				};
				listenerThread.Start();
				Log("listening on " + info.Host + ":" + info.Port);
			}
			catch (Exception e)
			{
				Console.Error.WriteLine("[McpBridge] failed to start TCP listener: " + e.Message);
			}
		}

		public void Disposing(Actor self)
		{
			stopping = true;
			try
			{
				listener?.Stop();
			}
			catch (Exception) { /* swallow */ }
		}

		void Log(string msg)
		{
			if (info.Verbose)
				Console.WriteLine("[McpBridge] " + msg);
		}

		void AcceptLoop()
		{
			while (!stopping)
			{
				TcpClient client;
				try
				{
					client = listener.AcceptTcpClient();
				}
				catch (SocketException)
				{
					return;
				}
				catch (ObjectDisposedException)
				{
					return;
				}

				Log("client connected: " + client.Client.RemoteEndPoint);
				var t = new Thread(() => HandleClient(client))
				{
					IsBackground = true,
					Name = "McpBridge client"
				};
				t.Start();
			}
		}

		void HandleClient(TcpClient client)
		{
			using (client)
			using (var stream = client.GetStream())
			using (var reader = new StreamReader(stream, Encoding.UTF8))
			using (var writer = new StreamWriter(stream, new UTF8Encoding(false)) { AutoFlush = true, NewLine = "\n" })
			{
				var writeLock = new object();

				while (!stopping)
				{
					string line;
					try
					{
						line = reader.ReadLine();
					}
					catch (IOException)
					{
						break;
					}

					if (line == null)
						break;
					if (line.Length == 0)
						continue;

					Log("recv: " + (line.Length > 200 ? line.Substring(0, 200) + "..." : line));

					var capturedLine = line;
					Game.RunAfterTick(() =>
					{
						string responseJson;
						try
						{
							responseJson = Dispatch(capturedLine);
						}
						catch (Exception e)
						{
							var trace = e.StackTrace ?? string.Empty;
							var first = trace.Length > 400 ? trace.Substring(0, 400) : trace;
							responseJson = ErrorJson("dispatch_exception", e.Message + " | " + first);
						}

						try
						{
							lock (writeLock)
								writer.WriteLine(responseJson);
						}
						catch (Exception) { /* socket closed */ }
					});
				}

				Log("client disconnected");
			}
		}

		// ====================================================================
		// Dispatch
		// ====================================================================

		string Dispatch(string line)
		{
			using var doc = JsonDocument.Parse(line);
			var root = doc.RootElement;

			if (!root.TryGetProperty("type", out var typeProp))
				return ErrorJson("parse_error", "missing 'type'");

			var type = typeProp.GetString();
			switch (type)
			{
				case "get_state": return HandleGetState(root);
				case "list_units": return HandleListUnits(root);
				case "find_unit": return HandleFindUnit(root);
				case "build": return HandleBuild(root);
				case "train": return HandleTrain(root);
				case "move": return HandleMove(root);
				case "attack": return HandleAttack(root);
				case "capture": return HandleCapture(root);
				case "set_stance": return HandleSetStance(root);
				case "pause": return HandlePause(true);
				case "resume": return HandlePause(false);
				case "screenshot": return HandleScreenshot(root);
				case "deploy": return HandleActorOnlyOrder(root, "DeployTransform");
				case "stop": return HandleActorOnlyOrder(root, "Stop");
				case "sell": return HandleActorOnlyOrder(root, "Sell");
				case "scatter": return HandleActorOnlyOrder(root, "Scatter");
				case "set_bot_focus": return HandleSetBotFocus(root);
				case "list_groups": return HandleListGroups(root);
				case "move_group": return HandleMoveGroup(root);
				case "attack_group": return HandleAttackGroup(root);
				case "stance_group": return HandleStanceGroup(root);
				case "assign_to_group": return HandleAssignGroup(root);
				case "rebalance_groups": return HandleRebalanceGroups(root);
				case "set_strategy": return HandleSetStrategy(root);
				case "get_strategy": return HandleGetStrategy(root);
				default:
					return ErrorJson("unknown_command", "type=" + (type ?? "<null>"));
			}
		}

		// --- get_state ------------------------------------------------------

		string HandleGetState(JsonElement root)
		{
			var includeEnemies = !root.TryGetProperty("include_enemies", out var ie) || ie.GetBoolean();
			var self = LocalPlayer();

			using var ms = new MemoryStream();
			using (var w = new Utf8JsonWriter(ms))
			{
				w.WriteStartObject();
				w.WriteBoolean("ok", true);
				w.WriteStartObject("state");
				w.WriteNumber("tick", world.WorldTick);
				w.WriteBoolean("paused", world.Paused);
				w.WriteString("map_name", world.Map.Title ?? string.Empty);
				w.WriteStartObject("map_size");
				w.WriteNumber("x", world.Map.MapSize.X);
				w.WriteNumber("y", world.Map.MapSize.Y);
				w.WriteEndObject();

				if (self != null)
				{
					int cash = 0, resources = 0, power = 0;
					var pr = self.PlayerActor.TraitOrDefault<PlayerResources>();
					if (pr != null)
					{
						cash = pr.Cash;
						resources = pr.Resources;
					}
					var pm = self.PlayerActor.TraitOrDefault<PowerManager>();
					if (pm != null)
						power = pm.ExcessPower;

					w.WriteNumber("self_cash", cash + resources);
					w.WriteNumber("self_power", power);
				}
				else
				{
					w.WriteNumber("self_cash", 0);
					w.WriteNumber("self_power", 0);
				}

				w.WriteStartArray("self_units");
				if (self != null)
					foreach (var a in OwnedActors(self))
						WriteUnitInfo(w, a);
				w.WriteEndArray();

				w.WriteStartArray("enemy_units");
				if (includeEnemies && self != null)
					foreach (var a in EnemyActors(self))
						WriteUnitInfo(w, a);
				w.WriteEndArray();

				w.WriteEndObject();
				w.WriteEndObject();
			}

			return Encoding.UTF8.GetString(ms.ToArray());
		}

		// --- list_units / find_unit ----------------------------------------

		string HandleListUnits(JsonElement root)
		{
			var owner = root.TryGetProperty("owner", out var oo) ? oo.GetString() : null;
			var kind = root.TryGetProperty("kind", out var kk) ? kk.GetString() : null;

			IEnumerable<Actor> source;
			var self = LocalPlayer();
			if (owner == "self") source = self != null ? OwnedActors(self) : Enumerable.Empty<Actor>();
			else if (owner == "enemy") source = self != null ? EnemyActors(self) : Enumerable.Empty<Actor>();
			else source = world.Actors.Where(IsRealActor);

			if (!string.IsNullOrEmpty(kind))
				source = source.Where(a => a.Info.Name.Equals(kind, StringComparison.OrdinalIgnoreCase));

			using var ms = new MemoryStream();
			using (var w = new Utf8JsonWriter(ms))
			{
				w.WriteStartObject();
				w.WriteBoolean("ok", true);
				w.WriteStartArray("units");
				foreach (var a in source.Take(500))
					WriteUnitInfo(w, a);
				w.WriteEndArray();
				w.WriteEndObject();
			}
			return Encoding.UTF8.GetString(ms.ToArray());
		}

		string HandleFindUnit(JsonElement root)
		{
			// Simple fuzzy fallback: match description against actor type name (case-insensitive substring).
			// Future: integrate with the MCP server LLM-side resolver.
			var desc = root.TryGetProperty("description", out var d) ? (d.GetString() ?? string.Empty) : string.Empty;
			desc = desc.ToLowerInvariant();
			var matches = world.Actors
				.Where(a => IsRealActor(a) && a.Info.Name.ToLowerInvariant().Contains(desc))
				.Take(50);

			using var ms = new MemoryStream();
			using (var w = new Utf8JsonWriter(ms))
			{
				w.WriteStartObject();
				w.WriteBoolean("ok", true);
				w.WriteStartArray("units");
				foreach (var a in matches)
					WriteUnitInfo(w, a);
				w.WriteEndArray();
				w.WriteEndObject();
			}
			return Encoding.UTF8.GetString(ms.ToArray());
		}

		// --- build (production queue) --------------------------------------

		string HandleBuild(JsonElement root)
		{
			var structure = root.GetProperty("structure").GetString();
			var count = root.TryGetProperty("count", out var cp) ? cp.GetInt32() : 1;
			return HandleProduction(structure, count);
		}

		string HandleTrain(JsonElement root)
		{
			var unit = root.GetProperty("unit").GetString();
			var count = root.TryGetProperty("count", out var cp) ? cp.GetInt32() : 1;
			return HandleProduction(unit, count);
		}

		string HandleProduction(string itemName, int count)
		{
			var self = LocalPlayer();
			if (self == null)
				return ErrorJson("no_local_player", "no human player in this world");

			// Search all production queues owned by the player; find one whose
			// buildable items contain itemName.
			ProductionQueue chosenQueue = null;
			foreach (var queue in self.PlayerActor.TraitsImplementing<ProductionQueue>())
			{
				if (queue.AllItems().Any(b => b.Name.Equals(itemName, StringComparison.OrdinalIgnoreCase)))
				{
					chosenQueue = queue;
					break;
				}
			}

			if (chosenQueue == null)
				return ErrorJson("not_buildable", itemName + " not produced by any owned queue");

			// Queue actor on which to issue StartProduction.
			var queueActor = chosenQueue.Actor;
			world.IssueOrder(Order.StartProduction(queueActor, itemName, count));

			return OkJson("issued_orders", 1);
		}

		// --- move / attack / set_stance ------------------------------------

		string HandleMove(JsonElement root)
		{
			var ids = ReadIntArray(root, "unit_ids");
			var target = ReadCell(root, "target");
			var attackMove = root.TryGetProperty("attack_move", out var am) && am.GetBoolean();
			var orderName = attackMove ? "AttackMove" : "Move";

			var actors = ResolveActors(ids).ToArray();
			if (actors.Length == 0)
				return ErrorJson("invalid_target", "no actors matched");

			foreach (var a in actors)
				world.IssueOrder(new Order(orderName, a, Target.FromCell(world, target), queued: false));

			return OkJson("issued_orders", actors.Length, actors.Select(a => (int)a.ActorID).ToArray());
		}

		string HandleAttack(JsonElement root)
		{
			var ids = ReadIntArray(root, "unit_ids");
			var targetId = (uint)root.GetProperty("target_id").GetInt32();
			var targetActor = world.Actors.FirstOrDefault(a => a.ActorID == targetId && !a.IsDead && a.IsInWorld);
			if (targetActor == null)
				return ErrorJson("invalid_target", "target id " + targetId + " not found");

			var actors = ResolveActors(ids).ToArray();
			if (actors.Length == 0)
				return ErrorJson("invalid_target", "no attacker actors matched");

			foreach (var a in actors)
				world.IssueOrder(new Order("Attack", a, Target.FromActor(targetActor), queued: false));

			return OkJson("issued_orders", actors.Length, actors.Select(a => (int)a.ActorID).ToArray());
		}

		// --- capture (engineer → Capturable building) ----------------------
		// Issues OpenRA's "CaptureActor" order (see Captures.cs in engine).
		// The Captures trait on the engineer resolves it and queues a
		// CaptureActor activity which walks adjacent then runs CaptureDelay
		// (~200 ticks for default e6) before transferring ownership.
		string HandleCapture(JsonElement root)
		{
			var ids = ReadIntArray(root, "unit_ids");
			var targetId = (uint)root.GetProperty("target_id").GetInt32();
			var targetActor = world.Actors.FirstOrDefault(a => a.ActorID == targetId && !a.IsDead && a.IsInWorld);
			if (targetActor == null)
				return ErrorJson("invalid_target", "target id " + targetId + " not found");

			var actors = ResolveActors(ids).ToArray();
			if (actors.Length == 0)
				return ErrorJson("invalid_target", "no captor actors matched");

			foreach (var a in actors)
				world.IssueOrder(new Order("CaptureActor", a, Target.FromActor(targetActor), queued: false));

			return OkJson("issued_orders", actors.Length, actors.Select(a => (int)a.ActorID).ToArray());
		}

		string HandleSetStance(JsonElement root)
		{
			var ids = ReadIntArray(root, "unit_ids");
			var stance = root.GetProperty("stance").GetString();
			var actors = ResolveActors(ids).ToArray();

			// "SetUnitStance" with target argument as the stance index.
			// AutoTarget recognises this order string.
			int stanceIndex;
			switch (stance)
			{
				case "HoldFire": stanceIndex = (int)UnitStance.HoldFire; break;
				case "ReturnFire": stanceIndex = (int)UnitStance.ReturnFire; break;
				case "Defend": stanceIndex = (int)UnitStance.Defend; break;
				case "AttackAnything": stanceIndex = (int)UnitStance.AttackAnything; break;
				default:
					return ErrorJson("invalid_target", "unknown stance " + stance);
			}

			foreach (var a in actors)
			{
				// Order encodes stance as ExtraData int.
				var o = new Order("SetUnitStance", a, false) { ExtraData = (uint)stanceIndex };
				world.IssueOrder(o);
			}

			return OkJson("issued_orders", actors.Length, actors.Select(a => (int)a.ActorID).ToArray());
		}

		// --- bot focus (路 D: hint bot SquadManagers where to attack) ----

		string HandleSetBotFocus(JsonElement root)
		{
			CPos? hint = null;
			if (root.TryGetProperty("target", out var t) && t.ValueKind != JsonValueKind.Null)
			{
				hint = new CPos(
					t.GetProperty("x").GetInt32(),
					t.GetProperty("y").GetInt32());
			}

			int affected = 0;
			foreach (var p in world.Players)
			{
				if (!p.IsBot) continue;
				var sm = p.PlayerActor.TraitOrDefault<SquadManagerBotModule>();
				if (sm == null) continue;
				sm.ExternalAttackTarget = hint;
				affected++;
			}

			using var ms = new MemoryStream();
			using (var w = new Utf8JsonWriter(ms))
			{
				w.WriteStartObject();
				w.WriteBoolean("ok", true);
				w.WriteNumber("bots_hinted", affected);
				if (hint.HasValue)
				{
					w.WriteStartObject("hint");
					w.WriteNumber("x", hint.Value.X);
					w.WriteNumber("y", hint.Value.Y);
					w.WriteEndObject();
				}
				else
				{
					w.WriteNull("hint");
				}
				w.WriteEndObject();
			}
			return Encoding.UTF8.GetString(ms.ToArray());
		}

		// --- strategy (路 D: HumanAssistantBot template hot-swap) -----------

		string HandleSetStrategy(JsonElement root)
		{
			var self = LocalPlayer();
			if (self == null)
				return ErrorJson("no_local_player", "no human player in this world");

			var controller = self.PlayerActor.TraitOrDefault<StrategyControllerBotModule>();
			if (controller == null)
				return ErrorJson("not_configured",
					"StrategyControllerBotModule not attached to PlayerActor");

			if (!root.TryGetProperty("patch", out var patchElem) ||
				patchElem.ValueKind != JsonValueKind.Object)
				return ErrorJson("parse_error", "missing 'patch' object");

			StrategyTransitionMode mode = StrategyTransitionMode.Soft;
			if (root.TryGetProperty("transition_mode", out var tm) && tm.ValueKind == JsonValueKind.String)
			{
				switch ((tm.GetString() ?? string.Empty).ToLowerInvariant())
				{
					case "soft": mode = StrategyTransitionMode.Soft; break;
					case "hard": mode = StrategyTransitionMode.Hard; break;
					case "hybrid": mode = StrategyTransitionMode.Hybrid; break;
					default:
						return ErrorJson("parse_error", "unknown transition_mode");
				}
			}

			// Convert the JsonElement patch to IDictionary<string, object> for the trait.
			var patchDict = new Dictionary<string, object>();
			foreach (var prop in patchElem.EnumerateObject())
				patchDict[prop.Name] = prop.Value;  // keep as JsonElement, trait unwraps

			var result = controller.ApplyPatch(patchDict, mode);

			using var ms = new MemoryStream();
			using (var w = new Utf8JsonWriter(ms))
			{
				w.WriteStartObject();
				w.WriteBoolean("ok", true);
				w.WriteString("transition_mode", mode.ToString().ToLowerInvariant());
				w.WriteNumber("repurposed_units", result.RepurposedUnits);

				w.WriteStartObject("applied");
				WriteStateDict(w, result.Applied);
				w.WriteEndObject();

				w.WriteStartObject("rejected");
				foreach (var kv in result.Rejected)
					w.WriteString(kv.Key, kv.Value);
				w.WriteEndObject();

				w.WriteStartObject("strategy");
				WriteStateDict(w, controller.GetStateDict());
				w.WriteEndObject();

				w.WriteEndObject();
			}
			return Encoding.UTF8.GetString(ms.ToArray());
		}

		string HandleGetStrategy(JsonElement root)
		{
			var self = LocalPlayer();
			if (self == null)
				return ErrorJson("no_local_player", "no human player in this world");

			var controller = self.PlayerActor.TraitOrDefault<StrategyControllerBotModule>();
			if (controller == null)
				return ErrorJson("not_configured",
					"StrategyControllerBotModule not attached to PlayerActor");

			using var ms = new MemoryStream();
			using (var w = new Utf8JsonWriter(ms))
			{
				w.WriteStartObject();
				w.WriteBoolean("ok", true);
				w.WriteStartObject("strategy");
				WriteStateDict(w, controller.GetStateDict());
				w.WriteEndObject();
				w.WriteEndObject();
			}
			return Encoding.UTF8.GetString(ms.ToArray());
		}

		static void WriteStateDict(Utf8JsonWriter w, IEnumerable<KeyValuePair<string, object>> dict)
		{
			foreach (var kv in dict)
			{
				switch (kv.Value)
				{
					case null:
						w.WriteNull(kv.Key);
						break;
					case string s:
						w.WriteString(kv.Key, s);
						break;
					case bool b:
						w.WriteBoolean(kv.Key, b);
						break;
					case int i:
						w.WriteNumber(kv.Key, i);
						break;
					case long l:
						w.WriteNumber(kv.Key, l);
						break;
					case double d:
						w.WriteNumber(kv.Key, d);
						break;
					case IDictionary<string, int> nested:
						w.WriteStartObject(kv.Key);
						foreach (var nk in nested) w.WriteNumber(nk.Key, nk.Value);
						w.WriteEndObject();
						break;
					case IDictionary<string, object> nestedObj:
						w.WriteStartObject(kv.Key);
						WriteStateDict(w, nestedObj);
						w.WriteEndObject();
						break;
					default:
						w.WriteString(kv.Key, kv.Value.ToString());
						break;
				}
			}
		}

		// --- deploy / stop / sell (actor-only orders) ----------------------

		string HandleActorOnlyOrder(JsonElement root, string orderString)
		{
			var ids = ReadIntArray(root, "unit_ids");
			var actors = ResolveActors(ids).ToArray();
			if (actors.Length == 0)
				return ErrorJson("invalid_target", "no actors matched");

			foreach (var a in actors)
				world.IssueOrder(new Order(orderString, a, queued: false));

			return OkJson("issued_orders", actors.Length, actors.Select(a => (int)a.ActorID).ToArray());
		}

		// --- pause / resume -------------------------------------------------

		string HandlePause(bool paused)
		{
			world.SetPauseState(paused);
			return OkJson("issued_orders", 0);
		}

		// --- screenshot -----------------------------------------------------

		string HandleScreenshot(JsonElement root)
		{
			// Game.TakeScreenshot writes to support dir on the next render tick.
			// We return the directory and let the MCP client read the latest file.
			Game.TakeScreenshot();

			using var ms = new MemoryStream();
			using (var w = new Utf8JsonWriter(ms))
			{
				w.WriteStartObject();
				w.WriteBoolean("ok", true);
				w.WriteString("info", "screenshot queued; written to support dir on next render tick");
				w.WriteEndObject();
			}
			return Encoding.UTF8.GetString(ms.ToArray());
		}

		// ====================================================================
		// Group support
		// ====================================================================

		void EnsureGroupsInitialized()
		{
			lock (groupLock)
			{
				if (groupsInitialized) return;
				RebalanceGroupsInner();
				groupsInitialized = true;
			}
		}

		void RebalanceGroupsInner()
		{
			groups.Clear();
			var self = LocalPlayer();
			if (self == null) return;
			var owned = OwnedActors(self).ToList();
			if (owned.Count == 0) return;

			var sorted = owned.OrderBy(a => groupAxis == 0 ? a.Location.X : a.Location.Y).ToList();
			var names = GroupNames(groupCount, groupAxis);

			int per = (sorted.Count + groupCount - 1) / groupCount;
			for (int i = 0; i < groupCount; i++)
			{
				var slice = sorted.Skip(i * per).Take(per);
				groups[names[i]] = new HashSet<uint>(slice.Select(a => a.ActorID));
			}
		}

		static string[] GroupNames(int count, int axis)
		{
			if (count == 3) return axis == 1 ? Names3Y : Names3X;
			if (count == 2) return axis == 1 ? Names2Y : Names2X;
			var names = new string[count];
			for (int i = 0; i < count; i++) names[i] = "g" + i;
			return names;
		}

		IEnumerable<Actor> GetGroupActors(string name)
		{
			EnsureGroupsInitialized();
			HashSet<uint> ids;
			lock (groupLock)
			{
				if (!groups.TryGetValue(name, out ids)) yield break;
				ids = new HashSet<uint>(ids);
			}
			foreach (var a in world.Actors)
				if (IsRealActor(a) && ids.Contains(a.ActorID))
					yield return a;
		}

		string HandleListGroups(JsonElement root)
		{
			EnsureGroupsInitialized();
			using var ms = new MemoryStream();
			using (var w = new Utf8JsonWriter(ms))
			{
				w.WriteStartObject();
				w.WriteBoolean("ok", true);
				w.WriteStartArray("groups");
				lock (groupLock)
				{
					foreach (var kv in groups)
					{
						w.WriteStartObject();
						w.WriteString("name", kv.Key);
						var live = world.Actors.Where(a => IsRealActor(a) && kv.Value.Contains(a.ActorID)).ToList();
						w.WriteNumber("count", live.Count);
						if (live.Count > 0)
						{
							int sumX = 0, sumY = 0;
							var kindCounts = new Dictionary<string, int>();
							double totalHp = 0;
							foreach (var a in live)
							{
								sumX += a.Location.X;
								sumY += a.Location.Y;
								var k = a.Info != null ? (a.Info.Name ?? "?") : "?";
								kindCounts[k] = kindCounts.TryGetValue(k, out var c) ? c + 1 : 1;
								try
								{
									var h = a.TraitOrDefault<Health>();
									if (h != null && h.MaxHP > 0) totalHp += (double)h.HP / h.MaxHP;
									else totalHp += 1.0;
								}
								catch { totalHp += 1.0; }
							}
							w.WriteStartObject("center");
							w.WriteNumber("x", sumX / live.Count);
							w.WriteNumber("y", sumY / live.Count);
							w.WriteEndObject();
							w.WriteStartObject("composition");
							foreach (var kc in kindCounts) w.WriteNumber(kc.Key, kc.Value);
							w.WriteEndObject();
							w.WriteNumber("avg_hp_pct", totalHp / live.Count);
							w.WriteStartArray("unit_ids");
							foreach (var a in live) w.WriteNumberValue((int)a.ActorID);
							w.WriteEndArray();
						}
						w.WriteEndObject();
					}
				}
				w.WriteEndArray();
				w.WriteEndObject();
			}
			return Encoding.UTF8.GetString(ms.ToArray());
		}

		string HandleMoveGroup(JsonElement root)
		{
			var name = root.GetProperty("group").GetString();
			var target = ReadCell(root, "target");
			var attackMove = root.TryGetProperty("attack_move", out var am) && am.GetBoolean();
			var orderName = attackMove ? "AttackMove" : "Move";

			var actors = GetGroupActors(name).ToArray();
			if (actors.Length == 0)
				return ErrorJson("invalid_target", "group '" + name + "' empty or unknown");

			foreach (var a in actors)
				world.IssueOrder(new Order(orderName, a, Target.FromCell(world, target), queued: false));

			return OkJson("issued_orders", actors.Length, actors.Select(a => (int)a.ActorID).ToArray());
		}

		string HandleAttackGroup(JsonElement root)
		{
			var name = root.GetProperty("group").GetString();
			var targetId = (uint)root.GetProperty("target_id").GetInt32();
			var targetActor = world.Actors.FirstOrDefault(a => a.ActorID == targetId && !a.IsDead && a.IsInWorld);
			if (targetActor == null)
				return ErrorJson("invalid_target", "target id " + targetId + " not found");

			var actors = GetGroupActors(name).ToArray();
			if (actors.Length == 0)
				return ErrorJson("invalid_target", "group '" + name + "' empty or unknown");

			foreach (var a in actors)
				world.IssueOrder(new Order("Attack", a, Target.FromActor(targetActor), queued: false));

			return OkJson("issued_orders", actors.Length, actors.Select(a => (int)a.ActorID).ToArray());
		}

		string HandleStanceGroup(JsonElement root)
		{
			var name = root.GetProperty("group").GetString();
			var stance = root.GetProperty("stance").GetString();
			int stanceIndex;
			switch (stance)
			{
				case "HoldFire": stanceIndex = (int)UnitStance.HoldFire; break;
				case "ReturnFire": stanceIndex = (int)UnitStance.ReturnFire; break;
				case "Defend": stanceIndex = (int)UnitStance.Defend; break;
				case "AttackAnything": stanceIndex = (int)UnitStance.AttackAnything; break;
				default:
					return ErrorJson("invalid_target", "unknown stance " + stance);
			}

			var actors = GetGroupActors(name).ToArray();
			foreach (var a in actors)
			{
				var o = new Order("SetUnitStance", a, false) { ExtraData = (uint)stanceIndex };
				world.IssueOrder(o);
			}
			return OkJson("issued_orders", actors.Length, actors.Select(a => (int)a.ActorID).ToArray());
		}

		string HandleAssignGroup(JsonElement root)
		{
			var name = root.GetProperty("group").GetString();
			var ids = ReadIntArray(root, "unit_ids");
			EnsureGroupsInitialized();
			lock (groupLock)
			{
				foreach (var kv in groups)
					foreach (var id in ids)
						kv.Value.Remove((uint)id);
				if (!groups.TryGetValue(name, out var set))
				{
					set = new HashSet<uint>();
					groups[name] = set;
				}
				foreach (var id in ids)
					set.Add((uint)id);
			}
			return OkJson("issued_orders", 0, ids);
		}

		string HandleRebalanceGroups(JsonElement root)
		{
			int count = root.TryGetProperty("count", out var cp) ? cp.GetInt32() : 3;
			string axis = root.TryGetProperty("axis", out var ap) ? (ap.GetString() ?? "y") : "y";

			lock (groupLock)
			{
				groupCount = Math.Max(1, Math.Min(10, count));
				groupAxis = axis == "x" ? 0 : 1;
				RebalanceGroupsInner();
				groupsInitialized = true;
			}
			return HandleListGroups(root);
		}

		// ====================================================================
		// Helpers
		// ====================================================================

		Player LocalPlayer()
		{
			// Skirmish: world.LocalPlayer is the human.
			return world.LocalPlayer ?? world.Players.FirstOrDefault(p => !p.NonCombatant && !p.IsBot);
		}

		static bool IsRealActor(Actor a)
		{
			// Skip system actors (WorldActor / PlayerActor) which have no OccupiesSpace.
			return a != null && !a.IsDead && a.IsInWorld
				&& a.OccupiesSpace != null;
		}

		IEnumerable<Actor> OwnedActors(Player p)
		{
			return world.Actors.Where(a => IsRealActor(a) && a.Owner == p);
		}

		IEnumerable<Actor> EnemyActors(Player p)
		{
			return world.Actors.Where(a => IsRealActor(a)
				&& a.Owner != null && a.Owner != p
				&& !a.Owner.NonCombatant
				&& a.Owner.RelationshipWith(p) == PlayerRelationship.Enemy);
		}

		IEnumerable<Actor> ResolveActors(int[] ids)
		{
			if (ids == null) yield break;
			var set = new HashSet<uint>(ids.Select(i => (uint)i));
			foreach (var a in world.Actors)
				if (IsRealActor(a) && set.Contains(a.ActorID))
					yield return a;
		}

		static int[] ReadIntArray(JsonElement root, string name)
		{
			if (!root.TryGetProperty(name, out var arr) || arr.ValueKind != JsonValueKind.Array)
				return Array.Empty<int>();
			var list = new List<int>(arr.GetArrayLength());
			foreach (var v in arr.EnumerateArray())
				list.Add(v.GetInt32());
			return list.ToArray();
		}

		static CPos ReadCell(JsonElement root, string name)
		{
			var o = root.GetProperty(name);
			return new CPos(o.GetProperty("x").GetInt32(), o.GetProperty("y").GetInt32());
		}

		static void WriteUnitInfo(Utf8JsonWriter w, Actor a)
		{
			w.WriteStartObject();
			w.WriteNumber("id", a.ActorID);
			w.WriteString("kind", a.Info != null ? (a.Info.Name ?? string.Empty) : string.Empty);
			w.WriteString("owner", a.Owner != null ? (a.Owner.PlayerName ?? a.Owner.InternalName ?? string.Empty) : string.Empty);

			w.WriteStartObject("pos");
			try
			{
				var cell = a.Location;
				w.WriteNumber("x", cell.X);
				w.WriteNumber("y", cell.Y);
			}
			catch
			{
				w.WriteNumber("x", 0);
				w.WriteNumber("y", 0);
			}
			w.WriteEndObject();

			double hpPct = 1.0;
			try
			{
				var health = a.TraitOrDefault<Health>();
				if (health != null && health.MaxHP > 0)
					hpPct = (double)health.HP / health.MaxHP;
			}
			catch { /* swallow */ }
			w.WriteNumber("hp_pct", hpPct);

			w.WriteEndObject();
		}

		static string OkJson(string countField, int count, int[] ids = null)
		{
			using var ms = new MemoryStream();
			using (var w = new Utf8JsonWriter(ms))
			{
				w.WriteStartObject();
				w.WriteBoolean("ok", true);
				w.WriteNumber(countField, count);
				w.WriteStartArray("affected_unit_ids");
				if (ids != null)
					foreach (var id in ids)
						w.WriteNumberValue(id);
				w.WriteEndArray();
				w.WriteEndObject();
			}
			return Encoding.UTF8.GetString(ms.ToArray());
		}

		static string ErrorJson(string code, string message)
		{
			using var ms = new MemoryStream();
			using (var w = new Utf8JsonWriter(ms))
			{
				w.WriteStartObject();
				w.WriteBoolean("ok", false);
				w.WriteString("error_code", code);
				w.WriteString("error", message);
				w.WriteEndObject();
			}
			return Encoding.UTF8.GetString(ms.ToArray());
		}
	}
}
