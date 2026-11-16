# Agent instructions

## Working on this repository

- `agent_picker.py` is the single implementation module and the popup entrypoint (`python3 agent_picker.py tui`).
- `tests/test_agent_picker.py` covers the user-visible ordering, filtering, search, key handling, and row text fitting.
- `herdr-plugin.toml` declares the `open` action and the `picker` popup pane.
- Use Python 3.9+ and the standard library only; do not add third-party dependencies.
- README is the user-facing contract for keys, ordering, and saved-machine behavior. Update it when behavior changes, and put changes under CHANGELOG's `Unreleased` heading.
- Call Herdr through `HERDR_BIN_PATH` with argv lists; never run commands through a shell.

```sh
python3 -m unittest discover -s tests -v
```

## Commit messages

Every commit follows [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/) and is written in English: `<type>[optional scope][!]: <description>`, at most 72 characters, imperative mood, lowercase description, no trailing period. Wrap the body at 72 columns and explain what changed and why.

## Live smoke tests

Popups open on the user's client and `agent focus` moves real focus. Test against an isolated Herdr server (own `HERDR_SOCKET_PATH`, `HERDR_CONFIG_PATH`, and XDG directories) driven through a PTY-hosted client. Focusing an agent on a saved machine changes that remote server's focus; restore it afterwards.
