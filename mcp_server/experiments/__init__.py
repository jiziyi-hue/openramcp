"""Experiment harness for openra_mcp research paper.

Three conditions:
  - solo_human:    player vs bot, NO LLM. Traditional UI only.
  - human_llm:     player + LLM (via Claude Code MCP) vs bot. Our system.
  - bot_baseline:  vanilla OpenRA bot vs another OpenRA bot. No human, no LLM.

Run scripts directly:
    python -m mcp_server.experiments.solo_human  --scenario S1_basic_rush
    python -m mcp_server.experiments.human_llm   --scenario S1_basic_rush
    python -m mcp_server.experiments.bot_baseline --scenario S1_basic_rush --seeds 20

Aggregate + plot:
    python -m mcp_server.experiments.analyze     --logs logs/ --out figures/
"""
