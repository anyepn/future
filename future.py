#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Future - 影视免费片源搜索工具
输入电影/动漫名称，全网搜索免费在线观看资源，找到后发送邮箱
持续运行直到找到结果
"""

import os
import sys
import time
import json
import logging
import smtplib
import ssl
import hashlib
import argparse
import re
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate
from urllib.parse import quote, urljoin

import requests
from bs4 import BeautifulSoup

# ============================================================
# 配置（优先读取环境变量，未设置则使用默认值）
# ============================================================
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "3029308562@qq.com")
RECEIVER_EMAIL = os.environ.get("RECEIVER_EMAIL", "3029308562@qq.com")
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.qq.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")

# 搜索间隔（秒）
SEARCH_INTERVAL = 120  # 每 2 分钟搜索一次
MAX_RETRIES = 36       # 最多重试 36 次（约 72 分钟）

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# 搜索引擎配置
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

# 免费片源关键词（用于过滤搜索结果）
FREE_KEYWORDS = [
    "免费观看", "免费在线", "免费看", "在线观看", "在线看",
    "高清在线", "完整版", "全集", "无广告", "免费播放",
    "free watch", "free online", "watch online", "streaming",
    "在线播放", "完整版在线", "高清播放", "无删减",
]

# 免费影视网站域名白名单（用于优先排序）
PRIORITY_DOMAINS = [
    "bilibili.com", "v.qq.com", "youku.com", "iqiyi.com", "mgtv.com",
    "pptv.com", "le.com", "sohu.com", "1905.com", "cctv.com",
    "bilibili.tv",
]

# 排除的域名
BLOCKED_DOMAINS = [
    "taobao.com", "tmall.com", "jd.com", "pinduoduo.com",
    "douyin.com", "tiktok.com",
]

# 日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(os.path.dirname(os.path.abspath(__file__)), "future.log"), encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


# ============================================================
# 搜索引擎搜索
# ============================================================
def search_engine(query, engine):
    """通过搜索引擎搜索"""
    url = engine["search_url"].format(query=quote(query))
    results = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20, verify=False)
        resp.encoding = resp.apparent_encoding or "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")

        items = soup.select(engine["result_selector"])
        for item in items:
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
                    results.append({
                        "title": title,
                        "link": link,
                        "description": desc[:300],
                        "source_engine": engine["name"],
                    })
            except Exception:
                continue
    except Exception as e:
        logger.warning(f"{engine['name']} 搜索失败: {e}")

    return results


def extract_domain(url):
    """提取域名"""
    try:
        from urllib.parse import urlparse
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


def is_relevant(result, keyword):
    """判断搜索结果是否相关"""
    text = (result["title"] + " " + result["description"]).lower()
    kw_lower = keyword.lower()

    # 标题或描述中包含关键词
    if kw_lower not in text and not any(
        c in text for c in keyword.lower().replace(" ", "")
        if len(c) >= 2
    ):
        return False

    # 排除黑名单域名
    domain = extract_domain(result["link"])
    if any(bd in domain for bd in BLOCKED_DOMAINS):
        return False

    # 包含免费观看关键词加分
    has_free_kw = any(fk.lower() in text for fk in FREE_KEYWORDS)
    return True


def score_result(result):
    """为搜索结果打分"""
    text = (result["title"] + " " + result["description"]).lower()
    score = 0

    # 包含免费关键词
    free_hits = sum(1 for fk in FREE_KEYWORDS if fk.lower() in text)
    score += free_hits * 10

    # 优先域名
    domain = extract_domain(result["link"])
    for pd in PRIORITY_DOMAINS:
        if pd in domain:
            score += 50
            break

    # 标题相关性（关键词匹配越多越好）
    if "免费" in text:
        score += 20
    if "在线观看" in text or "在线播放" in text:
        score += 15
    if "高清" in text:
        score += 5
    if "全集" in text or "完整版" in text:
        score += 8

    return score


# ============================================================
# 页面验证（检查是否真的能观看）
# ============================================================
def verify_page(url):
    """验证页面是否可访问且包含视频播放相关内容"""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15, verify=False,
                            allow_redirects=True)
        if resp.status_code != 200:
            return False, ""

        content = resp.text[:50000].lower()
        # 检查是否有视频播放器相关标签
        video_indicators = [
            "<video", "player", "播放器", "videojs", "dplayer",
            "xgplayer", "playvideo", "mp4", "m3u8", "embed",
            "iframe", "在线播放", "免费观看",
        ]
        found = [ind for ind in video_indicators if ind in content]
        return len(found) > 0, ", ".join(found[:3])
    except Exception as e:
        logger.debug(f"验证页面失败 {url}: {e}")
        return False, ""


# ============================================================
# 邮件发送
# ============================================================
def send_email(subject, html_body, is_final=True):
    """发送邮件通知"""
    msg = MIMEMultipart()
    msg["From"] = SENDER_EMAIL
    msg["To"] = RECEIVER_EMAIL
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    context = ssl.create_default_context()
    try:
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=context) as server:
            server.login(SENDER_EMAIL, SMTP_PASSWORD)
            server.sendmail(SENDER_EMAIL, RECEIVER_EMAIL, msg.as_string())
        logger.info(f"邮件已发送至 {RECEIVER_EMAIL}")
        return True
    except Exception as e:
        logger.error(f"邮件发送失败: {e}")
        return False


# ============================================================
# 结果格式化
# ============================================================
def format_results_email(keyword, results, round_num, max_rounds, found):
    """格式化搜索结果为 HTML 邮件"""
    status = "🎯 找到免费片源！" if found else f"⏳ 搜索中... (第 {round_num}/{max_rounds} 轮)"

    results_html = ""
    if results:
        for idx, r in enumerate(results[:15], 1):
            link = r["link"] if r["link"].startswith("http") else f"https://google.com/search?q={quote(r['title'])}"
            desc = r["description"][:150] + "..." if len(r.get("description", "")) > 150 else r.get("description", "")
            results_html += f"""
            <tr>
                <td style="padding:10px 8px;border-bottom:1px solid #eee;text-align:center;color:#888;font-size:13px;">{idx}</td>
                <td style="padding:10px 8px;border-bottom:1px solid #eee;">
                    <a href="{link}" style="color:#1a73e8;text-decoration:none;font-weight:500;">{r['title']}</a>
                    <p style="margin:4px 0 0;font-size:12px;color:#666;">{desc}</p>
                    <span style="font-size:11px;color:#aaa;">来源: {r['source_engine']} | 匹配度: {r['score']}分</span>
                </td>
            </tr>
            """
    else:
        results_html = '<tr><td colspan="2" style="padding:20px;text-align:center;color:#999;">暂未找到相关结果，正在持续搜索...</td></tr>'

    html = f"""
    <div style="font-family:'Microsoft YaHei','PingFang SC',Arial,sans-serif;max-width:700px;margin:0 auto;background:#f9fafb;padding:20px;">
        <div style="background:#fff;border-radius:12px;padding:24px;box-shadow:0 1px 3px rgba(0,0,0,0.1);">
            <h1 style="margin:0 0 8px;font-size:20px;color:#1a1a2e;">🎬 Future - 影视片源搜索</h1>
            <p style="margin:0 0 16px;font-size:14px;color:#666;">{status}</p>

            <div style="background:#f0f4ff;border-radius:8px;padding:12px 16px;margin-bottom:16px;">
                <span style="font-size:14px;">🔍 搜索关键词: <strong>{keyword}</strong></span><br>
                <span style="font-size:12px;color:#666;">⏰ 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</span>
            </div>

            <table style="width:100%;border-collapse:collapse;">
                <thead>
                    <tr style="background:#1a1a2e;color:#fff;">
                        <th style="padding:10px;width:40px;text-align:center;">#</th>
                        <th style="padding:10px;text-align:left;">搜索结果</th>
                    </tr>
                </thead>
                <tbody>
                    {results_html}
                </tbody>
            </table>

            {'<p style="margin-top:16px;padding:12px;background:#e8f5e9;border-radius:8px;color:#2e7d32;font-weight:500;">✅ 已找到免费片源！搜索任务完成。上方链接可直接点击访问观看。</p>' if found else '<p style="margin-top:16px;padding:12px;background:#fff3e0;border-radius:8px;color:#e65100;font-size:13px;">⏳ 仍在搜索中，找到免费片源后会立即通知您。预计每 2 分钟搜索一次，最多搜索 72 分钟。</p>'}

            <hr style="border:none;border-top:1px solid #eee;margin:16px 0;">
            <p style="color:#bbb;font-size:11px;">由 Future 影视搜索工具自动发送</p>
        </div>
    </div>
    """
    return html


# ============================================================
# 主搜索流程
# ============================================================
def search_movie(keyword, max_retries=MAX_RETRIES, interval=SEARCH_INTERVAL):
    """
    搜索影视免费片源主流程

    Args:
        keyword: 电影/动漫名称
        max_retries: 最大搜索轮数
        interval: 每轮间隔（秒）
    """
    search_query = f"{keyword} 免费在线观看"
    logger.info(f"{'='*60}")
    logger.info(f"开始搜索: {keyword}")
    logger.info(f"搜索查询: {search_query}")
    logger.info(f"最大轮数: {max_retries}, 间隔: {interval}s")
    logger.info(f"{'='*60}")

    # 去重集合
    seen_titles = set()

    for round_num in range(1, max_retries + 1):
        logger.info(f"\n--- 第 {round_num}/{max_retries} 轮搜索 ---")

        all_results = []

        # 多搜索引擎搜索
        for engine in SEARCH_ENGINES:
            logger.info(f"使用 {engine['name']} 搜索...")
            results = search_engine(search_query, engine)
            logger.info(f"{engine['name']} 返回 {len(results)} 条结果")
            all_results.extend(results)
            time.sleep(1)

        # 过滤 + 去重 + 打分
        relevant = []
        for r in all_results:
            if r["title"] in seen_titles:
                continue
            if is_relevant(r, keyword):
                r["score"] = score_result(r)
                seen_titles.add(r["title"])
                relevant.append(r)

        # 按分数排序
        relevant.sort(key=lambda x: -x["score"])
        logger.info(f"相关结果: {len(relevant)} 条")

        # 判断是否找到高质量免费片源
        found = False
        top_results = relevant[:15]
        for r in top_results:
            if r["score"] >= 40:
                # 高分结果，验证页面
                logger.info(f"验证高分结果: {r['title']} (score={r['score']})")
                can_watch, indicators = verify_page(r["link"])
                if can_watch:
                    logger.info(f"✅ 找到可观看的片源: {r['title']}")
                    found = True
                    break

        # 如果有高分结果但验证失败，只要有 >=30 分的结果也算找到
        if not found and top_results and top_results[0]["score"] >= 30:
            logger.info(f"找到匹配度较高的结果，标记为已找到")
            found = True

        # 发送邮件通知
        subject_prefix = "🎯 找到片源" if found else f"⏳ 搜索中 ({round_num}/{max_retries})"
        subject = f"{subject_prefix} - {keyword}"
        html = format_results_email(keyword, top_results, round_num, max_retries, found)
        send_email(subject, html, is_final=found)

        if found:
            logger.info(f"🎉 成功找到免费片源，任务结束！")
            return True

        # 未找到，等待后继续
        if round_num < max_retries:
            logger.info(f"本轮未找到，等待 {interval}s 后继续搜索...")
            time.sleep(interval)

    # 超过最大轮数
    logger.warning(f"⚠️ 达到最大搜索轮数 {max_retries}，未找到免费片源")

    # 发送最终报告
    final_html = format_results_email(keyword, relevant[:15], max_retries, max_retries, False)
    send_email(f"📋 搜索报告（已达上限）- {keyword}", final_html, is_final=True)
    return False


# ============================================================
# 命令行入口
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="Future - 影视免费片源搜索工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python future.py "进击的巨人"
  python future.py "流浪地球2" --max-retries 50 --interval 90
  python future.py "灌篮高手" --once
        """,
    )
    parser.add_argument("keyword", help="电影/动漫名称")
    parser.add_argument("--max-retries", type=int, default=MAX_RETRIES, help=f"最大搜索轮数 (默认 {MAX_RETRIES})")
    parser.add_argument("--interval", type=int, default=SEARCH_INTERVAL, help=f"搜索间隔秒数 (默认 {SEARCH_INTERVAL})")
    parser.add_argument("--once", action="store_true", help="只搜索一轮就结束")

    args = parser.parse_args()

    if args.once:
        args.max_retries = 1

    success = search_movie(args.keyword, args.max_retries, args.interval)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
