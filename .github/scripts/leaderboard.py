"""上报积分榜：从 xiangqi-demo 的全部 issue 全量重算积分，写 leaderboard.json 与 adfree.json。

由 xiangqi-demo 的 GitHub Action 运行（.github/workflows/leaderboard.yml），也可本地手动跑：
  python tool/leaderboard.py --dry-run      只打印判定和积分榜，不写任何东西
  python tool/leaderboard.py --seed-cache   上线前：记录已有 issue 的判定，不留言不打标签
token：环境变量 GITHUB_TOKEN，否则用 `gh auth token`。
规则见 docs/superpowers/specs/2026-10-13-leaderboard-design.md。只用标准库。
"""
import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass, field

REPO = "jerronl/xiangqi-demo"
FILES = {"board": "leaderboard.json", "adfree": "adfree.json", "cache": "leaderboard_cache.json"}
PER_DAY = 5         # 每 5 分 = 1 天免广告
DAILY_CAP = 20      # 每账号每 UTC 日最多计分
NAME_MAX = 24
INVALID, SCORED = "invalid", "scored"
MARK = "<!-- leaderboard -->"

ACCOUNT = re.compile(r"^adfree-account:\s*([0-9a-f]{64})\s*$", re.M)
NAME = re.compile(r"^leaderboard-name:[ \t]*(.*)$", re.M)
# 照片只认本仓库 reports/ 下的文件和 GitHub 附件；markdown 图片或网页编辑器插入的 <img src="...">
_PHOTO_URL = (r"https://github\.com/" + re.escape(REPO) + r"/blob/main/reports/[\w.-]+/photo\.[a-z]+\?raw=true"
              r"|https://github\.com/user-attachments/assets/[\w-]+")
PHOTO = re.compile(r"!\[[^\]]*\]\((" + _PHOTO_URL + r")\)|<img\b[^>]*\bsrc=\"(" + _PHOTO_URL + r")\"")
MAX_PHOTO = 20 << 20       # 照片最大 20 MB
DOWNLOAD_SECONDS = 60
FENLINE = re.compile(r"^- (识别|纠正后) FEN：`([^`]*)`", re.M)
TABLE = re.compile(r"(识别结果（屏幕方向，大写红方）：|纠正后：)\n(（无）|```\n(.*?)\n```)", re.S)
REC_TABLE, COR_TABLE = "识别结果（屏幕方向，大写红方）：", "纠正后："

XQ_CAPS = {"k": 1, "a": 2, "b": 2, "n": 2, "r": 2, "c": 2, "p": 5}
CHESS_CAPS = {"k": 1, "q": 9, "r": 10, "b": 10, "n": 10, "p": 8}


@dataclass
class Report:
    number: int
    created: str
    account: str | None
    name: str | None
    kind: str           # xiangqi / chess / corners / other
    photo: str | None
    recognized: list | None = None
    corrected: list | None = None
    corner_fixed: bool = False
    labels: set = field(default_factory=set)
    author: str | None = None    # issue 的 GitHub 作者


def fen_rows(fen):
    """FEN 第一段 → 格子行（数字展开成 '.'）"""
    return ["".join("." * int(c) if c.isdigit() else c for c in row) for row in fen.split(" ")[0].split("/")]


# 双向控制、零宽等不可见字符：可以把名字显示成别的样子
INVISIBLE = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff]")


def clean_name(s):
    s = INVISIBLE.sub("", s).strip()
    if len(s) >= 2 and s[0] == s[-1] == "`":     # App 把名字放在代码格式里，免得 @ 和 # 变成提及和链接
        s = s[1:-1]
    s = " ".join(s.replace("`", "").split())
    return s[:NAME_MAX] or None


def parse_issue(i):
    body = (i.get("body") or "").replace("\r\n", "\n")   # 网页上提交的正文是 CRLF
    title = i.get("title") or ""
    # 账号和显示名取最后一处：App 把它们写在正文末尾，说明文字里的同名行不算
    acc, name = ACCOUNT.findall(body), NAME.findall(body)
    photo = PHOTO.search(body)
    r = Report(number=i["number"], created=i["created_at"], account=acc[-1] if acc else None,
               name=clean_name(name[-1]) if name else None, kind="other",
               photo=photo and (photo.group(1) or photo.group(2)),
               labels={x["name"] for x in i.get("labels", [])}, author=(i.get("user") or {}).get("login"))
    if title.startswith("找角问题"):
        r.kind = "corners"
        r.corner_fixed = "已手动纠正" in title or "没找到棋盘" in title
    elif title.startswith("国际象棋识别问题"):
        r.kind = "chess"
        fens = dict(FENLINE.findall(body))
        rec = fens.get("识别")
        r.recognized = fen_rows(rec) if rec and "（无）" not in rec else None
        r.corrected = fen_rows(fens["纠正后"]) if "纠正后" in fens else None
    elif title.startswith("识别问题"):
        r.kind = "xiangqi"
        tables = {k: v.split("\n") if v else None for k, _, v in TABLE.findall(body)}
        r.recognized = tables.get(REC_TABLE)
        r.corrected = tables.get(COR_TABLE)
    return r


def legal(rows, kind):
    """只看棋子数量（和兵的行），与棋盘方向无关"""
    h, w = (10, 9) if kind == "xiangqi" else (8, 8)
    if len(rows) != h or any(len(x) != w for x in rows):
        return False
    caps = XQ_CAPS if kind == "xiangqi" else CHESS_CAPS
    cnt = Counter(c for row in rows for c in row if c != ".")
    if any(c.lower() not in caps for c in cnt):
        return False
    for side in (str.upper, str.lower):
        if cnt[side("k")] != 1 or any(cnt[side(p)] > n for p, n in caps.items()):
            return False
    if kind == "chess" and any(c in "Pp" for c in rows[0] + rows[7]):
        return False
    return True


def check(r):
    """不计分的原因；合法返回 None（去重和每日上限在 score 里）"""
    if INVALID in r.labels:
        return "已被标记为无效"
    if r.account is None:
        return "没有绑定 Google 账号"
    if r.photo is None:
        return "没有照片"
    if r.kind == "corners":
        return None if r.corner_fixed else "找角结果未纠正"
    if r.kind == "other":
        return "不是识别问题上报"
    if r.recognized is None:
        return "没有识别结果"
    if r.corrected is None or not legal(r.corrected, r.kind):
        return "纠正后的局面不合法"
    if r.recognized == r.corrected:
        return "识别结果与纠正后相同"
    return None


@dataclass
class Result:
    board: dict
    adfree: dict
    verdict: dict
    claims: dict    # 照片哈希 → 计分的 issue 号（存进 cache，重复照片的归属不再变）


def _time(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def score(reports, photo_hash, now, claims=None):
    """全量计分。photo_hash: issue 号 → 照片 SHA-256（下载失败为 None）；
    claims: 上次的 照片哈希 → issue 号，原占用者仍合法时照片继续归它"""
    ok = {r.number for r in reports if check(r) is None and photo_hash.get(r.number)}
    owner = {h: n for h, n in (claims or {}).items() if n in ok and photo_hash.get(n) == h}
    seen, per_day, verdict, players = set(), Counter(), {}, {}
    for r in sorted(reports, key=lambda r: (r.created, r.number)):
        why = check(r)
        h = photo_hash.get(r.number)
        if why is None and h is None:
            why = "照片无法下载"
        if why is None and (h in seen or owner.get(h, r.number) != r.number):
            why = "这张照片已经上报过"
        if why is None and per_day[(r.account, r.created[:10])] >= DAILY_CAP:
            why = f"今天已达上限 {DAILY_CAP} 分"
        verdict[r.number] = why
        if why is not None:
            continue
        seen.add(h)
        per_day[(r.account, r.created[:10])] += 1
        p = players.setdefault(r.account, {"id": r.account, "name": r.account[:8], "points": 0, "times": [],
                                           "author": r.author})
        p["points"] += 1
        p["times"].append(r.created)
        # 账号哈希是公开的，别人可以冒用它上报；显示名只认该账号第一个计分上报的 GitHub 作者
        if r.name and r.author == p["author"]:
            p["name"] = r.name
    adfree = {}
    for acc, p in players.items():
        until = None
        for k in range(PER_DAY - 1, len(p["times"]), PER_DAY):
            t = _time(p["times"][k])
            until = max(until or t, t) + dt.timedelta(days=1)
        if until and until > now:
            adfree[acc] = until.isoformat(timespec="seconds")
    ranked = sorted(players.values(), key=lambda p: (-p["points"], p["times"][-1]))
    board = {"updated": now.isoformat(timespec="seconds"), "per_day": PER_DAY,
             "players": [{"id": p["id"], "name": p["name"], "points": p["points"], "last": p["times"][-1][:10]}
                         for p in ranked]}
    claimed = {photo_hash[n]: n for n, v in verdict.items() if v is None}
    return Result(board, adfree, verdict, claimed)


def comment_for(verdict, points, until):
    if verdict is None:
        s = f"{MARK}\n感谢上报！+1 分，当前 {points} 分（每 {PER_DAY} 分自动换 1 天免广告）。"
        if until:
            s += f"免广告到 {until[:16].replace('T', ' ')} UTC。"
        return (s + "\n\n积分榜：App“关于 → 积分榜”，或仓库根目录 leaderboard.json。\n"
                f"Thanks! +1 point ({points} in total). Every {PER_DAY} points = 1 ad-free day.")
    return f"{MARK}\n没有计分：{verdict}。\nNot scored (reason above)."


class GitHub:
    def __init__(self, repo, token):
        self.repo, self.token = repo, token

    def req(self, method, path, body=None, raw=False):
        url = path if path.startswith("https://") else f"https://api.github.com/repos/{self.repo}{path}"
        r = urllib.request.Request(url, method=method, data=None if body is None else json.dumps(body).encode())
        if url.startswith("https://api.github.com/") or url.startswith(f"https://github.com/{self.repo}/"):
            r.add_header("Authorization", f"Bearer {self.token}")   # token 只发给 GitHub 本仓库
        r.add_header("X-GitHub-Api-Version", "2022-11-28")
        if not raw:
            r.add_header("Accept", "application/vnd.github+json")
        try:
            with urllib.request.urlopen(r, timeout=60) as resp:
                data = resp.read()
                return (data if raw else json.loads(data) if data else None), resp.headers
        except urllib.error.HTTPError as e:
            if e.code == 404 and method == "GET":
                return None, e.headers
            raise

    def issues(self):
        out, url = [], "/issues?state=all&per_page=100"
        while url:
            page, h = self.req("GET", url)
            out += [i for i in page if "pull_request" not in i]
            m = re.search(r'<([^>]+)>;\s*rel="next"', h.get("Link", "") or "")
            url = m.group(1) if m else None
        return out

    def get_file(self, name):
        f, _ = self.req("GET", f"/contents/{name}")
        return (json.loads(base64.b64decode(f["content"])), f["sha"]) if f else (None, None)

    def put_file(self, name, obj, sha, msg):
        text = json.dumps(obj, ensure_ascii=False, indent=1) + "\n"
        body = {"message": msg, "content": base64.b64encode(text.encode()).decode()}
        if sha:
            body["sha"] = sha
        self.req("PUT", f"/contents/{name}", body)

    def photo_sha(self, url):
        """下载照片算哈希（不带 token：仓库是公开的）；失败返回 None"""
        try:
            with urllib.request.urlopen(url, timeout=20) as resp:
                return hash_stream(resp, time.monotonic() + DOWNLOAD_SECONDS)
        except Exception:   # 任何网络错误都只让这张照片本次不计分
            return None


def hash_stream(f, deadline=None):
    """分块读取并算 SHA-256；超过 MAX_PHOTO、超时或读取出错返回 None"""
    h, n = hashlib.sha256(), 0
    try:
        while True:
            if deadline is not None and time.monotonic() > deadline:
                return None
            chunk = f.read(1 << 16)
            if not chunk:
                break
            n += len(chunk)
            if n > MAX_PHOTO:
                return None
            h.update(chunk)
    except Exception:
        return None
    return h.hexdigest() if n else None


def token():
    t = os.environ.get("GITHUB_TOKEN")
    if t:
        return t
    return subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=True).stdout.strip()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写")
    ap.add_argument("--seed-cache", action="store_true", help="只记录判定，不留言不打标签")
    a = ap.parse_args()
    gh = GitHub(a.repo, token())
    reports = [parse_issue(i) for i in gh.issues()]
    cache, cache_sha = gh.get_file(FILES["cache"])
    old_cache = json.loads(json.dumps(cache)) if cache else None
    cache = cache or {"photos": {}, "commented": {}}
    hashes = {}
    for r in reports:
        if check(r) is not None:
            continue
        key = f"{r.number}:{r.photo}"
        if cache["photos"].get(key) is None:   # 下载失败的下次再试
            h = gh.photo_sha(r.photo)
            if h:
                cache["photos"][key] = h
        hashes[r.number] = cache["photos"].get(key)
    now = dt.datetime.now(dt.timezone.utc)
    res = score(reports, hashes, now, cache.get("claims"))
    cache["claims"] = res.claims
    points = {p["id"]: p["points"] for p in res.board["players"]}
    quiet = a.dry_run or a.seed_cache
    try:
        for r in reports:
            if r.kind == "other":
                continue
            v = res.verdict[r.number]
            state = "scored" if v is None else v
            if cache["commented"].get(str(r.number)) == state:
                continue
            print(f"#{r.number}: {state}")
            if not quiet:
                gh.req("POST", f"/issues/{r.number}/comments",
                       {"body": comment_for(v, points.get(r.account, 0), res.adfree.get(r.account))})
                if v is None:
                    gh.req("POST", f"/issues/{r.number}/labels", {"labels": [SCORED]})
                elif SCORED in r.labels:
                    gh.req("DELETE", f"/issues/{r.number}/labels/{SCORED}")
            cache["commented"][str(r.number)] = state
        print(json.dumps(res.board, ensure_ascii=False, indent=1))
        if a.dry_run:
            return
        for key, obj in (("board", res.board), ("adfree", res.adfree)):
            old, sha = gh.get_file(FILES[key])
            strip = (lambda d: {k: v for k, v in (d or {}).items() if k != "updated"}) if key == "board" else (lambda d: d)
            if strip(old) != strip(obj):
                gh.put_file(FILES[key], obj, sha, f"leaderboard: update {FILES[key]}")
    finally:
        # 出错时也记下已经留过言的 issue，下次不重复留言
        if not a.dry_run and old_cache != cache:
            gh.put_file(FILES["cache"], cache, gh.get_file(FILES["cache"])[1], "leaderboard: cache")

if __name__ == "__main__":
    main()
