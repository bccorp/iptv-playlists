# IPTV Playlists

每天自动汇聚、清洗并刷新 IPTV 播放列表，生成可直接导入 IPTV 播放器的 m3u 订阅地址。

## 功能

- **汇聚 6 个上游信源**：`iptv-org/iptv`、`vbskycn/iptv`、`hujingguang/ChinaIPTV`、`zhi35/iptv`、`CCSH/IPTV`、`akiralereal/iptv`
- **按地区分流**：`cn.m3u`（中国）、`asia.m3u`（亚洲）、`eu.m3u`（欧洲）、`us.m3u`（北美），其他地区丢弃
- **两级树状目录**：国内按「省 / 市」，国外按「洲 / 国家」，通过 `group-title` 中的 `/` 分隔
- **多源备份与排序**：同一频道多个线路保留为备份；具备 4K / 1080p / FHD 标签的源优先
- **链路验证**：连接超时 5 秒判为失效；HLS 要求能取到 playlist 和首个视频分片，DASH 要求能取到 MPD；失效线路剔除，剩余线路按时延重排
- **EPG 嵌入**：在 `#EXTM3U` 中写入 `x-tvg-url`；国内使用 112114 + epg.pw 大陆/港澳台 XMLTV，并按频道名把 `tvg-id` 预对齐到 EPG channel id（约 65% 线路可自动匹配），境外使用 epgshare01 分区域 XMLTV

## 文件说明

| 路径 | 说明 |
| --- | --- |
| `cn.m3u` / `asia.m3u` / `eu.m3u` / `us.m3u` | 最终订阅文件，由 workflow 自动生成并提交 |
| `validated_urls.json.gz` | 已实测可用的 URL 白名单（基线模式使用） |
| `scripts/aggregate.py` | 抓取 6 个仓库、分类去重、生成两级分组 |
| `scripts/validate.py` | 并发链路验证，记录时延 |
| `scripts/finalize.py` | 过滤失效线路、按时延排序、嵌入 EPG、输出 m3u |
| `scripts/geo.py` | 国家 / 省 / 市映射表 |
| `scripts/check_m3u.py` | 输出文件结构自检 |
| `.github/workflows/update.yml` | 定时任务与自动提交 |

## 自动刷新

workflow 在 **UTC 09:00（北京时间 17:00）** 每天运行一次；推送 `scripts/**`、workflow、`validated_urls.json.gz` 或 `requirements.txt` 时也会自动触发一次。自动提交带 `[skip ci]`，不会造成循环。

由于 GitHub 官方 runner 在境外，用它验证中国内地 IPTV 源会误杀大量可用线路，因此默认使用 `baseline` 模式：只输出白名单中已验证过的链接。可在仓库 Variables 中设置 `VALIDATION_MODE` 切换：

- `baseline`（默认）：使用 `validated_urls.json.gz` 白名单，最稳
- `overseas`：只验证亚洲 / 欧洲 / 北美，中国源不验证
- `full`：全量验证（中国源会大量误杀）
- `skip`：不验证，会保留失效链接

若需每天全量准确验证，建议把 workflow 的 `runs-on` 改为部署在中国大陆的 `self-hosted` runner。

## 订阅地址

仓库为公开仓库，播放器直接填 raw 链接即可（把 `<owner>` / `<repo>` 替换为实际值）：

```
https://raw.githubusercontent.com/<owner>/<repo>/main/cn.m3u
https://raw.githubusercontent.com/<owner>/<repo>/main/asia.m3u
https://raw.githubusercontent.com/<owner>/<repo>/main/eu.m3u
https://raw.githubusercontent.com/<owner>/<repo>/main/us.m3u
```

若播放器缓存，可在末尾追加 `?t=20260101` 之类的参数强制刷新。

## xTeVe 自动匹配

在 xTeVe 的 XMLTV 菜单添加以下源（与 m3u 内嵌的 `x-tvg-url` 一致），未分配频道会自动按 `tvg-id == channel id` 匹配：

```
https://gitee.com/taksssss/tv/raw/main/epg/112114.xml.gz
https://gitee.com/taksssss/tv/raw/main/epg/epgpw_cn.xml.gz
https://gitee.com/taksssss/tv/raw/main/epg/epgpw_hk.xml.gz
https://gitee.com/taksssss/tv/raw/main/epg/epgpw_tw.xml.gz
```

已有 active 映射不会被覆盖；如需让旧频道重新匹配，在 Mapping → Bulk Edit 中取消激活并清空分配，再触发更新重建。`tvg-id-orig` 保留了原始 id 便于核对。

## 说明

- GitHub 定时任务可能延迟数分钟；仓库连续 60 天无活动会自动停用调度。
- raw 链接有约几分钟的 CDN 缓存，刚刷新后可能不会立刻生效。
- 本项目仅聚合公开信源，不提供任何内容托管；请遵守所在地法律法规。
