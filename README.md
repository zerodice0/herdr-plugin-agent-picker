# Herdr Agent Picker

`zerodice0.agent-picker` is a [Herdr](https://herdr.dev/) popup that lists every agent pane in the order you should look at it. It works like Herdr's Goto picker (`prefix+g`), but shows only agents and lets you choose the order.

Goto lists rows in the fixed workspace → tab → pane tree. This picker sorts by attention instead:

1. `blocked` agents (waiting for approval or an answer),
2. `done` agents (finished, not yet viewed),
3. `working` agents,
4. `idle` agents, then any agent Herdr cannot classify.

Within each group the agent whose state changed earliest comes first, using Herdr's `state_change_seq`. Press `s` to switch to the recent order, where the latest state change comes first.

Each row shows the state icon, the agent name (Herdr's `display_agent`, including labels such as HAM roles), the workspace and tab, the HAM recap token when present, and the terminal title. The list refreshes every second while the popup is open.

The plugin supports Herdr 0.9.5 or newer on Linux and macOS. It requires Python 3.9 or newer and uses only the standard library (`curses`).

## Install

Install from GitHub:

```sh
herdr plugin install zerodice0/herdr-plugin-agent-picker
```

For local development, link this checkout:

```sh
herdr plugin link .
```

Bind the `open` action to a key in `~/.config/herdr/config.toml`, then reload the config (`herdr server reload-config`):

```toml
[[keys.command]]
key = "prefix+ctrl+a"
type = "plugin_action"
command = "zerodice0.agent-picker.open"
description = "agent picker"
```

Pick any key that is free in your config. To replace the Goto picker, set `goto` to another key under `[keys]` (or unset it) and bind `prefix+g` to the action instead. The action can also be run from the command palette or with `herdr plugin action invoke zerodice0.agent-picker.open`.

## Keys

The picker uses the same keys as Herdr's Goto picker.

| Key | Action |
| --- | --- |
| `j` / `k`, `↓` / `↑` | Move the selection |
| `Ctrl+D` / `Ctrl+U`, `PgDn` / `PgUp` | Move by eight rows |
| `g` / `G`, `Home` / `End` | Jump to the first or last row |
| `b` / `w` / `i` / `d` | Show only blocked / working / idle / done agents |
| `a` | Show all agents and clear the search |
| `s` | Toggle the attention and recent order |
| `/` | Search; every space-separated word must match the name, machine, workspace, tab, recap, title, working directory, or state |
| `r` | Refresh now |
| `Enter` | Focus the selected agent and close the picker |
| `Esc` | Clear the search while searching; otherwise close the picker (`q` also closes) |

With a Korean input method active, the 2-set jamo for the navigation keys (`ㅓ`, `ㅏ`, `ㅠ`, `ㅈ`, `ㅑ`, `ㅇ`, `ㅁ`, `ㄴ`, `ㄱ`, `ㅂ`, `ㅎ`) work the same way outside search.

## Saved machines

When `herdr machine list` has enabled machines, the picker also lists their agents and prefixes each location with the machine label. Each machine is polled in the background with `herdr --machine <id> api snapshot`; an unreachable machine shows its error in the footer and is retried every 15 seconds without blocking the rest of the list.

`state_change_seq` is a per-server counter, so agents on different machines are never compared by it. Within a state group the local agents come first, then each saved machine in `herdr machine list` order.

Enter on a remote agent runs `herdr --machine <id> agent focus`, which moves that server's focus to the pane. Herdr 0.9.5 has no API for switching the client's selected machine, so the picker shows a toast naming the machine; select it in the sidebar to see the focused agent.

## Development

```sh
python3 -m unittest discover -s tests -v
```
