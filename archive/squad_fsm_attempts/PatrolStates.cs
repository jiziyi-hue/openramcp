#region Copyright & License Information
/*
 * openra_mcp Phase E5b: re-issue-on-arrival patrol.
 *
 * Mirrors the Assault pattern: state machine tracks a current waypoint
 * index. Each tick:
 *   1. If squad centroid is within 4 cells of current waypoint, advance
 *      the cursor (wrap to 0 at the end → infinite loop).
 *   2. Issue an AttackMove to the current waypoint, but ONLY to units
 *      that haven't received it yet, OR to units that went idle since
 *      the last issue. Avoid per-tick spam.
 *
 * No pre-queued circuit (the bot's order-rate limiter dropped the loop
 * appends in the prior attempt). One fresh order per advance.
 */
#endregion

using System.Collections.Generic;
using System.Linq;
using OpenRA.Traits;

namespace OpenRA.Mods.Common.Traits.BotModules.Squads
{
	sealed class PatrolIdleState : GroundStateBase, IState
	{
		public void Activate(Squad owner) { }

		public void Tick(Squad owner)
		{
			if (!owner.IsValid)
				return;
			if (owner.Waypoints == null || owner.Waypoints.Count == 0)
				return;
			owner.FuzzyStateMachine.ChangeState(owner, new PatrolMoveState());
		}

		public void Deactivate(Squad owner) { }
	}

	sealed class PatrolMoveState : GroundStateBase, IState
	{
		CPos? lastIssuedWaypoint;
		readonly HashSet<uint> issuedUnits = new();

		public void Activate(Squad owner)
		{
			lastIssuedWaypoint = null;
			issuedUnits.Clear();
		}

		public void Tick(Squad owner)
		{
			if (!owner.IsValid)
				return;
			if (owner.Waypoints == null || owner.Waypoints.Count == 0)
			{
				owner.FuzzyStateMachine.ChangeState(owner, new PatrolIdleState());
				return;
			}

			// 1. Advance cursor when squad centroid is near current waypoint.
			var wp = owner.Waypoints[owner.CurrentWaypointIndex];
			var center = owner.CenterPosition();
			var wpPos = owner.World.Map.CenterOfCell(wp);
			var arrivalDist = WDist.FromCells(4).Length;
			if ((center - wpPos).HorizontalLengthSquared < arrivalDist * arrivalDist)
			{
				owner.CurrentWaypointIndex = (owner.CurrentWaypointIndex + 1) % owner.Waypoints.Count;
				wp = owner.Waypoints[owner.CurrentWaypointIndex];
			}

			// 2. Issue on waypoint change, or to specific idle units.
			var waypointChanged = !lastIssuedWaypoint.HasValue || lastIssuedWaypoint.Value != wp;
			if (waypointChanged)
				issuedUnits.Clear();

			var target = Target.FromCell(owner.World, wp);
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

			lastIssuedWaypoint = wp;
		}

		public void Deactivate(Squad owner) { }
	}
}
