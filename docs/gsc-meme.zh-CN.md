# meme.ransomz.dev：GSC 接入和关键词研究

## 2026-11-01 检查结果与边界

检查基线：main 提交 `e48989a832c32839e2e6cfacf8506cf5ece95964`。

- Git 跟踪文件只有 `config.example.json`，其中 `gsc: {}`；没有实际 `config.json`、凭据、报告或数据库。
- 原 `radar.yml` 只有在 `RADAR_ENABLED=true` 时运行，日任务读 `config.json`，凭据可来自本地文件或 Actions secret `GSC_TOKEN_JSON`。仅设置 secret、未配置 `gsc.site` 时，该模块仍关闭。
- GitHub Actions runs API 返回 `total_count: 0`。本次可访问的仓库没有现存运行记录可供验证。
- 当前工作环境没有实际配置文件、GSC token，也没有 `GSC_TOKEN_JSON`。连接浏览器超时，未能读取 GSC 会话。
- GitHub 连接器不支持 Secrets/Variables 管理接口；没有查看其内容或认定它们为空。外部电脑、服务器、cron 等也不在可核实范围内。

结论：**没有取得成功接入的证据，也无法断言所有外部环境均未接入**。实际属性名称和数据要通过下述授权/检查命令确认。`sc-domain:ransomz.dev`、`sc-domain:meme.ransomz.dev` 和 `https://meme.ransomz.dev/` 都只是可能的属性格式，不应预先选定。

## 本机完成一次只读授权

在你自己的电脑上运行，Google 同意页面也在该电脑打开；不要在云端终端启动后指望电脑浏览器回调到云端 localhost。

1. 在 Google Cloud Console 选一个项目，启用 **Google Search Console API**。
2. 在 Google Auth Platform 配置 OAuth 应用的受众和同意页面；若使用 External/Testing，把自己的 Google 账号添加为测试用户。仅申请 `https://www.googleapis.com/auth/webmasters.readonly`。使用对目标属性有访问权限的账号。
3. 创建类型为 **Desktop app（桌面应用）** 的 OAuth 客户端。下载 JSON，保存在克隆目录的 `.secrets/gsc_client.json`，不要放进聊天或 Git。
4. 从仓库根目录执行：

```bash
python -m pip install -r requirements-gsc.txt
mkdir -p .secrets
cp config.meme.example.json config.json
python scripts/gsc_auth.py .secrets/gsc_client.json --config config.json
python -m radar.gsc --config config.json --check
python -m radar.gsc --config config.json --out gsc-report.json
```

授权脚本只请求 readonly scope，登录后调用 `sites.list`，只允许选择账号真实可访问且覆盖目标 URL 的属性。若匹配多个，会要求你选择；若匹配不到，不写配置。可使用 `--site 'API返回的准确属性名'` 显式选择。

成功后，它将准确属性写进本地 `config.json`，将 refresh token 写到 `.secrets/gsc_token.json`，权限设为 `0600`。这两个文件均不提交。后续 `--check` 验证凭据刷新、属性权限和覆盖范围；采集命令成功并返回 API 结果才算数据链路验证完成。空结果也会记录窗口和行数；错误返回非零退出码，不冒充“没有新词”。

如果已有凭据和配置，先执行 `python -m radar.gsc --config config.json --check`，无需重新授权。旧凭据未记录 readonly scope 或包含更宽权限时，本实现要求重新只读授权。

External/Testing 状态的 refresh token 通常会在 7 天后过期（此 scope 不属于仅基本身份信息的例外）；长期无人值守前应按 Google 的受众、发布和验证要求处理，出现刷新失败时重新授权。不要把短期测试凭据当作永久接入。

## GitHub Actions（可选，手动运行）

新增 `GSC read-only check and discovery` 工作流，只读、不依赖 AIsa、不启动全套雷达、不发邮件。

1. 本机授权成功后，在仓库 Settings → Secrets and variables → Actions → **Secrets** 添加 `GSC_TOKEN_JSON`，值为 `.secrets/gsc_token.json` 的完整内容。不要设成普通 Variable。
2. 在 **Variables** 添加 `GSC_SITE`，值为授权程序确认的准确属性名；这是非机密信息。
3. 在 Actions 手动运行该工作流。它会验证并采集，生成 `gsc-report-运行ID` artifact（保留 7 天）。日志不打印查询正文、token 或报告内容。拥有仓库 Actions 读取权限的人可能下载 artifact；请按你的仓库访问范围使用。
4. 下载 `gsc-report.json` 供研究。没有把运行结果写回代码仓库。

工作流合并后才会在默认分支可见。本次没有设置真实 secret、没有启用原全套雷达，也没有假定该属性已经存在。

## 数据口径

- Web 搜索；GSC 的 `America/Los_Angeles` 日期；默认截止三天前，最近 3 天对比紧接之前 60 天，窗口不重叠。
- 默认 `dataState=final`；配置 `fresh: true` 可截止昨天、读取 provisional 数据，报告会标注。新数据会修订，不能与 finalized 数据混为稳定增长。
- 以 `query,page,date` 读取近期数据，分页抓取，汇总每个新查询的全部页面和逐日曝光；门槛是页面/日曝光加总至少 5，导出全部达标候选。
- 选择父域属性时，两个窗口都按页面 origin 过滤为 `https://meme.ransomz.dev/`，避免其他子域混入。
- “新增”仅表示在 API 返回的 60 天基线中未见，不代表历史首次出现。匿名查询、API 内部限制和低量数据缺失会造成遗漏；即使分页也不保证全量。
- 曝光、点击是该站数据，不能解释为关键词市场搜索量。按页面聚合的曝光也可能与属性汇总不同。本工具未输出市场搜索量、竞争难度分数或虚构排名。

## 取得真实数据后的筛选方法

先按搜索意图合并同义表达，再检查网站真实页面和目标国家/语言的搜索结果。每条研究记录至少保留：查询原文、窗口、总曝光/点击、daily、全部落地页、意图、覆盖结论、竞争证据、下一步。

| 维度 | 如何判断 | 研究动作 |
| --- | --- | --- |
| 搜索意图 | 找工具、找模板/素材、找教程，还是品牌导航；是否贴合本站实际功能 | 不匹配的词不因新曝光就建页 |
| 增长趋势 | 看 3 天分布是否集中单天；后续比较完整 7 天与此前 7 天 | 3 天仅标“早期信号”；基线为零不输出无限增长率 |
| 现有页面覆盖 | 查看每个落地页内容、标题、语言、功能和站点地图；一个查询可能落到多个页面 | 已有匹配页面优先优化；意图明显不同且功能真实存在时才考虑独立页 |
| 潜在竞争 | 在目标地区/语言查看 SERP 前列结果、工具完成度、专门页面、大品牌占比和结果形式 | 未检查 SERP 就标“未知”，不能用低曝光或长尾长度推算低竞争 |
| 研究优先级 | 业务匹配、重复曝光、页面可满足需求、SERP 有具体切入点 | 优先研究有证据的词；缺趋势/竞争证据的保留待验证 |

本次没有授权、没有真实 GSC 数据，所以没有关键词名单或排名。测试文件中的词和数字全是合成测试数据，不能用于 SEO 决策。

官方参考：
- https://developers.google.com/webmaster-tools/v1/sites/list
- https://developers.google.com/webmaster-tools/v1/searchanalytics/query
- https://developers.google.com/identity/protocols/oauth2/native-app
