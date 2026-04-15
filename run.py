#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Future - 一站式自动化脚本
启动后自动执行：检查邮箱 → 提取片名 → 搜索 → 发送结果
"""

import os
import sys
import re
import imaplib
import email
import time
import json
import logging
import smtplib
import ssl
from email.header import decode_header
from email.utils import formataddr, parseaddr, formatdate
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

# ============================================================
# 配置
# ============================================================
IMAP_SERVER = os.environ.get("IMAP_SERVER", "imap.qq.com")
IMAP_PORT = int(os.environ.get("IMAP_PORT", "993"))
EMAIL_ACCOUNT = os.environ.get("EMAIL_ACCOUNT", "3029308562@qq.com")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "")

SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "3029308562@qq.com")
RECEIVER_EMAIL = os.environ.get("RECEIVER_EMAIL", "3029308562@qq.com")
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.qq.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
MAX_ROUNDS = int(os.environ.get("MAX_ROUNDS", "3"))
INTERVAL = int(os.environ.get("INTERVAL", "30"))

PROCESSED_FILE = os.environ.get("PROCESSED_FILE", "/tmp/future_processed.json")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# Serper API (Google 搜索替代)
SERPER_API_KEY = os.environ.get("SERPER_API_KEY", "")

SEARCH_ENGINES = [
    # ===== 国内引擎（中文搜索词）- 主要依赖 =====
    {"name": "360搜索", "search_url": "https://www.so.com/s?q={query}",
     "result_selector": "li.res-list", "title_selector": "h3 a", "link_selector": "h3 a[href]",
     "desc_selector": "div.res-desc", "queries": ["zh"]},
    {"name": "搜狗", "search_url": "https://www.sogou.com/web?query={query}",
     "result_selector": "div.vrwrap, div.rb", "title_selector": "h3 a", "link_selector": "h3 a[href], a.title[href]",
     "desc_selector": "div.str_info, p.str_time_info", "queries": ["zh"]},
    {"name": "百度", "search_url": "https://www.baidu.com/s?wd={query}&rn=30",
     "result_selector": "div.result, div.c-container", "title_selector": "h3 a, a.c-font-large",
     "link_selector": "h3 a[href], a[href]", "desc_selector": "div.c-abstract, span.content-right_8Zs40", "queries": ["zh"]},
]

# Serper API 搜索（英文词）
SERPER_QUERIES = [
    "{keyword} free watch online",
    "{keyword} free streaming",
]


FREE_KEYWORDS = ["免费观看", "免费在线", "免费看", "在线观看", "在线看", "高清在线", "完整版", "全集",
                  "无广告", "免费播放", "在线播放", "高清播放", "无删减", "free online", "streaming"]
PRIORITY_DOMAINS = ["bilibili.com", "v.qq.com", "youku.com", "iqiyi.com", "mgtv.com", "sohu.com", "1905.com", "cctv.com"]
BLOCKED_DOMAINS = ["taobao.com", "tmall.com", "jd.com", "pinduoduo.com", "douyin.com"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ============================================================
# 邮件解析
# ============================================================
def decode_str(s):
    if not s:
        return ""
    parts = decode_header(s)
    return "".join(
        p.decode(c or "utf-8", errors="replace") if isinstance(p, bytes) else p
        for p, c in parts
    )


def parse_command(subject):
    m = re.match(r"^(?:搜|搜索)\s*[:：]\s*(.+?)(?:\s+(\d+)[轮圈])?\s*(持续)?$", subject.strip())
    if not m:
        return None, 5
    kw = m.group(1).strip()
    if len(kw) < 1:
        return None, 5
    rounds = int(m.group(2)) if m.group(2) else (36 if m.group(3) else 5)
    return kw, rounds


# ============================================================
# 检查邮箱
# ============================================================
def check_emails():
    processed = set()
    if os.path.exists(PROCESSED_FILE):
        try:
            processed = set(json.load(open(PROCESSED_FILE)))
        except Exception:
            pass

    commands = []
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
        mail.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
        mail.select("INBOX")
        _, msgs = mail.search(None, "UNSEEN")

        for eid in msgs[0].split():
            eid_str = eid.decode()
            if eid_str in processed:
                continue
            try:
                _, data = mail.fetch(eid, "(RFC822)")
                msg = email.message_from_bytes(data[0][1])
                subject = decode_str(msg.get("Subject", ""))
                keyword, rounds = parse_command(subject)
                if keyword:
                    logger.info(f"发现搜索命令: [{keyword}] {rounds}轮")
                    commands.append((keyword, rounds))
                mail.store(eid, "+FLAGS", "\\Seen")
                processed.add(eid_str)
            except Exception:
                pass

        mail.logout()
    except Exception as e:
        logger.error(f"邮箱连接失败: {e}")

    json.dump(list(processed), open(PROCESSED_FILE, "w"))
    return commands


# ============================================================
# 搜索
# ============================================================
def search_engine(query, engine):
    url = engine["search_url"].format(query=quote(query))
    results = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20, verify=False)
        resp.encoding = resp.apparent_encoding or "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")
        for item in soup.select(engine["result_selector"]):
            try:
                title_el = item.select_one(engine["title_selector"])
                link_el = item.select_one(engine["link_selector"])
                desc_el = item.select_one(engine["desc_selector"])
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)
                link = link_el.get("href", "") if link_el else ""
                desc = desc_el.get_text(strip=True) if desc_el else ""
                if title and link:
                    results.append({"title": title, "link": link, "description": desc[:300], "source_engine": engine["name"]})
            except Exception:
                continue
    except Exception as e:
        logger.warning(f"{engine['name']} 搜索失败: {e}")
    return results


def search_serper(keyword):
    """通过 Serper API 搜索 Google 结果"""
    if not SERPER_API_KEY:
        return []
    results = []
    for q_tpl in SERPER_QUERIES:
        q = q_tpl.format(keyword=keyword)
        try:
            resp = requests.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
                json={"q": q, "num": 10, "hl": "en"},
                timeout=15,
            )
            if resp.status_code != 200:
                logger.warning(f"Serper API 失败: HTTP {resp.status_code}")
                continue
            data = resp.json()
            for item in data.get("organic", []):
                title = item.get("title", "")
                link = item.get("link", "")
                desc = item.get("snippet", "")
                if title and link:
                    results.append({"title": title, "link": link, "description": desc[:300], "source_engine": "Google(Serper)"})
            logger.info(f"Serper/API [{q[:15]}...]: {len(data.get('organic', []))} 条")
        except Exception as e:
            logger.warning(f"Serper API 失败: {e}")
    return results


def extract_domain(url):
    try:
        from urllib.parse import urlparse
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


def is_relevant(result, keyword):
    text = (result["title"] + " " + result["description"]).lower()
    kw = keyword.lower()
    if kw not in text and not any(c in text for c in kw.replace(" ", "") if len(c) >= 2):
        return False
    if any(bd in extract_domain(result["link"]) for bd in BLOCKED_DOMAINS):
        return False
    return True


def score_result(result):
    text = (result["title"] + " " + result["description"]).lower()
    score = sum(10 for fk in FREE_KEYWORDS if fk.lower() in text)
    if any(pd in extract_domain(result["link"]) for pd in PRIORITY_DOMAINS):
        score += 50
    if "免费" in text: score += 20
    if "在线观看" in text or "在线播放" in text: score += 15
    if "高清" in text: score += 5
    if "全集" in text or "完整版" in text: score += 8
    return score


def verify_page(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15, verify=False, allow_redirects=True)
        if resp.status_code != 200:
            return False
        content = resp.text[:50000].lower()
        return any(i in content for i in ["<video", "player", "播放器", "videojs", "dplayer", "mp4", "m3u8", "iframe", "在线播放", "免费观看"])
    except Exception:
        return False


def search_movie(keyword, max_rounds):
    # 精简搜索词，每个引擎只搜 1 次
    queries = {
        "zh": f"{keyword} 免费在线观看",
        "en": f"{keyword} free watch online",
    }
    seen, all_found = set(), []

    for rn in range(1, max_rounds + 1):
        logger.info(f"--- 第 {rn}/{max_rounds} 轮 ---")
        results = []
        for engine in SEARCH_ENGINES:
            qtype = engine.get("queries", ["zh"])[0]
            q = queries.get(qtype, queries["zh"])
            r = search_engine(q, engine)
            logger.info(f"{engine['name']}: {len(r)} 条")
            results.extend(r)
            time.sleep(0.5)

        # Serper API (Google 搜索替代)
        serper_results = search_serper(keyword)
        results.extend(serper_results)  # 缩短等待时间

        for r in results:
            if r["title"] not in seen and is_relevant(r, keyword):
                r["score"] = score_result(r)
                seen.add(r["title"])
                all_found.append(r)

        all_found.sort(key=lambda x: -x["score"])
        top = all_found[:15]
        logger.info(f"相关: {len(top)} 条, 最高: {top[0]['score'] if top else 0}分")

        for r in top:
            if r["score"] >= 30:
                ok = verify_page(r["link"]) if r["score"] >= 40 else True
                if ok:
                    logger.info(f"✅ 找到: {r['title']} ({r['score']}分)")
                    return top, True

        if rn < max_rounds:
            logger.info(f"未找到，等 {INTERVAL}s...")
            time.sleep(INTERVAL)

    return all_found[:15], False


# ============================================================
# 发送邮件
# ============================================================
def send_email(results, keyword, found):
    msg = MIMEMultipart()
    msg["From"] = SENDER_EMAIL
    msg["To"] = RECEIVER_EMAIL
    msg["Subject"] = f"{'🎯 找到片源' if found else '📋 搜索结果'} - {keyword}"
    msg["Date"] = formatdate(localtime=True)

    rows = ""
    for i, r in enumerate(results, 1):
        link = r["link"] if r["link"].startswith("http") else "#"
        desc = r["description"][:150] + "..." if len(r.get("description", "")) > 150 else r.get("description", "")
        rows += f'<tr><td style="padding:10px 8px;border-bottom:1px solid #eee;text-align:center;color:#888;font-size:13px;">{i}</td><td style="padding:10px 8px;border-bottom:1px solid #eee;"><a href="{link}" style="color:#1a73e8;text-decoration:none;font-weight:500;">{r["title"]}</a><p style="margin:4px 0 0;font-size:12px;color:#666;">{desc}</p><span style="font-size:11px;color:#aaa;">{r["source_engine"]} | {r["score"]}分</span></td></tr>'

    style = "background:#e8f5e9;color:#2e7d32;" if found else "background:#fff3e0;color:#e65100;"
    status = "🎯 找到免费片源！" if found else "📋 搜索结果（未找到精确匹配）"

    html = f"""<div style="font-family:'Microsoft YaHei',sans-serif;max-width:700px;margin:0 auto;background:#f9fafb;padding:20px;">
<div style="background:#fff;border-radius:12px;padding:24px;box-shadow:0 1px 3px rgba(0,0,0,0.1);">
<h1 style="margin:0 0 8px;font-size:20px;color:#1a1a2e;">🎬 Future</h1>
<p style="margin:0 0 16px;font-size:14px;color:#666;">{status}</p>
<div style="background:#f0f4ff;border-radius:8px;padding:12px 16px;margin-bottom:16px;">
<span style="font-size:14px;">🔍 {keyword}</span><br>
<span style="font-size:12px;color:#666;">⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')}</span></div>
<table style="width:100%;border-collapse:collapse;">
<thead><tr style="background:#1a1a2e;color:#fff;"><th style="padding:10px;width:40px;text-align:center;">#</th><th style="padding:10px;text-align:left;">搜索结果</th></tr></thead>
<tbody>{rows or '<tr><td colspan="2" style="padding:20px;text-align:center;color:#999;">暂无结果</td></tr>'}</tbody></table>
<p style="margin-top:16px;padding:12px;{style}border-radius:8px;font-size:13px;">{'✅ 上方可直接点击观看。' if found else '建议增加搜索轮数。'}</p>
</div></div>"""

    msg.attach(MIMEText(html, "html", "utf-8"))

    ctx = ssl.create_default_context()
    for attempt in range(1, 4):
        try:
            with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=ctx, timeout=30) as s:
                s.login(SENDER_EMAIL, EMAIL_PASSWORD)
                s.sendmail(SENDER_EMAIL, RECEIVER_EMAIL, msg.as_string())
            logger.info(f"邮件已发送至 {RECEIVER_EMAIL}")
            return True
        except Exception as e:
            logger.warning(f"发信第{attempt}次失败: {e}")
            time.sleep(5 * attempt)
    return False


# ============================================================
# 主入口
# ============================================================
def main():
    logger.info("=" * 50)
    logger.info("🎬 Future 启动")

    # 1. 检查邮箱
    logger.info("检查邮箱...")
    commands = check_emails()
    if not commands:
        logger.info("没有新的搜索命令，退出")
        return

    # 2. 执行搜索 + 发送结果
    for keyword, rounds in commands:
        logger.info(f"▶ 搜索: [{keyword}] ({rounds}轮)")
        results, found = search_movie(keyword, rounds)
        send_email(results, keyword, found)
        logger.info(f"✅ 完成 - {'找到' if found else '未找到'}")


if __name__ == "__main__":
    main()
