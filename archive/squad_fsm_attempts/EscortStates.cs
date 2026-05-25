#region Copyright & License Information
/*
 * openra_mcp Phase E5: throttled escort.
 *
 * Re-issues AttackMove to the escortee's current cell only when:
 *   - the escortee moved more than 3 cells since the last issue, OR
 *   - the unit went idle (e.g. arrived and stopped), OR
 *   - the unit has never received an order from this squad
 *
 * Engine ActivityQueue + per-unit AutoTarget handle the rest. Unregisters
 * the squad if the escortee dies.
 */
#endregion

using System.Collections.Generic;
using System.Linq;
using OpenRA.Traits;

namespace OpenRA.Mods.Common.Traits.BotModules.Squads
{
	sealed class EscortIdleState : GroundStateBase, IState
	{
		public void Activate(Squad owner) { }

		public void Tick(Squad owner)
		{
			if (!owner.IsValid)
				return;
			if (!owner.EscorteeActorId.HasValue)
				return;
			owner.FuzzyStateMachine.ChangeState(owner, new EscortFollowState());
		}

		public void Deactivate(Squad owner) { }
	}

	sealed class EscortFollowState : GroundStateBase, IState
	{
		CPos? lastIssuedCell;
		readonly HashSet<uint> issuedUnits = new();

		public void Activate(Squad owner)
		{
			lastIssuedCell = null;
			issuedUnits.Clear();
		}

		public void Tick(Squad owner)
		{
			if (!owner.IsValid)
				return;
			if (!owner.EscorteeActorId.HasValue)
			{
				owner.FuzzyStateMachine.ChangeState(owner, new EscortIdleState());
				return;
			}

			var escortee = owner.World.Actors.FirstOrDefault(a => a.ActorID == owner.EscorteeActorId.Value);
			if (escortee == null || escortee.IsDead || !escortee.IsInWorld)
			{
				owner.SquadManager.UnregisterSquad(owner);
				return;
			}

			var cell = escortee.Location;
			var moved = !lastIssuedCell.HasValue
				|| (lastIssuedCell.Value - cell).LengthSquared > 9;  // 3 cells
			if (moved)
				issuedUnits.Clear();

			var target = Target.FromCell(owner.World, cell);
			foreach (var a in owner.Units)
			{
				if (a == null || a.IsDead || !a.IsInWorld)
					continue;
				var needsOrder = !issuedUnits.Contains(a.ActorID) || a.IsIdle;
				if (!needsOrder)
					continue;
				owner.Bot.QueueOrder(new Order("AttackMove", a, target, queued: false));
				issuedUnits.Add(a.ActorID);
			}

			lastIssuedCell = cell;
		}

		public void Deactivate(Squad owner) { }
	}
}
