# Herdr Agent Picker

[한국어](README.ko.md) · English

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

Install a release from GitHub on every Herdr server you run the picker on (plugin actions run on the server of the machine the client has selected):

```sh
herdr plugin install zerodice0/herdr-plugin-agent-picker --ref v0.2.0
```

For local development, link this checkout:

```sh
herdr plugin link .
```

Bind the actions to keys in the client's `~/.config/herdr/config.toml`, then reload the config (`herdr server reload-config`, and reload config in each running client):

```toml
[[keys.command]]
key = "prefix+ctrl+a"
type = "plugin_action"
command = "zerodice0.agent-picker.open"
description = "agent picker"

[[keys.command]]
key = "prefix+ctrl+s"
type = "plugin_action"
command = "zerodice0.agent-picker.open-project"
description = "agent picker (current project)"
```

Pick any keys that are free in your config. To replace the Goto picker, set `goto` to another key under `[keys]` (or unset it) and bind `prefix+g` to the action instead. The actions can also be run from the command palette or with `herdr plugin action invoke zerodice0.agent-picker.open`.

## Keys

The picker uses the keys of Herdr's Goto picker and adds finished, machine, and project filters.

| Key | Action |
| --- | --- |
| `j` / `k`, `↓` / `↑` | Move the selection |
| `Ctrl+D` / `Ctrl+U`, `PgDn` / `PgUp` | Move by eight rows |
| `g` / `G`, `Home` / `End` | Jump to the first or last row |
| `b` / `w` / `i` / `d` | Show only blocked / working / idle / done agents |
| `e` | Show only finished agents: done and idle |
| `a` | Clear the state filter and the search |
| `m` | Cycle the machine filter: all, the local machine, then each saved machine |
| `p` | Open the project list |
| `x` | Clear every filter (state, machine, project) and the search |
| `Backspace` | Clear the state filter |
| `s` | Toggle the attention and recent order |
| `/` | Search; every space-separated word must match the name, machine, workspace, tab, recap, title, working directory, project, or state |
| `r` | Refresh now |
| `Enter` | Focus the selected agent and close the picker |
| `Esc` | Clear the search while searching; otherwise close the picker (`q` also closes) |

With a Korean input method active, the 2-set jamo for these keys (`ㅓ`, `ㅏ`, `ㅠ`, `ㅈ`, `ㅑ`, `ㅇ`, `ㄷ`, `ㅁ`, `ㅡ`, `ㅔ`, `ㅌ`, `ㄴ`, `ㄱ`, `ㅂ`, `ㅎ`) work the same way outside search.

Done means finished but not yet viewed; Herdr turns a done agent idle once you focus it. Use `e` to see every agent that is not working or blocked.

## Remembered view

The picker remembers the sort order and the state, machine, and project filters when it closes, and opens with them next time. The search text is not remembered. The view is stored in `view.json` under the plugin state directory (`$HERDR_PLUGIN_STATE_DIR`, normally `~/.local/state/herdr/plugins/zerodice0.agent-picker/`) of the server that runs the picker.

While any filter is active, the header lists it in a highlighted color instead of `filter: all`, and an empty list says `x clears filters`. If the remembered machine is no longer in `herdr machine list`, the picker drops that filter and says so in the footer.

## Projects

Every agent belongs to a project: the git repository name of its workspace (`repo_name` in Herdr's worktree metadata), or the workspace name when the workspace is not a git checkout. Linked worktrees and the same repository on other machines share one project, so a project groups agents whose workspace names differ.

`p` lists every project across all machines with its agent, blocked, and done counts and the machines it runs on, regardless of the current filters. `Enter` filters the picker to the highlighted project (`All projects` removes the filter); `Esc`, `q`, or `p` goes back without changing it. Projects you focus agents in most often through the picker come first, then the most recently used, then by name; the counts are stored in `usage.json` next to `view.json`.

## Current project action

The `open-project` action opens the picker filtered to the project of the workspace under the popup, on all machines. It starts from the remembered sort and state filter, ignores the remembered machine and project, and does not save its view, so the plain `open` action keeps its own remembered view. If Herdr cannot report the focused workspace, the picker opens without a project filter and says so in the footer.

## Saved machines

When `herdr machine list` has enabled machines, the picker also lists their agents and prefixes each location with the machine label. The local machine is the server that runs the picker, and the other machines are the ones in that server's `herdr machine list`, so the list can differ between servers. Each machine is polled in the background with `herdr --machine <id> api snapshot`; an unreachable machine shows its error in the footer and is retried every 15 seconds without blocking the rest of the list.

`state_change_seq` is a per-server counter, so agents on different machines are never compared by it. Within a state group the local agents come first, then each saved machine in `herdr machine list` order.

Enter on a remote agent runs `herdr --machine <id> agent focus`, which moves that server's focus to the pane. Herdr 0.9.5 has no API for switching the client's selected machine, so the picker shows a toast naming the machine; select it in the sidebar to see the focused agent.

## Development

```sh
python3 -m unittest discover -s tests -v
```
