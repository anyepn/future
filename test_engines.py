#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""搜索引擎可用性测试"""

import requests
from bs4 import BeautifulSoup
import time

UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
H = {'User-Agent': UA, 'Accept': 'text/html,application/xhtml+xml,*/*', 'Accept-Language': 'zh-CN,zh;q=0.9'}

keyword = "流浪地球"

engines = [
    {"name": "360搜索", "url": f"https://www.so.com/s?q={keyword}+免费在线观看",
     "rsel": "li.res-list", "tsel": "h3 a"},
    {"name": "搜狗", "url": f"https://www.sogou.com/web?query={keyword}+免费在线观看",
     "rsel": "div.vrwrap, div.rb", "tsel": "h3 a, a.title"},
    {"name": "百度", "url": f"https://www.baidu.com/s?wd={keyword}+免费在线观看&rn=20",
     "rsel": "div.result, div.c-container", "tsel": "h3 a, a.c-font-large"},
    {"name": "Bing中文", "url": f"https://cn.bing.com/search?q={keyword}+免费在线观看&cc=cn&setlang=zh-Hans&count=20",
     "rsel": "li.b_algo", "tsel": "h2 a"},
    {"name": "Bing国际", "url": f"https://www.bing.com/search?q={keyword}+free+watch+online&count=20",
     "rsel": "li.b_algo", "tsel": "h2 a"},
    {"name": "Google", "url": f"https://www.google.com/search?q={keyword}+免费在线观看&num=20",
     "rsel": "div.g", "tsel": "h3"},
]

print("=" * 60)
print(f"搜索引擎可用性测试 (关键词: {keyword})")
print(f"运行环境: GitHub Actions (ubuntu-latest)")
print("=" * 60)
print()

ok_count = 0
for eng in engines:
    try:
        resp = requests.get(eng["url"], headers=H, timeout=15, verify=False, allow_redirects=True)
        soup = BeautifulSoup(resp.text, 'html.parser')
        items = soup.select(eng["rsel"])
        titles = soup.select(eng["tsel"])
        has_captcha = 'captcha' in resp.text.lower() or '验证' in resp.text or resp.status_code == 429

        # 提取前 3 个标题作为样本
        samples = [t.get_text(strip=True)[:40] for t in titles[:3]]

        if has_captcha:
            status = "🚫 验证码拦截"
        elif len(titles) >= 2:
            status = f"✅ 正常 ({len(titles)} 条结果)"
            ok_count += 1
        elif len(titles) == 0 and len(items) == 0:
            status = f"⚠️ 无结果 (HTTP {resp.status_code})"
        else:
            status = f"⚠️ 结果少 ({len(titles)} 条)"

        print(f"【{eng['name']}】 {status}")
        for i, s in enumerate(samples, 1):
            print(f"  {i}. {s}")
        if not samples:
            print(f"  (无样本)")

    except Exception as e:
        print(f"【{eng['name']}】 ❌ 连接失败: {str(e)[:50]}")

    print()
    time.sleep(1)

print("=" * 60)
print(f"测试完成: {ok_count}/{len(engines)} 个引擎正常可用")
print("=" * 60)
