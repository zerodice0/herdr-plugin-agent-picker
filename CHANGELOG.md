# Changelog

All notable changes to this project are documented here.

## [Unreleased]

## [0.2.0] - 2026-11-22

- Remember the sort order and the state, machine, and project filters in `view.json` under the plugin state directory, and reopen the picker with them. The search text is not remembered.
- Add `e` to show finished agents (done and idle), `m` to cycle the machine filter, `p` to open a project list, and `x` to clear every filter, plus the jamo `ㄷ`, `ㅡ`, `ㅔ`, and `ㅌ`.
- Group agents into projects by git repository name, or workspace name outside git, so linked worktrees and the same repository on other machines appear together; the project is also searchable.
- Order the project list by how often the picker focused an agent in each project, stored in `usage.json`.
- Highlight active filters in the header and suggest `x` when filters hide every agent.
- Add the `open-project` action, which opens the picker filtered to the project of the focused workspace without changing the remembered view.
- Run the Python 3.9 CI job on Ubuntu 24.04, because the latest Ubuntu image has no Python 3.9 build.

## [0.1.0] - 2026-11-17

- Add a popup agent picker that lists agent panes as blocked, done, working, then idle, oldest state change first within each group, with a recent order toggle.
- Add Goto-style keys: `j`/`k` movement, `b`/`w`/`i`/`d`/`a` state filters, `/` word search, `s` sort toggle, `Enter` to focus, and `Esc` to close, plus Korean 2-set jamo equivalents.
- List agents on enabled saved machines alongside local agents, show per-machine errors in the footer, and toast the machine name after focusing a remote agent.
