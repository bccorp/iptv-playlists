#!/usr/bin/env python3
"""检查 m3u 结构：EXTINF/URL 配对、分组层级、备份排序、EPG 头。"""
import re, sys

for fn in sys.argv[1:]:
    lines = open(fn, encoding="utf-8").read().splitlines()
    if not lines or not lines[0].startswith("#EXTM3U"):
        print(fn, "BAD HEADER")
        continue
    pairs = 0
    bad = 0
    pending = False
    groups = set()
    lats = []
    for ln in lines[1:]:
        if ln.startswith("#EXTINF:"):
            if pending:
                bad += 1
            pending = True
            m = re.search(r'group-title="([^"]+)"', ln)
            if m:
                groups.add(m.group(1))
        elif ln.strip() and not ln.startswith("#"):
            if not pending:
                bad += 1
            else:
                pairs += 1
            pending = False
        m = re.match(r"# 备份\d+ \| 时延 (\d+)ms", ln)
        if m:
            lats.append(int(m.group(1)))
    print(f"{fn}: pairs={pairs} bad={bad} groups={len(groups)} "
          f"backups={len(lats)} max_backup_latency={max(lats) if lats else 0}ms "
          f"epg={'x-tvg-url' in lines[0]}")
