"""
Root orchestrator agent -- coordinates Architecture, Security, and Performance sub-agents.
Uses ADK Agent with AgentTool wrappers so each specialist is called as a tool.
"""

from google.adk.agents import Agent
from google.adk.tools.agent_tool import AgentTool

from agent.architecture_agent.agent import architecture_agent
from agent.security_agent.agent import security_agent
from agent.performance_agent.agent import performance_agent

ORCHESTRATOR_INSTRUCTION = """You are the Code Review Orchestrator.

You receive a single file's source code together with its filename and language.
Your job is to delegate the analysis to ALL THREE specialist agents and collect their findings.

PROCESS (always follow all three steps):
1. Call the architecture_agent tool with the exact source code -- to find design and structural issues.
2. Call the security_agent tool with the exact source code -- to find vulnerabilities and unsafe patterns.
3. Call the performance_agent tool with the exact source code -- to find algorithmic and efficiency issues.

After all three calls, merge all JSON arrays returned by the specialists into a single flat JSON array
and return it. Do not filter, re-score, or modify any finding.

If a specialist returns [], that is fine -- include nothing from it.
Return ONLY the final merged JSON array. No markdown. No explanation."""

orchestrator = Agent(
    name="code_review_orchestrator",
    model="gemini-2.5-pro",
    description="Root orchestrator that dispatches source code to Architecture, Security, and Performance specialist agents and merges their findings.",
    instruction=ORCHESTRATOR_INSTRUCTION,
    tools=[
        AgentTool(agent=architecture_agent),
        AgentTool(agent=security_agent),
        AgentTool(agent=performance_agent),
    ],
)

# Required for ADK discovery / adk web
root_agent = orchestrator
