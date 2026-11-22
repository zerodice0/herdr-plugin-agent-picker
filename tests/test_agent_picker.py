import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent_picker as ap  # noqa: E402

LOCAL = ap.Endpoint(0, "winmini")
REMOTE = ap.Endpoint(1, "rogallyx", "m-1")


def agent(pane, status, seq, endpoint=LOCAL, **fields):
    values = dict(name=pane, workspace="ws", tab="1", recap="", title="", cwd="", project="proj")
    values.update(fields)
    return ap.Agent(endpoint=endpoint, pane_id=pane, status=status, seq=seq, **values)


def panes(agents):
    return [(a.endpoint.label, a.pane_id) for a in agents]


class SortTest(unittest.TestCase):
    def test_attention_sort_groups_by_status_then_oldest_change_first(self):
        agents = [
            agent("idle-old", "idle", 1), agent("working", "working", 5),
            agent("done-new", "done", 9), agent("blocked-new", "blocked", 8),
            agent("done-old", "done", 3), agent("blocked-old", "blocked", 2),
            agent("odd", "unknown", 0), agent("idle-new", "idle", 7),
        ]
        self.assertEqual(
            [a.pane_id for a in ap.sort_agents(agents, "status")],
            ["blocked-old", "blocked-new", "done-old", "done-new", "working",
             "idle-old", "idle-new", "odd"],
        )

    def test_recent_sort_puts_latest_change_first(self):
        agents = [agent("a", "idle", 1), agent("b", "blocked", 9), agent("c", "working", 4)]
        self.assertEqual([a.pane_id for a in ap.sort_agents(agents, "recent")], ["b", "c", "a"])

    def test_per_server_counters_are_not_compared_across_machines(self):
        agents = [
            agent("r-blocked", "blocked", 1, REMOTE), agent("l-blocked", "blocked", 500),
            agent("r-idle", "idle", 2, REMOTE), agent("l-idle", "idle", 900),
        ]
        self.assertEqual(panes(ap.sort_agents(agents, "status")), [
            ("winmini", "l-blocked"), ("rogallyx", "r-blocked"),
            ("winmini", "l-idle"), ("rogallyx", "r-idle"),
        ])
        self.assertEqual(panes(ap.sort_agents(agents, "recent")), [
            ("winmini", "l-idle"), ("winmini", "l-blocked"),
            ("rogallyx", "r-idle"), ("rogallyx", "r-blocked"),
        ])


class FilterTest(unittest.TestCase):
    agents = [
        agent("w1:p1", "blocked", 1, name="🟩 [메인] green-owl", workspace="kidori-cafe"),
        agent("w2:p1", "idle", 2, name="red-fox", recap="질문 대기"),
        agent("w3:p1", "idle", 3, REMOTE, name="blue-koala", title="Fix CI"),
    ]

    def test_status_filter(self):
        self.assertEqual([a.pane_id for a in ap.filter_agents(self.agents, "idle", "")], ["w2:p1", "w3:p1"])
        self.assertEqual(len(ap.filter_agents(self.agents, None, "")), 3)

    def test_query_words_must_all_match_case_insensitively(self):
        self.assertEqual([a.pane_id for a in ap.filter_agents(self.agents, None, "OWL cafe")], ["w1:p1"])
        self.assertEqual(ap.filter_agents(self.agents, None, "owl ci"), [])
        self.assertEqual([a.pane_id for a in ap.filter_agents(self.agents, None, "질문")], ["w2:p1"])
        self.assertEqual([a.pane_id for a in ap.filter_agents(self.agents, None, "rogallyx fix")], ["w3:p1"])

    def test_query_and_status_combine(self):
        self.assertEqual(ap.filter_agents(self.agents, "blocked", "red"), [])


class ViewFilterTest(unittest.TestCase):
    agents = [
        agent("l-done", "done", 1, project="kidori-incense-alchemist", workspace="remake-trip"),
        agent("l-idle", "idle", 2, project="kidori-cafe"),
        agent("l-work", "working", 3, project="kidori-incense-alchemist"),
        agent("r-idle", "idle", 4, REMOTE, project="kidori-incense-alchemist", workspace="ui-overhaul"),
        agent("r-block", "blocked", 5, REMOTE, project="kidori-cafe"),
    ]

    def ids(self, *args, **kwargs):
        return [a.pane_id for a in ap.filter_agents(self.agents, *args, **kwargs)]

    def test_ready_matches_done_and_idle(self):
        self.assertEqual(self.ids("ready", ""), ["l-done", "l-idle", "r-idle"])

    def test_machine_filter_keeps_one_machine(self):
        self.assertEqual(self.ids(None, "", machine="m-1"), ["r-idle", "r-block"])
        self.assertEqual(self.ids(None, "", machine="local"), ["l-done", "l-idle", "l-work"])

    def test_project_and_status_combine_across_machines(self):
        self.assertEqual(self.ids("ready", "", project="kidori-incense-alchemist"), ["l-done", "r-idle"])

    def test_search_matches_project_when_workspace_name_differs(self):
        self.assertEqual(self.ids(None, "alchemist"), ["l-done", "l-work", "r-idle"])


class SnapshotTest(unittest.TestCase):
    def test_rows_use_workspace_and_tab_labels_and_herdr_display_name(self):
        snapshot = {
            "workspaces": [{"workspace_id": "w1", "label": "kidori-cafe"}],
            "tabs": [{"tab_id": "w1:t2", "label": "review"}],
            "agents": [
                {"pane_id": "w1:p3", "workspace_id": "w1", "tab_id": "w1:t2", "agent_status": "done",
                 "state_change_seq": 42, "display_agent": "🟦 [워커] blue-yak", "name": "blue-yak",
                 "tokens": {"recap": "구현 중"}, "terminal_title_stripped": "π > Fix", "cwd": "/w"},
                {"pane_id": "w9:p1", "agent": "codex"},
            ],
        }
        first, second = ap.agents_from_snapshot(snapshot, REMOTE)
        self.assertEqual(
            (first.name, first.workspace, first.tab, first.status, first.seq, first.recap, first.title),
            ("🟦 [워커] blue-yak", "kidori-cafe", "review", "done", 42, "구현 중", "π > Fix"),
        )
        self.assertEqual((second.name, second.workspace, second.status, second.seq), ("codex", "w9", "unknown", 0))
        self.assertIs(first.endpoint, REMOTE)

    def test_project_is_repository_name_or_workspace_label(self):
        snapshot = {
            "workspaces": [
                {"workspace_id": "w2", "label": "remake-trip",
                 "worktree": {"repo_name": "kidori-incense-alchemist", "is_linked_worktree": True}},
                {"workspace_id": "w1", "label": "~", "worktree": None},
            ],
            "agents": [{"pane_id": "w2:p1"}, {"pane_id": "w1:p1"}, {"pane_id": "w7:p1"}],
        }
        linked, home, unknown = ap.agents_from_snapshot(snapshot, LOCAL)
        self.assertEqual((linked.workspace, linked.project), ("remake-trip", "kidori-incense-alchemist"))
        self.assertEqual(home.project, "~")
        self.assertEqual(unknown.project, "w7")


class ProjectRowsTest(unittest.TestCase):
    def test_most_used_first_after_all_projects_and_saved_project_kept(self):
        agents = [
            agent("a", "blocked", 1, project="cafe"),
            agent("b", "done", 2, REMOTE, project="alchemist"),
            agent("c", "idle", 3, project="alchemist"),
            agent("d", "idle", 4, project="zoo"),
        ]
        usage = {"alchemist": (5, 100.0), "zoo": (5, 200.0), "cafe": (1, 300.0)}
        rows = ap.project_rows(agents, usage, keep="gone")
        self.assertEqual([r.name for r in rows], [None, "zoo", "alchemist", "cafe", "gone"])
        self.assertEqual(rows[0].agents, 4)
        self.assertEqual(rows[2].summary(), "2 agents · 1 done · winmini, rogallyx")
        self.assertEqual(rows[4].summary(), "0 agents")


class StateTest(unittest.TestCase):
    def test_view_round_trips(self):
        with tempfile.TemporaryDirectory() as state:
            view = ap.View(sort="recent", status="ready", machine="m-1", project="kidori-cafe")
            ap.save_view(state, view)
            self.assertEqual(ap.load_view(state), view)

    def test_missing_or_broken_state_gives_default_view(self):
        self.assertEqual(ap.load_view(None), ap.View())
        with tempfile.TemporaryDirectory() as state:
            self.assertEqual(ap.load_view(state), ap.View())
            Path(state, "view.json").write_text("{not json", encoding="utf-8")
            self.assertEqual(ap.load_view(state), ap.View())

    def test_invalid_field_falls_back_alone(self):
        with tempfile.TemporaryDirectory() as state:
            Path(state, "view.json").write_text(
                json.dumps({"sort": "bogus", "status": "working", "machine": "", "project": "cafe"}),
                encoding="utf-8")
            self.assertEqual(ap.load_view(state), ap.View(status="working", project="cafe"))

    def test_usage_counts_focuses(self):
        with tempfile.TemporaryDirectory() as state:
            ap.record_usage(state, "cafe")
            ap.record_usage(state, "cafe")
            ap.record_usage(state, "alchemist")
            usage = ap.load_usage(state)
            self.assertEqual((usage["cafe"][0], usage["alchemist"][0]), (2, 1))
            self.assertEqual(os.listdir(state), ["usage.json"])


class PickerKeysTest(unittest.TestCase):
    class FakeCollector:
        def __init__(self, agents, endpoints=(LOCAL,)):
            self.agents = agents
            self.endpoints = list(endpoints)
            self.refreshed = False

        def snapshot(self):
            return 0, list(self.agents), {}, {e.index for e in self.endpoints}

        def refresh(self):
            self.refreshed = True

    def picker(self, view=None):
        picker = ap.Picker(self.FakeCollector([
            agent("idle", "idle", 1, name="idle-agent"), agent("blocked", "blocked", 2, name="blocked-agent"),
            agent("done", "done", 3, name="done-agent"),
        ]), view or ap.View())
        picker.compute_rows()
        return picker

    def two_machine_picker(self, view=None):
        picker = ap.Picker(self.FakeCollector([
            agent("l-cafe", "idle", 1, project="cafe"), agent("l-alc", "working", 2, project="alchemist"),
            agent("r-alc", "done", 3, REMOTE, project="alchemist"),
        ], (LOCAL, REMOTE)), view or ap.View(), usage={"alchemist": (3, 1.0)})
        picker.compute_rows()
        return picker

    def ids(self, picker):
        return [a.pane_id for a in picker.rows]

    def press(self, picker, *keys):
        result = None
        for key in keys:
            result = picker.handle(key)
            picker.compute_rows()
        return result

    def test_goto_style_status_keys_and_sort_toggle(self):
        picker = self.picker()
        self.assertEqual([a.pane_id for a in picker.rows], ["blocked", "done", "idle"])
        self.press(picker, "i")
        self.assertEqual([a.pane_id for a in picker.rows], ["idle"])
        self.press(picker, "a", "s")
        self.assertEqual([a.pane_id for a in picker.rows], ["done", "blocked", "idle"])

    def test_hangul_jamo_act_as_navigation_keys(self):
        picker = self.picker()
        self.press(picker, "ㅓ")
        self.assertEqual(picker.selected, (0, "done"))
        self.press(picker, "ㅠ")
        self.assertEqual([a.pane_id for a in picker.rows], ["blocked"])

    def test_search_mode_types_letters_and_escape_clears_then_closes(self):
        picker = self.picker()
        self.press(picker, "/", "d", "o", "n")
        self.assertEqual(picker.query, "don")
        self.assertEqual([a.pane_id for a in picker.rows], ["done"])
        self.assertEqual(self.press(picker, "\n"), "focus")
        self.assertIsNone(self.press(picker, "\x1b"))
        self.assertEqual((picker.query, len(picker.rows)), ("", 3))
        self.assertEqual(self.press(picker, "\x1b"), "close")

    def test_ready_key_and_jamo(self):
        picker = self.picker()
        self.press(picker, "e")
        self.assertEqual(self.ids(picker), ["done", "idle"])
        self.press(picker, "a", "ㄷ")
        self.assertEqual(picker.view.status, "ready")

    def test_machine_key_cycles_all_local_remote(self):
        picker = self.two_machine_picker()
        seen = []
        for _ in range(3):
            self.press(picker, "m")
            seen.append((picker.view.machine, self.ids(picker)))
        self.assertEqual(seen, [
            ("local", ["l-alc", "l-cafe"]), ("m-1", ["r-alc"]), (None, ["r-alc", "l-alc", "l-cafe"]),
        ])

    def test_clear_key_drops_every_filter_and_search(self):
        picker = self.two_machine_picker(ap.View(status="done", machine="m-1", project="alchemist"))
        self.press(picker, "/", "z")
        self.assertEqual(self.ids(picker), [])
        self.press(picker, "\x1b", "x")
        self.assertEqual((picker.view, picker.query, picker.searching), (ap.View(), "", False))
        self.assertEqual(len(picker.rows), 3)

    def test_project_list_selects_project_across_machines(self):
        picker = self.two_machine_picker()
        self.press(picker, "p")
        self.assertEqual([r.name for r in picker.projects], [None, "alchemist", "cafe"])
        self.press(picker, "j", "\n")
        self.assertEqual((picker.mode, picker.view.project), ("agents", "alchemist"))
        self.assertEqual(self.ids(picker), ["r-alc", "l-alc"])

    def test_project_list_escape_keeps_filter_and_cursor_starts_on_current(self):
        picker = self.two_machine_picker(ap.View(project="cafe"))
        self.press(picker, "ㅔ")
        self.assertEqual(picker.projects[picker.project_cursor].name, "cafe")
        self.press(picker, "k", "\x1b")
        self.assertEqual((picker.mode, picker.view.project), ("agents", "cafe"))

    def test_unavailable_saved_machine_is_dropped_with_message(self):
        picker = self.picker(ap.View(machine="m-9"))
        self.assertIsNone(picker.view.machine)
        self.assertIn("m-9", picker.message)


class FitTest(unittest.TestCase):
    def test_wide_characters_are_truncated_by_terminal_cells(self):
        self.assertEqual(ap.fit("🟩 [메인] green-owl", 10), "🟩 [메인]…")
        self.assertEqual(ap.text_width(ap.fit("🟩 [메인] green-owl", 10)), 10)
        self.assertEqual(ap.fit("ab", 4), "ab  ")


if __name__ == "__main__":
    unittest.main()
