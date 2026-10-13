# Evals

Micro-scenarios for the agent surface. Each is a situation taken from a real failure, plus the decision expected.
Run them with `uv run dev eval [name ...] --trials 3` whenever CLAUDE.md, an agent, a skill or a hook changes. Every
trial runs `claude -p` in a throwaway clone with no remote, so nothing can be pushed. It costs quota: run the
scenarios the change touches, not all of them. Results are stored in `.git/devkit/evals/`.

Add a scenario every time a real failure is found (the `harness-ops:harness` skill). Graders and fields: the evals
module of the agent-harness devkit. The template owns the scenarios it ships; give your own files other names.
