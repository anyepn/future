#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Future - 一站式自动化脚本
GitHub Actions 调用此脚本即可完成：检查邮箱 → 提取片名 → 搜索 → 发送结果
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
import hashlib
from email.header import decode_header
from email.utils import formataddr, parseaddr, formatdate
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

# ============================================================
# 配置（全部从环境变量读取）
# ============================================================
IMAP_SERVER = os.environ.get("IMAP_SERVER", "imap.qq.com")
IMAP_PORT = int(os.environ.get("IMAP_PORT", "993"))
EMAIL_ACCOUNT = os.environ.get("EMAIL_ACCOUNT", "3029308562@qq.com")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "")

SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "3029308562@qq.com")
RECEIVER_EMAIL = os.environ.get("RECEIVER_EMAIL", "3029308562@qq.com")
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.qq.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

SEARCH_ENGINES = [
    {
        "name": "Google",
        "search_url": "https://www.google.com/search?q={query}&hl=zh-CN&num=30",
        "result_selector": "div.g",
        "title_selector": "h3",
        "link_selector": "a[href]",
        "desc_selector": "div[data-sncf], span.aCOpRe, div.VwiC3b",
    },
    {
        "name": "Bing",
        "search_url": "https://www.bing.com/search?q={query}&cc=cn&setlang=zh-Hans&count=30",
        "result_selector": "li.b_algo",
        "title_selector": "h2 a",
        "link_selector": "h2 a[href]",
        "desc_selector": "p, div.b_caption p",
    },
    {
        "name": "百度",
        "search_url": "https://www.baidu.com/s?wd={query}&rn=30",
        "result_selector": "div.result, div.c-container",
        "title_selector": "h3 a, a.c-font-large",
        "link_selector": "h3 a[href], a[href]",
        "desc_selector": "div.c-abstract, span.content-right_8Zs40",
    },
]

FREE_KEYWORDS = [
    "免费观看", "免费在线", "免费看", "在线观看", "在线看",
    "高清在线", "完整版", "全集", "无广告", "免费播放",
    "free watch", "free online", "watch online", "streaming",
    "在线播放", "完整版在线", "高清播放", "无删减",
]

PRIORITY_DOMAINS = [
    "bilibili.com", "v.qq.com", "youku.com", "iqiyi.com", "mgtv.com",
    "pptv.com", "le.com", "sohu.com", "1905.com", "cctv.com",
]

BLOCKED_DOMAINS = ["taobao.com", "tmall.com", "jd.com", "pinduoduo.com", "douyin.com"]

# 日志
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


def parse_search_command(subject):
    pattern = r"^(?:搜|搜索)\s*[:：]\s*(.+?)(?:\s+(\d+)[轮圈])?\s*(持续)?$"
    match = re.match(pattern, subject.strip())
    if not match:
        return None, 1
    keyword = match.group(1).strip()
    if len(keyword) < 1:
        return None, 1
    rounds = int(match.group(2)) if match.group(2) else (36 if match.group(3) else 5)
    return keyword, rounds


# ============================================================
# 邮箱检查
# ============================================================
def check_emails(processed_file="/tmp/processed.json"):
    """检查未读邮件中的搜索命令，返回 [(keyword, rounds, sender), ...]"""
    processed = set()
    if os.path.exists(processed_file):
        try:
            processed = set(json.load(open(processed_file)))
        except Exception:
            pass

    commands = []
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
        mail.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
        mail.select("INBOX")

        status, messages = mail.search(None, "UNSEEN")
        if status != "OK":
            return commands

        for eid in messages[0].split():
            eid_str = eid.decode()
            if eid_str in processed:
                continue
            try:
                _, msg_data = mail.fetch(eid, "(RFC822)")
                msg = email.message_from_bytes(msg_data[0][1])
                subject = decode_str(msg.get("Subject", ""))
                sender_name, sender_addr = parseaddr(decode_str(msg.get("From", "")))

                keyword, rounds = parse_search_command(subject)
                if keyword:
                    logger.info(f"检测到搜索命令: [{keyword}] {rounds}轮, 来自: {sender_addr}")
                    commands.append((keyword, rounds, sender_addr))

                # 标记已处理
                mail.store(eid, "+FLAGS", "\\Seen")
                processed.add(eid_str)
            except Exception as e:
                logger.warning(f"处理邮件失败: {e}")

        mail.logout()
    except Exception as e:
        logger.error(f"邮箱连接失败: {e}")

    # 保存已处理列表
    json.dump(list(processed), open(processed_file, "w"))
    return commands


# ============================================================
# 搜索引擎
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


def extract_domain(url):
    try:
        from urllib.parse import urlparse
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


def is_relevant(result, keyword):
    text = (result["title"] + " " + result["description"]).lower()
    kw_lower = keyword.lower()
    if kw_lower not in text and not any(c in text for c in kw_lower.replace(" ", "") if len(c) >= 2):
        return False
    domain = extract_domain(result["link"])
    if any(bd in domain for bd in BLOCKED_DOMAINS):
        return False
    return True


def score_result(result):
    text = (result["title"] + " " + result["description"]).lower()
    score = sum(10 for fk in FREE_KEYWORDS if fk.lower() in text)
    domain = extract_domain(result["link"])
    if any(pd in domain for pd in PRIORITY_DOMAINS):
        score += 50
    if "免费" in text:
        score += 20
    if "在线观看" in text or "在线播放" in text:
        score += 15
    if "高清" in text:
        score += 5
    if "全集" in text or "完整版" in text:
        score += 8
    return score


def verify_page(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15, verify=False, allow_redirects=True)
        if resp.status_code != 200:
            return False
        content = resp.text[:50000].lower()
        indicators = ["<video", "player", "播放器", "videojs", "dplayer", "xgplayer", "mp4", "m3u8", "iframe", "在线播放", "免费观看"]
        return len([i for i in indicators if i in content]) > 0
    except Exception:
        return False


# ============================================================
# 搜索主流程
# ============================================================
def search_movie(keyword, max_retries=5, interval=60):
    search_query = f"{keyword} 免费在线观看"
    seen_titles = set()
    all_relevant = []

    for round_num in range(1, max_retries + 1):
        logger.info(f"--- 第 {round_num}/{max_retries} 轮 ---")

        results = []
        for engine in SEARCH_ENGINES:
            r = search_engine(search_query, engine)
            logger.info(f"{engine['name']}: {len(r)} 条")
            results.extend(r)
            time.sleep(1)

        for r in results:
            if r["title"] not in seen_titles and is_relevant(r, keyword):
                r["score"] = score_result(r)
                seen_titles.add(r["title"])
                all_relevant.append(r)

        all_relevant.sort(key=lambda x: -x["score"])
        top = all_relevant[:15]
        logger.info(f"相关: {len(top)} 条, 最高分: {top[0]['score'] if top else 0}")

        # 找到高分结果
        for r in top:
            if r["score"] >= 30:
                if r["score"] >= 40:
                    can_watch = verify_page(r["link"])
                else:
                    can_watch = True
                if can_watch:
                    logger.info(f"✅ 找到片源: {r['title']} ({r['score']}分)")
                    return top, True

        if round_num < max_retries:
            logger.info(f"未找到，等待 {interval}s...")
            time.sleep(interval)

    return all_relevant[:15], False


# ============================================================
# 邮件发送
# ============================================================
def send_email(subject, results, keyword, found):
    msg = MIMEMultipart()
    msg["From"] = SENDER_EMAIL
    msg["To"] = RECEIVER_EMAIL
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)

    results_html = ""
    if results:
        for idx, r in enumerate(results, 1):
            link = r["link"] if r["link"].startswith("http") else "#"
            desc = r["description"][:150] + "..." if len(r.get("description", "")) > 150 else r.get("description", "")
            results_html += f"""
            <tr>
                <td style="padding:10px 8px;border-bottom:1px solid #eee;text-align:center;color:#888;font-size:13px;">{idx}</td>
                <td style="padding:10px 8px;border-bottom:1px solid #eee;">
                    <a href="{link}" style="color:#1a73e8;text-decoration:none;font-weight:500;">{r['title']}</a>
                    <p style="margin:4px 0 0;font-size:12px;color:#666;">{desc}</p>
                    <span style="font-size:11px;color:#aaa;">{r['source_engine']} | {r['score']}分</span>
                </td>
            </tr>"""

    status = "🎯 找到免费片源！" if found else "📋 搜索结果（未找到精确匹配）"
    status_style = "background:#e8f5e9;color:#2e7d32;" if found else "background:#fff3e0;color:#e65100;"

    html = f"""
    <div style="font-family:'Microsoft YaHei',sans-serif;max-width:700px;margin:0 auto;background:#f9fafb;padding:20px;">
        <div style="background:#fff;border-radius:12px;padding:24px;box-shadow:0 1px 3px rgba(0,0,0,0.1);">
            <h1 style="margin:0 0 8px;font-size:20px;color:#1a1a2e;">🎬 Future - 影视片源搜索</h1>
            <p style="margin:0 0 16px;font-size:14px;color:#666;">{status}</p>
            <div style="background:#f0f4ff;border-radius:8px;padding:12px 16px;margin-bottom:16px;">
                <span style="font-size:14px;">🔍 {keyword}</span><br>
                <span style="font-size:12px;color:#666;">⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')}</span>
            </div>
            <table style="width:100%;border-collapse:collapse;">
                <thead><tr style="background:#1a1a2e;color:#fff;">
                    <th style="padding:10px;width:40px;text-align:center;">#</th>
                    <th style="padding:10px;text-align:left;">搜索结果</th>
                </tr></thead>
                <tbody>{results_html or '<tr><td colspan="2" style="padding:20px;text-align:center;color:#999;">暂无结果</td></tr>'}</tbody>
            </table>
            <p style="margin-top:16px;padding:12px;{status_style}border-radius:8px;font-size:13px;">
                {'✅ 上方链接可直接点击观看。' if found else '建议尝试更多轮数搜索。'}
            </p>
            <hr style="border:none;border-top:1px solid #eee;margin:16px 0;">
            <p style="color:#bbb;font-size:11px;">Future 影视搜索工具 · GitHub Actions 自动执行</p>
        </div>
    </div>"""

    msg.attach(MIMEText(html, "html", "utf-8"))

    context = ssl.create_default_context()
    for attempt in range(1, 4):
        try:
            with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=context, timeout=30) as server:
                server.login(SENDER_EMAIL, EMAIL_PASSWORD)
                server.sendmail(SENDER_EMAIL, RECEIVER_EMAIL, msg.as_string())
            logger.info(f"邮件已发送至 {RECEIVER_EMAIL}")
            return True
        except Exception as e:
            logger.warning(f"发信第{attempt}次失败: {e}")
            if attempt < 3:
                time.sleep(5 * attempt)
    logger.error("邮件发送最终失败")
    return False


# ============================================================
# 主入口
# ============================================================
def main():
    if not EMAIL_PASSWORD:
        logger.error("未配置 EMAIL_PASSWORD 环境变量")
        sys.exit(1)

    logger.info("=" * 50)
    logger.info("🎬 Future 自动化搜索启动")
    logger.info("=" * 50)

    # 1. 检查邮箱
    commands = check_emails()
    if not commands:
        logger.info("没有新的搜索命令，退出")
        return

    # 2. 逐个执行搜索
    for keyword, rounds, sender in commands:
        logger.info(f"\n▶ 搜索: [{keyword}] ({rounds}轮)")
        results, found = search_movie(keyword, max_retries=rounds, interval=60)

        # 3. 发送结果
        subject = f"{'🎯 找到片源' if found else '📋 搜索结果'} - {keyword}"
        send_email(subject, results, keyword, found)

        logger.info(f"✅ [{keyword}] 完成 - {'找到' if found else '未找到'}")


if __name__ == "__main__":
    main()
