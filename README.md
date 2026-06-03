# Moseng Backpack

**EN** | [한국어](#한국어)

A desktop **asset library for 3D artists**. Think of it as a "Lightroom for your
textures, materials, and 3D files" — one window where you browse everything you
own, tag and rate it, preview it, and send it straight into Houdini.

Built with **Python + PySide6**. Optionally talks to
**[Backpack for Houdini](https://github.com/ansanclay/Backpack-for-Houdini)** to
drop materials straight into a Redshift network.

![Moseng Backpack UI](backpack/ui/resources/logo.png)

---

## Table of contents

1. [What is this, in plain terms?](#1-what-is-this-in-plain-terms)
2. [Installation](#2-installation)
3. [First launch — the one-time setup](#3-first-launch--the-one-time-setup)
4. [Where your files live (the BACKPACK folder)](#4-where-your-files-live-the-backpack-folder)
5. [The window, panel by panel](#5-the-window-panel-by-panel)
6. [Everyday tasks](#6-everyday-tasks)
7. [Power features](#7-power-features)
8. [Keyboard shortcuts](#8-keyboard-shortcuts)
9. [Houdini integration](#9-houdini-integration)
10. [Where settings & data are stored](#10-where-settings--data-are-stored)
11. [Troubleshooting / FAQ](#11-troubleshooting--faq)
12. [For developers](#12-for-developers)
13. [License](#13-license)

---

## 1. What is this, in plain terms?

If you do 3D work, your textures and models pile up across dozens of folders and
drives. Finding "that one mossy rock material" later is painful.

**Moseng Backpack points at one folder on a drive and turns it into a searchable,
visual library.** You get:

- **Thumbnails** for every texture, material, and model — no more guessing from
  filenames.
- **Tags, star ratings, and notes** you attach to any asset, so you can filter
  ("show me 4K outdoor stone") instead of digging through folders.
- **A flexible window** of dockable panels you arrange however you like.
- **One-click send to Houdini** (if you use it) to build a Redshift material from
  a texture set.

It's a **desktop app** (a normal program window) — not a website or a plugin.
You run it, point it at your library folder once, and browse.

---

## 2. Installation

### Prerequisites

- **Python 3.10 or newer.** Check with:
  ```bash
  python --version
  ```
  If you don't have it, get it from [python.org](https://www.python.org/downloads/).
  On Windows, tick **"Add Python to PATH"** during install.
- **Git** (to download the code), or just download the ZIP from GitHub.

### Steps

```bash
# 1. Download the code
git clone https://github.com/ansanclay/Moseng-Backpack.git
cd Moseng-Backpack

# 2. (Recommended) create an isolated environment so it doesn't touch your system Python
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

# 3. Install the libraries it needs
pip install -r requirements.txt

# 4. Run it
python main.py
```

> **Windows shortcut:** there's a `Backpack.bat` you can double-click. Note it
> contains a hard-coded Python path from the author's machine — open it in a text
> editor and point it at *your* `pythonw.exe` if it doesn't start. When in doubt,
> just use `python main.py`.

> ⚠️ The old command `python -m backpack` does **not** work — use `python main.py`.

---

## 3. First launch — the one-time setup

The very first time you run it, the app doesn't know *where* your library should
live, so it asks:

1. **A "Select a drive" dialog appears.** Pick the drive where you want your asset
   library to live (e.g. `D:`). It can be an internal disk, an external drive, or
   a network drive.
2. The app then creates (or reuses) a folder called **`BACKPACK`** on that drive
   and works inside it. That's it — setup is done.

You can change the drive later in **Settings**.

At this point the library is **empty**. Nothing will show in the browser until
you put some files into the `BACKPACK/ASSETS` folder (next section), then press
the **Refresh** button.

---

## 4. Where your files live (the BACKPACK folder)

On the drive you picked, the app uses this layout:

```
<DRIVE>:/BACKPACK/
├── ASSETS/                 ← put ALL your content in here
│   ├── Materials/          ← PBR material folders (albedo + normal + roughness …)
│   ├── Images/
│   │   ├── Textures/       ← loose texture images
│   │   ├── Photos/         ← reference photos
│   │   ├── Gobos/          ← light gobos (.ies, etc.)
│   │   └── HDRI/           ← environment maps (.hdr / .exr)
│   ├── Models/
│   │   ├── 3D_Assets/      ← .fbx / .obj / .usd / … (each in its own folder)
│   │   └── Foliages/
│   └── Quixel/             ← (optional) Quixel / Megascans downloads
│
├── JSON/                   ← tags, ratings, notes  (auto-managed — don't edit)
└── PREVIEWS/               ← cached thumbnails      (auto-managed — don't edit)
```

**You only ever touch `ASSETS/`.** Drop your textures and models into the matching
subfolders. The `JSON/` and `PREVIEWS/` folders are created and maintained
automatically — they mirror your `ASSETS/` tree to store metadata and thumbnails.

A **"material"** is simply a folder that contains the image maps of one surface
(e.g. `Rock_Mossy/` with `..._albedo.png`, `..._normal.png`, `..._roughness.png`).
The app detects the map types from the filenames automatically.

> Don't have this structure yet? Just create the folders you need under `ASSETS/`
> and copy files in — or use the **Import** dialog (drag-and-drop) inside the app.

---

## 5. The window, panel by panel

The app is a set of **dockable panels** — like Houdini or Maya. You can drag any
panel by its tab to move, split, tab, or float it, and the layout is remembered
between sessions. The default arrangement:

| Panel | What it's for |
|---|---|
| **Folders** | A tree of your `ASSETS` library. Click a folder to show its contents in the Explorer. |
| **Project** | Point at a *separate* working-project folder and browse it the same way (great for per-shot files). |
| **Filters** | Tags and resolution checkboxes. Tick them to narrow what the Explorer shows. Has its own tag search + sort. |
| **Explorer** | The main grid of thumbnails. Grid / list / compact views. Search box, sort, "Latest" toggle. |
| **Inspector** | Details for the selected item: large preview (zoom/pan), star rating, notes, tags, map list, and the **Send to Houdini** buttons. |

Two extra panels you can add from the **`＋`** button on any panel's tab bar (or
the **Window** menu):

| Panel | What it's for |
|---|---|
| **Synapse** | A "mind-map" graph of the current folder. Each item is a dot, connected to its **tags** and its **folder**, color-coded by file type. Great for *seeing* how a folder is organized at a glance. |
| **Collection** | A scratch "tray". Select assets anywhere and click **Add Selection** to gather them in one place — handy when assembling a shot. |

Every panel type can be opened **more than once** (e.g. two Explorers side by
side), and each instance works independently.

---

## 6. Everyday tasks

- **Browse:** click a folder in **Folders** → its thumbnails fill the **Explorer**.
- **Inspect:** click a thumbnail → the **Inspector** shows a big preview + details.
  Double-click an item to open the file in its default app.
- **Search:** type in the Explorer's search box (or press **Ctrl + F**).
- **Filter:** tick tags / resolutions in the **Filters** panel. The Explorer
  updates live. Use the Filters search box to find a tag fast, and **Clear** to
  reset.
- **Tag & rate:** select an item, then add tags / set a star rating / write notes
  in the **Inspector**. Tags are color-coded and shared across the whole library.
- **Add new assets:**
  1. Copy files into the right `ASSETS/` subfolder, **or** drag-and-drop them onto
     the window to use the **Import** dialog, then
  2. press **Refresh** (it generates thumbnails and updates metadata).
- **"Latest" toggle:** collapses versioned/backup scene files (`v001…v003`,
  Houdini `_bak1…`) down to just the newest one, so the grid isn't cluttered.

---

## 7. Power features

- **Panel Editor (press `Tab`):** an overlay where each panel is a *node*. Drag a
  wire from one panel's output dot to another's input to control the data flow —
  e.g. wire a *Folders* panel into a specific *Explorer*, or an *Explorer* into a
  *Synapse* / *Collection*. Left-drag a port to connect; right-drag across a wire
  to cut it. This is how two Explorers can show different folders at once.
- **Quick-Open (press `Ctrl + K`):** a search box that jumps you to **any folder**
  in the library by typing part of its name (like VS Code's file finder).
- **Synapse graph:** colors dots by type — Images (blue), Models (green), Scene
  files (orange), Materials (teal), HDRI (violet), **Caches** (pink: `.bgeo`,
  `.vdb`, …), **Backups** (grey: anything in a `*backup*` folder). A **"Show all
  connections"** button graphs every item in the folder, not just the busiest.
- **Downscale:** generate lower-resolution copies (2K/1K) of a material on the fly.
- **Themes:** in **Settings → Appearance**, three colors (primary / secondary /
  background) drive the entire palette, including the window's title bar.

---

## 8. Keyboard shortcuts

| Shortcut | Action |
|---|---|
| **Tab** | Open / close the **Panel Editor** |
| **Ctrl + K** | **Quick-Open** — jump to any folder by name |
| **Ctrl + F** | Focus the Explorer's search box |
| **Esc** | Close the Panel Editor / Quick-Open |
| **Double-click a panel tab** | Float that panel as its own window |
| **Drag a panel tab** | Move / split / tab panels |
| **`＋` on a panel's tab bar** | Add another panel (any type) |

---

## 9. Houdini integration

This is **optional** — the app works fully without Houdini.

1. Install **[Backpack for Houdini](https://github.com/ansanclay/Backpack-for-Houdini)**
   and press **Start** on its shelf inside Houdini.
2. In Moseng Backpack's **Inspector**, the status dot turns **green** when it
   detects Houdini listening on `localhost:29700`.
3. With a material or texture selected, use:
   - **⬡ Build RS Material** — builds a complete Redshift OpenPBR network in `/mat`.
   - **→ Add to RS Builder** — drops a single `rsTexture_*` node into the active
     Material Builder.

---

## 10. Where settings & data are stored

| What | Location |
|---|---|
| **App settings** (drive, theme, window size, saved layout) | `~/.moseng_backpack/settings.json` |
| **Your assets** | `<DRIVE>:/BACKPACK/ASSETS/` |
| **Tags / ratings / notes** | `<DRIVE>:/BACKPACK/JSON/` (auto-managed) |
| **Thumbnails** | `<DRIVE>:/BACKPACK/PREVIEWS/` (auto-managed) |

(`~` means your user home folder, e.g. `C:\Users\YourName` on Windows.)
Deleting the `PREVIEWS/` folder is safe — thumbnails are regenerated on the next
Refresh.

---

## 11. Troubleshooting / FAQ

- **The grid is empty.** Your `BACKPACK/ASSETS/` folder has no files yet — add
  some, then press **Refresh**.
- **`python -m backpack` fails.** That command isn't supported. Use `python main.py`.
- **Thumbnails are slow the first time.** They're generated on first view and
  cached afterward; later visits are instant. Press **Refresh** to pre-build them.
- **The Houdini dot stays grey.** Houdini (with Backpack for Houdini started) isn't
  running, or it's listening on a different port than `29700`.
- **`Backpack.bat` doesn't start.** It has a machine-specific Python path — edit it
  to point at your `pythonw.exe`, or just run `python main.py`.
- **Can I move my library to another drive?** Yes — move the whole `BACKPACK`
  folder, then pick the new drive in **Settings**.

---

## 12. For developers

```
main.py                     # Entry point  →  python main.py
backpack/
  app.py                    # App bootstrap: settings, stylesheet, drive selector, window
  constants.py              # Color tokens, tag palette
  core/
    scanner.py              # Filesystem scan → ScannedMaterial / ScannedAsset
    map_detector.py         # PBR map-type detection from filenames
    metadata.py             # Per-asset JSON sidecar read/write
    tag_registry.py         # Global tag list
    preview.py              # 512px thumbnail cache (Pillow / imageio)
    downscale.py            # Resolution downscaling
    folder_model.py         # BACKPACK folder tree model + project scaffolder
    settings.py             # Persisted settings (theme, layout, project template)
  ui/
    main_window.py          # Frameless shell: custom title bar + QtAds dock manager + panel factory
    library_session.py      # Controller: shared state + scan/filter orchestration + panel data-flow
    asset_browser.py        # Explorer: grid/list/compact browser + toolbar
    asset_detail.py         # Inspector: zoomable preview + metadata + Houdini buttons
    folder_tree.py          # Assets tree, Project tree, breadcrumb bar
    tag_bar.py              # Filters panel (tags + resolution, search/sort)
    synapse_view.py         # Synapse graph (force-directed; tags + folder links)
    quick_open.py           # Quick-Open palette (Ctrl+K)
    node_overlay.py         # Panel Editor overlay (Tab)
    splash.py               # Startup splash screen
    win_titlebar.py         # Windows caption tint (legacy; window is now frameless)
    houdini_bridge.py       # TCP client → Backpack for Houdini (port 29700)
    theme.py                # Color tokens → Qt stylesheet
    panels/                 # Panel wrappers (base, asset grid, collection)
    delegates/              # Thumbnail card/row painter (+ OS file-type icons)
    dialogs/                # Import, Settings, tag picker, drive selector
```

Run it from the repo root with `python main.py`. Settings live outside the repo
(`~/.moseng_backpack/`), so your dev setup won't be committed.

---

## 13. License

MIT

---
---

<a name="한국어"></a>

# 한국어

[English](#moseng-backpack) | **한국어**

3D 아티스트를 위한 데스크탑 **에셋 라이브러리**입니다. "텍스처·머티리얼·3D 파일을
위한 라이트룸"이라고 생각하면 됩니다 — 가지고 있는 모든 에셋을 한 창에서 탐색하고,
태그·별점을 매기고, 미리 보고, Houdini로 바로 보낼 수 있습니다.

**Python + PySide6**로 제작되었고, 선택적으로
**[Backpack for Houdini](https://github.com/ansanclay/Backpack-for-Houdini)**와
연동해 Redshift 네트워크로 머티리얼을 바로 보낼 수 있습니다.

![Moseng Backpack UI](backpack/ui/resources/logo.png)

---

## 목차

1. [한마디로 무엇인가요?](#1-한마디로-무엇인가요)
2. [설치](#2-설치)
3. [첫 실행 — 최초 1회 설정](#3-첫-실행--최초-1회-설정)
4. [내 파일이 저장되는 곳 (BACKPACK 폴더)](#4-내-파일이-저장되는-곳-backpack-폴더)
5. [패널별 화면 설명](#5-패널별-화면-설명)
6. [기본 사용법](#6-기본-사용법)
7. [고급 기능](#7-고급-기능)
8. [단축키](#8-단축키)
9. [Houdini 연동](#9-houdini-연동)
10. [설정·데이터 저장 위치](#10-설정데이터-저장-위치)
11. [문제 해결 / FAQ](#11-문제-해결--faq)
12. [개발자용](#12-개발자용)
13. [라이선스](#13-라이선스)

---

## 1. 한마디로 무엇인가요?

3D 작업을 하다 보면 텍스처와 모델이 여러 폴더·드라이브에 흩어집니다. 나중에 "그
이끼 낀 바위 머티리얼"을 다시 찾기가 정말 번거롭죠.

**Moseng Backpack은 드라이브의 폴더 하나를 가리켜, 검색 가능한 시각적 라이브러리로
바꿔 줍니다.** 제공하는 것:

- 모든 텍스처·머티리얼·모델의 **썸네일** — 파일명만 보고 추측할 필요가 없습니다.
- 에셋에 붙이는 **태그·별점·메모** — 폴더를 뒤지는 대신 필터링("4K 야외 스톤만
  보기")으로 찾습니다.
- 원하는 대로 배치하는 **도킹 패널 창**.
- (Houdini 사용 시) 텍스처 세트로 Redshift 머티리얼을 만드는 **원클릭 전송**.

웹사이트나 플러그인이 아니라 **데스크탑 앱**(일반 프로그램)입니다. 실행 후 라이브러리
폴더를 한 번만 지정하면 됩니다.

---

## 2. 설치

### 사전 준비

- **Python 3.10 이상.** 확인:
  ```bash
  python --version
  ```
  없으면 [python.org](https://www.python.org/downloads/)에서 설치하세요. Windows는
  설치 시 **"Add Python to PATH"**를 체크하세요.
- **Git**(코드 다운로드용) 또는 GitHub에서 ZIP 다운로드.

### 단계

```bash
# 1. 코드 다운로드
git clone https://github.com/ansanclay/Moseng-Backpack.git
cd Moseng-Backpack

# 2. (권장) 시스템 Python과 분리된 가상환경 생성
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

# 3. 필요한 라이브러리 설치
pip install -r requirements.txt

# 4. 실행
python main.py
```

> **Windows 바로가기:** 더블클릭용 `Backpack.bat`이 있습니다. 단, 제작자 PC의
> Python 경로가 하드코딩되어 있으니, 실행되지 않으면 텍스트 편집기로 열어 *내*
> `pythonw.exe` 경로로 수정하세요. 가장 확실한 방법은 `python main.py`입니다.

> ⚠️ 예전 명령 `python -m backpack`은 **동작하지 않습니다** — `python main.py`를
> 사용하세요.

---

## 3. 첫 실행 — 최초 1회 설정

처음 실행하면 라이브러리를 어디에 둘지 모르기 때문에 물어봅니다:

1. **"드라이브 선택" 창이 뜹니다.** 라이브러리를 둘 드라이브(예: `D:`)를 고르세요.
   내장 디스크·외장 드라이브·네트워크 드라이브 모두 가능합니다.
2. 선택한 드라이브에 **`BACKPACK`** 폴더를 만들고(있으면 재사용) 그 안에서
   동작합니다. 설정 끝입니다.

드라이브는 나중에 **Settings(설정)**에서 바꿀 수 있습니다.

이 시점에는 라이브러리가 **비어 있습니다.** `BACKPACK/ASSETS` 폴더(다음 항목)에
파일을 넣고 **Refresh(새로고침)**를 눌러야 브라우저에 나타납니다.

---

## 4. 내 파일이 저장되는 곳 (BACKPACK 폴더)

선택한 드라이브에서 다음 구조를 사용합니다:

```
<드라이브>:/BACKPACK/
├── ASSETS/                 ← 모든 콘텐츠를 여기에 넣습니다
│   ├── Materials/          ← PBR 머티리얼 폴더 (albedo + normal + roughness …)
│   ├── Images/
│   │   ├── Textures/       ← 낱장 텍스처 이미지
│   │   ├── Photos/         ← 레퍼런스 사진
│   │   ├── Gobos/          ← 라이트 고보 (.ies 등)
│   │   └── HDRI/           ← 환경 맵 (.hdr / .exr)
│   ├── Models/
│   │   ├── 3D_Assets/      ← .fbx / .obj / .usd … (각자 폴더 안에)
│   │   └── Foliages/
│   └── Quixel/             ← (선택) Quixel / Megascans 다운로드
│
├── JSON/                   ← 태그·별점·메모 (자동 관리 — 수정 금지)
└── PREVIEWS/               ← 썸네일 캐시      (자동 관리 — 수정 금지)
```

**직접 다루는 건 `ASSETS/`뿐입니다.** 텍스처와 모델을 알맞은 하위 폴더에 넣으세요.
`JSON/`과 `PREVIEWS/`는 `ASSETS/` 구조를 따라 메타데이터·썸네일을 저장하기 위해
자동으로 생성·관리됩니다.

**"머티리얼"**은 한 표면의 이미지 맵들이 담긴 폴더입니다(예: `Rock_Mossy/` 안에
`..._albedo.png`, `..._normal.png`, `..._roughness.png`). 파일명에서 맵 종류를
자동으로 인식합니다.

> 아직 이 구조가 없다면 `ASSETS/` 아래에 필요한 폴더를 만들고 파일을 복사하거나,
> 앱 안의 **Import(드래그 앤 드롭)** 기능을 쓰세요.

---

## 5. 패널별 화면 설명

이 앱은 Houdini·Maya처럼 **도킹 패널**로 구성됩니다. 탭을 드래그해 이동·분할·탭·독립
창으로 만들 수 있고, 배치는 세션 간 저장됩니다. 기본 배치:

| 패널 | 용도 |
|---|---|
| **Folders** | `ASSETS` 라이브러리의 폴더 트리. 폴더를 클릭하면 Explorer에 내용이 표시됩니다. |
| **Project** | *별도의* 작업 프로젝트 폴더를 지정해 같은 방식으로 탐색(샷별 파일 관리에 유용). |
| **Filters** | 태그·해상도 체크박스. 체크하면 Explorer 결과가 좁혀집니다. 태그 검색·정렬 내장. |
| **Explorer** | 썸네일 그리드(메인). 그리드/리스트/컴팩트 뷰, 검색, 정렬, "Latest" 토글. |
| **Inspector** | 선택 항목의 상세: 큰 미리보기(줌/패닝), 별점, 메모, 태그, 맵 목록, **Houdini 전송** 버튼. |

패널 탭의 **`＋`** 버튼(또는 **Window** 메뉴)으로 추가할 수 있는 패널 둘:

| 패널 | 용도 |
|---|---|
| **Synapse** | 현재 폴더의 "마인드맵" 그래프. 각 항목이 점으로, **태그**와 **폴더**에 연결되고 파일 타입별 색으로 표시됩니다. 폴더 구성을 한눈에 파악하기 좋습니다. |
| **Collection** | 임시 "트레이". 어디서든 에셋을 선택하고 **Add Selection**으로 한곳에 모읍니다 — 샷 구성에 유용. |

모든 패널은 **여러 개** 열 수 있고(예: Explorer 2개 나란히), 각 인스턴스는 독립적으로
동작합니다.

---

## 6. 기본 사용법

- **탐색:** **Folders**에서 폴더 클릭 → **Explorer**에 썸네일 표시.
- **상세 보기:** 썸네일 클릭 → **Inspector**에 큰 미리보기·상세. 더블클릭하면 기본
  앱으로 파일이 열립니다.
- **검색:** Explorer 검색창에 입력(또는 **Ctrl + F**).
- **필터:** **Filters**에서 태그·해상도 체크. Explorer가 실시간 갱신됩니다. 검색창으로
  태그를 빠르게 찾고, **Clear**로 초기화.
- **태그·별점:** 항목 선택 후 **Inspector**에서 태그 추가·별점·메모. 태그는 색상으로
  구분되며 라이브러리 전체에서 공유됩니다.
- **에셋 추가:**
  1. 알맞은 `ASSETS/` 하위 폴더로 파일을 복사하거나, 창에 **드래그 앤 드롭**해
     **Import** 다이얼로그 사용 후,
  2. **Refresh**를 누릅니다(썸네일 생성·메타데이터 갱신).
- **"Latest" 토글:** 버전/백업 씬 파일(`v001…v003`, Houdini `_bak1…`)을 가장 최신 것
  하나로 묶어 그리드를 깔끔하게 유지합니다.

---

## 7. 고급 기능

- **패널 에디터(`Tab` 키):** 각 패널이 *노드*가 되는 오버레이. 한 패널의 출력 점에서
  다른 패널의 입력 점으로 선을 연결해 데이터 흐름을 제어합니다 — 예: *Folders* →
  특정 *Explorer*, *Explorer* → *Synapse* / *Collection*. 좌클릭 드래그로 연결,
  우클릭 드래그로 선을 자릅니다. 이렇게 하면 Explorer 두 개가 서로 다른 폴더를 동시에
  보여줄 수 있습니다.
- **Quick-Open(`Ctrl + K`):** 이름 일부를 입력해 라이브러리의 **아무 폴더로나** 점프하는
  검색창(VS Code 파일 찾기 같은 기능).
- **Synapse 그래프:** 타입별 색 — 이미지(파랑), 모델(초록), 씬 파일(주황), 머티리얼
  (청록), HDRI(보라), **캐시**(분홍: `.bgeo`, `.vdb` …), **백업**(회색: `*backup*`
  폴더 내 항목). **"Show all connections"** 버튼은 폴더의 모든 항목을 그래프로 표시합니다.
- **다운스케일:** 머티리얼의 저해상도(2K/1K) 사본을 즉석 생성.
- **테마:** **Settings → Appearance**의 세 색상(기본/보조/배경)이 제목 표시줄을 포함한
  전체 팔레트를 결정합니다.

---

## 8. 단축키

| 단축키 | 동작 |
|---|---|
| **Tab** | **패널 에디터** 열기/닫기 |
| **Ctrl + K** | **Quick-Open** — 이름으로 폴더 점프 |
| **Ctrl + F** | Explorer 검색창 포커스 |
| **Esc** | 패널 에디터 / Quick-Open 닫기 |
| **패널 탭 더블클릭** | 해당 패널을 독립 창으로 띄우기 |
| **패널 탭 드래그** | 이동 / 분할 / 탭으로 합치기 |
| **탭 바의 `＋`** | 패널 추가(모든 종류) |

---

## 9. Houdini 연동

**선택 사항**입니다 — Houdini 없이도 모든 기능이 동작합니다.

1. **[Backpack for Houdini](https://github.com/ansanclay/Backpack-for-Houdini)**를
   설치하고 Houdini 셸프에서 **Start**를 누르세요.
2. **Inspector**의 상태 점이 `localhost:29700`에서 Houdini를 감지하면 **초록색**으로
   바뀝니다.
3. 머티리얼/텍스처를 선택한 상태에서:
   - **⬡ Build RS Material** — `/mat`에 Redshift OpenPBR 네트워크 전체 빌드.
   - **→ Add to RS Builder** — 활성 Material Builder에 `rsTexture_*` 노드 하나 추가.

---

## 10. 설정·데이터 저장 위치

| 항목 | 위치 |
|---|---|
| **앱 설정**(드라이브, 테마, 창 크기, 레이아웃) | `~/.moseng_backpack/settings.json` |
| **에셋** | `<드라이브>:/BACKPACK/ASSETS/` |
| **태그·별점·메모** | `<드라이브>:/BACKPACK/JSON/` (자동 관리) |
| **썸네일** | `<드라이브>:/BACKPACK/PREVIEWS/` (자동 관리) |

(`~`는 사용자 홈 폴더입니다. 예: Windows의 `C:\Users\사용자이름`.) `PREVIEWS/` 폴더는
지워도 안전합니다 — 다음 Refresh 때 다시 생성됩니다.

---

## 11. 문제 해결 / FAQ

- **그리드가 비어 있어요.** `BACKPACK/ASSETS/`에 파일이 없습니다 — 파일을 넣고
  **Refresh**를 누르세요.
- **`python -m backpack`이 실패해요.** 지원하지 않는 명령입니다. `python main.py`를
  쓰세요.
- **처음엔 썸네일이 느려요.** 첫 조회 때 생성되어 캐시되며, 이후엔 즉시 표시됩니다.
  **Refresh**로 미리 생성할 수 있습니다.
- **Houdini 점이 회색에서 안 바뀌어요.** Houdini(Backpack for Houdini Start 상태)가
  실행 중이 아니거나, `29700`이 아닌 다른 포트를 쓰고 있습니다.
- **`Backpack.bat`이 안 켜져요.** PC별 Python 경로가 박혀 있으니 내 `pythonw.exe`로
  수정하거나 `python main.py`를 쓰세요.
- **라이브러리를 다른 드라이브로 옮길 수 있나요?** 네 — `BACKPACK` 폴더 전체를 옮긴 뒤
  **Settings**에서 새 드라이브를 선택하세요.

---

## 12. 개발자용

```
main.py                     # 진입점  →  python main.py
backpack/
  app.py                    # 부트스트랩: 설정, 스타일시트, 드라이브 선택, 윈도우
  constants.py              # 색상 토큰, 태그 팔레트
  core/
    scanner.py              # 파일시스템 스캔 → ScannedMaterial / ScannedAsset
    map_detector.py         # 파일명으로 PBR 맵 종류 감지
    metadata.py             # 에셋별 JSON 사이드카 입출력
    tag_registry.py         # 전역 태그 목록
    preview.py              # 512px 썸네일 캐시 (Pillow / imageio)
    downscale.py            # 해상도 다운스케일
    folder_model.py         # BACKPACK 폴더 트리 모델 + 프로젝트 스캐폴더
    settings.py             # 영구 설정 (테마, 레이아웃, 프로젝트 템플릿)
  ui/
    main_window.py          # 프레임리스 셸: 커스텀 타이틀바 + QtAds 도크 + 패널 팩토리
    library_session.py      # 컨트롤러: 공유 상태 + 스캔/필터 + 패널 데이터 흐름
    asset_browser.py        # Explorer: 그리드/리스트/컴팩트 + 툴바
    asset_detail.py         # Inspector: 줌 미리보기 + 메타데이터 + Houdini 버튼
    folder_tree.py          # Assets/Project 트리, 경로 표시줄
    tag_bar.py              # Filters 패널 (태그 + 해상도, 검색/정렬)
    synapse_view.py         # Synapse 그래프 (포스 레이아웃; 태그 + 폴더 연결)
    quick_open.py           # Quick-Open 팔레트 (Ctrl+K)
    node_overlay.py         # 패널 에디터 오버레이 (Tab)
    splash.py               # 시작 스플래시 화면
    win_titlebar.py         # Windows 캡션 색 (레거시; 현재는 프레임리스)
    houdini_bridge.py       # TCP 클라이언트 → Backpack for Houdini (포트 29700)
    theme.py                # 색상 토큰 → Qt 스타일시트
    panels/                 # 패널 래퍼 (base, asset grid, collection)
    delegates/              # 썸네일 카드/행 페인터 (+ OS 파일 타입 아이콘)
    dialogs/                # Import, Settings, 태그 선택, 드라이브 선택
```

저장소 루트에서 `python main.py`로 실행합니다. 설정은 저장소 밖
(`~/.moseng_backpack/`)에 있어 개발 환경이 커밋되지 않습니다.

---

## 13. 라이선스

MIT
