#!/usr/bin/env python3
"""汇聚 6 个 IPTV 信源 -> 4 个分区 m3u（验证前草稿）"""
import json, os, re, sys, time, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from geo import (CODE_REGION, COUNTRY_CN, REGION_CN, PROVINCES, CITY_PROVINCE,
                 EN_PROVINCE, KEYWORD_COUNTRY)

WORK = os.path.dirname(os.path.abspath(__file__))
JSD = "https://cdn.jsdelivr.net/gh/"

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

SOURCES = {
    "iptv-org": "iptv-org/iptv@master/",
    "vbskycn": "vbskycn/iptv@master/",
    "hujingguang": "hujingguang/ChinaIPTV@main/",
    "zhi35": "zhi35/iptv@master/",
    "CCSH": "CCSH/IPTV@main/",
    "akiralereal": "akiralereal/iptv@main/",
}


def fetch(path, retries=3, timeout=75):
    url = JSD + path
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            return urllib.request.urlopen(req, timeout=timeout).read()
        except Exception as e:
            last = e
            time.sleep(1 + i * 2)
    raise RuntimeError(f"fetch failed {path}: {last!r}")


def decode(raw):
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", errors="replace")


def ensure_repo_tree():
    """首次运行时从 GitHub API 生成仓库文件索引（CI 环境无预置文件）。"""
    path = os.path.join(WORK, "repo_tree.json")
    if os.path.exists(path):
        return
    branches = {
        "iptv-org": ("iptv-org/iptv", "master"),
        "vbskycn": ("vbskycn/iptv", "master"),
        "hujingguang": ("hujingguang/ChinaIPTV", "main"),
        "zhi35": ("zhi35/iptv", "master"),
        "CCSH": ("CCSH/IPTV", "main"),
        "akiralereal": ("akiralereal/iptv", "main"),
    }
    out = {}
    for key, (repo, branch) in branches.items():
        url = f"https://api.github.com/repos/{repo}/git/trees/{branch}?recursive=1"
        last = None
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers={**HEADERS, "Accept": "application/vnd.github+json"})
                d = json.load(urllib.request.urlopen(req, timeout=180))
                out[key] = {"paths": [x["path"] for x in d.get("tree", [])
                                      if x["path"].lower().endswith((".m3u", ".m3u8", ".txt"))]}
                print(f"  tree {repo}: {len(out[key]['paths'])} files", flush=True)
                break
            except Exception as e:
                last = e
                time.sleep(2 + attempt * 3)
        else:
            raise RuntimeError(f"cannot fetch tree {repo}: {last!r}")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)


def parse_m3u(text, origin, code=None):
    out = []
    extinf = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#EXTINF:"):
            info = line[len("#EXTINF:"):]
            if "," in info:
                attrs_part, name = info.rsplit(",", 1)
            else:
                attrs_part, name = info, ""
            attrs = dict(re.findall(r'([\w\-]+)="([^"]*)"', attrs_part))
            extinf = {"name": name.strip(), "attrs": attrs}
        elif line and not line.startswith("#") and extinf and line.lower().startswith(("http://", "https://")):
            e = dict(extinf)
            e["url"] = line
            e["origin"] = origin
            e["code"] = code
            out.append(e)
    return out


def quality_score(s):
    t = s.lower()
    if any(k in t for k in ("4k", "2160p", "uhd", "ultra hd")):
        return 4
    if any(k in t for k in ("1080p", "fhd", "full hd", "fullhd")):
        return 3
    if any(k in t for k in ("720p", "hd")):
        return 2
    return 1


def norm_name(s):
    s = re.sub(r"\[[^\]]*\]", "", s)
    s = re.sub(r"\([^)]*\)", "", s)
    s = re.sub(r"（[^）]*）", "", s)
    s = s.replace("❌", "").replace("✅", "").replace("✔", "")
    s = re.sub(r"[\s\-_·•、,，。/\\:：|]+", "", s)
    return s.lower()


def find_country(text):
    t = text.lower()
    for keys, region, country in KEYWORD_COUNTRY:
        for k in keys:
            if k in t:
                return region, country
    return None


def province_from(text):
    t = text.lower()
    # 英文字段（iptv-org）
    for en, prov in EN_PROVINCE.items():
        if en in t:
            return prov
    for zh, prov in PROVINCES.items():
        if zh in text:
            return prov
    return None


def city_from(text, province):
    hits = [(len(c), c) for c in CITY_PROVINCE if c in text and CITY_PROVINCE[c] == province]
    if not hits:
        return None
    hits.sort(reverse=True)
    return hits[0][1]


def make_group(region, code, name, group, origin):
    if region == "cn":
        prov = province_from(group) or province_from(name)
        if code in ("hk", "mo", "tw"):
            prov = COUNTRY_CN[code]
        if not prov and any(k in name for k in ("香港", "澳门", "台湾")):
            prov = province_from(name)
        if not prov and "港澳台" in group:
            # 名称无明确地区，放在港澳台/其他
            return "中国/中国港澳台/其他"
        if not prov:
            # 央视/卫视/分类频道
            if "卫星" in group or "卫视" in name or group == "卫视频道":
                sub = "卫视"
            elif "央视" in group or "CCTV" in name.upper() or group == "央视频道":
                sub = "央视"
            elif group and group not in ("默认", "Default", "其他"):
                sub = group[:12]
            else:
                sub = "央视与卫视"
            return f"中国/中央/{sub}"
        city = city_from(name, prov)
        if city:
            return f"中国/{prov}/{city}"
        return f"中国/{prov}/省级频道"
    # 洲/国两级
    country = COUNTRY_CN.get(code)
    if not country:
        found = find_country(name + " " + group)
        country = found[1] if found else "其他"
    return f"{REGION_CN[region]}/{country}"


def classify(e):
    code = (e.get("code") or "").lower().split("_")[0]
    region = CODE_REGION.get(code)
    text = e["name"] + " " + e["attrs"].get("group-title", "")
    if not region:
        if re.search(r"[\u4e00-\u9fff]", text):
            region = "cn"
        else:
            found = find_country(text)
            region = found[0] if found else None
    if not region:
        return None
    e["region"] = region
    e["group"] = make_group(region, code, e["name"], e["attrs"].get("group-title", ""), e["origin"])
    e["quality"] = quality_score(e["name"] + " " + " ".join(e["attrs"].values()) + " " + e["url"])
    e["key"] = (region, norm_name(e["name"]))
    return e


def main():
    ensure_repo_tree()
    tree = json.load(open(os.path.join(WORK, "repo_tree.json"), encoding="utf-8"))
    tasks = []

    # 1) iptv-org: 目标洲/国家的所有分国家文件
    for p in tree["iptv-org"]["paths"]:
        m = re.match(r"streams/([a-z]{2})(?:_[^/]*)?\.m3u$", p)
        if m and m.group(1) in CODE_REGION:
            tasks.append((p, "iptv-org", m.group(1)))

    # 2) 其余五个仓库
    for p in [
        "tv/iptv4.m3u", "tv/iptv6.m3u",
    ]:
        tasks.append((p, "vbskycn", None))
    for p in [
        "cnTV1_ALL.m3u8", "cnTV2_GuoNei.m3u8", "cnTV1_GuoJi.m3u8",
        "cnTV_AutoUpdate.m3u8", "Global.m3u8", "HongKong.m3u8",
        "Macao.m3u8", "TaiWan.m3u8", "Japan.m3u8", "southKorea.m3u8",
        "Singapore.m3u8", "UK.m3u8", "USA.m3u8", "Russia.m3u8",
        "otherCountry.m3u8",
    ]:
        tasks.append((p, "hujingguang", None))
    for p in ["live-china.m3u", "iptv.m3u"]:
        tasks.append((p, "zhi35", None))
    for p in ["live.m3u", "live_lite.m3u"]:
        tasks.append((p, "CCSH", None))
    for p in ["IPTV.m3u", "IPTV-overseas.m3u", "from-the-web/from-the-web.m3u"]:
        tasks.append((p, "akiralereal", None))

    print(f"[fetch] {len(tasks)} files from 6 repos", flush=True)
    entries = []
    failed = []
    with ThreadPoolExecutor(max_workers=16) as ex:
        futs = {ex.submit(fetch, SOURCES[src] + p): (p, src, code) for p, src, code in tasks}
        for i, fut in enumerate(as_completed(futs), 1):
            p, src, code = futs[fut]
            try:
                entries += parse_m3u(decode(fut.result()), src, code)
            except Exception as e:
                failed.append((src, p, str(e)[:100]))
            if i % 25 == 0:
                print(f"  fetched {i}/{len(tasks)}, entries={len(entries)}", flush=True)

    print(f"[parse] raw entries={len(entries)}, failed files={len(failed)}", flush=True)
    for f in failed[:20]:
        print("  FAILED", f)

    ok = [e for e in entries if classify(e)]
    print(f"[classify] kept={len(ok)} dropped={len(entries)-len(ok)}", flush=True)

    # 去重：URL 全局唯一；同频道多源聚合
    seen_urls = set()
    channels = defaultdict(list)
    region_count = defaultdict(int)
    for e in ok:
        u = e["url"].strip()
        if not u or u in seen_urls:
            continue
        seen_urls.add(u)
        channels[e["key"]].append(e)
        region_count[e["region"]] += 1

    print(f"[dedup] unique urls={len(seen_urls)}, channels={len(channels)}", flush=True)
    print("  by region:", dict(region_count), flush=True)

    # 写中间 JSON 供验证阶段使用
    payload = []
    for key, srcs in channels.items():
        srcs.sort(key=lambda x: (-x["quality"], x["origin"]))
        payload.append({
            "region": key[0], "name": srcs[0]["name"], "key": list(key),
            "group": srcs[0]["group"],
            "sources": [{
                "url": s["url"], "origin": s["origin"], "quality": s["quality"],
                "name": s["name"], "attrs": s["attrs"],
            } for s in srcs],
        })
    with open(os.path.join(WORK, "channels_raw.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print("[write] channels_raw.json", flush=True)


if __name__ == "__main__":
    main()
