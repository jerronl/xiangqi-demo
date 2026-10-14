# 隐私政策 / Privacy Policy

最后更新 / Last updated: 2026-10-14

本政策适用于安卓应用“棋王 / Chess King”（中国象棋与国际象棋，包名 `com.jerron.xiangqi`，开发者 jerronl）。
This policy applies to the Android app "棋王 / Chess King" (Chinese chess and chess, package `com.jerron.xiangqi`, developer jerronl).

## 概要 / Summary

下棋、引擎分析、拍照和本地识别都在手机上完成。**照片只有在你主动上报、或同意分享之后才会离开手机。** 开发者不出售任何数据。
Playing, engine analysis, taking photos and on-device recognition all run on your phone. **Photos leave your phone only when you report them or agree to share them.** The developer never sells any data.

## 我们收集什么 / What we collect

1. **上报识别问题 / Reporting recognition problems**（你主动点“上报”时 / when you tap "Report"）
   - 你用 GitHub 账号登录（GitHub 设备授权）。照片、拉正后的棋盘图和元数据先保存在**你自己 GitHub 账号下的私密 gist**，再在公开仓库 [jerronl/xiangqi-demo](https://github.com/jerronl/xiangqi-demo) 开一个 issue；机器人把照片复制到该仓库。
   - **公开内容**：照片、识别结果、你纠正后的局面、你写的说明、你的 GitHub 用户名和积分榜显示名，**任何人可见**。上报前请确认照片里没有不想公开的内容。
   - 授权只用于开 issue 和创建存放照片的 gist；应用看不到你的密码、邮箱或代码仓库。登录凭证只保存在手机上，可随时在“关于”里退出，或在 GitHub 设置 → Applications 里撤销。
   - You sign in with GitHub (device authorization). The photo, rectified board image and metadata are stored in a **secret gist in your own GitHub account**, then an issue is opened in the public repository above and a bot copies the photo into it.
   - **Public**: the photo, recognition result, your corrected position, your note, your GitHub username and leaderboard display name are **visible to everyone**. Check the photo before reporting.
   - The authorization is used only to open issues and create the photo gist; the app cannot see your password, email or repositories. The sign-in stays on your phone; sign out in About or revoke it in GitHub Settings → Applications.

2. **积分榜 / Leaderboard**
   合法的上报会计分，公开文件 `leaderboard.json` 和 `adfree.json` 记录 GitHub 用户名、显示名、积分和免广告到期时间。
   Valid reports earn points; the public files `leaderboard.json` and `adfree.json` list GitHub usernames, display names, points and ad-free expiry.

3. **自愿分享识别出错的照片 / Optional sharing of misrecognized photos**（需你同意，默认关闭 / opt-in, off by default）
   你同意后，当你纠正了识别结果（改动了棋子或棋盘四角），应用会把照片和纠正结果发送给开发者，用于训练识别模型。
   - **不公开**：保存在开发者的私有仓库，只有开发者能看到。
   - **不带身份信息**：不附带账号、名字或设备标识；请求经过 Cloudflare 转发，Cloudflare 会按其政策短暂处理 IP 地址。
   - 随时可以在“关于”里关闭。
   If you agree, whenever you correct a recognition (pieces or board corners) the app sends the photo and your correction to the developer to train the recognizer. **Not public** (stored in the developer's private repository), **no account, name or device identifier attached**; requests pass through Cloudflare, which briefly processes the IP address under its own policy. Turn it off at any time in About.

4. **云端识别 / Cloud recognition**（可选，需你在设置里填写自己的 API key / optional, with your own API key）
   照片会发送给你选择的服务商（Anthropic Claude，或 OpenAI 兼容服务），受该服务商的隐私政策约束。
   The photo is sent to the provider you choose and is governed by that provider's privacy policy.

5. **广告 / Advertising**
   应用通过 Google AdMob 显示广告。AdMob 可能收集设备标识符（如广告 ID）、IP 地址和广告互动数据，用于投放和衡量广告。在需要征得同意的地区（如欧洲经济区、英国），应用会先显示 Google 的同意框，你可以随时在“关于 → 广告隐私设置”中修改。详见 [Google 如何使用数据](https://policies.google.com/technologies/partner-sites)。
   The app shows ads via Google AdMob, which may collect device identifiers (such as the advertising ID), IP address and ad interaction data. Where consent is required (e.g. EEA, UK) Google's consent form is shown first; change your choice at any time in About → Ad privacy settings. See [How Google uses information from sites or apps that use its services](https://policies.google.com/technologies/partner-sites).

## 保存在手机上的数据 / Data stored on your device

GitHub 登录凭证、API key、识别方式、积分榜显示名、是否同意分享等设置只保存在手机上，不参与系统备份，卸载应用即删除。
The GitHub sign-in, API keys, recognizer choice, leaderboard display name and sharing choice are stored only on your phone, are excluded from system backups, and are deleted when you uninstall the app.

## 权限 / Permissions

- 相机 / Camera：在应用内拍棋盘 / to photograph a board in the app
- 照片 / Photos：从相册选图识别 / to pick a picture to recognize
- 网络 / Internet：上报、分享、云端识别、积分榜、广告 / reporting, sharing, cloud recognition, leaderboard, ads

## 删除 / Deletion

- 公开上报：在 [xiangqi-demo](https://github.com/jerronl/xiangqi-demo/issues) 提 issue 说明要删除的上报编号；你也可以自己删除 gist。
- 分享的照片：照片不带身份信息，无法按人查找；如需删除，请提供照片和大致时间，开发者会尽力查找删除。
- Public reports: open an issue in the repository with the report number; you can delete your own gist. Shared photos carry no identity, so provide the photo and approximate time and the developer will try to find and delete it.

## 儿童 / Children

本应用不面向 13 岁以下儿童，也不会有意收集儿童的信息。
The app is not directed at children under 13 and does not knowingly collect their information.

## 联系 / Contact

https://github.com/jerronl/xiangqi-demo/issues
