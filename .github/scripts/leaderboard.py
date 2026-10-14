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

# App 附在正文末尾的机读数据（网页上不显示）；有它就不靠中文字样解析
DATA = re.compile(r"<!-- qiwang-report (\{[^\n]{0,8000}?\}) -->")   # 一行、限长，坏数据拖不慢
GIST_URL = re.compile(r"^https://gist\.github\.com/([\w-]+)/([0-9a-f]+)$")
GIST_LINE = re.compile(r"^- (?:照片|Photo)[：:]\s*(https://gist\.github\.com/[\w-]+/[0-9a-f]+)\s*$", re.M)
KINDS = {"xiangqi", "chess", "corners"}
MAX_META = 64 << 10

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
    gist: str | None = None      # 照片所在 gist（App 登录后上报），机器人搬进仓库
    lang: str = "zh"             # 留言语言
    state: str = "open"
    photo_error: str | None = None


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


def _rows(v):
    return v if isinstance(v, list) and all(isinstance(x, str) for x in v) and v else None


def parse_issue(i):
    body = (i.get("body") or "").replace("\r\n", "\n")   # 网页上提交的正文是 CRLF
    title = i.get("title") or ""
    author = (i.get("user") or {}).get("login")
    name, photo = NAME.findall(body), PHOTO.search(body)
    r = Report(number=i["number"], created=i["created_at"], account=author.lower() if author else None,
               name=clean_name(name[-1]) if name else None, kind="other",
               photo=photo and (photo.group(1) or photo.group(2)),
               labels={x["name"] for x in i.get("labels", [])}, author=author, state=i.get("state") or "open")
    data = None
    for m in DATA.findall(body):
        try:
            d = json.loads(m)
        except Exception:      # 包括极深嵌套引起的 RecursionError：坏数据块直接忽略
            continue
        if isinstance(d, dict) and d.get("kind") in KINDS:
            data = d
    if data is not None:
        r.kind = data["kind"]
        r.lang = "en" if data.get("lang") == "en" else "zh"
        if isinstance(data.get("name"), str):
            r.name = clean_name(data["name"])
        r.recognized, r.corrected = _rows(data.get("recognized")), _rows(data.get("corrected"))
        r.corner_fixed = data.get("corner_fixed") is True
        g = data.get("photo_gist")
        r.gist = g if isinstance(g, str) and GIST_URL.match(g) else None
        return r
    g = GIST_LINE.search(body)
    r.gist = g and g.group(1)
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
        return "没有 GitHub 作者"
    if r.author.endswith("[bot]"):
        return "机器人的 issue"
    # 先看内容，最后才看照片：内容不合格的上报不用等照片，直接告诉用户原因
    if r.kind == "other":
        return "不是识别问题上报"
    if r.kind == "corners":
        if not r.corner_fixed:
            return "找角结果未纠正"
    elif r.recognized is None:
        return "没有识别结果"
    elif r.corrected is None or not legal(r.corrected, r.kind):
        return "纠正后的局面不合法"
    elif r.recognized == r.corrected:
        return "识别结果与纠正后相同"
    if r.photo is None:
        return r.photo_error or (PENDING if r.gist else "没有照片")
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
        p = players.setdefault(r.account, {"id": r.account, "name": r.author or r.account, "points": 0, "times": []})
        p["points"] += 1
        p["times"].append(r.created)
        if r.name:      # 账号就是 issue 作者，显示名只能本人改
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


REASON_EN = {
    "已被标记为无效": "marked invalid", "没有 GitHub 作者": "no GitHub author", "机器人的 issue": "issue by a bot",
    "没有照片": "no photo", "照片还没有取到": "photo not fetched yet", "照片不属于上报者": "the photo gist is not yours",
    "gist 不存在": "the photo gist does not exist", "gist 里没有照片": "no photo in the gist",
    "照片格式不对": "the photo is not a JPEG/PNG", "照片数据损坏": "the photo data is corrupted",
    "照片太大": "the photo is too large", "照片无法下载": "the photo could not be downloaded",
    "找角结果未纠正": "the corners were not corrected", "不是识别问题上报": "not a recognition report",
    "没有识别结果": "no recognition result", "纠正后的局面不合法": "the corrected position is not legal",
    "识别结果与纠正后相同": "the corrected position equals the recognized one",
    "这张照片已经上报过": "this photo was already reported",
}


PENDING = "照片还没有取到"


def comment_for(verdict, points, until, lang="zh"):
    """issue 留言；照片还在等待搬运时不留言（返回 None）"""
    if verdict == PENDING:
        return None
    en = lang == "en"
    if verdict is None:
        if en:
            s = f"{MARK}\nThanks for the report! +1 point, {points} in total (every {PER_DAY} points = 1 ad-free day)."
            if until:
                s += f" Ad-free until {until[:16].replace('T', ' ')} UTC."
            return s + "\n\nLeaderboard: in the app under About → Leaderboard, or leaderboard.json in this repository."
        s = f"{MARK}\n感谢上报！+1 分，当前 {points} 分（每 {PER_DAY} 分自动换 1 天免广告）。"
        if until:
            s += f"免广告到 {until[:16].replace('T', ' ')} UTC。"
        return s + "\n\n积分榜：App“关于 → 积分榜”，或仓库根目录 leaderboard.json。"
    if en:
        why = (f"daily limit of {DAILY_CAP} points reached" if verdict.startswith("今天已达上限")
               else REASON_EN.get(verdict, verdict))
        return f"{MARK}\nNot scored: {why}."
    return f"{MARK}\n没有计分：{verdict}。"


def _b64(text):
    try:
        return base64.b64decode("".join(text.split()), validate=True)
    except (ValueError, TypeError):
        return None


def _image_ext(data):
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    return None


MISSING = "missing"   # gist 确实不存在（404）；None 表示暂时取不到（限流、网络），下次再试


def gist_files(g, author, fetch):
    """从 gist 取出照片等文件：({文件名: 字节}, None)、(None, 原因) 或暂时取不到时 (None, None)。
    fetch(raw_url) → 文本或 None"""
    if g is None:
        return None, None
    if g == MISSING:
        return None, "gist 不存在"
    if ((g.get("owner") or {}).get("login") or "").lower() != (author or "").lower():
        return None, "照片不属于上报者"
    files = g.get("files") or {}

    def text(name):
        f = files.get(name)
        if not f:
            return None
        t = fetch(f.get("raw_url", "")) if f.get("truncated") else f.get("content")
        return t if isinstance(t, str) else None

    if "photo.jpg.b64" not in files:
        return None, "gist 里没有照片"
    t = text("photo.jpg.b64")
    if t is None:
        return None, None
    if len(t) > MAX_PHOTO * 4 // 3 + 1024:
        return None, "照片太大"
    data = _b64(t)
    if data is None:
        return None, "照片数据损坏"
    ext = _image_ext(data)
    if ext is None:
        return None, "照片格式不对"
    out = {f"photo.{ext}": data}
    t = text("rectified.png.b64")
    rect = _b64(t) if t and len(t) <= MAX_PHOTO else None
    if rect and _image_ext(rect) == "png":
        out["rectified.png"] = rect
    t = text("meta.json")
    if t and len(t) <= MAX_META:
        out["meta.json"] = t.encode()
    return out, None


def rewrite_body(body, gist_url, photo_url, lang):
    """把照片插进正文：可见的 gist 行换成图片，并留一行原 gist 链接"""
    body = body.replace("\r\n", "\n")
    label = "Original gist" if lang == "en" else "原始 gist"
    img = f"![photo]({photo_url})\n- {label}: {gist_url}"
    m = GIST_LINE.search(body)
    if m:
        return body[:m.start()] + img + body[m.end():]
    m = DATA.search(body)
    at = m.start() if m else len(body)
    return body[:at] + "\n" + img + "\n" + body[at:]


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

    def put_bytes(self, path, data, msg):
        """上传文件；已经存在（上次传了一半）就不再传"""
        try:
            self.req("PUT", f"/contents/{path}", {"message": msg, "content": base64.b64encode(data).decode()})
        except urllib.error.HTTPError as e:
            if e.code != 422:
                raise

    @staticmethod
    def public_bytes(url, limit=MAX_PHOTO * 4 // 3 + 4096):
        """不带 token 下载（公开资源）；超过 limit、超时或出错返回 None"""
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "qiwang-leaderboard"}),
                                        timeout=20) as resp:
                out, deadline = bytearray(), time.monotonic() + DOWNLOAD_SECONDS
                while chunk := resp.read(1 << 16):
                    out += chunk
                    if len(out) > limit or time.monotonic() > deadline:
                        return None
                return bytes(out)
        except Exception:
            return None

    def gist(self, gid):
        """gist 的 JSON；不存在返回 MISSING，暂时取不到（限流、网络）返回 None"""
        try:
            g, _ = self.req("GET", f"https://api.github.com/gists/{gid}")
        except Exception:
            return None
        return MISSING if g is None else g

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


def should_move(r, moved_today):
    """只搬会计分的上报：issue 开着、没作废、作者当天搬运没超上限、除了照片以外都合格"""
    if r.state != "open" or moved_today >= DAILY_CAP:
        return False
    return check(Report(**{**r.__dict__, "photo": "pending"})) is None


def fetch_gists(gh, issues, cache, now, repo, write=True):
    """把 App 上报放在 gist 里的照片搬进仓库 reports/<issue 号>/，改写 issue 正文插入照片。
    返回 {issue 号: 失败原因}（暂时取不到的不算失败）"""
    errors, moved = {}, Counter()
    failed = cache.setdefault("gist_errors", {})
    for i in issues:
        try:
            r = parse_issue(i)
            if not r.gist or r.photo or not r.author:
                continue
            key = f"{r.number}:{r.gist}"
            if key in failed:                  # 永久错误（不存在、不是作者的、格式不对）不再重试
                errors[r.number] = failed[key]
                continue
            day = (r.account, now.strftime("%Y-%m-%d"))
            if not should_move(r, moved[day]):
                continue
            files, err = gist_files(gh.gist(GIST_URL.match(r.gist).group(2)), r.author,
                                    lambda u: (gh.public_bytes(u) or b"").decode("ascii", "replace") or None)
            if err:
                errors[r.number] = failed[key] = err
                continue
            if files is None:
                continue                       # 暂时取不到，下次再试，不留言
            folder = f"reports/{r.number}"
            photo = next(k for k in files if k.startswith("photo."))
            url = f"https://github.com/{repo}/blob/main/{folder}/{photo}?raw=true"
            new_body = rewrite_body(i.get("body") or "", r.gist, url, r.lang)
            if len(new_body) > 65000:
                errors[r.number] = failed[key] = "正文太长"
                continue
            print(f"#{r.number}: gist → {folder}")
            moved[day] += 1
            if not write:
                continue
            for name, data in files.items():
                gh.put_bytes(f"{folder}/{name}", data, f"report #{r.number}: {name}")
            gh.req("PATCH", f"/issues/{r.number}", {"body": new_body})
            i["body"] = new_body
            cache["photos"][f"{r.number}:{url}"] = hashlib.sha256(files[photo]).hexdigest()
        except Exception as e:                 # 一个 issue 出错不影响其他
            print(f"#{i.get('number')}: gist error {e!r}")
    return errors


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写")
    ap.add_argument("--seed-cache", action="store_true", help="只记录判定，不留言不打标签")
    a = ap.parse_args()
    gh = GitHub(a.repo, token())
    issues = gh.issues()
    cache, cache_sha = gh.get_file(FILES["cache"])
    old_cache = json.loads(json.dumps(cache)) if cache else None
    cache = cache or {"photos": {}, "commented": {}}
    now = dt.datetime.now(dt.timezone.utc)
    gist_error = fetch_gists(gh, issues, cache, now, a.repo, write=not (a.dry_run or a.seed_cache))
    reports = [parse_issue(i) for i in issues]
    for r in reports:
        r.photo_error = gist_error.get(r.number)
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
            msg = comment_for(v, points.get(r.account, 0), res.adfree.get(r.account), r.lang)
            if msg is None or cache["commented"].get(str(r.number)) == state:
                continue                       # 照片还在等待搬运：先不留言
            print(f"#{r.number}: {state}")
            try:
                if not quiet:
                    gh.req("POST", f"/issues/{r.number}/comments", {"body": msg})
                    if v is None:
                        gh.req("POST", f"/issues/{r.number}/labels", {"labels": [SCORED]})
                    elif SCORED in r.labels:
                        gh.req("DELETE", f"/issues/{r.number}/labels/{SCORED}")
                    if v is not None and v.startswith("今天已达上限") and r.state == "open":   # 刷屏的直接关掉
                        gh.req("PATCH", f"/issues/{r.number}", {"state": "closed", "state_reason": "not_planned"})
                cache["commented"][str(r.number)] = state
            except Exception as e:             # 一个 issue 出错不影响其他，下次再试
                print(f"#{r.number}: comment error {e!r}")
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
