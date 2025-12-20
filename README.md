# 🚀 WTD – What To Do

> **The Ultimate Recursive TODO Engine**

Turn every TODO into a self-replicating, AI-orchestrated action factory.

WTD is not a list — it is a **recursive agency engine** that reads a TODO, decides the shortest path to completion, spawns the exact subtasks needed, and executes them with zero human friction.

```
╦ ╦╔╦╗╔╦╗
║║║ ║  ║║
╚╩╝ ╩ ═╩╝
```

## ✨ Features

- **🔍 Smart Scanning** - Automatically finds TODOs in code comments, markdown files, issues, and notes
- **🧠 Context Detection** - AI determines if you're fixing a bug, writing docs, planning, or building
- **🌳 Recursive TODO Tree** - Tasks spawn subtasks until the root is truly done
- **🖥️ Workspace Orchestration** - Opens the perfect IDE layout, terminals, and browser tabs
- **📊 Interactive Dashboard** - Beautiful terminal UI with real-time progress tracking
- **🌐 REST API** - Full API for integrations and automation
- **🔒 Local-First** - Your data stays on your machine (optional cloud sync)

## 🚀 Quick Start

### Installation

```bash
# Clone the repository
cd wtd

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -e .

# Or install from requirements
pip install -r requirements.txt
```

### Usage

Just type `wtd` in any directory:

```bash
# Scan current directory and start working
wtd

# Scan a specific path
wtd /path/to/project

# Just scan without interactive mode
wtd scan

# Open the dashboard
wtd dashboard

# Execute the next TODO
wtd execute

# Show progress status
wtd status

# Start the API server
wtd serve
```

## 📖 Commands

| Command | Description |
|---------|-------------|
| `wtd` | Scan, analyze, and start working |
| `wtd scan` | Scan for TODOs only |
| `wtd dashboard` | Open interactive dashboard |
| `wtd execute` | Execute next actionable TODO |
| `wtd status` | Show progress status |
| `wtd config` | View/edit configuration |
| `wtd serve` | Start REST API server |

## ⚙️ Configuration

WTD can be configured via environment variables or a `.env` file:

```bash
# LLM Provider (ollama, openai, anthropic)
WTD_LLM_PROVIDER=ollama
WTD_OLLAMA_MODEL=llama3.2
WTD_OLLAMA_HOST=http://localhost:11434

# OpenAI (optional)
WTD_OPENAI_API_KEY=your-key-here
WTD_OPENAI_MODEL=gpt-4-turbo-preview

# Anthropic (optional)
WTD_ANTHROPIC_API_KEY=your-key-here
WTD_ANTHROPIC_MODEL=claude-3-opus-20240229

# Recursion Settings
WTD_MAX_RECURSION_DEPTH=7
WTD_FITNESS_DECAY_RATE=0.15

# Execution
WTD_AUTO_EXECUTE=false
WTD_TIMEOUT_SECONDS=90

# UI
WTD_THEME=dark
```

## 🧠 How It Works

### 1. Scan
WTD scans your codebase for tasks in:
- Code comments (TODO, FIXME, HACK, XXX, BUG, NOTE)
- Markdown task lists (checkbox items)
- GitHub issues (coming soon)
- Plain text notes

### 2. Analyze
The AI agent analyzes your TODOs and determines the context:
- 🐛 **Bugfix** - Fix bugs and errors
- ✍️ **Write** - Create documentation or content
- 📋 **Plan** - Design and architecture
- 📚 **Learn** - Research and study
- 🔨 **Build** - Implement features
- 🔧 **Refactor** - Improve code quality
- 🧪 **Test** - Write and run tests
- 🚀 **Deploy** - Ship to production

### 3. Execute
WTD creates a recursive TODO tree:
```
● Root TODO [build]
  ◐ Design API [plan]
    ● Define endpoints
    ○ Write schemas
  ○ Implement handlers [build]
  ○ Add tests [test]
```

Each TODO can spawn subtasks, and completing all subtasks collapses the parent.

### 4. Orchestrate
Based on context, WTD sets up your perfect workspace:
- Opens relevant files in VSCode/Cursor
- Creates terminal splits for dev/test
- Opens documentation in browser

## 🌐 API

WTD includes a full REST API:

```bash
# Start the server
wtd serve

# Endpoints
POST /v1/wtd/scan          # Scan for TODOs
POST /v1/wtd/execute       # Execute a TODO
GET  /v1/wtd/dashboard/:id # Get workspace config
GET  /v1/wtd/tree/:id      # Get TODO tree
POST /v1/wtd/spawn         # Spawn subtasks
```

API documentation available at `http://localhost:8787/docs`

## 🎨 Dashboard

The interactive dashboard provides:
- Real-time TODO tree visualization
- Progress tracking with completion percentage
- One-click task execution
- Keyboard shortcuts for power users

### Keyboard Shortcuts
| Key | Action |
|-----|--------|
| `q` | Quit |
| `r` | Refresh |
| `e` | Execute selected |
| `c` | Complete selected |
| `s` | Spawn subtasks |
| `?` | Help |

## 🔌 LLM Support

WTD supports multiple LLM providers:

### Ollama (Default - Local)
```bash
# Install Ollama
curl -fsSL https://ollama.ai/install.sh | sh

# Pull a model
ollama pull llama3.2

# WTD will use Ollama by default
wtd
```

### OpenAI
```bash
export WTD_LLM_PROVIDER=openai
export WTD_OPENAI_API_KEY=your-key
wtd
```

### Anthropic
```bash
export WTD_LLM_PROVIDER=anthropic
export WTD_ANTHROPIC_API_KEY=your-key
wtd
```

## 📊 KFIs (Key Feature Indicators)

| Metric | Target | Description |
|--------|--------|-------------|
| Time to First Action | ≤ 90s | From `wtd` to measurable progress |
| Context Accuracy | ≥ 95% | Correct context detection |
| Max Recursion Depth | 7 levels | Prevent infinite spawn |
| Human Keystrokes | ≤ 5 | Per completed TODO |

## 🛣️ Roadmap

| Milestone | Objective | Target |
|-----------|-----------|--------|
| Alpha | Scan + workspace setup | Q1 2026 |
| Beta | Recursive tree + auto-execution | Q2 2026 |
| 1.0 | Full agency: root TODO resolves itself | Q3 2026 |

## 🤝 Contributing

Contributions welcome! Please read our contributing guidelines.

## 📄 License

MIT License - see LICENSE for details.

---

**Reality WTD'd.**  
Just type `wtd`.  
The machine does the rest.

*Ship it.* 🚀

