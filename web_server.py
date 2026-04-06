#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Future - Web 服务器
提供网页搜索界面，用户在浏览器输入片名即可搜索免费片源。

使用方法:
  python web_server.py
  然后访问 http://localhost:5000
"""

import os
import sys
import json
import logging
from flask import Flask, render_template, request, jsonify

# ============================================================
# 日志
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_server.log"), encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

# ============================================================
# Flask 应用
# ============================================================
app = Flask(__name__)
app.secret_key = "future-movie-search-2024"

# 单轮搜索引擎结果（不持续循环）
from future import (
    search_engine, extract_domain, is_relevant, score_result,
    verify_page, send_email, format_results_email, SENDER_EMAIL,
    RECEIVER_EMAIL, SMTP_SERVER, SMTP_PORT, SMTP_PASSWORD,
    SEARCH_ENGINES, FREE_KEYWORDS, PRIORITY_DOMAINS, BLOCKED_DOMAINS,
    HEADERS,
)
from urllib.parse import quote
import time


def quick_search(keyword, max_rounds=3):
    """
    快速搜索模式（适用于 Web 接口）
    搜索指定轮数，返回所有结果
    """
    search_query = f"{keyword} 免费在线观看"
    all_results = []
    seen_titles = set()
    found = False

    for round_num in range(1, max_rounds + 1):
        logger.info(f"Web搜索 第 {round_num}/{max_rounds} 轮: {keyword}")

        round_results = []
        for engine in SEARCH_ENGINES:
            try:
                results = search_engine(search_query, engine)
                round_results.extend(results)
            except Exception as e:
                logger.warning(f"{engine['name']} 搜索失败: {e}")
            time.sleep(0.5)

        # 过滤 + 去重 + 打分
        for r in round_results:
            if r["title"] in seen_titles:
                continue
            if is_relevant(r, keyword):
                r["score"] = score_result(r)
                seen_titles.add(r["title"])
                all_results.append(r)

        all_results.sort(key=lambda x: -x["score"])

        # 检查是否找到高质量结果
        for r in all_results[:10]:
            if r["score"] >= 30:
                found = True
                break

        if found:
            break

        if round_num < max_rounds:
            time.sleep(2)

    top_results = all_results[:20]
    logger.info(f"Web搜索完成: {keyword}, 结果 {len(top_results)} 条, 找到={found}")
    return top_results, found


@app.route("/")
def index():
    """首页"""
    return render_template("index.html")


@app.route("/api/search", methods=["POST"])
def api_search():
    """搜索 API"""
    data = request.get_json()
    keyword = data.get("keyword", "").strip()
    max_rounds = data.get("max_rounds", 3)
    send_email_flag = data.get("send_email", False)

    if not keyword:
        return jsonify({"error": "请输入搜索关键词"}), 400

    if max_rounds < 1:
        max_rounds = 1
    if max_rounds > 36:
        max_rounds = 36

    try:
        results, found = quick_search(keyword, max_rounds)

        # 可选：发送邮件通知
        if send_email_flag:
            try:
                html = format_results_email(keyword, results[:15], max_rounds, max_rounds, found)
                subject = f"{'🎯 找到片源' if found else '📋 搜索结果'} - {keyword}"
                send_email(subject, html)
                logger.info(f"搜索结果已发送邮件")
            except Exception as e:
                logger.error(f"发送邮件失败: {e}")

        return jsonify({
            "keyword": keyword,
            "results": results,
            "found": found,
            "total": len(results),
        })

    except Exception as e:
        logger.error(f"搜索失败: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/health", methods=["GET"])
def health():
    """健康检查"""
    return jsonify({"status": "ok", "service": "Future"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logger.info("=" * 60)
    logger.info("🌐 Future Web 服务器已启动")
    logger.info(f"📍 端口: {port}")
    logger.info("=" * 60)
    app.run(host="0.0.0.0", port=port, debug=False)
