#!/usr/bin/env python3
"""并发验证链路：5 秒连接/读取超时；HLS 需能取到 playlist + 首个视频分片。"""
import asyncio, json, os, re, sys, time
import aiohttp
from urllib.parse import urljoin

WORK = os.path.dirname(os.path.abspath(__file__))
MODE = os.environ.get("VAL_MODE", "direct")  # direct | proxy
RESULT = os.path.join(WORK, f"validation_results_{MODE}.jsonl")
TIMEOUT_CONNECT = 5
TIMEOUT_READ = 8
CONCURRENCY = int(os.environ.get("VAL_CONC", "240"))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

TS_SYNC = b"\x47"


def parse_urls(body, base):
    urls = []
    for ln in body.decode("utf-8", "replace").splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            urls.append(urljoin(base, ln))
    return urls


def pick_variant(body, base):
    lines = body.decode("utf-8", "replace").splitlines()
    best, best_score = None, -1
    pending = ""
    if "#EXT-X-STREAM-INF" not in body.decode("utf-8", "replace"):
        return None
    for ln in lines:
        if ln.startswith("#EXT-X-STREAM-INF:"):
            pending = ln
        elif pending and ln.strip() and not ln.startswith("#"):
            score = 0
            m = re.search(r"RESOLUTION=(\d+)x(\d+)", pending)
            if m:
                score = int(m.group(1)) * int(m.group(2))
            m = re.search(r"BANDWIDTH=(\d+)", pending)
            if m:
                score = max(score, int(m.group(1)) // 1000)
            if score > best_score:
                best, best_score = urljoin(base, ln.strip()), score
            pending = ""
    return best


async def get_bytes(session, url, limit=65536):
    async with session.get(url) as resp:
        if resp.status not in (200, 206):
            return None, resp.status, ""
        ctype = resp.headers.get("Content-Type", "")
        data = await resp.content.read(limit)
        return data, resp.status, ctype


async def check(session, url, sem):
    async with sem:
        t0 = time.monotonic()  # 计时从真正获得并发槽位开始，排除排队等待
        try:
            data, status, ctype = await get_bytes(session, url)
            if not data:
                return (False, 0, f"empty/status-{status}")
            low = data.lstrip()
            text_head = data[:500].decode("utf-8", "replace")
            is_mpd = ("<MPD" in text_head or "dash+xml" in ctype.lower()) and "<Representation" in text_head
            if is_mpd:
                return (True, round((time.monotonic() - t0) * 1000), "dash-manifest")
            is_hls = low.startswith(b"#EXTM3U") or "#EXTM3U" in data[:200].decode("utf-8", "replace")
            if is_hls:
                variant = pick_variant(data, url)
                if variant:
                    d2, _, _ = await get_bytes(session, variant)
                    if not d2 or "#EXTM3U" not in d2[:200].decode("utf-8", "replace"):
                        return (False, 0, "master variant failed")
                    data = d2
                segs = parse_urls(data, url if not variant else variant)
                if not segs:
                    return (False, 0, "playlist no segment")
                seg = segs[0]
                d3, st3, _ = await get_bytes(session, seg)
                if not d3 or len(d3) < 64:
                    return (False, 0, f"segment-{st3}")
                # HLS 分片需有 TS 同步字节或 fmp4 box
                h3 = d3[:64].lower()
                if d3[:1] != TS_SYNC and b"ftyp" not in h3 and b"moov" not in h3 \
                        and "video" not in ctype.lower() and "octet-stream" not in ctype.lower() \
                        and b"<html" in h3[:16]:
                    return (False, 0, "segment not media")
            else:
                head = data[:16]
                html = b"<html" in data[:64].lower() or b"<!doctype" in data[:64].lower()
                looks_media = (head[:1] == TS_SYNC or b"ftyp" in head or b"moov" in head
                               or "video" in ctype.lower() or "mpegurl" in ctype.lower()
                               or "octet-stream" in ctype.lower() or "dash" in ctype.lower())
                if html or not looks_media:
                    return (False, 0, f"not-media({ctype[:24]})")
            latency = round((time.monotonic() - t0) * 1000)
            return (True, latency, "ok")
        except asyncio.TimeoutError:
            return (False, 0, "timeout")
        except Exception as e:
            return (False, 0, type(e).__name__)


async def main():
    channels = json.load(open(os.path.join(WORK, "channels_raw.json"), encoding="utf-8"))
    urls = []
    for ch in channels:
        for s in ch["sources"]:
            urls.append((ch["key"], s["url"]))
    only = os.environ.get("VAL_URLS")
    if only:
        wanted = set(json.load(open(only, encoding="utf-8")))
        urls = [(k, u) for k, u in urls if u in wanted]
    regions = os.environ.get("VAL_REGIONS")
    if regions:
        want = set(x.strip() for x in regions.split(",") if x.strip())
        urls = [(k, u) for k, u in urls if k[0] in want]
    print(f"[validate] {len(urls)} urls, concurrency={CONCURRENCY}", flush=True)

    done = set()
    if os.path.exists(RESULT):
        for ln in open(RESULT, encoding="utf-8"):
            try:
                done.add(json.loads(ln)["url"])
            except Exception:
                pass
    print(f"[validate] resume: {len(done)} already checked", flush=True)

    timeout = aiohttp.ClientTimeout(connect=TIMEOUT_CONNECT, sock_connect=TIMEOUT_CONNECT,
                                    sock_read=TIMEOUT_READ, total=30)
    connector = aiohttp.TCPConnector(limit=CONCURRENCY + 20, ttl_dns_cache=600,
                                     ssl=False, force_close=False)
    sem = asyncio.Semaphore(CONCURRENCY)
    ok = fail = 0
    state = {"n": 0}

    async def worker(session, sem, url, f):
        nonlocal ok, fail
        good, latency, reason = await check(session, url, sem)
        f.write(json.dumps({"url": url, "ok": good, "latency": latency,
                            "reason": reason}, ensure_ascii=False) + "\n")
        state["n"] += 1
        if state["n"] % 200 == 0:
            f.flush()
        if good:
            ok += 1
        else:
            fail += 1
        if state["n"] % 500 == 0:
            print(f"  checked {state['n']}/{len(pending)} ok={ok} fail={fail}", flush=True)

    print(f"[validate] mode={MODE} trust_env={MODE == 'proxy'}", flush=True)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout,
                                     headers={"User-Agent": UA}, trust_env=(MODE == "proxy")) as session:
        pending = [(k, u) for k, u in urls if u not in done]
        with open(RESULT, "a", encoding="utf-8") as f:
            await asyncio.gather(*[worker(session, sem, u, f) for _, u in pending],
                                 return_exceptions=True)
    print(f"[validate] done ok={ok} fail={fail}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
