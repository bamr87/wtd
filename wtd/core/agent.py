"""
WTD AI Agent - The brain of the recursive TODO engine
"""

import json
from typing import Any

from wtd.config import WTDConfig, get_config
from wtd.core.models import (
    ExecutionResult,
    ScanResult,
    TodoContext,
    TodoNode,
    TodoPriority,
    WorkspaceConfig,
)
from wtd.core.tree import TodoTree


# System prompts for different contexts
CONTEXT_PROMPTS = {
    TodoContext.BUGFIX: """You are a debugging expert. Analyze the bug, identify root cause, and create a systematic fix plan.
Focus on: reproduction steps, root cause analysis, fix implementation, testing verification.""",

    TodoContext.WRITE: """You are a technical writer. Create compelling, well-structured content.
Focus on: outline, research, drafting, editing, publishing preparation.""",

    TodoContext.PLAN: """You are a strategic planner. Break down complex goals into actionable milestones.
Focus on: requirements gathering, timeline estimation, resource allocation, risk assessment.""",

    TodoContext.LEARN: """You are an educational guide. Create effective learning paths.
Focus on: prerequisites, core concepts, practical exercises, knowledge validation.""",

    TodoContext.BUILD: """You are a software architect. Design and implement robust solutions.
Focus on: design, implementation, integration, documentation.""",

    TodoContext.REFACTOR: """You are a code quality expert. Improve code without changing behavior.
Focus on: identifying smells, planning changes, safe refactoring, verification.""",

    TodoContext.TEST: """You are a QA engineer. Ensure comprehensive test coverage.
Focus on: test strategy, test cases, automation, coverage analysis.""",

    TodoContext.DEPLOY: """You are a DevOps engineer. Ensure smooth deployments.
Focus on: preparation, staging, production rollout, monitoring, rollback plan.""",

    TodoContext.UNKNOWN: """You are a productivity assistant. Help break down and complete tasks efficiently.
Focus on: understanding the goal, breaking into subtasks, prioritization, execution.""",
}


class LLMClient:
    """Base LLM client interface."""

    async def generate(self, prompt: str, system: str = "") -> str:
        """Generate a response from the LLM."""
        raise NotImplementedError


class OllamaClient(LLMClient):
    """Ollama LLM client."""

    def __init__(self, config: WTDConfig):
        self.config = config

    async def generate(self, prompt: str, system: str = "") -> str:
        """Generate using Ollama."""
        try:
            import ollama
            
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            
            response = ollama.chat(
                model=self.config.ollama_model,
                messages=messages,
            )
            return response["message"]["content"]
        except Exception as e:
            return f"Error: {e}"


class OpenAIClient(LLMClient):
    """OpenAI LLM client."""

    def __init__(self, config: WTDConfig):
        self.config = config

    async def generate(self, prompt: str, system: str = "") -> str:
        """Generate using OpenAI."""
        try:
            from openai import AsyncOpenAI
            
            client = AsyncOpenAI(api_key=self.config.openai_api_key)
            
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            
            response = await client.chat.completions.create(
                model=self.config.openai_model,
                messages=messages,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            return f"Error: {e}"


class MockClient(LLMClient):
    """
    Deterministic offline LLM client for tests.

    This intentionally returns small, valid JSON payloads that satisfy the prompts used by WTDAgent.
    """

    async def generate(self, prompt: str, system: str = "") -> str:
        p = (prompt or "").lower()

        # Context detection prompt (expects a single word)
        if "respond with only the context word" in p:
            return "bugfix"

        # Clarify prompt
        if "respond with either the question or \"clear\"" in p:
            return "CLEAR"

        # Subtasks prompt (expects JSON array)
        if "break down this todo into specific, actionable subtasks" in p and "respond in json format" in p:
            return json.dumps(
                [
                    {"title": "Identify the failing behavior", "description": "Reproduce and isolate", "priority": "high"},
                    {"title": "Implement the fix", "description": "Make the smallest safe change", "priority": "medium"},
                ]
            )

        # Execution plan prompt (expects JSON array of actions)
        if "plan the execution steps for this todo" in p and "respond in json format" in p:
            return json.dumps(
                [
                    {"action": "open_file", "target": "README.md", "description": "Review context"},
                    {"action": "run_command", "target": "pytest -q", "description": "Verify behavior"},
                ]
            )

        # Fallback: return a minimal valid JSON array to keep callers safe
        if "respond in json format" in p:
            return "[]"

        return ""


def get_llm_client(config: WTDConfig | None = None) -> LLMClient:
    """Get the appropriate LLM client based on configuration."""
    config = config or get_config()
    
    if config.llm_provider == "ollama":
        return OllamaClient(config)
    elif config.llm_provider == "openai":
        return OpenAIClient(config)
    elif config.llm_provider == "mock":
        return MockClient()
    else:
        # Default to Ollama
        return OllamaClient(config)


class WTDAgent:
    """
    The WTD AI Agent.
    
    Orchestrates TODO detection, context analysis, subtask generation,
    and execution planning.
    """

    def __init__(self, config: WTDConfig | None = None):
        self.config = config or get_config()
        self.llm = get_llm_client(self.config)
        self.tree = TodoTree()

    async def analyze_context(self, scan_result: ScanResult) -> TodoContext:
        """
        Analyze scanned TODOs to determine the dominant context.
        Uses AI for ambiguous cases.
        """
        if scan_result.confidence > 0.7:
            return scan_result.context

        # Use AI for context detection when confidence is low
        todos_text = "\n".join([
            f"- {t.title}" for t in scan_result.todos[:20]  # Limit for context
        ])

        prompt = f"""Analyze these TODO items and determine the primary context.

TODOs:
{todos_text}

Classify as one of: bugfix, write, plan, learn, build, refactor, test, deploy

Respond with ONLY the context word, nothing else."""

        response = await self.llm.generate(prompt)
        response = response.strip().lower()

        # Map response to TodoContext
        context_map = {
            "bugfix": TodoContext.BUGFIX,
            "write": TodoContext.WRITE,
            "plan": TodoContext.PLAN,
            "learn": TodoContext.LEARN,
            "build": TodoContext.BUILD,
            "refactor": TodoContext.REFACTOR,
            "test": TodoContext.TEST,
            "deploy": TodoContext.DEPLOY,
        }

        return context_map.get(response, scan_result.context)

    async def generate_subtasks(
        self,
        todo: TodoNode,
        max_subtasks: int = 5,
    ) -> list[dict[str, Any]]:
        """
        Generate subtasks for a TODO using AI.
        
        Returns a list of subtask definitions ready to spawn.
        """
        if not todo.can_spawn_children(self.config.max_recursion_depth):
            return []

        system_prompt = CONTEXT_PROMPTS.get(todo.context, CONTEXT_PROMPTS[TodoContext.UNKNOWN])

        prompt = f"""Break down this TODO into specific, actionable subtasks.

TODO: {todo.title}
Description: {todo.description}
Context: {todo.context.value}
Current Depth: {todo.depth}/{self.config.max_recursion_depth}

Generate {max_subtasks} or fewer subtasks. Each should be:
1. Specific and actionable
2. Completable independently
3. Contributing directly to the parent TODO

Respond in JSON format:
[
  {{"title": "Subtask title", "description": "Details", "priority": "high|medium|low"}},
  ...
]

Respond with ONLY the JSON array, no other text."""

        response = await self.llm.generate(prompt, system_prompt)

        try:
            # Extract JSON from response
            response = response.strip()
            if response.startswith("```"):
                response = response.split("```")[1]
                if response.startswith("json"):
                    response = response[4:]
            
            subtasks = json.loads(response)
            
            # Validate and convert
            result = []
            for st in subtasks[:max_subtasks]:
                priority_map = {
                    "critical": TodoPriority.CRITICAL,
                    "high": TodoPriority.HIGH,
                    "medium": TodoPriority.MEDIUM,
                    "low": TodoPriority.LOW,
                }
                result.append({
                    "title": st.get("title", "Untitled"),
                    "description": st.get("description", ""),
                    "priority": priority_map.get(
                        st.get("priority", "medium").lower(),
                        TodoPriority.MEDIUM
                    ),
                })
            
            return result
        except (json.JSONDecodeError, KeyError, TypeError):
            return []

    async def suggest_workspace(
        self,
        context: TodoContext,
        todos: list[TodoNode],
    ) -> WorkspaceConfig:
        """
        Suggest workspace configuration based on context and TODOs.
        """
        # Get files mentioned in TODOs
        files_to_open = []
        for todo in todos:
            if todo.source and todo.source.file_path:
                files_to_open.append(todo.source.file_path)

        # Remove duplicates and limit
        files_to_open = list(set(files_to_open))[:10]

        # Context-specific browser URLs
        browser_urls = []
        if context == TodoContext.BUGFIX:
            browser_urls = ["https://github.com"]  # Would be project-specific
        elif context == TodoContext.WRITE:
            browser_urls = []
        elif context == TodoContext.LEARN:
            browser_urls = ["https://devdocs.io"]

        # Context-specific terminal commands
        terminals = []
        if context in (TodoContext.BUILD, TodoContext.BUGFIX, TodoContext.TEST):
            terminals = [
                {"name": "dev", "command": "echo 'Development terminal ready'"},
                {"name": "test", "command": "echo 'Test terminal ready'"},
            ]

        return WorkspaceConfig(
            files_to_open=files_to_open,
            browser_urls=browser_urls,
            terminals=terminals,
        )

    async def plan_execution(self, todo: TodoNode) -> list[dict[str, Any]]:
        """
        Plan the execution steps for a TODO.
        
        Returns a list of action definitions.
        """
        system_prompt = CONTEXT_PROMPTS.get(todo.context, CONTEXT_PROMPTS[TodoContext.UNKNOWN])

        prompt = f"""Plan the execution steps for this TODO.

TODO: {todo.title}
Description: {todo.description}
Context: {todo.context.value}
Source: {todo.source.file_path if todo.source else 'None'}

Generate a sequence of actions to complete this TODO.
Available action types:
- open_file: Open a file in editor
- run_command: Run a terminal command
- open_url: Open a URL in browser
- edit_file: Make changes to a file
- create_file: Create a new file
- spawn_subtask: Create a subtask

Respond in JSON format:
[
  {{"action": "action_type", "target": "path/url/command", "description": "why"}},
  ...
]

Respond with ONLY the JSON array."""

        response = await self.llm.generate(prompt, system_prompt)

        try:
            response = response.strip()
            if response.startswith("```"):
                response = response.split("```")[1]
                if response.startswith("json"):
                    response = response[4:]
            
            return json.loads(response)
        except (json.JSONDecodeError, KeyError):
            return []

    async def clarify(self, todo: TodoNode) -> str | None:
        """
        Generate a clarifying question if the TODO is ambiguous.
        Returns None if the TODO is clear enough.
        """
        prompt = f"""Evaluate this TODO for clarity.

TODO: {todo.title}
Description: {todo.description}

If this TODO is ambiguous or needs clarification, respond with ONE specific question.
If it's clear enough to work on, respond with "CLEAR".

Respond with either the question or "CLEAR", nothing else."""

        response = await self.llm.generate(prompt)
        response = response.strip()

        if response.upper() == "CLEAR":
            return None
        return response

    async def execute_todo(self, todo: TodoNode) -> ExecutionResult:
        """
        Execute a TODO (or plan its execution).
        
        For MVP, this plans but doesn't auto-execute unless configured.
        """
        import time
        start = time.time()

        # Plan execution
        actions = await self.plan_execution(todo)
        todo.actions = actions

        # Generate subtasks if needed
        spawned = []
        if todo.is_leaf() and todo.can_spawn_children(self.config.max_recursion_depth):
            subtask_data = await self.generate_subtasks(todo)
            for data in subtask_data:
                child = self.tree.spawn_child(todo.id, **data)
                if child:
                    spawned.append(child)

        # Mark as in progress
        self.tree.start_node(todo.id)

        duration = int((time.time() - start) * 1000)

        return ExecutionResult(
            success=True,
            action_type="plan",
            output=f"Planned {len(actions)} actions, spawned {len(spawned)} subtasks",
            spawned_todos=spawned,
            duration_ms=duration,
        )

    def initialize_tree(self, scan_result: ScanResult) -> TodoTree:
        """Initialize the TODO tree from scan results."""
        self.tree = TodoTree()
        self.tree.add_todos(scan_result.todos)
        return self.tree

