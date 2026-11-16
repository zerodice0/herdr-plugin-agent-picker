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
FILTER_KEYS = {"b": "blocked", "w": "working", "i": "idle", "d": "done"}
SORT_MODES = ("status", "recent")
SORT_LABELS = {"status": "attention", "recent": "recent"}
STATUS_ICONS = {"blocked": "!", "done": "✓", "working": "●", "idle": "○"}

# Korean 2-set layout jamo for the navigation keys, so the picker works while
# the input method is in Hangul mode.
HANGUL_KEYS = {
    "ㅓ": "j", "ㅏ": "k", "ㅠ": "b", "ㅈ": "w", "ㅑ": "i", "ㅇ": "d",
    "ㅁ": "a", "ㄴ": "s", "ㄱ": "r", "ㅂ": "q", "ㅎ": "g",
}

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

    @property
    def key(self) -> Tuple[int, str]:
        return (self.endpoint.index, self.pane_id)

    def haystack(self) -> str:
        return " ".join(
            (self.name, self.endpoint.label, self.workspace, self.tab,
             self.recap, self.title, self.cwd, self.status)
        ).lower()


def agents_from_snapshot(snapshot: dict, endpoint: Endpoint) -> List[Agent]:
    workspaces = {
        item.get("workspace_id"): item.get("label") or item.get("workspace_id") or ""
        for item in snapshot.get("workspaces") or []
    }
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
        agents.append(Agent(
            endpoint=endpoint,
            pane_id=pane_id,
            status=item.get("agent_status") or "unknown",
            seq=int(item.get("state_change_seq") or 0),
            name=item.get("display_agent") or item.get("name") or item.get("agent") or pane_id,
            workspace=workspaces.get(workspace_id, workspace_id),
            tab=tabs.get(item.get("tab_id"), item.get("tab_id") or ""),
            recap=str(tokens.get("recap") or ""),
            title=item.get("terminal_title_stripped") or item.get("terminal_title") or "",
            cwd=item.get("cwd") or "",
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


def filter_agents(agents: Sequence[Agent], status: Optional[str], query: str) -> List[Agent]:
    words = query.lower().split()
    return [
        agent for agent in agents
        if (status is None or agent.status == status)
        and all(word in agent.haystack() for word in words)
    ]


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
    def __init__(self, collector: Collector):
        self.collector = collector
        self.sort = "status"
        self.status: Optional[str] = None
        self.query = ""
        self.searching = False
        self.selected: Optional[Tuple[int, str]] = None
        self.scroll = 0
        self.message = ""
        self.rows: List[Agent] = []
        self.multi = len(collector.endpoints) > 1

    def compute_rows(self) -> None:
        _, agents, _, _ = self.collector.snapshot()
        self.rows = filter_agents(sort_agents(agents, self.sort), self.status, self.query)
        keys = [agent.key for agent in self.rows]
        if self.selected not in keys:
            self.selected = keys[0] if keys else None

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
        self.status = status
        self.query = ""
        self.selected = None

    def handle(self, key) -> Optional[str]:
        """Apply one key. Returns ``"close"`` or ``"focus"`` to end the picker."""
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
            self.status = None
        elif key == "s":
            self.sort = SORT_MODES[(SORT_MODES.index(self.sort) + 1) % len(SORT_MODES)]
        elif key == "/":
            self.searching = True
            self.status = None
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


def draw(window, picker: Picker, colors: Dict[str, int]) -> None:
    window.erase()
    height, width = window.getmaxyx()
    _, agents, errors, loaded = picker.collector.snapshot()
    filter_label = picker.status or "all"
    header = " Agents  sort: %s  filter: %s  %d/%d" % (
        SORT_LABELS[picker.sort], filter_label, len(picker.rows), len(agents))
    put(window, 0, 0, fit(header, width), colors["accent"])
    if picker.searching or picker.query:
        put(window, 1, 0, fit(" / " + picker.query + ("▏" if picker.searching else ""), width))
    else:
        put(window, 1, 0, fit(" b blocked  w working  i idle  d done  a all  s sort  / search", width), colors["dim"])

    body_top, body_bottom = 2, height - 1
    visible = max(0, body_bottom - body_top)
    position = picker.index()
    if position < picker.scroll:
        picker.scroll = position
    elif position >= picker.scroll + visible:
        picker.scroll = position - visible + 1
    picker.scroll = max(0, min(picker.scroll, max(0, len(picker.rows) - visible)))

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
        else:
            empty = " no matching agents"
        put(window, body_top, 0, fit(empty, width), colors["dim"])

    footer = picker.message or footer_text(picker, errors, loaded)
    attr = colors["error"] if picker.message or errors else colors["dim"]
    put(window, height - 1, 0, fit(footer, width - 1), attr)
    window.refresh()


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
    if picker.searching:
        return " type to search  Esc clear  Enter focus  ↑↓ move"
    return " j/k move  Enter focus  r refresh  Esc close"


def run(window) -> int:
    curses.curs_set(0)
    window.keypad(True)
    window.timeout(200)
    colors = init_colors()
    collector = Collector(load_endpoints())
    collector.start()
    picker = Picker(collector)
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
                    return 0
                picker.message = " %s: %s" % (agent.endpoint.label, error)
    finally:
        collector.close()


def main(argv: Sequence[str]) -> int:
    if list(argv) != ["tui"]:
        print("usage: agent_picker.py tui", file=sys.stderr)
        return 2
    os.environ.setdefault("ESCDELAY", "25")
    import locale
    locale.setlocale(locale.LC_ALL, "")
    return curses.wrapper(run)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
