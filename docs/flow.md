# TokenLeak — Application Flow

Four diagrams cover the complete execution path: command dispatch, per-repository
orchestration, full scan agent interaction, and diff scan pre-filter decision.

---

## 1. Command dispatch

```mermaid
flowchart TD
    CLI(["python -m tokenleak &lt;command&gt;"])
    CLI --> CMD{{"Command?"}}

    CMD -->|status| ST1["Query DB"]
    ST1 --> ST2["Print table:\nrepos · scans · alerts\ntokens · last scan time"]

    CMD -->|mcp| MCP1["Start FastMCP\nstdio server"]
    MCP1 --> MCP2["MCP tools available\nto external clients\n(Claude Desktop, etc.)"]

    CMD -->|"scan / rescan"| LK1["Acquire PID lock\ntokenleak.pid"]
    LK1 --> LK2{{"Lock free?"}}
    LK2 -->|no| LK3["Exit — another\ninstance is running"]
    LK2 -->|yes| TGT

    TGT{{"Targets on\nCLI?"}}
    TGT -->|yes| TGT1["Use provided\nURLs / specifiers"]
    TGT -->|no| TGT2["Read repos.txt\n(or config repo)"]

    TGT1 --> PRV["Resolve providers"]
    TGT2 --> PRV

    PRV --> PRV1["github:org → GitHub API\ngitlab:user → GitLab API\ngitea:user  → Gitea API\nhttps://… → pass-through"]
    PRV1 --> LOOP(["For each git URL\n→ scan_repo"])
    LOOP --> DONE["Release PID lock"]
```

---

## 2. Per-repository orchestration

```mermaid
flowchart TD
    REPO(["scan_repo(url)"])

    REPO --> CLN["git clone\n· GIT_TERMINAL_PROMPT=0\n· hooks wiped immediately\n· exec bits removed"]
    CLN --> SZ{{"Repo size\n> MAX_REPO_SIZE_MB?"}}
    SZ -->|yes| BIG["Skip repo\nlog warning\nMattermost notify"]
    SZ -->|no| STR

    STR{{"rescan\nor first run?"}}

    STR -->|"rescan\nor first scan"| P1A

    subgraph FIRSTRUN ["First run / rescan"]
        P1A["Phase 1a — HEAD full scan\nPass 1 + Pass 2 + OCR"]
        P1A --> BR{{"SCAN_ALL_BRANCHES\n= true?"}}
        BR -->|yes| P1B["Phase 1b — branch tips\nFor each unique tip SHA\nnot yet scanned:\ngit checkout --detach SHA\nFull scan (Pass 1+2+OCR)\ngit checkout -"]
        BR -->|no| P1C
        P1B --> P1C["Phase 1c — history diff scan\nAll commits, skip done_shas"]
    end

    STR -->|"scan\n(incremental)"| P2

    subgraph INCREMENTAL ["Subsequent scan"]
        P2["Phase 2 — new commits only\nDiff scan commits newer\nthan last successful scan"]
    end

    P1C --> POST
    P2 --> POST

    POST["Post-scan:\nsend_scan_summary → Mattermost\nGenerate report (if --report)"]
    POST --> CLR["Delete clone from disk\n(try/finally — always runs)"]
    BIG --> CLR
    CLR --> LOOP(["Next URL"])
```

---

## 3. Full scan — agent and MCP interaction

One full scan consists of two agent passes followed by an optional OCR pass.
MCP tools are called directly in-process (no stdio transport overhead).

```mermaid
flowchart TD
    FS(["Full scan start\nscan_id created in DB"])
    FS --> IC["init_context:\ndb · scan_id · repo_path\nnotifications · ai_model\ncommit_sha · triggered_by"]

    IC --> P1["PASS 1 — Map"]
    P1 --> P1MSG["System prompt + agent.md\n+ file tree + commit log"]
    P1MSG --> P1LP["Agent loop\n(max_iterations)"]

    P1LP --> P1TC{{"Tool\ncalled?"}}
    P1TC -->|save_note| P1N["Write risk map\nto DB notes table"]
    P1N --> P1TC
    P1TC -->|"any other tool"| P1ERR["Ignored — prompt\nprohibits other tools\nin Pass 1"]
    P1ERR --> P1TC
    P1TC -->|"no tool calls"| P1END["Pass 1 complete"]

    P1END --> P2["PASS 2 — Deep Scan"]
    P2 --> P2MSG["System prompt + agent.md\n+ 'Begin deep scan'"]
    P2MSG --> P2LP["Agent loop\n(max_iterations)"]

    P2LP --> P2TC{{"Tool\ncalled?"}}

    P2TC -->|get_notes| TN["Read Pass 1 notes from DB"]
    P2TC -->|read_file| TRF["Read file at HEAD"]
    P2TC -->|read_file_at_commit| TRFC["Read historical file\nvia git show SHA:path"]
    P2TC -->|list_files| TLF["Glob files in repo"]
    P2TC -->|search_content| TSC["git grep across repo"]
    P2TC -->|get_commit_log| TCL["git log text"]
    P2TC -->|get_file_tree| TFT["File tree at HEAD"]
    P2TC -->|analyze_image_file| TOCR["OCR via vision model\n(TOKENLEAK_OCR_MODEL)"]
    P2TC -->|save_note| TN2["Write intermediate note\nto DB"]
    P2TC -->|save_alert| TALERT["Write alert to DB\n→ auto Mattermost notify\n(per-alert message)"]
    P2TC -->|send_mattermost| TMM["Send final summary\nto Mattermost\n(once, at end of Pass 2)"]

    TN --> P2TC
    TRF --> P2TC
    TRFC --> P2TC
    TLF --> P2TC
    TSC --> P2TC
    TCL --> P2TC
    TFT --> P2TC
    TOCR --> P2TC
    TN2 --> P2TC
    TALERT --> P2TC
    TMM --> P2TC

    P2TC -->|"ContextWindowExceeded"| CWE["Stop loop early\nalerts saved so far preserved\nlog WARNING"]
    P2TC -->|"no tool calls"| P2END["Pass 2 complete"]

    CWE --> OCRQ
    P2END --> OCRQ{{"TOKENLEAK_OCR_MODEL\nconfigured?"}}
    OCRQ -->|yes| OCRP["OCR pass:\nall images + .ipynb\nin repo at current HEAD\nSave alerts directly to DB"]
    OCRQ -->|no| FSEND
    OCRP --> FSEND(["Full scan done\nupdate scan status → DONE"])
```

---

## 4. Diff scan — pre-filter and agent

Each commit in the history is processed as a diff scan.
The pre-filter runs locally (no AI calls) and decides whether to involve the agent.

```mermaid
flowchart TD
    DS(["Diff scan start\ncommit SHA"])
    DS --> IC["init_context:\ndb · scan_id · repo_path\nnotifications · ai_model"]
    IC --> EXT["git show --unified=0\nextract added lines per file"]

    EXT --> MT{{"Any added\nlines?"}}
    MT -->|no| SKIP["Mark DONE\n0 tokens spent"]
    MT -->|yes| PFON{{"TOKENLEAK_PREFILTER_ENABLED?"}}

    PFON -->|false / --no-prefilter| SENDALL["Send all files to AI"]
    PFON -->|true| PF["Pre-filter each file\nin the diff"]

    PF --> EXC{{"Excluded filename?\n.env.example\n*.sample · *.template\netc."}}
    EXC -->|yes| DROP1["Drop — never\nsend to AI"]
    EXC -->|no| SUSP{{"Suspicious name\nor extension?\n.env · id_rsa\n*.pem · *.key · etc."}}
    SUSP -->|yes| CAND
    SUSP -->|no| PLHOLD["Check each line:\nis it a placeholder?\nCHANGE_ME · sk-...\n&lt;YOUR_KEY&gt; · ${VAR}…"]
    PLHOLD --> REGX{{"Non-placeholder\nline matches\nregex pattern?\nAWS · JWT · GitHub\npassword= · etc."}}
    REGX -->|yes| CAND
    REGX -->|no| ENT{{"Any token ≥ 20 chars\nwith entropy ≥ 4.5?"}}
    ENT -->|yes| CAND
    ENT -->|no| DROP2["Drop — no signal\nfound in this file"]

    CAND(["File is candidate"])
    DROP1 --> NEXTF(["Next file in diff"])
    DROP2 --> NEXTF
    NEXTF --> PF

    CAND --> POOL["Candidate pool\n(1..N files)"]
    POOL --> NC{{"Any candidates\nin pool?"}}
    SENDALL --> AILOOP
    NC -->|no| NODONE["Mark DONE\n0 tokens spent"]
    NC -->|yes| AILOOP

    AILOOP["Format diff text\nfor agent\n(added lines only, up to 400K chars)"]
    AILOOP --> ALLOOP["Agent loop\n(single pass, max_iterations)"]

    ALLOOP --> ATC{{"Tool called?"}}
    ATC -->|save_alert| SA["Write alert to DB\n→ auto Mattermost notify"]
    ATC -->|read_file| RF["Read full file\nfor surrounding context"]
    ATC -->|"ContextWindowExceeded"| ACWE["Stop loop early\nalerts preserved"]
    ATC -->|"no tool calls"| ADONE["Agent done\n(no send_mattermost here —\nper-commit summary not needed)"]

    SA --> ATC
    RF --> ATC

    ADONE --> DOCO{{"TOKENLEAK_OCR_MODEL\nconfigured?"}}
    ACWE --> DOCO
    DOCO -->|yes| DOCR["OCR pass:\nimages + .ipynb\nadded in this commit"]
    DOCO -->|no| DSEND
    DOCR --> DSEND(["Diff scan done\nupdate scan status → DONE"])
```

---

## Error handling across all scan modes

| Error | Behaviour |
|-------|-----------|
| `InsufficientFundsError` (API billing) | Scanning stops immediately; all in-progress scans marked ERROR; user sees clear message |
| `ContextWindowExceededError` | Current agent loop stops; alerts saved so far are kept; scan continues to next commit |
| Tool call with invalid JSON arguments | Runner repairs escape sequences; if unrecoverable, returns error to agent as tool result; agent loop continues |
| Clone failure | Scan row marked ERROR; clone dir cleaned up; next URL processed |
| Branch tip checkout failure | Branch tip scan marked ERROR; `git checkout -` attempted in finally block; diff history scan continues |
| Mattermost send failure | Logged as WARNING; never stops a scan |
```
