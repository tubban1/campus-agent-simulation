# 团队 Git 管理流程

本文档定义本项目多人协作时的 Git 使用方式。目标是让 `main` 始终可运行、可部署，同时让每个成员能并行开发、尽早发现冲突。

## 核心原则

- `main` 只保存稳定代码，不直接在 `main` 上开发。
- 所有代码、文档、配置变更都通过分支和 Pull Request 合并。
- 每个分支只处理一个任务，PR 越小越容易 review。
- 开 PR 前同步最新 `main`，避免把过期分支合进去。
- `.env`、数据库密码、API key、Supabase 密钥永远不要提交。

## 分支模型

常用分支：

| 分支 | 用途 |
| --- | --- |
| `main` | 稳定分支，只接受 PR 合并 |
| `feat/<task>` | 新功能 |
| `fix/<bug>` | bug 修复 |
| `docs/<topic>` | 文档 |
| `chore/<task>` | 依赖、脚本、部署配置等维护工作 |
| `refactor/<area>` | 不改变功能的结构调整 |

Codex 自动创建的分支可使用 `codex/<task>`。

示例：

```bash
feat/agent-memory-search
fix/supabase-pooler
docs/git-workflow
chore/render-config
refactor/simulation-routes
```

## 日常开发流程

开始一个新任务：

```bash
git switch main
git pull origin main
git switch -c feat/your-task
```

开发并提交：

```bash
git status
git add <files>
git commit -m "feat: add your task"
git push -u origin feat/your-task
```

然后在 GitHub 创建 PR，目标分支选择 `main`。

## 同步 main

开发过程中经常同步最新 `main`。推荐使用 rebase，让分支历史保持线性：

```bash
git fetch origin
git rebase origin/main
```

如果出现冲突：

```bash
# 手动编辑冲突文件
git add <resolved-files>
git rebase --continue
```

如果分支已经推到远端，rebase 后需要：

```bash
git push --force-with-lease
```

只使用 `--force-with-lease`，不要使用普通 `--force`。

## Pull Request 规则

PR 必须包含：

- 变更摘要。
- 测试或验证方式。
- 是否涉及数据库 schema、环境变量、部署配置。
- 如果有 UI 变化，附截图或说明测试页面。

推荐 PR 模板：

```markdown
## Summary
- 

## Verification
- 

## Notes
- 
```

合并前检查：

- 至少 1 位同事 review。
- 分支已同步最新 `main`。
- 本地服务可以启动。
- 相关接口通过最小验证。

本项目基础验证：

```bash
curl http://127.0.0.1:8000/api/state
```

涉及 Agent 日志时：

```bash
curl "http://127.0.0.1:8000/api/agents/1/simulation-logs?limit=1"
```

涉及模拟推进时注意：后台 runner 与 `/api/admin/world/tick` 会写入当前 `.env` 指向的数据库。

## 冲突处理

谁的分支落后，谁在自己的分支里解决冲突。不要在 `main` 上解决冲突。

常见处理流程：

```bash
git switch feat/your-task
git fetch origin
git rebase origin/main
# 解决冲突
git add <resolved-files>
git rebase --continue
git push --force-with-lease
```

如果冲突集中在同一个大文件，先和相关同事沟通再改。当前项目尤其容易冲突的文件：

- `app/main.py`
- `app/schema.py`
- `frontend/index.html`
- `docs/supabase_schema.sql`

## 数据库变更

数据库相关 PR 必须说明：

- 新增、删除或修改了哪些表/字段。
- Supabase 是否需要手动执行 SQL。
- 是否会影响已有线上数据。
- 是否需要补数据脚本。

推荐把 schema 变更和业务逻辑变更拆成两个 PR；如果不能拆，PR 描述里必须写清楚迁移顺序。

重要提醒：

- `scripts/deploy_database.py` 只创建全新的当前版本世界。
- `scripts/reset_fresh_world.py` 会删除确认的 schema；提交涉及它时必须明确说明目标环境。
- `.env` 指向线上 Supabase 时，本地接口也会写线上数据库。

## 提交信息规范

使用简单的 Conventional Commits：

```text
feat: add group goal dashboard
fix: disable prepared statements for Supabase pooler
docs: add Git workflow guide
chore: update Render config
refactor: split simulation services
```

常用类型：

| 类型 | 含义 |
| --- | --- |
| `feat` | 新功能 |
| `fix` | 修复 bug |
| `docs` | 文档 |
| `chore` | 维护任务 |
| `refactor` | 重构 |
| `test` | 测试 |
| `style` | 格式或样式 |

## GitHub 仓库建议设置

建议在 GitHub 对 `main` 开启保护：

- Require a pull request before merging。
- Require at least 1 approval。
- Require branches to be up to date before merging。
- Restrict who can push to matching branches。
- Do not allow force pushes。
- Do not allow deletions。

后续加入 CI 后，再开启 required status checks。

## 多人并行开发建议

- 每天开始前先同步 `main`。
- 大改动先开 draft PR，让同事知道你正在改哪些文件。
- 避免多人同时大改 `app/main.py`；如果必须改，先约定改动范围。
- PR 不要长期堆积，能小步合并就小步合并。
- 合并后及时删除远端分支，本地也清理旧分支。

清理本地已合并分支：

```bash
git switch main
git pull origin main
git branch --merged
git branch -d <branch-name>
```
