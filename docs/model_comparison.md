# Running side-by-side model comparisons

This guide explains how to scan the same repository with two or more AI models
and compare their results — using a single database for both SQLite and PostgreSQL.

## How it works

The `scans` table has a `UNIQUE(repo_id, commit_sha, ai_model)` constraint.
This means:

- Two different models can each have their own scan row for the same commit.
- Results are isolated by `ai_model` without needing separate database files.
- All standard reporting and filtering tools work on a single database.

## Step-by-step procedure

### 1. Scan with the first model (normal scan)

```bash
TOKENLEAK_AI_MODEL=openai/gpt-oss-120b \
  tokenleak scan https://github.com/org/target-repo
```

This runs a full scan of HEAD plus diff scans of all historical commits.

### 2. Scan with the second model using `rescan`

Use `rescan` so the second model gets its own scan rows for every commit,
even the ones that Model A already completed:

```bash
TOKENLEAK_AI_MODEL=deepseek-ai/DeepSeek-V4-Pro \
  tokenleak rescan https://github.com/org/target-repo
```

The `done_shas` skip-list is always filtered by `ai_model`, so Model B will
never skip a commit just because Model A already scanned it.

### 3. Comparing results

Generate a report for each model:

```bash
TOKENLEAK_AI_MODEL=openai/gpt-oss-120b      tokenleak report --output report_gpt.md
TOKENLEAK_AI_MODEL=deepseek-ai/DeepSeek-V4-Pro tokenleak report --output report_deepseek.md
```

Or query the database directly:

```sql
-- Alerts per model for a specific repo
SELECT ai_model, severity, COUNT(*) AS alerts
FROM alerts
WHERE repo_id = (SELECT id FROM repos WHERE url = 'https://github.com/org/target-repo')
GROUP BY ai_model, severity
ORDER BY ai_model, severity;

-- Side-by-side: which commits have alerts from both models?
SELECT a.commit_sha, a.ai_model, a.severity, a.alert_type, a.file_path
FROM alerts a
WHERE a.repo_id = (SELECT id FROM repos WHERE url = 'https://github.com/org/target-repo')
ORDER BY a.commit_sha, a.ai_model;

-- Commits flagged by Model A but missed by Model B
SELECT DISTINCT s1.commit_sha
FROM scans s1
JOIN repos r ON r.id = s1.repo_id
WHERE r.url = 'https://github.com/org/target-repo'
  AND s1.ai_model = 'openai/gpt-oss-120b'
  AND s1.alert_count > 0
  AND NOT EXISTS (
      SELECT 1 FROM scans s2
      WHERE s2.repo_id = s1.repo_id
        AND s2.commit_sha = s1.commit_sha
        AND s2.ai_model = 'deepseek-ai/DeepSeek-V4-Pro'
        AND s2.alert_count > 0
  );
```

### 4. Scanning additional repos

Any repos scanned before adding a second model are present in both models'
result sets (they have no scans for Model B yet). Run `rescan` for each of
them when you add a new model.

## Why `rescan` for the second model?

A normal `scan` skips commits that already have a `DONE` scan **for the same
model**. Since the second model has no scans yet, a normal `scan` would work
for the first repository. However, `rescan` is safer and more explicit: it
unconditionally creates new scan rows regardless of existing history, making
the intent clear in logs and CI pipelines.

## What appears in the log

Both model runs produce their own scan IDs that auto-increment from the current
state of the database.  Because both runs share the same database, the IDs are
not the same:

```
# Model A
2026-05-29T15:33:51 ... [INFO] [scan 137] Full scan — Pass 1 (map)
...
# Model B (scans start from the next available ID)
2026-05-29T15:44:58 ... [INFO] [scan 305] Full scan — Pass 1 (map)
```

Each alert row records `ai_model`, so reports and queries can always identify
which model produced which finding.
