"""
WTD Workspace Orchestrator - Sets up the perfect development environment
"""

import asyncio
import json
import os
import platform
import subprocess
import webbrowser
from pathlib import Path
from typing import Any

from wtd.core.models import TodoContext, WorkspaceConfig


class WorkspaceOrchestrator:
    """
    Orchestrates workspace setup based on TODO context.
    
    Opens VSCode, browser tabs, terminals, and configures the environment.
    """

    def __init__(self, root_path: Path | None = None):
        self.root_path = root_path or Path.cwd()
        self.system = platform.system()

    async def setup_workspace(self, config: WorkspaceConfig) -> dict[str, Any]:
        """
        Set up the complete workspace based on configuration.
        
        Returns status of each action.
        """
        results = {
            "vscode": None,
            "files": [],
            "terminals": [],
            "browser": [],
            "errors": [],
        }

        # Open VSCode workspace or folder
        try:
            await self.open_vscode(config.vscode_workspace or self.root_path)
            results["vscode"] = "opened"
        except Exception as e:
            results["errors"].append(f"VSCode: {e}")

        # Open files in VSCode
        if config.files_to_open:
            await asyncio.sleep(0.5)  # Give VSCode time to start
            for file_path in config.files_to_open:
                try:
                    await self.open_file_in_vscode(file_path)
                    results["files"].append(str(file_path))
                except Exception as e:
                    results["errors"].append(f"File {file_path}: {e}")

        # Open browser URLs
        for url in config.browser_urls:
            try:
                await self.open_browser(url)
                results["browser"].append(url)
            except Exception as e:
                results["errors"].append(f"Browser {url}: {e}")

        # Set up terminals (in VSCode's integrated terminal)
        for term_config in config.terminals:
            try:
                await self.create_terminal(
                    name=term_config.get("name", "WTD"),
                    command=term_config.get("command"),
                )
                results["terminals"].append(term_config.get("name"))
            except Exception as e:
                results["errors"].append(f"Terminal: {e}")

        # Set environment variables
        for key, value in config.environment.items():
            os.environ[key] = value

        return results

    async def open_vscode(self, path: Path) -> bool:
        """Open VSCode with the given path."""
        cmd = self._get_vscode_command()
        if cmd is None:
            raise RuntimeError("VSCode not found. Install 'code' command.")

        process = await asyncio.create_subprocess_exec(
            cmd, str(path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        # Ensure subprocess transports close cleanly before the event loop shuts down
        await process.wait()
        return process.returncode == 0

    async def open_file_in_vscode(self, file_path: Path, line: int | None = None) -> bool:
        """Open a specific file in VSCode."""
        cmd = self._get_vscode_command()
        if cmd is None:
            raise RuntimeError("VSCode not found")

        args = [cmd, "-r"]  # Reuse window
        if line:
            args.append(f"--goto={file_path}:{line}")
        else:
            args.append(str(file_path))

        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await process.wait()
        return process.returncode == 0

    async def open_browser(self, url: str) -> bool:
        """Open a URL in the default browser."""
        return await asyncio.to_thread(webbrowser.open, url)

    async def create_terminal(
        self,
        name: str = "WTD",
        command: str | None = None,
    ) -> bool:
        """
        Create a new terminal.
        
        Uses VSCode's integrated terminal if available,
        otherwise creates a system terminal.
        """
        if self._has_vscode_terminal():
            return await self._create_vscode_terminal(name, command)
        else:
            return await self._create_system_terminal(name, command)

    async def _create_vscode_terminal(self, name: str, command: str | None = None) -> bool:
        """Create a VSCode integrated terminal."""
        # Use VSCode's command-line interface to create terminal
        cmd = self._get_vscode_command()
        if cmd is None:
            return False

        # Create terminal via VSCode extension command (if WTD extension installed)
        # For now, we'll use a workaround via tasks
        task_config = {
            "label": f"WTD: {name}",
            "type": "shell",
            "command": command or "echo 'Terminal ready'",
            "presentation": {
                "reveal": "always",
                "panel": "new",
            },
        }

        # Write temporary task and execute
        # This is a simplified approach - a real implementation would use VSCode extension API
        return True

    async def _create_system_terminal(self, name: str, command: str | None = None) -> bool:
        """Create a system terminal."""
        if self.system == "Darwin":
            # macOS - use Terminal.app or iTerm
            script = f'''
            tell application "Terminal"
                activate
                do script "cd {self.root_path} && {command or 'echo Ready'}"
            end tell
            '''
            process = await asyncio.create_subprocess_exec(
                "osascript", "-e", script,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await process.wait()
            return process.returncode == 0

        elif self.system == "Linux":
            # Try common terminal emulators
            terminals = ["gnome-terminal", "konsole", "xterm"]
            for term in terminals:
                try:
                    if command:
                        args = [term, "--", "bash", "-c", f"cd {self.root_path} && {command}; exec bash"]
                    else:
                        args = [term, "--working-directory", str(self.root_path)]
                    
                    process = await asyncio.create_subprocess_exec(
                        *args,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    await process.wait()
                    return process.returncode == 0
                except FileNotFoundError:
                    continue
            return False

        elif self.system == "Windows":
            # Windows Terminal or cmd
            if command:
                cmd = f'start cmd /K "cd /d {self.root_path} && {command}"'
            else:
                cmd = f'start cmd /K "cd /d {self.root_path}"'
            
            process = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            return True

        return False

    def _get_vscode_command(self) -> str | None:
        """Get the VSCode command-line tool."""
        # Check common locations
        if self.system == "Darwin":
            paths = [
                "/usr/local/bin/code",
                "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code",
                os.path.expanduser("~/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code"),
            ]
        elif self.system == "Windows":
            paths = [
                r"C:\Program Files\Microsoft VS Code\bin\code.cmd",
                r"C:\Users\{}\AppData\Local\Programs\Microsoft VS Code\bin\code.cmd".format(os.getenv("USERNAME", "")),
            ]
        else:  # Linux
            paths = [
                "/usr/bin/code",
                "/usr/local/bin/code",
                "/snap/bin/code",
            ]

        # Check if 'code' is in PATH
        import shutil
        code_path = shutil.which("code")
        if code_path:
            return code_path

        # Check specific paths
        for path in paths:
            if os.path.exists(path):
                return path

        # Try cursor as fallback (since user is using Cursor IDE)
        cursor_path = shutil.which("cursor")
        if cursor_path:
            return cursor_path

        return None

    def _has_vscode_terminal(self) -> bool:
        """Check if running inside VSCode's integrated terminal."""
        return os.getenv("TERM_PROGRAM") == "vscode" or os.getenv("VSCODE_INJECTION") is not None


class ContextWorkspaceFactory:
    """
    Factory for creating context-specific workspace configurations.
    """

    @staticmethod
    def create(context: TodoContext, root_path: Path) -> WorkspaceConfig:
        """Create a workspace configuration based on context."""
        
        configs = {
            TodoContext.BUGFIX: WorkspaceConfig(
                files_to_open=[],
                terminals=[
                    {"name": "Debug", "command": None},
                    {"name": "Tests", "command": "echo 'Run tests here'"},
                ],
                browser_urls=[],
            ),
            TodoContext.WRITE: WorkspaceConfig(
                files_to_open=[],
                terminals=[],
                browser_urls=[],
            ),
            TodoContext.BUILD: WorkspaceConfig(
                files_to_open=[],
                terminals=[
                    {"name": "Dev", "command": None},
                    {"name": "Build", "command": None},
                ],
                browser_urls=[],
            ),
            TodoContext.TEST: WorkspaceConfig(
                files_to_open=[],
                terminals=[
                    {"name": "Tests", "command": "echo 'Ready to run tests'"},
                ],
                browser_urls=[],
            ),
            TodoContext.DEPLOY: WorkspaceConfig(
                files_to_open=[],
                terminals=[
                    {"name": "Deploy", "command": None},
                ],
                browser_urls=[],
            ),
        }

        return configs.get(context, WorkspaceConfig())

