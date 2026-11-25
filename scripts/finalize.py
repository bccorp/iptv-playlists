#!/usr/bin/env python3
"""按验证结果生成最终 4 份 m3u：删除失效链路、按时延排序、嵌入 EPG。"""
import json, os, gzip, datetime, collections, re

WORK = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(WORK)
OUT = {
    "cn": os.path.join(ROOT, "cn.m3u"),
    "asia": os.path.join(ROOT, "asia.m3u"),
    "eu": os.path.join(ROOT, "eu.m3u"),
    "us": os.path.join(ROOT, "us.m3u"),
}
EPG_BASE = "https://epgshare01.online/epgshare01/"
EPG = {
    "cn": ("https://ghfast.top/https://github.com/CCSH/IPTV/raw/refs/heads/main/e.xml.gz,"
           "https://cdn.jsdelivr.net/gh/CCSH/IPTV@main/e.xml.gz," + EPG_BASE + "epg_ripper_HK1.xml.gz"),
    "asia": ",".join(EPG_BASE + f"epg_ripper_{x}.xml.gz" for x in
                     ["JP1", "KR1", "HK1", "SG1", "IN1", "TR1", "TH1", "ID1", "PH1", "VN1", "MY1"]),
    "eu": ",".join(EPG_BASE + f"epg_ripper_{x}.xml.gz" for x in
                   ["UK1", "DE1", "FR1", "ES1", "IT1", "NL1", "PL1", "SE1", "NO1", "FI1", "DK1", "BE2", "CZ1", "GR1", "CH1", "AT1"]),
    "us": ",".join(EPG_BASE + f"epg_ripper_{x}.xml.gz" for x in
                   ["US2", "US_SPORTS1", "CA2", "MX1"]),
}
TITLE = {"cn": "中国电视直播", "asia": "亚洲电视直播", "eu": "欧洲电视直播", "us": "北美电视直播"}
QLABEL = {4: "4K", 3: "1080p/FHD", 2: "HD", 1: "SD"}

# EPG id 映射：把 m3u 的 tvg-id 改写为 EPG 的 channel id，提升播放器自动匹配率
COUNTRY_SUFFIX = (r"(us2|us|uk|ca2|ca|mx1|mx|de|fr|es|it|nl|pl|se|no|fi|dk|cz|gr|ch|at|"
                  r"hk|jp|kr|sg|in|tr|th|id|vn|my|au|br|ru|ua)$")
QUALITY_TOKEN = r"(hd|sd|fhd|uhd|4k|hevc)"

EPG_MAP = {}
try:
    _map_path = os.path.join(WORK, "epg_id_map.json")
    if os.path.exists(_map_path):
        EPG_MAP = json.load(open(_map_path, encoding="utf-8"))
except Exception as e:
    print(f"[final] cannot load epg_id_map.json: {e!r}")


def esc(s):
    return str(s or "").replace('"', "'").replace("\n", " ")


def lookup_epg_id(region, candidates):
    """按候选名(tvg-id/频道名)查 EPG channel id，命中返回 id，否则 None。"""
    table = EPG_MAP.get(region)
    if not table:
        return None
    for raw in candidates:
        if not raw:
            continue
        s = str(raw).strip().lower()
        s = re.sub(r"@[a-z0-9_.\-]+$", "", s)
        base = re.sub(r"[^a-z0-9]+", "", s)
        keys = [base, re.sub(QUALITY_TOKEN, "", base)]
        c = re.sub(r"[^a-z0-9]+", "", re.sub(COUNTRY_SUFFIX, "", s))
        keys += [c, re.sub(QUALITY_TOKEN, "", c)]
        for k in keys:
            if k and k in table:
                return table[k]
    return None


def load_validation():
    best = {}
    for mode in ("proxy", "direct"):
        path = os.path.join(WORK, f"validation_results_{mode}.jsonl")
        if not os.path.exists(path):
            continue
        for line in open(path, encoding="utf-8"):
            try:
                d = json.loads(line)
            except Exception:
                continue
            if not d.get("ok"):
                continue
            u = d["url"]
            lat = int(d.get("latency") or 0)
            if u not in best or lat < best[u]:
                best[u] = lat
    return best


def load_whitelist():
    path = os.environ.get("VALID_URLS_FILE")
    if not path or not os.path.exists(path):
        return None
    if path.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return set(json.load(f))
    return set(json.load(open(path, encoding="utf-8")))


def load_skipped_regions():
    if os.environ.get("SKIP_VALIDATION") == "1":
        return {"cn", "asia", "eu", "us"}
    path = os.path.join(WORK, "validation_skipped_regions.json")
    if os.path.exists(path):
        return set(json.load(open(path, encoding="utf-8")))
    return set()


def main():
    channels = json.load(open(os.path.join(WORK, "channels_raw.json"), encoding="utf-8"))
    best = load_validation()
    whitelist = load_whitelist()
    skipped = load_skipped_regions()
    print(f"[final] validated usable urls: {len(best)}, whitelist: "
          f"{len(whitelist) if whitelist is not None else 'off'}, skipped: {sorted(skipped)}")

    kept = collections.defaultdict(list)
    dropped_channels = 0
    dropped_sources = 0
    for ch in channels:
        srcs = []
        for s in ch["sources"]:
            if whitelist is not None and s["url"] not in whitelist:
                dropped_sources += 1
                continue
            if s["url"] in best:
                s2 = dict(s)
                s2["latency"] = best[s["url"]]
                srcs.append(s2)
            elif ch["region"] in skipped or whitelist is not None:
                s2 = dict(s)
                s2["latency"] = 9999
                srcs.append(s2)
            else:
                dropped_sources += 1
        if not srcs:
            dropped_channels += 1
            continue
        srcs.sort(key=lambda x: (x["latency"], -x["quality"]))
        kept[ch["region"]].append((ch, srcs))

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    stats = {}
    for region, items in kept.items():
        items.sort(key=lambda x: (x[0]["group"], x[0]["name"]))
        path = OUT[region]
        nsrc = sum(len(s) for _, s in items)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(f'#EXTM3U x-tvg-url="{EPG[region]}"\n')
            f.write(f"# {TITLE[region]} — {region}.m3u\n")
            f.write(f"# 生成时间: {now}\n")
            f.write(f"# 信源: iptv-org/iptv, vbskycn/iptv, hujingguang/ChinaIPTV, zhi35/iptv, CCSH/IPTV, akiralereal/iptv\n")
            f.write(f"# 目录: group-title 采用两级路径，国内=省/市，国外=洲/国（以 / 分隔，播放器可显示为树状分组）\n")
            if whitelist is not None:
                line_note = "仅保留已验证白名单链接，按画质排序"
            elif region in skipped:
                line_note = "未做链路验证（CI 跳过验证模式），线路按画质排序"
            else:
                line_note = "已按实测时延升序排列，失效链路(连接超时5秒)已剔除"
            f.write(f"# 线路: 同一频道首个为首选，其后为备份；{line_note}\n")
            f.write(f"# EPG: {EPG[region]}\n")
            f.write(f"# 统计: {len(items)} 个频道 / {nsrc} 条有效线路\n\n")
            last_group = None
            mapped = 0
            for ch, srcs in items:
                if ch["group"] != last_group:
                    f.write(f"\n# ==================== {ch['group']} ====================\n")
                    last_group = ch["group"]
                for i, s in enumerate(srcs):
                    if i:
                        f.write(f"# 备份{i} | 时延 {s['latency']}ms | 画质 {QLABEL.get(s['quality'], 'SD')} | 来源 {s['origin']}\n")
                    a = s.get("attrs", {})
                    name = s.get("name") or ch["name"]
                    orig_id = a.get("tvg-id") or ch["name"]
                    # 用 tvg-id、频道名、源名联合查询 EPG channel id
                    epg_id = lookup_epg_id(region, [orig_id, name, s.get("name"), ch["name"]])
                    tvg_id = epg_id or orig_id
                    if epg_id:
                        mapped += 1
                    tvg_logo = a.get("tvg-logo", "")
                    extra = f' tvg-id-orig="{esc(orig_id)}"' if epg_id and orig_id != epg_id else ""
                    f.write(f'#EXTINF:-1 tvg-id="{esc(tvg_id)}" tvg-name="{esc(name)}" '
                            f'tvg-logo="{esc(tvg_logo)}" group-title="{esc(ch["group"])}"{extra},{name}\n')
                    f.write(s["url"] + "\n")
        stats[region] = {"channels": len(items), "sources": nsrc,
                         "epg_mapped": mapped,
                         "epg_mapped_pct": round(100 * mapped / nsrc, 1) if nsrc else 0,
                         "file": path}
        print(f"  {region}: {len(items)} channels / {nsrc} sources -> {path} "
              f"(EPG mapped {mapped}/{nsrc} = {stats[region]['epg_mapped_pct']}%)")

    stats["_meta"] = {
        "generated": now,
        "candidate_channels": len(channels),
        "dropped_channels_no_valid_source": dropped_channels,
        "dropped_invalid_sources": dropped_sources,
        "validated_urls": len(best),
    }
    json.dump(stats, open(os.path.join(WORK, "final_stats.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print("[final]", stats["_meta"])


if __name__ == "__main__":
    main()
