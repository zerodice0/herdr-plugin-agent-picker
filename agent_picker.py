#!/usr/bin/env python3
"""Herdr popup picker that lists agents in attention and state-change order."""

import curses
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import unicodedata
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

STATUS_ORDER = ("blocked", "done", "working", "idle")
FILTER_KEYS = {"b": "blocked", "w": "working", "i": "idle", "d": "done", "e": "ready"}
# Filter values that match more than one agent status; any other value matches only itself.
STATUS_FILTERS = {"ready": ("done", "idle")}
SORT_MODES = ("status", "recent")
SORT_LABELS = {"status": "attention", "recent": "recent"}
STATUS_ICONS = {"blocked": "!", "done": "✓", "working": "●", "idle": "○"}

# Korean 2-set layout jamo for the navigation keys, so the picker works while
# the input method is in Hangul mode.
HANGUL_KEYS = {
    "ㅓ": "j", "ㅏ": "k", "ㅠ": "b", "ㅈ": "w", "ㅑ": "i", "ㅇ": "d", "ㄷ": "e",
    "ㅁ": "a", "ㄴ": "s", "ㄱ": "r", "ㅂ": "q", "ㅎ": "g", "ㅡ": "m", "ㅔ": "p", "ㅌ": "x",
}

VIEW_FILE = "view.json"
USAGE_FILE = "usage.json"

LOCAL_REFRESH_SECONDS = 1.0
REMOTE_REFRESH_SECONDS = 5.0
FAILED_REFRESH_SECONDS = 15.0
LOCAL_TIMEOUT_SECONDS = 5.0
REMOTE_TIMEOUT_SECONDS = 15.0


@dataclass(frozen=True)
class Endpoint:
    index: int
    label: str
    machine_id: Optional[str] = None

    @property
    def remote(self) -> bool:
        return self.machine_id is not None

    @property
    def key(self) -> str:
        """Stable machine filter value: the saved machine id, or ``local``."""
        return self.machine_id or "local"


@dataclass(frozen=True)
class Agent:
    endpoint: Endpoint
    pane_id: str
    status: str
    seq: int
    name: str
    workspace: str
    tab: str
    recap: str
    title: str
    cwd: str
    project: str

    @property
    def key(self) -> Tuple[int, str]:
        return (self.endpoint.index, self.pane_id)

    def haystack(self) -> str:
        return " ".join(
            (self.name, self.endpoint.label, self.workspace, self.tab,
             self.recap, self.title, self.cwd, self.project, self.status)
        ).lower()


def project_of(workspace: dict, fallback: str) -> str:
    """Name the project a workspace belongs to.

    Git workspaces use the repository name, so linked worktrees and the same
    repository on other machines group together; others use the workspace label.
    """
    repo_name = (workspace.get("worktree") or {}).get("repo_name")
    return repo_name or workspace.get("label") or fallback


def agents_from_snapshot(snapshot: dict, endpoint: Endpoint) -> List[Agent]:
    workspaces = {item.get("workspace_id"): item for item in snapshot.get("workspaces") or []}
    tabs = {
        item.get("tab_id"): item.get("label") or item.get("tab_id") or ""
        for item in snapshot.get("tabs") or []
    }
    agents = []
    for item in snapshot.get("agents") or []:
        pane_id = item.get("pane_id")
        if not pane_id:
            continue
        tokens = item.get("tokens") or {}
        workspace_id = item.get("workspace_id") or pane_id.split(":", 1)[0]
        workspace = workspaces.get(workspace_id) or {}
        agents.append(Agent(
            endpoint=endpoint,
            pane_id=pane_id,
            status=item.get("agent_status") or "unknown",
            seq=int(item.get("state_change_seq") or 0),
            name=item.get("display_agent") or item.get("name") or item.get("agent") or pane_id,
            workspace=workspace.get("label") or workspace_id,
            tab=tabs.get(item.get("tab_id"), item.get("tab_id") or ""),
            recap=str(tokens.get("recap") or ""),
            title=item.get("terminal_title_stripped") or item.get("terminal_title") or "",
            cwd=item.get("cwd") or "",
            project=project_of(workspace, workspace_id),
        ))
    return agents


def status_rank(status: str) -> int:
    try:
        return STATUS_ORDER.index(status)
    except ValueError:
        return len(STATUS_ORDER)


def sort_agents(agents: Sequence[Agent], mode: str) -> List[Agent]:
    """Order agents for display.

    ``state_change_seq`` is a per-server counter, so agents on different
    machines are never compared by it: within the same status group the local
    machine comes first, then saved machines in ``herdr machine list`` order.
    """
    if mode == "recent":
        return sorted(agents, key=lambda a: (a.endpoint.index, -a.seq, a.pane_id))
    return sorted(agents, key=lambda a: (status_rank(a.status), a.endpoint.index, a.seq, a.pane_id))


def filter_agents(agents: Sequence[Agent], status: Optional[str], query: str,
                  machine: Optional[str] = None, project: Optional[str] = None) -> List[Agent]:
    """Keep agents matching every active filter.

    ``machine`` is an ``Endpoint.key`` and ``project`` an ``Agent.project``.
    """
    statuses = STATUS_FILTERS.get(status, (status,))
    words = query.lower().split()
    return [
        agent for agent in agents
        if (status is None or agent.status in statuses)
        and (machine is None or agent.endpoint.key == machine)
        and (project is None or agent.project == project)
        and all(word in agent.haystack() for word in words)
    ]


@dataclass
class View:
    """The picker view remembered between popups (the search query is not)."""
    sort: str = "status"
    status: Optional[str] = None
    machine: Optional[str] = None
    project: Optional[str] = None


def read_state(state_dir: Optional[str], name: str) -> dict:
    if not state_dir:
        return {}
    try:
        with open(os.path.join(state_dir, name), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_state(state_dir: Optional[str], name: str, data: dict) -> None:
    """Replace a state file atomically; failures never block the picker."""
    if not state_dir:
        return
    path = os.path.join(state_dir, name)
    try:
        os.makedirs(state_dir, exist_ok=True)
        with open(path + ".tmp", "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False)
        os.replace(path + ".tmp", path)
    except OSError:
        pass


def load_view(state_dir: Optional[str]) -> View:
    data = read_state(state_dir, VIEW_FILE)
    view = View()
    if data.get("sort") in SORT_MODES:
        view.sort = data["sort"]
    if data.get("status") in FILTER_KEYS.values():
        view.status = data["status"]
    for field in ("machine", "project"):
        value = data.get(field)
        if isinstance(value, str) and value:
            setattr(view, field, value)
    return view


def save_view(state_dir: Optional[str], view: View) -> None:
    write_state(state_dir, VIEW_FILE, {
        "version": 1, "sort": view.sort, "status": view.status,
        "machine": view.machine, "project": view.project,
    })


def load_usage(state_dir: Optional[str]) -> Dict[str, Tuple[int, float]]:
    """Return ``{project: (focus count, last focus time)}`` from ``usage.json``."""
    projects = read_state(state_dir, USAGE_FILE).get("projects")
    usage = {}
    if isinstance(projects, dict):
        for name, entry in projects.items():
            if not isinstance(entry, dict):
                continue
            count, last = entry.get("count"), entry.get("last")
            if isinstance(count, int) and isinstance(last, (int, float)):
                usage[name] = (count, float(last))
    return usage


def record_usage(state_dir: Optional[str], project: str) -> None:
    if not state_dir:
        return
    usage = load_usage(state_dir)
    count = usage.get(project, (0, 0.0))[0]
    usage[project] = (count + 1, time.time())
    write_state(state_dir, USAGE_FILE, {"version": 1, "projects": {
        name: {"count": count, "last": last} for name, (count, last) in usage.items()
    }})


@dataclass(frozen=True)
class ProjectRow:
    name: Optional[str]  # None is the "All projects" row
    agents: int
    blocked: int
    done: int
    machines: Tuple[str, ...]

    @property
    def label(self) -> str:
        return "All projects" if self.name is None else self.name

    def summary(self) -> str:
        parts = ["%d agent%s" % (self.agents, "" if self.agents == 1 else "s")]
        if self.blocked:
            parts.append("%d blocked" % self.blocked)
        if self.done:
            parts.append("%d done" % self.done)
        if self.machines:
            parts.append(", ".join(self.machines))
        return " · ".join(parts)


def project_row(name: Optional[str], agents: Sequence[Agent]) -> ProjectRow:
    endpoints = sorted({agent.endpoint for agent in agents}, key=lambda endpoint: endpoint.index)
    return ProjectRow(
        name, len(agents),
        sum(agent.status == "blocked" for agent in agents),
        sum(agent.status == "done" for agent in agents),
        tuple(endpoint.label for endpoint in endpoints),
    )


def project_rows(agents: Sequence[Agent], usage: Dict[str, Tuple[int, float]],
                 keep: Optional[str] = None) -> List[ProjectRow]:
    """List projects across every machine, most used in the picker first.

    The first row is "All projects"; ``keep`` (the active project filter) stays
    listed even when none of its agents are running.
    """
    groups: Dict[str, List[Agent]] = {}
    for agent in agents:
        groups.setdefault(agent.project, []).append(agent)
    if keep is not None:
        groups.setdefault(keep, [])

    def order(name: str) -> Tuple[int, float, str]:
        count, last = usage.get(name, (0, 0.0))
        return (-count, -last, name)

    return [project_row(None, agents)] + [project_row(name, groups[name]) for name in sorted(groups, key=order)]


def cell_width(char: str) -> int:
    if unicodedata.combining(char) or char in "\u200d\ufe0e\ufe0f":
        return 0
    return 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1


def text_width(text: str) -> int:
    return sum(cell_width(char) for char in text)


def fit(text: str, width: int) -> str:
    """Truncate ``text`` to ``width`` terminal cells and pad it with spaces."""
    if width <= 0:
        return ""
    text = "".join(" " if char in "\t\r\n" else char for char in text)
    if text_width(text) <= width:
        return text + " " * (width - text_width(text))
    out, used = [], 0
    for char in text:
        w = cell_width(char)
        if used + w > width - 1:
            break
        out.append(char)
        used += w
    return "".join(out) + "…" + " " * (width - 1 - used)


def herdr_bin() -> str:
    return os.environ.get("HERDR_BIN_PATH") or "herdr"


def machine_args(endpoint: Endpoint) -> List[str]:
    return ["--machine", endpoint.machine_id] if endpoint.machine_id else []


def cli_error(stderr: bytes, fallback: str) -> str:
    """Reduce a Herdr CLI failure to its message, without the machine prefix."""
    lines = stderr.decode("utf-8", "replace").strip().splitlines()
    if not lines:
        return fallback
    message = lines[-1]
    wrapped = re.search(r'error: "(.*)" \}$', message)
    if wrapped:
        message = wrapped.group(1)
    message = re.sub(r"^Error:\s*", "", message)
    return re.sub(r"^machine '[^']*'( \(session [^)]*\))?: ", "", message)


class Collector:
    """Polls each endpoint's ``api snapshot`` on a background thread."""

    def __init__(self, endpoints: Sequence[Endpoint]):
        self.endpoints = list(endpoints)
        self.lock = threading.Lock()
        self.agents: Dict[int, List[Agent]] = {}
        self.errors: Dict[int, str] = {}
        self.version = 0
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.processes = set()

    def start(self) -> None:
        for endpoint in self.endpoints:
            threading.Thread(target=self._poll, args=(endpoint,), daemon=True).start()

    def refresh(self) -> None:
        self.wake.set()

    def close(self) -> None:
        self.stop.set()
        self.wake.set()
        with self.lock:
            processes = list(self.processes)
        for process in processes:
            if process.poll() is None:
                process.kill()

    def snapshot(self) -> Tuple[int, List[Agent], Dict[int, str], set]:
        with self.lock:
            agents = [agent for items in self.agents.values() for agent in items]
            return self.version, agents, dict(self.errors), set(self.agents)

    def _fetch(self, endpoint: Endpoint) -> dict:
        timeout = REMOTE_TIMEOUT_SECONDS if endpoint.remote else LOCAL_TIMEOUT_SECONDS
        process = subprocess.Popen(
            [herdr_bin(), *machine_args(endpoint), "api", "snapshot"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        with self.lock:
            self.processes.add(process)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise RuntimeError("timed out")
        finally:
            with self.lock:
                self.processes.discard(process)
        if process.returncode != 0:
            raise RuntimeError(cli_error(stderr, "exit %d" % process.returncode))
        return json.loads(stdout)["result"]["snapshot"]

    def _poll(self, endpoint: Endpoint) -> None:
        interval = REMOTE_REFRESH_SECONDS if endpoint.remote else LOCAL_REFRESH_SECONDS
        while not self.stop.is_set():
            delay = interval
            try:
                agents = agents_from_snapshot(self._fetch(endpoint), endpoint)
                with self.lock:
                    self.agents[endpoint.index] = agents
                    self.errors.pop(endpoint.index, None)
                    self.version += 1
            except Exception as error:  # surfaced in the footer, polling continues
                if self.stop.is_set():
                    return
                with self.lock:
                    self.errors[endpoint.index] = str(error) or type(error).__name__
                    self.version += 1
                delay = FAILED_REFRESH_SECONDS if endpoint.remote else interval
            self.wake.wait(delay)
            self.wake.clear()


def load_endpoints() -> List[Endpoint]:
    endpoints = [Endpoint(0, socket.gethostname().split(".", 1)[0] or "local")]
    try:
        output = subprocess.run(
            [herdr_bin(), "machine", "list", "--json"],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=LOCAL_TIMEOUT_SECONDS, check=True,
        ).stdout
        machines = json.loads(output)
    except (OSError, subprocess.SubprocessError, ValueError):
        machines = []
    for machine in machines:
        if machine.get("enabled") and machine.get("id"):
            endpoints.append(Endpoint(len(endpoints), machine.get("label") or machine["id"], machine["id"]))
    return endpoints


def current_project() -> Optional[str]:
    """Return the project of the local server's focused workspace.

    A popup is not a pane, so the focused workspace is the one under the popup.
    """
    try:
        output = subprocess.run(
            [herdr_bin(), "api", "snapshot"],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=LOCAL_TIMEOUT_SECONDS, check=True,
        ).stdout
        snapshot = json.loads(output)["result"]["snapshot"]
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        return None
    focused = snapshot.get("focused_workspace_id")
    for workspace in snapshot.get("workspaces") or []:
        if focused and workspace.get("workspace_id") == focused:
            return project_of(workspace, focused)
    return None


def focus_agent(agent: Agent) -> Optional[str]:
    """Focus the agent's pane on its server and return an error message on failure.

    Herdr has no API to switch the client's selected machine, so a remote focus
    only moves that server's focus; a local toast tells the user where to look.
    """
    try:
        result = subprocess.run(
            [herdr_bin(), *machine_args(agent.endpoint), "agent", "focus", agent.pane_id],
            stdin=subprocess.DEVNULL, capture_output=True,
            timeout=REMOTE_TIMEOUT_SECONDS if agent.endpoint.remote else LOCAL_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return "focus timed out"
    except OSError as error:
        return str(error)
    if result.returncode != 0:
        return cli_error(result.stderr, "focus failed")
    if agent.endpoint.remote:
        try:
            subprocess.run(
                [herdr_bin(), "notification", "show", "%s: %s" % (agent.endpoint.label, agent.name),
                 "--body", "Focused on %s. Select %s in the sidebar to view it."
                 % (agent.endpoint.label, agent.endpoint.label),
                 "--sound", "none"],
                stdin=subprocess.DEVNULL, capture_output=True, timeout=LOCAL_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.SubprocessError):
            pass  # the focus itself succeeded; the toast is only a hint
    return None


class Picker:
    def __init__(self, collector: Collector, view: View, persist: bool = False,
                 usage: Optional[Dict[str, Tuple[int, float]]] = None):
        self.collector = collector
        self.view = view
        self.persist = persist  # save the view when the popup closes
        self.usage = usage or {}
        self.mode = "agents"  # or "projects"
        self.query = ""
        self.searching = False
        self.selected: Optional[Tuple[int, str]] = None
        self.scroll = 0
        self.message = ""
        self.rows: List[Agent] = []
        self.projects: List[ProjectRow] = []
        self.project_cursor = 0
        self.project_scroll = 0
        self.multi = len(collector.endpoints) > 1
        if view.machine is not None and view.machine not in [e.key for e in collector.endpoints]:
            self.message = " saved machine %s is not available" % view.machine
            view.machine = None

    def compute_rows(self) -> None:
        _, agents, _, _ = self.collector.snapshot()
        view = self.view
        self.rows = filter_agents(sort_agents(agents, view.sort), view.status, self.query,
                                  view.machine, view.project)
        keys = [agent.key for agent in self.rows]
        if self.selected not in keys:
            self.selected = keys[0] if keys else None
        if self.mode == "projects":
            self.refresh_projects(agents)

    def refresh_projects(self, agents: Sequence[Agent]) -> None:
        # Keep the cursor on the same project when rows appear or reorder.
        current = self.projects[self.project_cursor].name if self.projects else self.view.project
        self.projects = project_rows(agents, self.usage, self.view.project)
        names = [row.name for row in self.projects]
        self.project_cursor = names.index(current) if current in names else 0

    def machine_label(self, key: str) -> str:
        for endpoint in self.collector.endpoints:
            if endpoint.key == key:
                return endpoint.label
        return key

    def active_filters(self) -> List[str]:
        filters = []
        if self.view.status:
            filters.append(self.view.status)
        if self.view.machine:
            filters.append(self.machine_label(self.view.machine))
        if self.view.project:
            filters.append("project: " + self.view.project)
        return filters

    def index(self) -> int:
        for position, agent in enumerate(self.rows):
            if agent.key == self.selected:
                return position
        return 0

    def move(self, delta: int) -> None:
        if self.rows:
            position = max(0, min(len(self.rows) - 1, self.index() + delta))
            self.selected = self.rows[position].key

    def jump(self, last: bool) -> None:
        if self.rows:
            self.selected = self.rows[-1 if last else 0].key

    def set_filter(self, status: Optional[str]) -> None:
        self.view.status = status
        self.query = ""
        self.selected = None

    def cycle_machine(self) -> None:
        keys = [None] + [endpoint.key for endpoint in self.collector.endpoints]
        position = keys.index(self.view.machine) if self.view.machine in keys else 0
        self.view.machine = keys[(position + 1) % len(keys)]
        self.selected = None

    def clear_filters(self) -> None:
        self.view.status = self.view.machine = self.view.project = None
        self.query = ""
        self.searching = False
        self.selected = None

    def open_projects(self) -> None:
        self.mode = "projects"
        self.projects = []
        self.project_scroll = 0
        self.refresh_projects(self.collector.snapshot()[1])

    def handle(self, key) -> Optional[str]:
        """Apply one key. Returns ``"close"`` or ``"focus"`` to end the picker."""
        if self.mode == "projects":
            return self.handle_projects(key)
        if key in (curses.KEY_DOWN,):
            self.move(1)
        elif key in (curses.KEY_UP,):
            self.move(-1)
        elif key == curses.KEY_NPAGE:
            self.move(8)
        elif key == curses.KEY_PPAGE:
            self.move(-8)
        elif key in ("\n", "\r", curses.KEY_ENTER):
            return "focus" if self.selected else None
        elif self.searching:
            return self.handle_search(key)
        else:
            return self.handle_navigation(key)
        return None

    def handle_search(self, key) -> Optional[str]:
        if key == "\x1b":
            self.searching = False
            self.query = ""
        elif key in ("\x7f", "\b", curses.KEY_BACKSPACE):
            self.query = self.query[:-1]
        elif key == "\x15":  # ctrl+u
            self.query = ""
        elif key == "\x0e":  # ctrl+n
            self.move(1)
        elif key == "\x10":  # ctrl+p
            self.move(-1)
        elif isinstance(key, str) and key.isprintable():
            self.query += key
        return None

    def handle_navigation(self, key) -> Optional[str]:
        if isinstance(key, str):
            key = HANGUL_KEYS.get(key, key)
        if key in ("\x1b", "q"):
            return "close"
        if key == "j":
            self.move(1)
        elif key == "k":
            self.move(-1)
        elif key == "\x04":  # ctrl+d
            self.move(8)
        elif key == "\x15":  # ctrl+u
            self.move(-8)
        elif key in ("g", curses.KEY_HOME):
            self.jump(False)
        elif key in ("G", curses.KEY_END):
            self.jump(True)
        elif key in FILTER_KEYS:
            self.set_filter(FILTER_KEYS[key])
        elif key == "a":
            self.set_filter(None)
        elif key in ("\x7f", "\b", curses.KEY_BACKSPACE):
            self.view.status = None
        elif key == "m":
            self.cycle_machine()
        elif key == "p":
            self.open_projects()
        elif key == "x":
            self.clear_filters()
        elif key == "s":
            self.view.sort = SORT_MODES[(SORT_MODES.index(self.view.sort) + 1) % len(SORT_MODES)]
        elif key == "/":
            self.searching = True
            self.view.status = None
        elif key == "r":
            self.collector.refresh()
        return None

    def handle_projects(self, key) -> Optional[str]:
        if isinstance(key, str):
            key = HANGUL_KEYS.get(key, key)
        last = len(self.projects) - 1
        if key in ("\x1b", "q", "p"):
            self.mode = "agents"
        elif key in ("\n", "\r", curses.KEY_ENTER):
            if self.projects:
                self.view.project = self.projects[self.project_cursor].name
                self.selected = None
            self.mode = "agents"
        elif key in ("j", curses.KEY_DOWN):
            self.project_cursor = min(last, self.project_cursor + 1)
        elif key in ("k", curses.KEY_UP):
            self.project_cursor = max(0, self.project_cursor - 1)
        elif key in ("\x04", curses.KEY_NPAGE):
            self.project_cursor = min(last, self.project_cursor + 8)
        elif key in ("\x15", curses.KEY_PPAGE):
            self.project_cursor = max(0, self.project_cursor - 8)
        elif key in ("g", curses.KEY_HOME):
            self.project_cursor = 0
        elif key in ("G", curses.KEY_END):
            self.project_cursor = max(0, last)
        elif key == "r":
            self.collector.refresh()
        return None


def init_colors() -> Dict[str, int]:
    colors = {"blocked": 0, "done": 0, "working": 0, "idle": curses.A_DIM, "dim": curses.A_DIM,
              "accent": curses.A_BOLD, "recap": 0, "error": curses.A_BOLD}
    if not curses.has_colors():
        return colors
    curses.start_color()
    try:
        curses.use_default_colors()
        background = -1
    except curses.error:
        background = curses.COLOR_BLACK
    for number, (name, color) in enumerate((
        ("blocked", curses.COLOR_RED), ("done", curses.COLOR_CYAN),
        ("working", curses.COLOR_YELLOW), ("accent", curses.COLOR_BLUE),
        ("recap", curses.COLOR_MAGENTA), ("error", curses.COLOR_RED),
    ), start=1):
        curses.init_pair(number, color, background)
        colors[name] = curses.color_pair(number) | (curses.A_BOLD if name in ("blocked", "accent", "error") else 0)
    return colors


def put(window, y: int, x: int, text: str, attr: int = 0) -> None:
    try:
        window.addstr(y, x, text, attr)
    except curses.error:
        pass  # writing the bottom-right cell raises after a successful write


def scroll_window(scroll: int, position: int, visible: int, count: int) -> int:
    """Return the first visible row that keeps ``position`` on screen."""
    if position < scroll:
        scroll = position
    elif position >= scroll + visible:
        scroll = position - visible + 1
    return max(0, min(scroll, max(0, count - visible)))


def draw(window, picker: Picker, colors: Dict[str, int]) -> None:
    window.erase()
    height, width = window.getmaxyx()
    _, agents, errors, loaded = picker.collector.snapshot()
    if picker.mode == "projects":
        draw_projects(window, picker, colors, height, width)
    else:
        draw_agents(window, picker, colors, height, width, len(agents), errors, loaded)
    footer = picker.message or footer_text(picker, errors, loaded)
    attr = colors["error"] if picker.message or errors else colors["dim"]
    put(window, height - 1, 0, fit(footer, width - 1), attr)
    window.refresh()


def draw_agents(window, picker: Picker, colors: Dict[str, int], height: int, width: int,
                total: int, errors: Dict[int, str], loaded: set) -> None:
    filters = picker.active_filters()
    sort_label = SORT_LABELS[picker.view.sort]
    if filters:
        header = " Agents  sort: %s  %s  %d/%d" % (sort_label, " · ".join(filters), len(picker.rows), total)
        header_attr = colors["recap"] | curses.A_BOLD
    else:
        header = " Agents  sort: %s  filter: all  %d/%d" % (sort_label, len(picker.rows), total)
        header_attr = colors["accent"]
    put(window, 0, 0, fit(header, width), header_attr)
    if picker.searching or picker.query:
        put(window, 1, 0, fit(" / " + picker.query + ("▏" if picker.searching else ""), width))
    else:
        put(window, 1, 0, fit(" b w i d e a state  m machine  p project  x clear  s sort  / search", width),
            colors["dim"])

    body_top, body_bottom = 2, height - 1
    visible = max(0, body_bottom - body_top)
    picker.scroll = scroll_window(picker.scroll, picker.index(), visible, len(picker.rows))

    name_width = min(30, max([text_width(a.name) for a in picker.rows] + [8]))
    place_width = min(34, max([text_width(place(a, picker.multi)) for a in picker.rows] + [8]))
    for row, agent in enumerate(picker.rows[picker.scroll:picker.scroll + visible]):
        y = body_top + row
        selected = agent.key == picker.selected
        base = curses.A_REVERSE if selected else 0
        x = 0
        put(window, y, x, fit(" " + STATUS_ICONS.get(agent.status, "?") + " ", 3),
            base | colors.get(agent.status, 0))
        x += 3
        put(window, y, x, fit(agent.name, name_width) + " ", base | curses.A_BOLD)
        x += name_width + 1
        put(window, y, x, fit(place(agent, picker.multi), place_width) + " ", base | colors["dim"])
        x += place_width + 1
        rest = width - x
        if rest > 0 and agent.recap:
            recap = fit(agent.recap, min(rest, text_width(agent.recap) + 1))
            put(window, y, x, recap, base | colors["recap"])
            x += text_width(recap)
            rest = width - x
        if rest > 0:
            put(window, y, x, fit(agent.title, rest), base)

    if not picker.rows:
        if len(loaded) < len(picker.collector.endpoints) - len(errors):
            empty = " loading…"
        elif filters or picker.query:
            empty = " no matching agents — x clears filters"
        else:
            empty = " no matching agents"
        put(window, body_top, 0, fit(empty, width), colors["dim"])


def draw_projects(window, picker: Picker, colors: Dict[str, int], height: int, width: int) -> None:
    put(window, 0, 0, fit(" Projects  Enter select  Esc back", width), colors["accent"])
    put(window, 1, 0, fit(" all machines, most used in the picker first", width), colors["dim"])
    body_top = 2
    visible = max(0, height - 1 - body_top)
    picker.project_scroll = scroll_window(picker.project_scroll, picker.project_cursor, visible,
                                          len(picker.projects))
    name_width = min(40, max([text_width(item.label) for item in picker.projects] + [12]))
    for row, item in enumerate(picker.projects[picker.project_scroll:picker.project_scroll + visible]):
        y = body_top + row
        base = curses.A_REVERSE if picker.project_scroll + row == picker.project_cursor else 0
        mark = "✓" if item.name == picker.view.project else " "
        put(window, y, 0, " " + mark + " ", base | colors["accent"])
        put(window, y, 3, fit(item.label, name_width) + " ", base | curses.A_BOLD)
        x = 3 + name_width + 1
        if width > x:
            put(window, y, x, fit(item.summary(), width - x), base | colors["dim"])


def place(agent: Agent, multi: bool) -> str:
    where = agent.workspace + (" · " + agent.tab if agent.tab else "")
    return (agent.endpoint.label + ": " + where) if multi else where


def footer_text(picker: Picker, errors: Dict[int, str], loaded: set) -> str:
    parts = []
    for endpoint in picker.collector.endpoints:
        if endpoint.index in errors:
            parts.append("%s: %s" % (endpoint.label, errors[endpoint.index]))
        elif endpoint.remote and endpoint.index not in loaded:
            parts.append("%s: loading" % endpoint.label)
    if parts:
        return " " + "  ".join(parts)
    if picker.mode == "projects":
        return " j/k move  g/G first/last  Enter select  Esc back"
    if picker.searching:
        return " type to search  Esc clear  Enter focus  ↑↓ move"
    return " j/k move  Enter focus  r refresh  Esc close"


def run(window, current: bool = False) -> int:
    """Run the picker; ``current`` opens it filtered to the focused workspace's project.

    The current-project preset starts from the remembered view but never saves
    it, so it does not change what the plain picker opens with.
    """
    curses.curs_set(0)
    window.keypad(True)
    window.timeout(200)
    colors = init_colors()
    state_dir = os.environ.get("HERDR_PLUGIN_STATE_DIR") or None
    view = load_view(state_dir)
    message = ""
    if current:
        view.machine = None
        view.project = current_project()
        if view.project is None:
            message = " current project unavailable"
    collector = Collector(load_endpoints())
    collector.start()
    picker = Picker(collector, view, persist=not current, usage=load_usage(state_dir))
    picker.message = message or picker.message
    seen = -1
    try:
        while True:
            version = collector.snapshot()[0]
            if version != seen:
                seen = version
                picker.compute_rows()
            draw(window, picker, colors)
            try:
                key = window.get_wch()
            except curses.error:
                continue
            if key == curses.KEY_RESIZE:
                continue
            picker.message = ""
            action = picker.handle(key)
            picker.compute_rows()
            if action == "close":
                return 0
            if action == "focus":
                agent = picker.rows[picker.index()]
                error = focus_agent(agent)
                if error is None:
                    record_usage(state_dir, agent.project)
                    return 0
                picker.message = " %s: %s" % (agent.endpoint.label, error)
    finally:
        collector.close()
        if picker.persist:
            save_view(state_dir, picker.view)


def main(argv: Sequence[str]) -> int:
    args = list(argv)
    if args not in (["tui"], ["tui", "--project", "current"]):
        print("usage: agent_picker.py tui [--project current]", file=sys.stderr)
        return 2
    os.environ.setdefault("ESCDELAY", "25")
    import locale
    locale.setlocale(locale.LC_ALL, "")
    return curses.wrapper(run, len(args) == 3)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
