# 微信公众号文章拉取工具

从一篇微信文章或一个专辑（合集）链接出发，拉取该公众号**已公开专辑**里的全部文章目录：标题、发布日期、链接。可选再抓正文。

不需要登录，也不需要第三方 Python 库。Windows / macOS / Linux 只要有 Python 3.9+ 就能跑。

## 用法

```bash
python fetch_wechat_articles.py https://mp.weixin.qq.com/s/文章短链
python fetch_wechat_articles.py "https://mp.weixin.qq.com/mp/appmsgalbum?__biz=...&action=getalbum&album_id=..."
python fetch_wechat_articles.py --biz MzkzNzYzNzE3Mg== --album-id 4164560801283358741
python fetch_wechat_articles.py URL -o ./out --content
```

常用参数：

- `-o` / `--out`：输出目录，默认 `./wechat-articles`
- `--content`：继续抓每篇文章正文，保存为 Markdown
- `--no-discover`：只拉入口里已经出现的专辑，不再打开文章寻找其他合集
- `--delay`：请求间隔秒数，默认 0.6，避免请求过快

输出：

- `article-list.md`：可读目录
- `articles.json`：完整机器可读数据
- `articles/*.md`：仅在加了 `--content` 时生成

## 它是怎么拉的

1. 打开你给的文章或专辑页，从页面或链接里解析公众号 `__biz`，以及所有 `album_id`（文章底部「所属合集」和页面里的专辑链接）。
2. 请求微信公开接口 `https://mp.weixin.qq.com/mp/appmsgalbum?action=getalbum&__biz=...&album_id=...&count=20&f=json`。这个接口无需登录。
3. 一页约 20 篇。若返回 `continue_flag=1`，把本页最后一篇的 `msgid`、`itemidx` 作为 `begin_msgid`、`begin_itemidx` 继续翻页。
4. 默认再从每个专辑里抽几篇文章打开，查找是否还有别的合集，直到没有新的为止。
5. 按 `msgid + idx` 去重后写成目录。时间按 Asia/Shanghai。

## 账号 ID 和专辑 ID 从哪来

你其实不一定要自己去抄这两个值：把一篇文章链接或一个合集链接交给脚本，它会自己解析。若你想手动找，方法如下。

**账号 ID（`__biz`）**

- 打开任意一篇该公众号的文章。如果链接是长链，形如 `https://mp.weixin.qq.com/s?__biz=MzkzNzYzNzE3Mg==&mid=...&idx=1&sn=...`，其中 `__biz=` 后面那一段就是账号 ID。
- 如果是短链，例如 `https://mp.weixin.qq.com/s/xxxxx`，在浏览器里打开后再看地址栏，通常会跳成长链，或者在页面源代码里搜索 `var biz =` / `var appuin =` / `__biz=`。
- `__biz` 一般是一段 Base64，例如 `MzkzNzYzNzE3Mg==`，同一个公众号固定不变。

**专辑 / 合集 ID（`album_id`）**

- 在一篇文章里点底部或标题附近的「所属合集」，打开后地址类似：
  `https://mp.weixin.qq.com/mp/appmsgalbum?__biz=...&action=getalbum&album_id=4164560801283358741`
  其中 `album_id=` 后面的数字就是合集 ID。
- 也可以在文章页面源代码里搜索 `album_id`。一篇文章可能属于多个合集，所以可能出现多个 ID。
- 公众号主页上的「合集」入口，打开后也是同样的专辑链接。

注意：没有被放进任何合集的文章，只靠这个不用登录的接口可能拉不到。

## 限制（请先读）

- **只能覆盖公开专辑里的文章。** 公众号主页时间线接口 `profile_ext` 需要登录，未收入任何合集的推文可能漏掉。
- 列出全部专辑的接口（`action=getall`）同样需要登录，所以本工具靠文章页里的合集链接来发现专辑。如果某个合集从未出现在已打开的文章里，也会漏掉。
- 部分文章会碰到微信验证页，目录一般仍能拉到，正文可能失败。
- 这是微信的非正式页面接口，对方若改字段或加强验证，脚本可能需要更新。请控制请求频率，只用于公开内容的阅读和整理。

## 示例

`examples/droidHZ/` 是用本脚本从公众号 droidHZ 拉到的一份真实结果：

- `article-list.md` 和 `articles.json`：去重后 283 篇公开文章的完整目录（标题、日期、链接）
- `articles/`：约 52 篇正文 Markdown，不是全部 283 篇的全文

该目录里的 README 说明了账号、专辑和这份数据的范围。

`examples/哥飞/` 是用本脚本从公众号 哥飞 拉到的一份真实结果：

- `article-list.md` 和 `articles.json`：去重后 313 篇公开文章的完整目录（标题、日期、链接），来自 11 个公开专辑，时间范围 2022-12-06 至 2026-11-22（Asia/Shanghai）
- `articles/`：309 篇全文 Markdown，以及 4 篇付费墙试读

该目录里的 README 说明了账号、专辑和这份数据的范围。
