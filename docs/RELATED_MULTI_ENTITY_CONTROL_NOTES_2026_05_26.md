# Related Multi-Entity NL/LLM Control Notes

Date: 2026-05-26  
Purpose: quick literature/project notes for positioning `openra_mcp` against drone-swarm / robot-swarm LLM control work.

## Bottom Line

`openra_mcp` should not claim to be the first natural-language / LLM system for controlling multiple entities. There is already a growing drone-swarm and robot-swarm literature.

The safer novelty claim is narrower:

> `openra_mcp` studies natural-language multi-unit tactical control in a real-time strategy game, where a human player expresses tactical intent and an MCP-based co-pilot translates that intent into executable engine-side squad FSM primitives for dozens to hundreds of heterogeneous combat units.

This keeps the novelty in the RTS / game-control / mixed-initiative / engine-FSM setting, not in "multi-entity control" as a whole field.

## Closest Non-Game Analogue: Web-of-Drones MCP Swarm Paper

The closest related work found so far is:

> Iannoli et al. (2026), "Say the Mission, Execute the Swarm: Agent-Enhanced LLM Reasoning in the Web-of-Drones", arXiv:2605.03788 / WoWMoM 2026.

Why it is close:

- It is explicitly about natural-language mission objectives.
- It controls a swarm, not only a single platform.
- It uses an LLM Agent Core plus an MCP gateway.
- It separates high-level LLM reasoning from low-level execution.
- It evaluates multiple LLMs and repeated runs.
- It reports token usage, mission success, collisions, execution time, and the effect of helper/planning tools.

Important differences from `openra_mcp`:

| Axis | Web-of-Drones paper | openra_mcp |
|---|---|---|
| Domain | UAV swarm missions | Real-time strategy game combat |
| User role | User gives mission objective; system executes without human-in-the-loop during execution | Player remains in the loop and owns strategy, economy, and battlefield judgment |
| Environment | ArduPilot SITL / cyber-physical swarm simulation | OpenRA RTS engine with adversarial battlefield dynamics |
| Entities | 10 multirotor UAVs by default | 30-100 heterogeneous RTS units |
| Task types | Area coverage, formation, smart irrigation | Pincer, feint, multi-squad split, recall, staged attack, tactical regroup |
| Execution abstraction | WoT Things + MCP tools; helper tools such as multi-drone dispatch and wait helpers | MCP squad commands + engine-side squad FSM primitives |
| Autonomy stance | Agent autonomously coordinates from mission start to completion | Mixed-initiative co-pilot; AI translates player intent rather than owning the whole mission |
| Failure concerns | Collisions, landing/disarming, stale state verification | Squad overlap, path congestion, combat/tactical coordination, player authority boundary |

Recommended positioning:

> Recent UAV-swarm work has shown that LLMs connected through MCP gateways can execute natural-language swarm missions over standardized device abstractions. `openra_mcp` is complementary: it transfers this architecture pattern into mixed-initiative RTS play, where the problem is not autonomous mission completion but translating a human player's tactical intent into coordinated control of many heterogeneous combat units under real-time game pressure.

Do not frame this paper as weak related work. It is strong and should be cited prominently. The novelty of `openra_mcp` should be RTS-specific and mixed-initiative-specific, not MCP-swarm-general.

## Directly Relevant Drone / Robot Swarm Work

| Work | Type | What it controls | Interface / architecture | Relevance to openra_mcp |
|---|---|---|---|---|
| Say the Mission, Execute the Swarm | arXiv 2026 / WoWMoM 2026 | UAV swarm missions | LLM Agent Core + MCP gateway + Web-of-Drones / WoT abstraction | Closest non-game analogue. Strong precedent for MCP-mediated NL swarm control; our contribution must be RTS/mixed-initiative specific. |
| SwarmGPT / SwarmGPT-Primitive | Academic + project | Drone swarms for choreography | Natural-language choreographer + safe motion planning / motion primitives | Strong precedent for NL-to-multi-drone choreography. Shows our "multi-entity" claim must be scoped to RTS tactical control. |
| FlockGPT | Academic + GitHub demo | UAV flocking | LLM-based UI + flocking controller, ROS/CrazySwarm | Strong precedent for NL interface to drone flock geometry. |
| LLM2Swarm | Academic + GitHub | Robot swarms | LLMs synthesize/validate controllers or run local LLM instances for robot collaboration | Relevant broad robot-swarm precedent, but not MCP and not RTS. |
| A Prompt-driven Task Planning Method for Multi-drones based on LLM | arXiv | Multi-drone systems | Prompt-driven task planning | Relevant as early LLM multi-drone task-planning work. |
| Intent-Driven Cooperative Control of UAV Swarms | Journal article | UAV swarms, including 50-UAV examples | LLM/RAG intent-to-code, dual-layer cognitive planning + real-time execution | Very relevant to multi-entity intent-driven control; similar high-level/low-level separation. |
| A Universal LLM Drone Command and Control Interface | arXiv 2026 | Real and simulated drones | MCP server + MAVLink / ArduPilot / PX4 | Strong single-drone / drone-C2 MCP precedent; less about swarm. |
| Skynet | GitHub | Robots and drones | MCP host for real devices with MCP servers | Tooling precedent for LLM/MCP physical-device control. Not specifically swarm tactics. |
| DroneSwarmGPT | GitHub | Claimed drone swarms | Natural-language swarm coordination claims | Treat cautiously unless code/evaluation is verified; more marketing-like than paper-grade evidence. |

## Implication for Thesis

Bad claim:

> We are the first system where natural language controls many entities.

Safer claim:

> Prior drone-swarm and robot-swarm systems show that LLMs can help express multi-entity objectives, often for choreography, formation, inspection, or physical swarm coordination. `openra_mcp` transfers this high-level-intent paradigm into a different domain: real-time RTS tactical co-piloting, where the entities are heterogeneous combat units, the environment is adversarial, the player remains in control of strategy and economy, and MCP calls trigger engine-side squad primitives rather than per-unit atomic commands.

## Suggested Related Work Paragraph

Recent work on LLM-mediated swarm control provides an important cross-domain precedent for natural-language multi-entity control. SwarmGPT and FlockGPT use language models to help non-expert users specify drone-swarm choreographies or flocking geometries, while LLM2Swarm explores LLM integration with robot-swarm collaboration. More recent UAV-swarm systems further separate high-level LLM reasoning from low-level real-time execution, including intent-to-code frameworks and MCP-based Web-of-Drones architectures. These works show that natural language can serve as an interface to coordinated multi-agent systems. `openra_mcp` differs in domain and control contract: it targets mixed-initiative tactical control inside a real-time strategy game, where a human player retains strategic and economic authority while the LLM translates tactical intent into auditable MCP calls over engine-side squad primitives for heterogeneous combat units.

## Positioning Table for Paper

| Axis | Drone / robot swarm work | openra_mcp |
|---|---|---|
| Domain | Robotics, UAV choreography, inspection, formation, physical/simulated swarms | Real-time strategy game tactical combat |
| Human role | Often mission designer/operator | Active player who keeps strategy, economy, and battlefield judgment |
| Entity type | Similar drones or simple robots | Heterogeneous RTS units: infantry, tanks, APCs, artillery, etc. |
| Time pressure | Often planned mission / choreography / periodic feedback | Real-time adversarial game control |
| Execution layer | Motion planner, formation controller, generated code, swarm controller | OpenRA engine + squad FSM primitives |
| MCP role | Present in some 2026 drone-C2 / Web-of-Drones work | Core interface between LLM co-pilot and game engine |
| Main contribution | NL-to-swarm mission or choreography | NL-to-multi-unit tactical intent translation in RTS |

## Source Links Checked

- SwarmGPT project page: https://utiasdsl.github.io/swarm_GPT/
- SwarmGPT arXiv: https://arxiv.org/abs/2412.08428
- FlockGPT GitHub: https://github.com/Taintedy/flock_gpt
- LLM2Swarm GitHub: https://github.com/Pold87/LLM2Swarm
- LLM2Swarm arXiv: https://arxiv.org/abs/2410.11387
- Prompt-driven multi-drone task planning arXiv: https://arxiv.org/abs/2406.00006
- Say the Mission, Execute the Swarm arXiv: https://arxiv.org/abs/2605.03788
- Universal LLM drone C2 interface arXiv: https://arxiv.org/abs/2601.15486
- Skynet GitHub: https://github.com/hybridgroup/skynet
- DroneSwarmGPT GitHub: https://github.com/The-Swarm-Corporation/DroneSwarmGPT
