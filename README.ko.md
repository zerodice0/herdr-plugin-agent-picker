# Herdr Agent Picker

한국어 · [English](README.md)

`zerodice0.agent-picker`는 [Herdr](https://herdr.dev/) 팝업 플러그인입니다. 모든 에이전트 패널을 먼저 살펴봐야 할 순서로 보여 줍니다. Herdr의 Goto 피커(`prefix+g`)처럼 동작하지만 에이전트만 보여 주고 순서를 고를 수 있습니다.

Goto는 workspace → tab → pane 트리 순서로 행을 고정해 보여 줍니다. 이 피커는 주의가 필요한 순서로 정렬합니다.

1. `blocked` 에이전트(승인이나 답변을 기다림)
2. `done` 에이전트(작업을 마쳤고 아직 보지 않음)
3. `working` 에이전트
4. `idle` 에이전트, 그다음 Herdr가 분류하지 못한 에이전트

같은 묶음 안에서는 Herdr의 `state_change_seq` 기준으로 상태가 가장 먼저 바뀐 에이전트가 앞에 옵니다. `s`를 누르면 상태가 가장 최근에 바뀐 에이전트가 앞에 오는 최근 순서로 바뀝니다.

각 행에는 상태 아이콘, 에이전트 이름(HAM 역할 표시를 포함한 Herdr의 `display_agent`), workspace와 tab, HAM recap 토큰(있을 때), 터미널 제목이 나옵니다. 팝업이 열려 있는 동안 목록을 1초마다 새로 고칩니다.

이 플러그인은 Linux와 macOS의 Herdr 0.9.5 이상을 지원합니다. Python 3.9 이상이 필요하며 표준 라이브러리(`curses`)만 사용합니다.

## 설치

피커를 실행할 모든 Herdr 서버에 GitHub 릴리스를 설치합니다. 플러그인 action은 클라이언트가 선택한 장비의 서버에서 실행됩니다.

```sh
herdr plugin install zerodice0/herdr-plugin-agent-picker --ref v0.2.0
```

로컬에서 개발할 때는 이 체크아웃을 link합니다.

```sh
herdr plugin link .
```

클라이언트의 `~/.config/herdr/config.toml`에 action을 키로 연결한 뒤 설정을 다시 읽습니다(`herdr server reload-config`, 실행 중인 각 클라이언트에서도 reload config).

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

설정에서 비어 있는 키라면 무엇이든 쓸 수 있습니다. Goto 피커를 대신하려면 `[keys]`의 `goto`를 다른 키로 바꾸거나 해제하고 `prefix+g`에 action을 연결합니다. action은 명령 팔레트나 `herdr plugin action invoke zerodice0.agent-picker.open`으로도 실행할 수 있습니다.

## 키

Herdr Goto 피커의 키를 그대로 쓰고, 작업 완료·장비·프로젝트 필터를 더했습니다.

| 키 | 동작 |
| --- | --- |
| `j` / `k`, `↓` / `↑` | 선택을 옮깁니다 |
| `Ctrl+D` / `Ctrl+U`, `PgDn` / `PgUp` | 여덟 행씩 옮깁니다 |
| `g` / `G`, `Home` / `End` | 첫 행이나 마지막 행으로 갑니다 |
| `b` / `w` / `i` / `d` | blocked / working / idle / done 에이전트만 보여 줍니다 |
| `e` | 작업을 마친 에이전트(done과 idle)만 보여 줍니다 |
| `a` | 상태 필터와 검색어를 지웁니다 |
| `m` | 장비 필터를 전체 → 로컬 장비 → 저장된 각 장비 순서로 바꿉니다 |
| `p` | 프로젝트 목록을 엽니다 |
| `x` | 모든 필터(상태, 장비, 프로젝트)와 검색어를 지웁니다 |
| `Backspace` | 상태 필터를 지웁니다 |
| `s` | 주의 순서와 최근 순서를 바꿉니다 |
| `/` | 검색합니다. 공백으로 나눈 모든 단어가 이름, 장비, workspace, tab, recap, 제목, 작업 디렉터리, 프로젝트, 상태 중 하나와 일치해야 합니다 |
| `r` | 바로 새로 고칩니다 |
| `Enter` | 선택한 에이전트로 포커스를 옮기고 피커를 닫습니다 |
| `Esc` | 검색 중에는 검색어를 지우고, 그 밖에는 피커를 닫습니다(`q`도 닫습니다) |

한글 입력기가 켜져 있어도 검색 밖에서는 이 키들의 두벌식 자모(`ㅓ`, `ㅏ`, `ㅠ`, `ㅈ`, `ㅑ`, `ㅇ`, `ㄷ`, `ㅁ`, `ㅡ`, `ㅔ`, `ㅌ`, `ㄴ`, `ㄱ`, `ㅂ`, `ㅎ`)가 같게 동작합니다.

done은 작업을 마쳤지만 아직 보지 않은 상태입니다. done 에이전트에 포커스하면 Herdr가 idle로 바꿉니다. working이나 blocked가 아닌 에이전트를 모두 보려면 `e`를 씁니다.

## 기억되는 보기

피커는 닫힐 때 정렬 순서와 상태·장비·프로젝트 필터를 기억하고 다음에 그대로 엽니다. 검색어는 기억하지 않습니다. 보기는 피커를 실행한 서버의 플러그인 상태 폴더(`$HERDR_PLUGIN_STATE_DIR`, 보통 `~/.local/state/herdr/plugins/zerodice0.agent-picker/`)의 `view.json`에 저장됩니다.

필터가 하나라도 켜져 있으면 머리줄에 `filter: all` 대신 켜진 필터를 강조 색으로 보여 주고, 목록이 비면 `x clears filters`라고 안내합니다. 기억된 장비가 더는 `herdr machine list`에 없으면 그 필터를 해제하고 바닥줄에 알립니다.

## 프로젝트

모든 에이전트는 프로젝트에 속합니다. 프로젝트 이름은 workspace의 git 저장소 이름(Herdr worktree 정보의 `repo_name`)이고, git 체크아웃이 아닌 workspace는 workspace 이름입니다. linked worktree와 다른 장비의 같은 저장소는 한 프로젝트로 묶이므로 workspace 이름이 달라도 한 프로젝트로 모아 볼 수 있습니다.

`p`는 현재 필터와 상관없이 모든 장비의 프로젝트를 에이전트 수, blocked 수, done 수, 실행 중인 장비와 함께 보여 줍니다. `Enter`는 강조된 프로젝트로 피커를 필터하고(`All projects`는 프로젝트 필터를 해제합니다), `Esc`·`q`·`p`는 바꾸지 않고 돌아갑니다. 피커로 에이전트에 포커스한 횟수가 많은 프로젝트가 먼저 오고, 그다음 최근에 쓴 순서, 이름 순서입니다. 횟수는 `view.json` 옆의 `usage.json`에 저장됩니다.

## 현재 프로젝트 action

`open-project` action은 팝업 아래 workspace의 프로젝트로 필터한 피커를 모든 장비에 걸쳐 엽니다. 기억된 정렬과 상태 필터에서 시작하되 기억된 장비·프로젝트는 쓰지 않고, 이 보기를 저장하지도 않습니다. 따라서 일반 `open` action의 기억된 보기는 바뀌지 않습니다. Herdr가 포커스된 workspace를 알려 주지 못하면 프로젝트 필터 없이 열고 바닥줄에 알립니다.

## 저장된 장비

`herdr machine list`에 켜진 장비가 있으면 그 장비의 에이전트도 함께 보여 주고 위치 앞에 장비 라벨을 붙입니다. 로컬 장비는 피커를 실행한 서버이고, 다른 장비는 그 서버의 `herdr machine list`에 있는 장비입니다. 따라서 서버마다 목록이 다를 수 있습니다. 각 장비는 백그라운드에서 `herdr --machine <id> api snapshot`으로 조회합니다. 닿지 않는 장비는 바닥줄에 오류를 보여 주고 15초마다 다시 시도하며, 나머지 목록을 막지 않습니다.

`state_change_seq`는 서버마다 따로 세는 값이므로 다른 장비의 에이전트끼리는 이 값으로 비교하지 않습니다. 같은 상태 묶음 안에서는 로컬 에이전트가 먼저 오고, 그다음 저장된 장비가 `herdr machine list` 순서로 옵니다.

원격 에이전트에서 Enter를 누르면 `herdr --machine <id> agent focus`를 실행해 그 서버의 포커스를 해당 패널로 옮깁니다. Herdr 0.9.5에는 클라이언트가 선택한 장비를 바꾸는 API가 없으므로, 피커는 장비 이름을 담은 토스트를 띄웁니다. 사이드바에서 그 장비를 선택하면 포커스된 에이전트가 보입니다.

## 개발

```sh
python3 -m unittest discover -s tests -v
```
