# 网站资源与可索引性审计升级设计规格

日期：2026-08-24

## 1. 目标

把现有单站点 URL 清单爬虫升级为部署前技术 SEO 检查工具。程序仍从首页执行单线程 BFS，但除 `<a href>` 外，还记录页面实际引用的图片、脚本、样式、字体和媒体资源，并为 HTML、PDF 和图片给出可解释的索引资格判断。

`indexable` 表示根据本次 HTTP 响应、robots.txt、通用 robots 指令和 canonical 得出的技术审计结果，不承诺搜索引擎一定收录页面。

## 2. CSV 字段

CSV 使用 UTF-8 with BOM，字段顺序固定为：

```text
url
status_code
final_url
title
canonical_url
canonical_self_reference
canonical_warning
meta_robots
x_robots_tag
source_url
source_tag
source_attribute
link_rel
discovery_count
crawl_depth
content_type
resource_type
indexable
indexability_reason
error
```

删除候选字段 `meta_description`、`h1` 和 `word_count`。`error` 只记录抓取层问题；SEO 判断写入 `indexability_reason`，非阻断 canonical 问题写入 `canonical_warning`。

## 3. URL 发现与来源

HTML 解析支持 `<a href>`、`<img src/srcset>`、`<script src>`、`<source src/srcset>`，以及关系为 `stylesheet`、`icon`、`shortcut icon`、`apple-touch-icon`、`mask-icon`、`manifest`、`preload` 或 `modulepreload` 的 `<link href>`。

`canonical`、`alternate`、`preconnect`、`dns-prefetch`、`next`、`prev`、`author` 和 `license` 不作为普通资源发现。canonical 只作为当前 HTML 文档的 SEO metadata。

`srcset` 拆分候选项并去掉 `400w`、`1x` 等 descriptor。`source_url`、`source_tag`、`source_attribute` 和 `link_rel` 只保存第一次发现来源。`discovery_count` 按引用出现次数累计，同一页面内重复标签也分别计数。起始 URL 和重定向目标的初始计数为 1。每个规范化 URL 仍最多请求一次。

## 4. 内外部资源

内部发现项加入 GET 请求队列。只有成功的 HTML 响应继续解析并发现下一层 URL；其他资源只记录，不递归解析。

外部 `<a href>` 继续忽略。外部嵌入资源记录但绝不请求：响应字段留空，`resource_type` 根据上下文或扩展名推断，`indexable=N/A`，`indexability_reason=External resource not evaluated`，`error=external_resource_not_requested`。重复引用只增加计数。

## 5. URL 与 canonical 规范化

普通 URL 去重继续删除 tracking 参数和 fragment。查询参数按参数名稳定排序；同名参数的多个值保持原始相对顺序且不去重。

canonical 相对 `final_url` 转为绝对 URL。`canonical_url` 保留普通参数和 tracking 参数，只移除 fragment；等价比较使用专用规范化值，忽略 tracking 参数和 fragment，并与规范化后的 `final_url` 比较。

比较保留 HTTP/HTTPS、裸域/`www`、路径大小写和尾部 `/` 的差异，删除默认端口并将 scheme/hostname 小写。canonical 原始值含 tracking 参数或 fragment 时，分别向 `canonical_warning` 添加 `Tracking parameters present` 或 `Fragment present`，多个 warning 用 `; ` 连接。

## 6. 多 canonical

- 缺失：`canonical_self_reference=N/A`，不阻止索引；
- 全部等价并指向 `final_url`：`YES`；
- 指向其他 URL：`NO`；
- 多个 canonical 指向不同 URL：`NO`，原因 `Conflicting canonical tags`；
- 多个 canonical 增加 warning `Multiple canonical tags`；
- 多个相同 canonical 时保存第一次出现的值；多个不同值时用 `; ` 合并；
- 无法解析的 canonical 是 blocker，原因 `Invalid canonical URL`。

## 7. Resource Type

优先根据响应 `Content-Type` 分类为：

```text
html
pdf
image
css
javascript
font
json
media
other
unknown
```

分别覆盖 HTML、PDF、`image/*`、CSS、JavaScript、字体、JSON、音视频、其他已知 MIME 和无信息。未请求或无 Content-Type 时按 `link as`、标签/rel、文件扩展名、`unknown` 的顺序推断。

## 8. Indexability

只解析通用 `<meta name="robots">` 和通用 `X-Robots-Tag`；不处理 agent-specific 指令。存在多个通用 meta robots 时按文档顺序以 `; ` 合并并检查全部内容。`noindex` 按大小写不敏感的完整 directive token 匹配。

HTML 为 `YES` 的条件是状态 200、未被 robots.txt 禁止、meta/X-Robots-Tag 都不含 `noindex`，且 canonical 缺失或指向 `final_url`。self canonical 原因是 `OK`；canonical 缺失原因是 `Canonical missing`。

PDF 为 `YES` 的条件是状态 200、未被 robots.txt 禁止且 X-Robots-Tag 不含 `noindex`。图片规则相同，正常原因是 `Image resource allowed`。PDF 和图片不要求 canonical。

其他类型使用 `indexable=N/A` 与 `indexability_reason=Resource type not evaluated`。外部嵌入资源使用 `N/A`。被 robots.txt 禁止的 HTML/PDF/图片使用 `NO` 和 `Blocked by robots.txt`；其他资源仍使用 `N/A` 并保留 `error=robots_disallowed`。

多个 blocker 按 HTTP 状态、robots.txt、X-Robots-Tag、meta robots、canonical 的顺序用 `; ` 合并。

状态不是 200 时使用 `HTTP status <code>`；因未请求或请求失败而没有状态时，对可评估资源使用 `HTTP status unavailable`。重定向源行按其 3xx 状态为 `NO`，重定向目标拥有独立结果行。

## 9. 非目标

- 不解析 CSS 内部的 `url(...)` 或 `@import`；
- 不执行 JavaScript；
- 不解析 sitemap；
- 不请求外部嵌入资源；
- 不提供搜索引擎特定判断；
- 不承诺真实搜索引擎收录；
- 不增加并发、数据库、GUI 或通用插件框架。

## 10. 测试与验证

继续使用 `unittest` 和本地 HTTP 服务器。覆盖稳定查询排序、重复参数、srcset、link rel 筛选、来源与计数、外部资源不请求、Content-Type 分类、仅 HTML 递归、meta/X-Robots-Tag、canonical 矩阵、多 blocker、robots 禁止、CSV 字段顺序、CLI 回归和现有测试。自动测试不得访问公网。
