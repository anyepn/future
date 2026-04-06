#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Future - 邮箱触发器
监控 QQ 邮箱收件箱，检测主题含"搜:"的邮件，自动执行搜索并回复结果。

使用方法:
  1. 启动守护进程: python email_bot.py
  2. 发送邮件到自己的 QQ 邮箱，主题格式: "搜: 流浪地球2"
  3. 等待自动搜索完成后收到回复邮件

触发格式:
  主题: 搜: 片名        → 默认搜索一轮
  主题: 搜: 片名 持续    → 持续搜索直到找到
  主题: 搜: 片名 3轮     → 搜索 3 轮
"""

import os
import sys
import re
import imaplib
import email
import time
import logging
import signal
import threading
from email.header import decode_header
from datetime import datetime
from email.utils import formataddr, parseaddr

# ============================================================
# 配置
# ============================================================
IMAP_SERVER = "imap.qq.com"
IMAP_PORT = 993

EMAIL_ACCOUNT = "3029308562@qq.com"
EMAIL_PASSWORD = "oqudvzgcdeyrdcfd"

CHECK_INTERVAL = 60  # 每 60 秒检查一次收件箱
PROCESSED_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".processed_emails.txt")

# ============================================================
# 日志
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(os.path.dirname(os.path.abspath(__file__)), "email_bot.log"), encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


# ============================================================
# 已处理邮件去重
# ============================================================
processed_ids = set()

def load_processed():
    """加载已处理的邮件 ID"""
    global processed_ids
    if os.path.exists(PROCESSED_FILE):
        try:
            with open(PROCESSED_FILE, "r") as f:
                processed_ids = set(line.strip() for line in f if line.strip())
        except Exception:
            processed_ids = set()
    logger.info(f"已加载 {len(processed_ids)} 条已处理记录")

def save_processed(msg_id):
    """保存已处理的邮件 ID"""
    with open(PROCESSED_FILE, "a") as f:
        f.write(f"{msg_id}\n")
    processed_ids.add(msg_id)


# ============================================================
# 邮件解析
# ============================================================
def decode_str(s):
    """解码邮件头字符串"""
    if not s:
        return ""
    parts = decode_header(s)
    result = []
    for part, charset in parts:
        if isinstance(part, bytes):
            result.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            result.append(part)
    return "".join(result)


def parse_search_command(subject):
    """
    解析搜索命令
    格式:
      "搜: 片名"       → (片名, 1, False)
      "搜: 片名 持续"   → (片名, 36, False)
      "搜: 片名 5轮"    → (片名, 5, False)
      "搜索: 片名"      → (片名, 1, False)
    """
    # 匹配 "搜: 片名" 或 "搜索: 片名"
    pattern = r"^(?:搜|搜索)\s*[:：]\s*(.+?)(?:\s+(\d+)[轮圈])?\s*(持续)?$"
    match = re.match(pattern, subject.strip())
    if not match:
        return None, 1, False

    keyword = match.group(1).strip()
    rounds = int(match.group(2)) if match.group(2) else 1
    persistent = bool(match.group(3))

    if persistent:
        rounds = 36  # 持续模式，搜索 36 轮

    # 关键词最小长度检查
    if len(keyword) < 1:
        return None, 1, False

    return keyword, rounds, persistent


# ============================================================
# IMAP 邮件监控
# ============================================================
def connect_imap():
    """连接 IMAP 服务器"""
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
        mail.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
        mail.select("INBOX")
        return mail
    except Exception as e:
        logger.error(f"IMAP 连接失败: {e}")
        return None


def check_new_emails(mail):
    """检查未读邮件中的搜索命令"""
    commands = []
    try:
        status, messages = mail.search(None, "UNSEEN")
        if status != "OK":
            return commands

        email_ids = messages[0].split()
        logger.info(f"发现 {len(email_ids)} 封未读邮件")

        for eid in email_ids:
            eid_str = eid.decode()
            if eid_str in processed_ids:
                continue

            try:
                status, msg_data = mail.fetch(eid, "(RFC822)")
                if status != "OK":
                    continue

                raw_email = msg_data[0][1]
                msg = email.message_from_bytes(raw_email)

                subject = decode_str(msg.get("Subject", ""))
                sender = decode_str(msg.get("From", ""))
                sender_name, sender_addr = parseaddr(sender)

                logger.info(f"邮件: 主题=[{subject}], 发件人=[{sender_addr}]")

                # 解析搜索命令
                keyword, rounds, persistent = parse_search_command(subject)
                if keyword:
                    logger.info(f"✅ 识别搜索命令: 片名=[{keyword}], 轮数=[{rounds}], 持续=[{persistent}]")
                    commands.append({
                        "keyword": keyword,
                        "rounds": rounds,
                        "persistent": persistent,
                        "sender": sender_addr,
                        "subject": subject,
                        "email_id": eid_str,
                    })
                    # 标记为已读
                    mail.store(eid, "+FLAGS", "\\Seen")
                    save_processed(eid_str)
                else:
                    # 非命令邮件，标记为已读避免重复检查
                    mail.store(eid, "+FLAGS", "\\Seen")
                    save_processed(eid_str)

            except Exception as e:
                logger.error(f"处理邮件失败 {eid}: {e}")

    except Exception as e:
        logger.error(f"检查邮件失败: {e}")

    return commands


# ============================================================
# 执行搜索并回复
# ============================================================
def execute_search_and_reply(command):
    """执行搜索并回复结果给发件人"""
    from future import search_movie, format_results_email, send_email, SENDER_EMAIL, RECEIVER_EMAIL

    keyword = command["keyword"]
    rounds = command["rounds"]
    persistent = command["persistent"]
    sender = command["sender"]

    logger.info(f"▶ 开始搜索: [{keyword}] ({rounds} 轮, 发送给: {sender})")

    # 临时修改收件人为命令发件人
    original_receiver = RECEIVER_EMAIL

    # 搜索并收集结果（修改 send_email 的目标）
    import future as ft
    ft.RECEIVER_EMAIL = sender

    # 使用 --once 模式（单轮）或自定义轮数
    success = ft.search_movie(keyword, max_retries=rounds, interval=90)

    # 恢复
    ft.RECEIVER_EMAIL = original_receiver

    logger.info(f"{'✅' if success else '⚠️'} 搜索完成: [{keyword}] → {sender}")


# ============================================================
# 优雅退出
# ============================================================
running = True

def handle_signal(signum, frame):
    global running
    logger.info("收到退出信号，正在停止...")
    running = False

signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)


# ============================================================
# 主循环
# ============================================================
def main():
    logger.info("=" * 60)
    logger.info("🎬 Future 邮箱触发器已启动")
    logger.info(f"📧 监控邮箱: {EMAIL_ACCOUNT}")
    logger.info(f"🔄 检查间隔: {CHECK_INTERVAL} 秒")
    logger.info(f"📝 触发格式: 邮件主题 '搜: 片名'")
    logger.info(f"📝 持续搜索: 邮件主题 '搜: 片名 持续'")
    logger.info(f"📝 指定轮数: 邮件主题 '搜: 片名 3轮'")
    logger.info("=" * 60)

    load_processed()
    mail = connect_imap()

    while running:
        try:
            if not mail:
                logger.warning("IMAP 连接断开，尝试重连...")
                mail = connect_imap()
                if not mail:
                    time.sleep(CHECK_INTERVAL)
                    continue

            # 检查新邮件
            commands = check_new_emails(mail)

            if commands:
                logger.info(f"📋 收到 {len(commands)} 个搜索命令")
                # 在线程中执行搜索，避免阻塞邮件监控
                for cmd in commands:
                    t = threading.Thread(target=execute_search_and_reply, args=(cmd,), daemon=True)
                    t.start()
                    logger.info(f"已启动搜索线程: [{cmd['keyword']}]")

            time.sleep(CHECK_INTERVAL)

        except imaplib.IMAP4.abort:
            logger.warning("IMAP 连接中断，重连中...")
            mail = None
            time.sleep(10)
        except Exception as e:
            logger.error(f"主循环异常: {e}")
            mail = None
            time.sleep(CHECK_INTERVAL)

    # 清理
    try:
        if mail:
            mail.logout()
    except Exception:
        pass
    logger.info("邮箱触发器已退出")


if __name__ == "__main__":
    main()
