# xiangqi-reports
象棋 App 拍照识别问题上报（照片、识别结果、纠正后的局面）

## 积分榜 / Leaderboard

在棋王 App 里登录 GitHub 后上报识别错误，机器人（`.github/workflows/leaderboard.yml`）会自动把照片放进 `reports/`、判定并计分，
结果在 `leaderboard.json`，每 5 分自动换 1 天免广告（`adfree.json`）。

Report recognition mistakes from the Chess King app (after signing in with GitHub). The bot moves the photo into `reports/`,
checks the report and scores it; see `leaderboard.json`. Every 5 points = 1 ad-free day (`adfree.json`).

### 维护者应急 / For maintainers

- 作废某个上报的积分：给 issue 打 `invalid` 标签。 Revoke a report: add the `invalid` label.
- 屏蔽刷屏用户：Settings → Moderation → Blocked users。 Block a spammer: Settings → Moderation → Blocked users.
- 临时限制互动：仓库 Settings → Moderation options → Interaction limits。 Temporarily limit interactions: repository Settings → Moderation options → Interaction limits.
- 同一账号一天超过 20 个上报会被机器人自动关闭。 More than 20 reports per account per day are closed automatically.
