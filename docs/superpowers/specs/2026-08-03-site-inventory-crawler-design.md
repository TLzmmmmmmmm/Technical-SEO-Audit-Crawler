# 网站资产清单爬虫设计规格

日期：2026-08-03

## 1. 目标

实现一个最小可行的单站点爬虫，从用户提供的首页开始，通过静态 HTML 中的 `<a href>` 发现站内 URL，生成网站资产清单 CSV。

程序不是通用爬虫框架。第一版不包含多线程、分布式抓取、登录、浏览器自动化、复杂断点恢复、数据库或图形界面。

## 2. 验收标准

- 能从首页自动发现内部页面。
- 不请求允许范围之外的网站。
- 每个规范化 URL 最多请求一次。
- 单个页面请求失败不会终止整个爬取。
- 遵守 robots.txt 的 `Allow` 和 `Disallow`。
- 所有已发现 URL 均可导出 CSV，包括被 robots 禁止或因限制未请求的 URL。
- 结束时明确说明自然完成或限制终止原因。

## 3. 技术选型与文件结构

使用 Python、Requests、Beautiful Soup 和 Python 标准库。项目保持最小结构：

```text
crawler.py
requirements.txt
README.md
tests/
  test_crawler.py
```

`crawler.py` 内部使用小函数分隔职责：

- `normalize_url()`：解析并规范化 URL。
- `is_allowed_host()`：判断允许主机。
- `load_robots()`：获取、解析并缓存 robots.txt。
- `request_once()`：执行限速、超时且禁止自动跳转的单次请求。
- `extract_links()`：从符合条件的 HTML 中提取链接和标题。
- `crawl()`：执行 BFS、去重和限制控制。
- `write_csv()`：导出 CSV。
- `main()`：处理命令行参数并输出结束摘要。

第一版不建立包级框架、插件系统、数据库或配置文件系统。运行期配置以 `crawler.py` 顶部常量提供。

## 4. 默认配置

```python
USER_AGENT = "LegacySiteInventoryBot/1.0"
REQUEST_DELAY = 0.5
REQUEST_TIMEOUT = 10
MAX_PAGES = 3000
MAX_DEPTH = 10
MAX_REDIRECTS = 5
MAX_HTML_BYTES = 5 * 1024 * 1024
RESPECT_ROBOTS_TXT = True
FOLLOW_INTERNAL_REDIRECTS = True
FOLLOW_EXTERNAL_REDIRECTS = False
RECORD_FIRST_SOURCE_ONLY = True
PARSE_HTML_ONLY = True
```

TLS 证书验证保持 Requests 默认开启。HTTP 请求不使用 TLS，因此该设置对当前 HTTP-only 目标没有影响；若发现 HTTPS URL，则正常验证证书。

## 5. 首页初始化与允许范围

1. 验证输入首页只使用 `http` 或 `https`。
2. 获取当前 origin 的 robots.txt，并检查首页是否允许访问。
3. 手动处理首页重定向，最多 5 次；每次请求目标前检查其 origin 的 robots.txt。
4. 首页初始化是唯一允许在站点范围尚未确定时改变主机的阶段。
5. 以首页重定向链的最终主机名建立允许主机集合：
   - 最终主机名；
   - 仅增加或删除 `www.` 的对应主机名。
6. 其他子域名或其他域名均视为外部，不访问。

裸域与 `www` 同属内部范围，但完整 URL 分别记录和去重。HTTP 与 HTTPS 也分别记录和去重。

首页初始化期间的每一次页面请求都计入 `MAX_PAGES`，并立即加入 `seen` 和 `results`。重定向目标保持深度 0，`source_url` 为前一个重定向 URL。最终首页响应直接用于提取链接，不在 BFS 开始后重复请求。若首页重定向超过 5 次或在建立允许范围前失败，则导出已经产生的记录并终止。

## 6. BFS 与去重

程序维护：

- `queue`：待处理项，包含 `url`、`source_url` 和 `crawl_depth`。
- `seen`：已发现的规范化 URL。
- `results`：按首次发现顺序保存全部 URL 及其结果。

用户输入的起始 URL 深度为 0 且 `source_url` 为空。普通链接目标的深度为当前页面加 1；重定向目标保持与重定向源相同的深度，并以重定向源作为首次来源。

URL 在加入队列或结果集时立即加入 `seen`，而不是等到发出请求时再加入，从而保证每个规范化 URL 最多请求一次。`source_url` 只保留首次发现来源。

## 7. URL 规范化

对每个 `<a href>`：

- 去除首尾空白。
- 使用当前页面 URL 转换相对地址。
- 只接受 `http` 和 `https`。
- 忽略空链接以及 `mailto:`、`tel:`、`javascript:`、`data:`。
- 删除 fragment。
- 将 scheme 和 hostname 转为小写。
- 删除默认端口 `:80` 和 `:443`，保留非默认端口。
- 将空路径规范为 `/`。
- 保留路径大小写和尾部 `/` 的区别。
- 保留普通查询参数。
- 删除参数名以 `utm_` 开头的参数，以及 `gclid`、`fbclid`、`msclkid`。
- 对剩余查询参数排序后用于去重。

第一版不处理 `<base href>`、canonical 标签、表单提交或 JavaScript 生成的 URL。

## 8. 请求与响应处理

程序单线程运行，复用一个 `requests.Session`。所有网络请求的开始时间至少间隔 0.5 秒，包括 robots.txt 请求。页面请求使用 `GET`、10 秒连接/读取超时和 `allow_redirects=False`。

每个 URL 按以下规则处理：

- `2xx` 且 MIME 类型为 HTML：读取并解析标题和 `<a href>`。
- `2xx` 非 HTML：记录状态和类型，不下载或解析完整正文。
- `3xx`：不解析正文，只处理 `Location`。
- `3xx` 内部目标：记录重定向，并将目标以相同深度加入队列。
- `3xx` 外部目标：记录目标和 `external_redirect`，不访问。
- `4xx/5xx`：记录状态和类型，不解析错误页面。
- 请求异常：记录简短错误，继续处理队列。

HTML 正文最多读取 5 MiB。超过限制时记录 `html_too_large`，不解析后续内容。只有 `2xx HTML` 可以继续发现链接。

## 9. robots.txt

robots 规则按 origin 缓存，因此区分 scheme、hostname 和非默认端口。同一 origin 在一次运行中最多获取一次最终 robots 内容。

- `200`：解析所有可识别规则，并遵守匹配当前爬虫 User-Agent 的 `Allow` / `Disallow`。
- `4xx`：视为 robots.txt 不存在，允许继续。
- `5xx`、网络错误或超时：视为暂时不可达，记录警告并停止整个爬取。
- robots.txt 重定向：手动处理，最多 5 次。
- robots.txt 重定向至允许主机之外：不访问，视为不可达并停止。在首页允许范围建立前，仅当前候选主机及其增删 `www.` 的对应主机可作为 robots 重定向目标。
- 解析错误：使用仍可解析的规则。

被 robots.txt 禁止的已发现 URL 进入 CSV，但不发出页面请求；其 `error` 为 `robots_disallowed`。

robots.txt 暂时不可达时，触发检查的 URL 标记 `robots_unreachable`；队列中其他尚未请求的记录标记 `crawl_stopped_robots_unreachable`，随后导出 CSV 并停止。

## 10. 限制语义

`MAX_PAGES` 统计实际页面请求尝试，包括收到重定向响应的请求和最终失败的请求；robots.txt 请求不计入该数字，但仍受请求间隔限制。

- 深度 0 至 10 的 URL 可进入请求队列。
- 从深度 10 页面发现的深度 11 URL 不请求，但保留结果并标记 `max_depth_exceeded`。
- 达到 3,000 次页面请求后停止发出页面请求。
- 队列中剩余的已发现 URL 继续导出，并标记 `max_pages_reached`。

## 11. CSV 输出

CSV 使用 UTF-8 with BOM，按 URL 首次发现顺序输出以下字段：

```text
url
status_code
final_url
title
source_url
crawl_depth
content_type
error
```

字段规则：

- 用户输入的起始 URL 的 `source_url` 为空。
- `title` 去除首尾空白，并将连续空白合并为一个空格。
- 普通响应的 `final_url` 等于 `url`。
- 重定向响应的 `final_url` 为解析后的 `Location`。
- 请求失败或未请求时，`status_code` 为空。
- 非 HTML 响应记录 `content_type`，但不解析。
- `error` 使用简短稳定标识，例如 `timeout`、`connection_error`、`tls_error`、`invalid_redirect`、`external_redirect`、`robots_disallowed`、`robots_unreachable`、`crawl_stopped_robots_unreachable`、`max_depth_exceeded`、`max_pages_reached` 和 `html_too_large`。

## 12. 终止行为与摘要

主循环在以下条件之一满足时结束：

- 队列自然清空。
- 页面请求数达到 3,000。
- robots.txt 暂时不可达。
- 用户按下 `Ctrl+C`。

无论以何种原因结束，都导出当前已发现的全部结果。用户中断时，队列中尚未请求的记录标记 `interrupted`。控制台输出：

```text
completion_reason
discovered_urls
requested_urls
successful_responses
redirects
request_failures
robots_disallowed
depth_limited
page_limited
csv_path
```

`completion_reason` 至少支持 `queue_exhausted`、`max_pages_reached`、`robots_unreachable`、`start_url_failed`、`start_url_redirect_limit` 和 `interrupted`。

## 13. 测试与验证

使用 Python 标准库 `unittest` 和本地 HTTP 测试服务器，不增加测试框架依赖。测试时将请求间隔覆盖为 0。

自动测试覆盖：

- 首页发现相对和绝对内部链接。
- fragment 和追踪参数规范化。
- HTTP/HTTPS、裸域/`www` 分别记录。
- 同一规范化 URL 最多请求一次。
- 外部链接和外部重定向不被请求。
- 内部重定向目标入队且深度不增加。
- robots `Allow` / `Disallow`、404 和临时失败行为。
- 非 HTML 资源记录但不解析。
- 404、500 或连接失败不终止队列。
- 最大深度和最大页面数的记录行为。
- CSV 字段、首次来源和 BFS 深度。
- 各种结束原因及摘要。

代码和自动测试完成后，只有在用户提供实际首页并明确允许执行时，才对真实网站进行小规模验证。

## 14. 明确不在第一版范围内

- 多线程、异步或分布式抓取。
- 登录、Cookie 持久化或浏览器自动化。
- sitemap.xml 自动发现。
- JavaScript 渲染或动态链接发现。
- 表单提交、canonical 合并、`<base href>` 支持。
- 数据库、断点恢复、可视化界面或通用插件系统。
