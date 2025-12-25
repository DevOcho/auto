### LLM System Prompt: "Auto" Project Context & Guidelines

I am providing you with the source code for a Python CLI project called **"Auto"**. This tool manages local development environments using **k3s/k3d** clusters. Please use the following context, technology stack, and development rules when analyzing code or generating solutions.

#### 1. Project Overview
"Auto" is a command-line interface helper that automates the setup of local Kubernetes environments. It pulls git repositories, builds Docker images, spins up a local `k3d` cluster, manages a local container registry, and installs pods via Helm or raw K8s manifests.

#### 2. Technology Stack
*   **Language:** Python 3 (Typing hints used).
*   **CLI Framework:** `click` (commands are defined in `autocli/commands.py`).
*   **UI/Output:** `rich` (used for formatted printing, progress bars, and error messaging).
*   **Infrastructure:**
    *   **k3d / k3s:** The core cluster provider.
    *   **Kubectl:** Used via `subprocess` calls to manage resources.
    *   **Helm:** Used for chart deployment.
    *   **Docker:** Used for building images and managing the registry.
*   **Configuration:** YAML (parsed via `PyYAML`). Configuration is strictly typed in `~/.auto/config/local.yaml`.
*   **Linting/Formatting:** The project uses `black`, `isort`, `flake8`, and `pylint` (as seen in `.pre-commit-config.yaml`).

#### 3. Development Rules (Strict Adherence Required)
*   **Preserve Comments:** Do **not** remove existing comments when modifying code unless the logic has fundamentally changed. Comments are vital for my understanding.
*   **Full Function Replacements:** When suggesting code changes, provide the **entire function** (or file, if small) rather than small snippets or diffs. I want to copy/paste the whole block to ensure indentation and context remain correct.
*   **Output Style:** Use `from rich import print as rprint` for user output. Do not use standard `print()` unless debugging specific raw output.
*   **Error Handling:** Use `utils.declare_error()` for fatal errors to maintain consistency with the existing CLI experience.
*   **Subprocess Calls:** Prefer `utils.run_and_wait` or `utils.run_command_inside_pod` over writing raw `subprocess.run` calls, to ensure consistent error trapping and output suppression.

#### 4. Architecture Reference
*   **`auto.py`**: Entry point.
*   **`autocli/commands.py`**: Defines the CLI commands (start, stop, mysql, etc.) and arguments.
*   **`autocli/core.py`**: Contains the heavy lifting/business logic (logic for starting clusters, building docker images).
*   **`autocli/utils.py`**: Low-level helpers (shell execution, config loading, string parsing).

**Please acknowledge you understand these rules and the project structure before we begin.**
