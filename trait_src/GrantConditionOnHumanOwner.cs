#region Copyright & License Information
/*
 * GrantConditionOnHumanOwner — companion to GrantConditionOnBotOwner.
 *
 * Grants a condition to the player actor when the player is NOT controlled by
 * a bot (i.e. is human). Used to enable the macro bot modules
 * (HarvesterBotModule / BaseBuilderBotModule / UnitBuilderBotModule /
 * BuildingRepairBotModule / McvManagerBotModule) for a human player driven by
 * the MCP bridge.
 *
 * Drop into OpenRA.Mods.Common/Traits/Conditions/.
 */
#endregion

using OpenRA.Traits;

namespace OpenRA.Mods.Common.Traits
{
	[Desc("Grants a condition to this actor when it is owned by a human (non-bot) player.")]
	public class GrantConditionOnHumanOwnerInfo : TraitInfo
	{
		[FieldLoader.Require]
		[GrantedConditionReference]
		[Desc("Condition to grant.")]
		public readonly string Condition = null;

		public override object Create(ActorInitializer init) { return new GrantConditionOnHumanOwner(this); }
	}

	public class GrantConditionOnHumanOwner : INotifyCreated, INotifyOwnerChanged
	{
		readonly GrantConditionOnHumanOwnerInfo info;

		int conditionToken = Actor.InvalidConditionToken;

		public GrantConditionOnHumanOwner(GrantConditionOnHumanOwnerInfo info)
		{
			this.info = info;
		}

		void INotifyCreated.Created(Actor self)
		{
			if (!self.Owner.IsBot && !self.Owner.NonCombatant)
				conditionToken = self.GrantCondition(info.Condition);
		}

		void INotifyOwnerChanged.OnOwnerChanged(Actor self, Player oldOwner, Player newOwner)
		{
			if (conditionToken != Actor.InvalidConditionToken)
				conditionToken = self.RevokeCondition(conditionToken);

			if (!newOwner.IsBot && !newOwner.NonCombatant)
				conditionToken = self.GrantCondition(info.Condition);
		}
	}
}
