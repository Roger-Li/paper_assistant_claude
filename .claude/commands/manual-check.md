Analyze all uncommitted changes in the working tree and generate a guided manual smoke test plan for the user.

## Steps

1. Run `git diff --stat` and `git diff` to understand what files changed and what the changes do.
2. Read any changed source files in full (not just the diff) to understand the feature context.
3. Check the runtime environment: is qmd installed? How many papers exist? Is the server likely running? What's in `.env`?
4. Produce a structured smoke test plan covering:

### What changed
A table summarizing each changed file and what's new/different.

### Pre-flight
Any setup commands the user needs to run before testing (e.g., start the server, rebuild an index, install a dependency).

### Numbered test cases
Each test case should include:
- **What to do** — exact steps, commands to run, or UI actions to perform
- **What to expect** — specific observable outcomes (HTTP status codes, UI elements appearing/disappearing, text content, behavior)
- **Good test inputs** — concrete example values that exercise the feature, drawn from the user's actual paper library when possible
- **Corner cases** — edge cases and failure modes that should degrade gracefully

### Coverage priorities
- Happy path first (the feature works as designed)
- Degraded/disabled state (feature hidden or returns actionable errors when dependencies are missing)
- Stale state / race conditions (if there's async or client-side behavior)
- Interaction with existing features (nothing else broke)
- Documentation accuracy (if docs changed, verify they match the code)

## Output format
- Use markdown with clear headings
- Number all test cases sequentially
- Keep each test case concise — what to do, what to expect, nothing more
- Include actual CLI commands the user can copy-paste
- Reference specific paper IDs or tags from the user's library when available
