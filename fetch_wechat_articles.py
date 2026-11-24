#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""拉取微信公众号公开专辑里的全部文章目录（标题、日期、链接）。

用法:
  python fetch_wechat_articles.py https://mp.weixin.qq.com/s/xxxxx
  python fetch_wechat_articles.py "https://mp.weixin.qq.com/mp/appmsgalbum?__biz=...&action=getalbum&album_id=..."
  python fetch_wechat_articles.py --biz MzkzNzYzNzE3Mg== --album-id 4164560801283358741
  python fetch_wechat_articles.py URL -o ./out --content

无需第三方库，无需登录。数据来自微信公开的专辑接口（appmsgalbum）和文章页里的合集链接。
公众号主页时间线接口需要登录，所以未收入任何专辑的推文可能收不全。
"""

from __future__ import annotations

import argparse
import html as htmlmod
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

SHANGHAI = timezone(timedelta(hours=8))
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)
ALBUM_API = "https://mp.weixin.qq.com/mp/appmsgalbum"
VERIFY_MARKERS = (
    "环境异常",
    "完成验证",
    "secitptpage/verify",
    "id=\"js_verify\"",
    "请完成安全验证",
)

class FetchError(RuntimeError):
    pass


def shanghai_time(ts: int | str | None) -> str:
    if ts in (None, "", 0, "0"):
        return ""
    try:
        value = int(str(ts))
    except ValueError:
        return ""
    return datetime.fromtimestamp(value, SHANGHAI).strftime("%Y-%m-%d %H:%M")


def shanghai_date(ts: int | str | None) -> str:
    s = shanghai_time(ts)
    return s[:10] if s else ""


def http_get(url: str, timeout: int = 30) -> tuple[int, str, str]:
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://mp.weixin.qq.com/",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", 200)
            content_type = resp.headers.get("Content-Type", "")
            raw = resp.read()
            encoding = "utf-8"
            match = re.search(r"charset=([\w-]+)", content_type, re.I)
            if match:
                encoding = match.group(1)
            text = raw.decode(encoding, errors="replace")
            return status, text, str(resp.geturl())
    except urllib.error.HTTPError as exc:
        raw = exc.read() if exc.fp else b""
        text = raw.decode("utf-8", errors="replace")
        return exc.code, text, url
    except urllib.error.URLError as exc:
        raise FetchError(f"请求失败: {url} ({exc})") from exc


def as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def parse_query(url: str) -> dict[str, str]:
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qs(parts.query, keep_blank_values=True)
    return {k: v[-1] for k, v in query.items()}


def normalize_article_url(url: str) -> str:
    if not url:
        return ""
    url = htmlmod.unescape(url).replace("http://", "https://", 1)
    url = url.replace("&amp;", "&")
    parts = urllib.parse.urlsplit(url)
    # 去掉 #rd 这类片段，保留查询参数，便于去重
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def article_dedupe_key(url: str, msgid: str = "", itemidx: str = "") -> str:
    if msgid:
        return f"{msgid}_{itemidx or '1'}"
    q = parse_query(url)
    if q.get("__biz") and q.get("mid"):
        return f"{q.get('mid')}_{q.get('idx') or '1'}"
    # 短链 /s/XXXX
    path = urllib.parse.urlsplit(url).path
    m = re.search(r"/s/([A-Za-z0-9_-]+)", path)
    if m:
        return "s:" + m.group(1)
    return normalize_article_url(url)


def extract_biz(text_or_url: str) -> str:
    for pat in (
        r"[?&]__biz=([A-Za-z0-9_=+/-]+)",
        r"var\s+biz\s*=\s*\"([^\"]+)\"",
        r"var\s+appuin\s*=\s*\"([^\"]+)\"",
    ):
        m = re.search(pat, text_or_url)
        if m:
            return urllib.parse.unquote(m.group(1))
    return ""


def extract_album_ids(text: str) -> set[str]:
    ids = set()
    for pat in (
        r"album_id(?:=|:\s*['\"]|_str\s*:\s*['\"])(\d{5,})",
        r"album_id=(\d{5,})",
        r"album_id['\"]?\s*:\s*['\"](\d{5,})['\"]",
        r"album_id_str['\"]?\s*:\s*['\"](\d{5,})['\"]",
    ):
        ids.update(re.findall(pat, text))
    return ids


def extract_meta_from_article_html(html: str) -> dict[str, Any]:
    def var(name: str) -> str:
        m = re.search(rf"var\s+{name}\s*=\s*\"([^\"]*)\"", html)
        return m.group(1) if m else ""

    title = var("msg_title") or var("title")
    if not title:
        m = re.search(r'property="og:title"\s+content="([^"]+)"', html)
        if m:
            title = htmlmod.unescape(m.group(1))
    nickname = ""
    m = re.search(r"nick_name:\s*'([^']+)'", html)
    if m:
        nickname = m.group(1)
    if not nickname:
        m = re.search(r'data-nickname="([^"]+)"', html)
        if m:
            nickname = htmlmod.unescape(m.group(1))

    info = {
        "biz": extract_biz(html),
        "user_name": var("user_name"),
        "nickname": nickname,
        "title": htmlmod.unescape(title) if title else "",
        "create_time": var("ct"),
        "datetime": shanghai_time(var("ct")),
        "album_ids": sorted(extract_album_ids(html)),
        "verified_blocked": is_verify_page(html),
    }
    return info


def is_verify_page(html: str) -> bool:
    if not html:
        return True
    if "id=\"js_content\"" in html or "id='js_content'" in html:
        # 有正文容器，即使页脚提到验证也不算拦截
        return False
    return any(marker in html for marker in VERIFY_MARKERS)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip = 0
        self._in_content = False
        self._content_depth = 0
        self._started = False
        self.found = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        ad = dict(attrs)
        if tag in {"script", "style", "noscript"}:
            self._skip += 1
        if not self.found and ad.get("id") == "js_content":
            self.found = True
            self._in_content = True
            self._content_depth = 1
            return
        if self._in_content:
            if tag in {"p", "br", "div", "section", "h1", "h2", "h3", "h4", "li", "tr"}:
                self._chunks.append("\n")
            self._content_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip:
            self._skip -= 1
        if self._in_content:
            self._content_depth -= 1
            if self._content_depth <= 0:
                self._in_content = False

    def handle_data(self, data: str) -> None:
        if self._skip or not self._in_content:
            return
        text = re.sub(r"\s+", " ", data).strip()
        if text:
            self._chunks.append(text)

    def text(self) -> str:
        raw = "".join(self._chunks)
        raw = re.sub(r"[ \t]+", " ", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def extract_article_text(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        pass
    return parser.text()


def fetch_album_page(biz: str, album_id: str, begin_msgid: str = "", begin_itemidx: str = "", count: int = 20) -> dict[str, Any]:
    params = {
        "action": "getalbum",
        "__biz": biz,
        "album_id": album_id,
        "count": str(count),
        "f": "json",
    }
    if begin_msgid:
        params["begin_msgid"] = begin_msgid
        params["begin_itemidx"] = begin_itemidx or "1"
    url = ALBUM_API + "?" + urllib.parse.urlencode(params)
    status, text, _ = http_get(url)
    if status != 200:
        raise FetchError(f"专辑接口 HTTP {status}: {url}")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise FetchError(f"专辑接口返回的不是 JSON，可能被拦截了: {url}") from exc
    ret = (data.get("base_resp") or {}).get("ret", data.get("ret"))
    if ret not in (0, "0", None):
        errmsg = (data.get("base_resp") or {}).get("errmsg") or data.get("errmsg") or ""
        raise FetchError(f"专辑接口失败 ret={ret} {errmsg}")
    return data


def iter_album_articles(biz: str, album_id: str, delay: float) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    articles: list[dict[str, Any]] = []
    base_info: dict[str, Any] = {}
    begin_msgid = ""
    begin_itemidx = ""
    seen_keys: set[str] = set()
    while True:
        data = fetch_album_page(biz, album_id, begin_msgid, begin_itemidx)
        resp = data.get("getalbum_resp") or {}
        incoming = resp.get("base_info") or {}
        # 后续翻页的 base_info 往往只剩少量字段，不能覆盖第一页的标题和篇数
        if incoming:
            if not base_info or incoming.get("title") or incoming.get("article_count"):
                merged = dict(base_info)
                for key, value in incoming.items():
                    if value not in (None, "", [], {}):
                        merged[key] = value
                base_info = merged
        page = as_list(resp.get("article_list"))
        if not page:
            break
        new_on_page = 0
        for item in page:
            msgid = str(item.get("msgid") or "")
            itemidx = str(item.get("itemidx") or "1")
            key = article_dedupe_key(item.get("url") or "", msgid, itemidx)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            new_on_page += 1
            url = normalize_article_url(item.get("url") or "")
            articles.append(
                {
                    "title": item.get("title") or "",
                    "datetime": shanghai_time(item.get("create_time")),
                    "date": shanghai_date(item.get("create_time")),
                    "create_time": str(item.get("create_time") or ""),
                    "url": url,
                    "msgid": msgid,
                    "itemidx": itemidx,
                    "key": item.get("key") or "",
                    "cover": item.get("cover_img_1_1") or item.get("cover") or "",
                    "album_id": album_id,
                    "album_title": (base_info.get("title") or "").strip(),
                }
            )
        continue_flag = str(resp.get("continue_flag") or "0")
        if continue_flag != "1" or new_on_page == 0:
            break
        last = page[-1]
        begin_msgid = str(last.get("msgid") or "")
        begin_itemidx = str(last.get("itemidx") or "1")
        if not begin_msgid:
            break
        if delay:
            time.sleep(delay)
    return base_info, articles


def sample_indices(n: int, k: int) -> list[int]:
    if n <= 0 or k <= 0:
        return []
    if n <= k:
        return list(range(n))
    # 头、尾，再均匀取中间，优先覆盖最早和最晚的文章
    chosen = {0, n - 1}
    if k >= 3:
        chosen.add(n // 2)
    remaining = k - len(chosen)
    if remaining > 0:
        step = n / (remaining + 1)
        for i in range(1, remaining + 1):
            chosen.add(min(n - 1, max(0, int(round(i * step)))))
    return sorted(chosen)


def discover_from_articles(articles: list[dict[str, Any]], known_ids: set[str], delay: float, sample_size: int) -> set[str]:
    found: set[str] = set()
    idxs = sample_indices(len(articles), sample_size)
    for i in idxs:
        url = articles[i].get("url")
        if not url:
            continue
        try:
            status, html, _ = http_get(url)
        except FetchError as exc:
            print(f"  警告: 打开文章失败，跳过合集发现 ({exc})", file=sys.stderr)
            continue
        if status != 200 or is_verify_page(html):
            print(f"  警告: 文章打开异常或触发验证，跳过: {url}", file=sys.stderr)
            continue
        new_ids = extract_album_ids(html) - known_ids
        found.update(new_ids)
        if delay:
            time.sleep(delay)
    return found


def write_markdown(path: Path, account: dict[str, Any], albums: list[dict[str, Any]], articles: list[dict[str, Any]]) -> None:
    lines = []
    nick = account.get("nickname") or account.get("user_name") or "未知公众号"
    lines.append(f"# {nick} 公开文章目录")
    lines.append("")
    lines.append(f"- 公众号：{nick}")
    if account.get("biz"):
        lines.append(f"- `__biz`：`{account['biz']}`")
    if account.get("user_name"):
        lines.append(f"- 原始 ID：`{account['user_name']}`")
    if albums:
        lines.append(f"- 专辑：{len(albums)} 个")
        for alb in albums:
            title = alb.get("title") or alb.get("album_id")
            count = alb.get("article_count") or len(alb.get("articles") or [])
            lines.append(f"  - {title}（{count} 篇，id `{alb.get('album_id')}`）")
    lines.append(f"- 去重后共 {len(articles)} 篇")
    dates = [a.get("date") for a in articles if a.get("date")]
    if dates:
        lines.append(f"- 时间范围：{min(dates)} 至 {max(dates)}（Asia/Shanghai）")
    lines.append("")
    lines.append("说明：本目录来自无需登录的公开专辑接口，以及文章页里能发现的合集。公众号主页时间线需要登录，未收入任何专辑的推文可能缺失。")
    lines.append("")

    by_album: dict[str, list[dict[str, Any]]] = {}
    for art in articles:
        by_album.setdefault(art.get("album_title") or art.get("album_id") or "未分组", []).append(art)
    for album_name, items in by_album.items():
        items = sorted(items, key=lambda x: (x.get("create_time") or "", x.get("msgid") or ""))
        lines.append(f"## {album_name}")
        lines.append("")
        for i, art in enumerate(items, 1):
            day = art.get("date") or art.get("datetime") or ""
            title = art.get("title") or "(无标题)"
            lines.append(f"{i}. {day} {title}")
            if art.get("url"):
                lines.append(f"   {art['url']}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_content_files(out_dir: Path, articles: list[dict[str, Any]], delay: float) -> list[dict[str, Any]]:
    content_dir = out_dir / "articles"
    content_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for i, art in enumerate(articles, 1):
        url = art.get("url")
        rec = {
            "title": art.get("title"),
            "url": url,
            "msgid": art.get("msgid"),
            "blocked": False,
            "file": "",
            "text_len": 0,
        }
        if not url:
            rec["blocked"] = True
            results.append(rec)
            continue
        try:
            status, html, _ = http_get(url)
        except FetchError as exc:
            rec["blocked"] = True
            rec["error"] = str(exc)
            results.append(rec)
            print(f"  [{i}/{len(articles)}] 失败: {art.get('title')} ({exc})", file=sys.stderr)
            continue
        if status != 200 or is_verify_page(html):
            rec["blocked"] = True
            rec["error"] = f"HTTP {status}" if status != 200 else "触发微信验证页"
            results.append(rec)
            print(f"  [{i}/{len(articles)}] 验证页或异常: {art.get('title')}", file=sys.stderr)
            if delay:
                time.sleep(delay)
            continue
        text = extract_article_text(html)
        day = art.get("date") or "undated"
        mid = art.get("msgid") or f"{i:04d}"
        safe = re.sub(r'[\\/:*?"<>|]', "_", f"{day}_{mid}")
        file_path = content_dir / f"{safe}.md"
        body = f"# {art.get('title') or ''}\n\n- 日期：{art.get('datetime') or day}\n- 链接：{url}\n\n{text}\n"
        file_path.write_text(body, encoding="utf-8")
        rec["file"] = str(file_path)
        rec["text_len"] = len(text)
        results.append(rec)
        print(f"  [{i}/{len(articles)}] 已保存 {file_path.name}（{len(text)} 字）")
        if delay:
            time.sleep(delay)
    return results


def resolve_seed(url: str | None, biz: str | None, album_id: str | None) -> dict[str, Any]:
    seed: dict[str, Any] = {
        "biz": biz or "",
        "album_ids": set(),
        "nickname": "",
        "user_name": "",
        "source_url": url or "",
    }
    if url:
        q = parse_query(url)
        seed["biz"] = seed["biz"] or q.get("__biz") or extract_biz(url)
        if q.get("album_id"):
            seed["album_ids"].add(q["album_id"])
        path = urllib.parse.urlsplit(url).path
        # 文章页或短链：打开 HTML，抽出 biz 和合集
        if "/s" in path or not seed["album_ids"] or not seed["biz"]:
            status, html, final = http_get(url)
            if status != 200:
                raise FetchError(f"打开入口页失败 HTTP {status}: {url}")
            if is_verify_page(html):
                raise FetchError("入口文章触发了微信验证页，换一篇同账号的文章再试，或稍后再试。")
            meta = extract_meta_from_article_html(html)
            seed["biz"] = seed["biz"] or meta["biz"]
            seed["nickname"] = meta.get("nickname") or ""
            seed["user_name"] = meta.get("user_name") or ""
            seed["album_ids"].update(meta.get("album_ids") or [])
            seed["source_url"] = final or url
    if album_id:
        seed["album_ids"].add(album_id)
    if not seed["biz"]:
        raise FetchError("没能解析出公众号 __biz。请提供带 __biz 的文章/专辑链接，或同时传入 --biz。")
    if not seed["album_ids"] and url:
        # 再从入口页已处理；如果仍没有合集，明确告诉用户
        raise FetchError(
            "在入口页里没有发现任何专辑/合集。这个工具依赖公开专辑接口；"
            "若该账号没有把文章收入合集，未登录时无法拉取完整时间线。"
        )
    if not seed["album_ids"]:
        raise FetchError("请提供文章链接、专辑链接，或使用 --album-id。")
    return seed


def collect(
    url: str | None,
    biz: str | None,
    album_id: str | None,
    delay: float,
    discover: bool,
    sample_size: int,
) -> dict[str, Any]:
    seed = resolve_seed(url, biz, album_id)
    biz_val = seed["biz"]
    pending = set(seed["album_ids"])
    seen_albums: set[str] = set()
    albums_out: list[dict[str, Any]] = []
    all_articles: list[dict[str, Any]] = []
    account = {
        "biz": biz_val,
        "nickname": seed.get("nickname") or "",
        "user_name": seed.get("user_name") or "",
    }

    print(f"账号 __biz = {biz_val}")
    if account["nickname"]:
        print(f"公众号：{account['nickname']}")
    print(f"起始专辑：{', '.join(sorted(pending))}")

    while pending:
        current = pending.pop()
        if current in seen_albums:
            continue
        seen_albums.add(current)
        print(f"正在拉取专辑 {current} …")
        try:
            base_info, articles = iter_album_articles(biz_val, current, delay)
        except FetchError as exc:
            print(f"  失败: {exc}", file=sys.stderr)
            albums_out.append({"album_id": current, "title": "", "error": str(exc), "article_count": 0})
            continue
        title = (base_info.get("title") or "").strip()
        nickname = (base_info.get("nickname") or "").strip()
        user_name = (base_info.get("username") or "").strip()
        if nickname and not account["nickname"]:
            account["nickname"] = nickname
        if user_name and not account["user_name"]:
            account["user_name"] = user_name
        declared = base_info.get("article_count")
        print(f"  《{title or current}》 {len(articles)} 篇（接口声明 {declared or '?'} 篇）")
        albums_out.append(
            {
                "album_id": current,
                "title": title,
                "article_count": int(declared) if str(declared).isdigit() else len(articles),
                "username": user_name,
                "nickname": nickname,
                "fetched": len(articles),
            }
        )
        for art in articles:
            if not art.get("album_title"):
                art["album_title"] = title
            all_articles.append(art)
        if discover and articles:
            known = seen_albums | pending
            newly = discover_from_articles(articles, known, delay, sample_size)
            if newly:
                print(f"  从文章页新发现专辑：{', '.join(sorted(newly))}")
                pending.update(newly)

    # 去重：同一篇文章可能出现在多个专辑
    deduped: list[dict[str, Any]] = []
    index: dict[str, dict[str, Any]] = {}
    for art in all_articles:
        key = article_dedupe_key(art.get("url") or "", art.get("msgid") or "", art.get("itemidx") or "")
        if key in index:
            # 记录所属多个专辑
            prev = index[key]
            albums = prev.setdefault("albums", [prev.get("album_title") or prev.get("album_id")])
            extra = art.get("album_title") or art.get("album_id")
            if extra and extra not in albums:
                albums.append(extra)
            continue
        art["albums"] = [art.get("album_title") or art.get("album_id")]
        index[key] = art
        deduped.append(art)

    deduped.sort(key=lambda x: (x.get("create_time") or "", x.get("msgid") or ""))
    account["source_url"] = seed.get("source_url") or ""
    return {"account": account, "albums": albums_out, "articles": deduped}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="拉取微信公众号公开专辑中的全部文章目录（标题、日期、链接）。无需登录。",
    )
    p.add_argument("url", nargs="?", help="一篇微信文章链接，或一个专辑（合集）链接")
    p.add_argument("--biz", help="公众号 __biz，例如 MzkzNzYzNzE3Mg==")
    p.add_argument("--album-id", help="专辑 album_id，可与 --biz 一起使用，或作为补充")
    p.add_argument("-o", "--out", default="wechat-articles", help="输出目录（默认 ./wechat-articles）")
    p.add_argument("--content", action="store_true", help="继续抓取每篇文章正文，保存为 Markdown")
    p.add_argument("--no-discover", action="store_true", help="只拉入口里已有的专辑，不再打开文章寻找其他合集")
    p.add_argument("--sample", type=int, default=5, help="每个专辑抽样几篇文章来发现其他合集（默认 5）")
    p.add_argument("--delay", type=float, default=0.6, help="请求间隔秒数（默认 0.6）")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.url and not (args.biz and args.album_id):
        build_parser().print_help()
        print("\n错误：请提供文章/专辑 URL，或同时提供 --biz 和 --album-id。", file=sys.stderr)
        return 2
    try:
        result = collect(
            url=args.url,
            biz=args.biz,
            album_id=args.album_id,
            delay=args.delay,
            discover=not args.no_discover,
            sample_size=max(0, args.sample),
        )
    except FetchError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "articles.json"
    md_path = out_dir / "article-list.md"
    payload = {
        "account": result["account"],
        "albums": result["albums"],
        "article_count": len(result["articles"]),
        "articles": result["articles"],
        "limitations": (
            "仅覆盖无需登录即可访问的公开专辑，以及从文章页发现的合集。"
            "公众号主页时间线（profile_ext）需要登录，未入集的推文可能缺失。"
        ),
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(md_path, result["account"], result["albums"], result["articles"])
    print(f"\n去重后共 {len(result['articles'])} 篇")
    print(f"已写入 {md_path}")
    print(f"已写入 {json_path}")

    if args.content:
        print("开始抓取正文…")
        content_results = write_content_files(out_dir, result["articles"], args.delay)
        (out_dir / "fetch_results.json").write_text(
            json.dumps(content_results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        ok = sum(1 for r in content_results if not r.get("blocked") and r.get("text_len"))
        blocked = sum(1 for r in content_results if r.get("blocked"))
        print(f"正文完成：成功 {ok}，失败或验证页 {blocked}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
