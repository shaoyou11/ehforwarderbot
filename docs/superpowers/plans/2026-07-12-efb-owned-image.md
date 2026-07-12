# EFB Owned Image Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 发布自有 EFB 镜像，将 comwechat 离线提醒改为首次一次、持续离线每 8 小时一次，并安全切换飞牛部署。

**Architecture:** 公开 EFB 容器仓库从公开 slave fork 的固定提交构建多架构镜像；私有配置仓库只保存脱敏模板；飞牛继续使用持久化 `/data`，默认拉取 `latest`，固定标签用于回滚。

**Tech Stack:** Python 3.11、pytest、Docker/Buildx、GitHub Actions、GHCR、Docker Compose、EFB/comwechat

## Global Constraints

- 公开镜像和源码不得包含 Token、API ID、API Hash、管理员 ID、密码或运行数据。
- NAS 默认使用 `ghcr.io/shaoyou11/efb:latest`。
- 每次发布同时保留固定版本标签用于回滚。
- `telegram-bot-api` 与 comwechat 镜像和网络结构不做功能性修改。
- 地图中间件和 `/forward` 功能必须保留。
- 修改 NAS 前创建带时间戳的本地备份。

---

### Task 1: Fork slave and implement offline notification policy

**Files:**
- Modify: `efb_wechat_comwechat_slave/ComWechat.py`
- Create: `tests/test_offline_notification_policy.py`

**Interfaces:**
- Produces: `OfflineNotificationPolicy(interval_seconds: int)` with `observe(logged_in: bool, now: float) -> bool`.
- Consumed by: `ComWeChatChannel.scheduled_job()` to decide whether to emit the existing system message.

- [ ] Write tests proving first offline observation notifies, repeated checks before 8 hours do not notify, the 8-hour boundary notifies, and a successful login resets the policy.
- [ ] Run `pytest tests/test_offline_notification_policy.py -v` and verify failure because the policy does not exist.
- [ ] Add the minimal policy and wire it into `scheduled_job()` with a 10-second login check while preserving the 1800-second contact refresh.
- [ ] Run the focused test and the upstream test suite; expect all available tests to pass.
- [ ] Verify `rg -n 'forward_pattern|/forward|ForwardMessage' efb_wechat_comwechat_slave/ComWechat.py` still finds the forwarding implementation.
- [ ] Commit with a Chinese requirement/implementation body.

### Task 2: Own the EFB image build

**Files:**
- Modify: `Dockerfile`
- Modify: `.github/workflows/ci.yaml`
- Modify: `docker-compose.yaml`
- Modify: `entrypoint.sh`

**Interfaces:**
- Consumes: fixed commit from `shaoyou11/efb-wechat-comwechat-slave`.
- Produces: `ghcr.io/shaoyou11/efb:latest` plus immutable commit/version tags.

- [ ] Replace the slave dependency URL with the public fork and its tested commit.
- [ ] Set workflow package permissions to `contents: read` and `packages: write`; derive tags from repository ownership rather than hard-coded upstream ownership.
- [ ] Change EFB services in Compose to `ghcr.io/shaoyou11/efb:latest` and remove the temporary runtime patch invocation from the repository entrypoint.
- [ ] Validate shell syntax with `sh -n entrypoint.sh`, parse Compose YAML, and inspect workflow YAML.
- [ ] Commit with a Chinese requirement/implementation body and push the public repository.

### Task 3: Create the private sanitized config repository

**Files:**
- Create: `.gitignore`
- Create: `README.md`
- Create: `docker-compose.example.yaml`
- Create: `profiles/comwechat/config.yaml`
- Create: sanitized profile configuration templates matching the NAS structure.

**Interfaces:**
- Produces: recovery templates containing placeholders only.

- [ ] Create the private repository and copy only configuration structure.
- [ ] Replace sensitive values with explicit environment-style placeholders.
- [ ] Exclude databases, logs, cache, QR images, Telegram API data and WeChat files.
- [ ] Scan tracked content for known live secret values without printing matches; require zero matches.
- [ ] Commit and push with a Chinese requirement/implementation body.

### Task 4: Publish and verify GHCR

**Files:**
- No new source files.

**Interfaces:**
- Consumes: public EFB repository main branch.
- Produces: multi-architecture `latest` and immutable version images.

- [ ] Trigger or observe GitHub Actions build.
- [ ] Require the workflow to finish successfully.
- [ ] Inspect the GHCR manifest and verify `linux/amd64` and `linux/arm64` are present.
- [ ] Record the fixed image tag and digest for rollback.

### Task 5: Back up and switch the Feiniu NAS

**Files:**
- Modify on NAS: `/vol4/1000/docker/efb/docker-compose.yaml`
- Modify on NAS: `/vol4/1000/docker/efb/entrypoint.sh`
- Preserve on NAS: `/vol4/1000/docker/efb/profiles/`

**Interfaces:**
- Consumes: verified GHCR image and existing persistent configuration.
- Produces: running `efb2026-efb-1` using the owned `latest` image.

- [ ] Record current container image IDs and Compose state without displaying secrets.
- [ ] Create a timestamped backup of Compose, entrypoint and profile configuration.
- [ ] Update only the EFB image reference and remove the now-unneeded runtime patch mount/invocation.
- [ ] Pull `latest` and recreate only the EFB service.
- [ ] Verify container running state, image source, Python syntax, 8-hour policy, map middleware registration and `/forward` implementation without printing message logs.
- [ ] Write a local deployment record containing backup path, fixed rollback tag/digest and exact rollback command.

### Task 6: Final verification

**Files:**
- Update: `docs/superpowers/specs/2026-07-12-efb-owned-image-design.md` only if implementation differs from the approved design.

**Interfaces:**
- Produces: evidence-backed completion report.

- [ ] Run repository tests and syntax/config checks again.
- [ ] Verify GitHub visibility: two public repositories and one private repository.
- [ ] Verify no secret values exist in public Git history or the private configuration repository.
- [ ] Verify all three NAS containers are running and EFB uses `ghcr.io/shaoyou11/efb:latest`.
- [ ] Report changes, persistence, backup location, fixed rollback tag and any residual risk.
