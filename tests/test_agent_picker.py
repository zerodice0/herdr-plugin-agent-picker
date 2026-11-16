import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent_picker as ap  # noqa: E402

LOCAL = ap.Endpoint(0, "winmini")
REMOTE = ap.Endpoint(1, "rogallyx", "m-1")


def agent(pane, status, seq, endpoint=LOCAL, **fields):
    values = dict(name=pane, workspace="ws", tab="1", recap="", title="", cwd="")
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


class PickerKeysTest(unittest.TestCase):
    class FakeCollector:
        endpoints = [LOCAL]

        def __init__(self, agents):
            self.agents = agents
            self.refreshed = False

        def snapshot(self):
            return 0, list(self.agents), {}, {0}

        def refresh(self):
            self.refreshed = True

    def picker(self):
        picker = ap.Picker(self.FakeCollector([
            agent("idle", "idle", 1, name="idle-agent"), agent("blocked", "blocked", 2, name="blocked-agent"),
            agent("done", "done", 3, name="done-agent"),
        ]))
        picker.compute_rows()
        return picker

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


class FitTest(unittest.TestCase):
    def test_wide_characters_are_truncated_by_terminal_cells(self):
        self.assertEqual(ap.fit("🟩 [메인] green-owl", 10), "🟩 [메인]…")
        self.assertEqual(ap.text_width(ap.fit("🟩 [메인] green-owl", 10)), 10)
        self.assertEqual(ap.fit("ab", 4), "ab  ")


if __name__ == "__main__":
    unittest.main()
