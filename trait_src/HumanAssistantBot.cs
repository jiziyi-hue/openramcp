#region Copyright & License Information
/*
 * Human Assistant Bot trait for OpenRA.
 *
 * A clone of ModularBot intended to run on a human player. It ticks any
 * IBotTick / IBotRespondToAttack traits attached to the human's PlayerActor,
 * so macro modules (HarvesterBotModule, BaseBuilderBotModule,
 * UnitBuilderBotModule, BuildingRepairBotModule, McvManagerBotModule) execute
 * for the human, automating economy and production.
 *
 * Combat-control modules (SquadManagerBotModule, CaptureManagerBotModule,
 * SupportPowerBotModule) are intentionally NOT attached to the human's
 * PlayerActor by yaml — combat is left to the MCP bridge.
 *
 * Activated by Player.cs when the player has IsBot == false and this trait
 * is present on PlayerActor (see Player.cs ~line 224).
 *
 * Drop into OpenRA.Mods.Common/Traits/Player/ alongside ModularBot.cs.
 */
#endregion

using System;
using System.Collections.Generic;
using System.Linq;
using OpenRA.Support;
using OpenRA.Traits;

namespace OpenRA.Mods.Common.Traits
{
	[Desc("Macro-only assistant bot that runs IBotTick modules for a human player.")]
	[TraitLocation(SystemActors.Player)]
	public sealed class HumanAssistantBotInfo : TraitInfo, IBotInfo
	{
		[Desc("Internal id for this bot. Must start with 'human-assistant' so Player.cs activates it on humans.")]
		public readonly string Type = "human-assistant";

		[FluentReference]
		[Desc("Human-readable name.")]
		public readonly string Name = "Macro Assistant";

		[Desc("Minimum portion of pending orders to issue each tick. Mirrors ModularBot.")]
		public readonly int MinOrderQuotientPerTick = 5;

		string IBotInfo.Type => Type;
		string IBotInfo.Name => Name;

		public override object Create(ActorInitializer init) { return new HumanAssistantBot(this, init); }
	}

	public sealed class HumanAssistantBot : ITick, IBot, INotifyDamage
	{
		public bool IsEnabled;

		readonly HumanAssistantBotInfo info;
		readonly World world;
		readonly Queue<Order> orders = new();

		Player player;

		IBotTick[] tickModules;
		IBotRespondToAttack[] attackResponseModules;

		IBotInfo IBot.Info => info;
		Player IBot.Player => player;

		public HumanAssistantBot(HumanAssistantBotInfo info, ActorInitializer init)
		{
			this.info = info;
			world = init.World;
		}

		public void Activate(Player p)
		{
			// Don't run in replays. Same rule as ModularBot.
			if (p.World.IsReplay)
				return;

			IsEnabled = true;
			player = p;
			tickModules = p.PlayerActor.TraitsImplementing<IBotTick>().ToArray();
			attackResponseModules = p.PlayerActor.TraitsImplementing<IBotRespondToAttack>().ToArray();
			foreach (var ibe in p.PlayerActor.TraitsImplementing<IBotEnabled>())
				ibe.BotEnabled(this);
		}

		void IBot.QueueOrder(Order order)
		{
			orders.Enqueue(order);
		}

		void ITick.Tick(Actor self)
		{
			if (!IsEnabled || self.World.IsLoadingGameSave)
				return;

			using (new PerfSample("human_assistant_bot_tick"))
			{
				Sync.RunUnsynced(Game.Settings.Debug.SyncCheckBotModuleCode, world, () =>
				{
					foreach (var t in tickModules)
						if (t.IsTraitEnabled())
							t.BotTick(this);
				});
			}

			var ordersToIssueThisTick = Math.Min(
				(orders.Count + info.MinOrderQuotientPerTick - 1) / info.MinOrderQuotientPerTick,
				orders.Count);
			for (var i = 0; i < ordersToIssueThisTick; i++)
				world.IssueOrder(orders.Dequeue());
		}

		void INotifyDamage.Damaged(Actor self, AttackInfo e)
		{
			if (!IsEnabled || self.World.IsLoadingGameSave)
				return;

			using (new PerfSample("human_assistant_bot_attack_response"))
			{
				Sync.RunUnsynced(Game.Settings.Debug.SyncCheckBotModuleCode, world, () =>
				{
					foreach (var t in attackResponseModules)
						if (t.IsTraitEnabled())
							t.RespondToAttack(this, self, e);
				});
			}
		}
	}
}
