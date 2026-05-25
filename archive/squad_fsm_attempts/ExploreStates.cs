#region Copyright & License Information
/*
 * openra_mcp Phase E5b: re-issue-on-arrival explore.
 *
 * Same pattern as Patrol: state machine maintains a cursor over an
 * 8-spoke ring around the seed cell. Each tick:
 *   1. If squad centroid is within 4 cells of the current spoke, advance
 *      to the next spoke. After 8 spokes, expand the ring (+6 cells) and
 *      reset cursor.
 *   2. Issue an AttackMove to the current spoke, only to units that
 *      haven't received it yet or that went idle.
 */
#endregion

using System;
using System.Collections.Generic;
using System.Linq;
using OpenRA.Traits;

namespace OpenRA.Mods.Common.Traits.BotModules.Squads
{
	sealed class ExploreIdleState : GroundStateBase, IState
	{
		public void Activate(Squad owner) { }

		public void Tick(Squad owner)
		{
			if (!owner.IsValid)
				return;
			owner.FuzzyStateMachine.ChangeState(owner, new ExploreMoveState());
		}

		public void Deactivate(Squad owner) { }
	}

	sealed class ExploreMoveState : GroundStateBase, IState
	{
		const int RingStepCells = 6;
		int ring;
		int spoke;
		CPos? lastIssuedSpoke;
		readonly HashSet<uint> issuedUnits = new();

		public void Activate(Squad owner)
		{
			ring = 1;
			spoke = 0;
			lastIssuedSpoke = null;
			issuedUnits.Clear();
		}

		public void Tick(Squad owner)
		{
			if (!owner.IsValid)
				return;

			var seed = owner.Target.Type != TargetType.Invalid
				? owner.World.Map.CellContaining(owner.Target.CenterPosition)
				: new CPos(owner.World.Map.MapSize.X / 2, owner.World.Map.MapSize.Y / 2);

			CPos SpokeCell(int s, int r)
			{
				var angle = s * (Math.PI / 4);
				var dx = (int)Math.Round(Math.Cos(angle) * r * RingStepCells);
				var dy = (int)Math.Round(Math.Sin(angle) * r * RingStepCells);
				return new CPos(seed.X + dx, seed.Y + dy);
			}

			// 1. Advance spoke when centroid near current goal.
			var goal = SpokeCell(spoke, ring);
			var center = owner.CenterPosition();
			var goalPos = owner.World.Map.CenterOfCell(goal);
			var arrival = WDist.FromCells(4).Length;
			if ((center - goalPos).HorizontalLengthSquared < arrival * arrival)
			{
				spoke++;
				if (spoke >= 8)
				{
					spoke = 0;
					ring++;
					if (ring * RingStepCells > owner.World.Map.MapSize.X)
						ring = 1;
				}
				goal = SpokeCell(spoke, ring);
			}

			// 2. Issue on goal change or to idle units.
			var goalChanged = !lastIssuedSpoke.HasValue || lastIssuedSpoke.Value != goal;
			if (goalChanged)
				issuedUnits.Clear();

			var target = Target.FromCell(owner.World, goal);
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

			lastIssuedSpoke = goal;
		}

		public void Deactivate(Squad owner) { }
	}
}
