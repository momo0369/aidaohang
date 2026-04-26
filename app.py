from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, NavigableString, Tag
from flask import Flask, abort, g, jsonify, render_template_string, request, send_file, url_for

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = BASE_DIR / "ai_tools.sqlite3"
INDEX_SHELL_PATH = BASE_DIR / "index.html"
DEFAULT_HOME_URL = os.getenv("AI_TOOLS_HOME_URL", "https://ai-bot.cn/")
DEFAULT_REQUEST_DELAY = float(os.getenv("AI_TOOLS_DELAY", "0.3"))
DEFAULT_REQUEST_TIMEOUT = int(os.getenv("AI_TOOLS_TIMEOUT", "20"))
DEFAULT_SYNC_INTERVAL = int(os.getenv("AI_TOOLS_SYNC_INTERVAL", "21600"))
DEFAULT_AUTO_SYNC = os.getenv("AI_TOOLS_AUTO_SYNC", "1") != "0"
USER_AGENT = os.getenv(
    "AI_TOOLS_UA",
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36 ai-tools-local-mirror/1.0"
    ),
)

TOOL_COLUMNS = (
    "source_id",
    "slug",
    "title",
    "subtitle",
    "description",
    "image_url",
    "detail_source_url",
    "official_url",
    "main_category_slug",
    "main_category_title",
    "subcategory_slug",
    "subcategory_title",
    "home_order",
    "tags_json",
    "sections_json",
    "raw_html",
    "fetched_at",
    "created_at",
    "updated_at",
)

CATEGORY_COLUMNS = (
    "slug",
    "title",
    "parent_slug",
    "source_url",
    "kind",
    "sort_order",
    "created_at",
    "updated_at",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
    slug TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    parent_slug TEXT,
    source_url TEXT,
    kind TEXT NOT NULL DEFAULT 'main',
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tools (
    source_id TEXT PRIMARY KEY,
    slug TEXT,
    title TEXT NOT NULL,
    subtitle TEXT,
    description TEXT,
    image_url TEXT,
    detail_source_url TEXT,
    official_url TEXT,
    main_category_slug TEXT,
    main_category_title TEXT,
    subcategory_slug TEXT,
    subcategory_title TEXT,
    home_order INTEGER NOT NULL DEFAULT 0,
    tags_json TEXT NOT NULL DEFAULT '[]',
    sections_json TEXT NOT NULL DEFAULT '[]',
    raw_html TEXT,
    fetched_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_categories_parent ON categories(parent_slug, sort_order);
CREATE INDEX IF NOT EXISTS idx_tools_main_category ON tools(main_category_slug, home_order);
CREATE INDEX IF NOT EXISTS idx_tools_subcategory ON tools(subcategory_slug, home_order);
"""

BASE_TEMPLATE = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ page_title }}</title>
  <meta name="description" content="{{ meta_description }}">
  <style>
    :root {
      --bg: #f4f5f7;
      --surface: rgba(255, 255, 255, 0.86);
      --surface-strong: #ffffff;
      --text: #172033;
      --muted: #5f6b85;
      --line: rgba(23, 32, 51, 0.08);
      --primary: #1c63ff;
      --primary-soft: rgba(28, 99, 255, 0.12);
      --accent: #ff8a3d;
      --shadow: 0 18px 52px rgba(18, 34, 66, 0.12);
      --radius: 24px;
      --radius-sm: 16px;
      --container: 1180px;
      --font: "IBM Plex Sans", "PingFang SC", "Microsoft YaHei", sans-serif;
    }

    * {
      box-sizing: border-box;
    }

    body {
      margin: 0;
      font-family: var(--font);
      color: var(--text);
      background:
        radial-gradient(circle at top left, rgba(28, 99, 255, 0.18), transparent 32%),
        radial-gradient(circle at top right, rgba(255, 138, 61, 0.2), transparent 22%),
        linear-gradient(180deg, #eef2f9 0%, #f7f8fb 40%, #f4f5f7 100%);
      min-height: 100vh;
    }

    a {
      color: inherit;
      text-decoration: none;
    }

    img {
      max-width: 100%;
      display: block;
    }

    .shell {
      width: min(var(--container), calc(100vw - 32px));
      margin: 0 auto;
      padding: 28px 0 72px;
    }

    .topbar {
      display: flex;
      gap: 16px;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 18px;
      flex-wrap: wrap;
    }

    .brand {
      display: inline-flex;
      align-items: center;
      gap: 12px;
      font-weight: 700;
      letter-spacing: 0.02em;
    }

    .brand-mark {
      width: 42px;
      height: 42px;
      border-radius: 14px;
      background: linear-gradient(135deg, var(--primary), #56a0ff 60%, #b8d1ff 100%);
      box-shadow: 0 10px 24px rgba(28, 99, 255, 0.24);
      position: relative;
      overflow: hidden;
    }

    .brand-mark::before,
    .brand-mark::after {
      content: "";
      position: absolute;
      inset: auto;
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.8);
    }

    .brand-mark::before {
      width: 18px;
      height: 18px;
      left: 8px;
      top: 8px;
    }

    .brand-mark::after {
      width: 10px;
      height: 10px;
      right: 8px;
      bottom: 8px;
    }

    .brand-meta {
      display: grid;
      gap: 2px;
    }

    .brand-meta small {
      color: var(--muted);
      font-weight: 500;
    }

    .toolbar {
      display: flex;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
    }

    .chip {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 8px 14px;
      background: rgba(255, 255, 255, 0.72);
      border: 1px solid rgba(255, 255, 255, 0.9);
      border-radius: 999px;
      color: var(--muted);
      font-size: 14px;
      backdrop-filter: blur(18px);
    }

    .chip strong {
      color: var(--text);
    }

    .hero {
      background: linear-gradient(145deg, rgba(255, 255, 255, 0.92), rgba(247, 249, 255, 0.86));
      border: 1px solid rgba(255, 255, 255, 0.9);
      border-radius: 32px;
      padding: 34px;
      box-shadow: var(--shadow);
      position: relative;
      overflow: hidden;
      margin-bottom: 24px;
    }

    .hero::before {
      content: "";
      position: absolute;
      width: 320px;
      height: 320px;
      border-radius: 999px;
      background: radial-gradient(circle, rgba(28, 99, 255, 0.16), transparent 68%);
      top: -120px;
      right: -90px;
      pointer-events: none;
    }

    .hero h1,
    .hero h2 {
      margin: 0 0 12px;
      font-size: clamp(30px, 4vw, 52px);
      line-height: 1.05;
      max-width: 760px;
    }

    .hero p {
      margin: 0;
      max-width: 760px;
      color: var(--muted);
      font-size: 16px;
      line-height: 1.7;
    }

    .searchbar {
      margin-top: 24px;
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 12px;
      max-width: 720px;
    }

    .searchbar input {
      width: 100%;
      border: 1px solid rgba(23, 32, 51, 0.08);
      border-radius: 18px;
      padding: 16px 18px;
      font: inherit;
      background: rgba(255, 255, 255, 0.9);
      box-shadow: inset 0 1px 1px rgba(12, 18, 28, 0.04);
    }

    .button,
    button.button {
      border: 0;
      border-radius: 18px;
      padding: 14px 20px;
      font: inherit;
      font-weight: 700;
      cursor: pointer;
      color: white;
      background: linear-gradient(135deg, var(--primary), #2f82ff);
      box-shadow: 0 14px 28px rgba(28, 99, 255, 0.24);
    }

    .button.secondary {
      background: white;
      color: var(--text);
      box-shadow: inset 0 0 0 1px var(--line);
    }

    .button.ghost {
      background: rgba(255, 255, 255, 0.78);
      color: var(--text);
      box-shadow: inset 0 0 0 1px rgba(23, 32, 51, 0.08);
    }

    .quick-nav {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin: 0 0 24px;
    }

    .quick-nav a {
      padding: 10px 14px;
      border-radius: 14px;
      background: rgba(255, 255, 255, 0.74);
      border: 1px solid rgba(255, 255, 255, 0.9);
      color: var(--muted);
      font-size: 14px;
      transition: transform 160ms ease, background 160ms ease;
    }

    .quick-nav a:hover {
      transform: translateY(-1px);
      background: rgba(255, 255, 255, 0.95);
    }

    .section {
      margin-top: 34px;
      scroll-margin-top: 24px;
    }

    .section-head {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: end;
      margin-bottom: 18px;
      flex-wrap: wrap;
    }

    .section-head h2,
    .section-head h3 {
      margin: 0;
      font-size: clamp(24px, 2.4vw, 34px);
      line-height: 1.15;
    }

    .section-head p {
      margin: 8px 0 0;
      color: var(--muted);
    }

    .section-actions {
      display: inline-flex;
      gap: 10px;
      flex-wrap: wrap;
    }

    .section-link {
      font-size: 14px;
      color: var(--primary);
      font-weight: 700;
    }

    .group {
      margin: 22px 0 0;
    }

    .group h3 {
      margin: 0 0 14px;
      font-size: 18px;
      color: var(--text);
    }

    .muted {
      color: var(--muted);
    }

    .empty {
      padding: 26px;
      border-radius: var(--radius);
      background: var(--surface);
      border: 1px solid rgba(255, 255, 255, 0.9);
      box-shadow: var(--shadow);
      color: var(--muted);
    }

    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 16px;
    }

    .card {
      display: block;
      padding: 18px;
      border-radius: 22px;
      background: var(--surface);
      border: 1px solid rgba(255, 255, 255, 0.94);
      box-shadow: var(--shadow);
      backdrop-filter: blur(18px);
      transition: transform 180ms ease, box-shadow 180ms ease;
    }

    .card:hover {
      transform: translateY(-3px);
      box-shadow: 0 24px 42px rgba(18, 34, 66, 0.16);
    }

    .card-top {
      display: grid;
      grid-template-columns: 52px 1fr;
      gap: 14px;
      align-items: center;
      margin-bottom: 12px;
    }

    .card-logo {
      width: 52px;
      height: 52px;
      border-radius: 16px;
      background: linear-gradient(135deg, rgba(28, 99, 255, 0.12), rgba(255, 138, 61, 0.14));
      overflow: hidden;
      border: 1px solid rgba(23, 32, 51, 0.06);
    }

    .card-logo img {
      width: 100%;
      height: 100%;
      object-fit: cover;
    }

    .card-title {
      margin: 0;
      font-size: 18px;
      line-height: 1.25;
    }

    .card-category {
      margin-top: 4px;
      color: var(--muted);
      font-size: 13px;
    }

    .card-desc {
      color: var(--muted);
      font-size: 14px;
      line-height: 1.65;
      min-height: 3.3em;
    }

    .card-actions {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
      margin-top: 16px;
    }

    .pill {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 7px 12px;
      border-radius: 999px;
      background: var(--primary-soft);
      color: var(--primary);
      font-weight: 700;
      font-size: 13px;
    }

    .detail-layout {
      display: grid;
      gap: 24px;
      grid-template-columns: minmax(0, 2fr) minmax(280px, 0.95fr);
      align-items: start;
    }

    .article,
    .aside-panel {
      background: var(--surface);
      border: 1px solid rgba(255, 255, 255, 0.92);
      border-radius: 28px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(18px);
    }

    .article {
      padding: 28px;
    }

    .aside-panel {
      padding: 22px;
      position: sticky;
      top: 18px;
    }

    .hero-image {
      margin-top: 22px;
      border-radius: 24px;
      overflow: hidden;
      border: 1px solid rgba(23, 32, 51, 0.08);
      background: rgba(245, 247, 252, 0.9);
    }

    .meta-row,
    .action-row,
    .tag-row {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 18px;
    }

    .detail-section {
      margin-top: 28px;
      padding-top: 28px;
      border-top: 1px solid var(--line);
    }

    .detail-section:first-of-type {
      margin-top: 0;
      padding-top: 0;
      border-top: 0;
    }

    .detail-section h2 {
      margin: 0 0 14px;
      font-size: 24px;
    }

    .detail-section p,
    .detail-section li {
      color: var(--muted);
      line-height: 1.8;
    }

    .detail-section ul,
    .detail-section ol {
      margin: 0;
      padding-left: 22px;
    }

    .aside-panel h3 {
      margin: 0 0 14px;
      font-size: 18px;
    }

    .stack {
      display: grid;
      gap: 14px;
    }

    .stack .card {
      padding: 16px;
      border-radius: 18px;
      box-shadow: none;
      background: rgba(255, 255, 255, 0.78);
    }

    .footer {
      margin-top: 36px;
      color: var(--muted);
      font-size: 14px;
      text-align: center;
    }

    @media (max-width: 940px) {
      .detail-layout {
        grid-template-columns: 1fr;
      }

      .aside-panel {
        position: static;
      }
    }

    @media (max-width: 720px) {
      .hero {
        padding: 24px;
        border-radius: 26px;
      }

      .searchbar {
        grid-template-columns: 1fr;
      }

      .shell {
        width: min(var(--container), calc(100vw - 20px));
        padding-top: 18px;
      }
    }
  </style>
</head>
<body>
  <div class="shell">
    <div class="topbar">
      <a href="{{ url_for('home') }}" class="brand">
        <span class="brand-mark"></span>
        <span class="brand-meta">
          <span>AI 工具站本地后端</span>
          <small>sqlite + Python + Flask</small>
        </span>
      </a>
      <div class="toolbar">
        {% if stats %}
          <span class="chip"><strong>{{ stats.tool_count }}</strong> 个工具</span>
          <span class="chip"><strong>{{ stats.category_count }}</strong> 个分类</span>
          {% if stats.last_sync %}
            <span class="chip">最近同步 {{ stats.last_sync }}</span>
          {% endif %}
        {% endif %}
      </div>
    </div>
    {{ content | safe }}
    <div class="footer">
      数据源解析后写入 sqlite，本页完全由 Python 从数据库回读后渲染。
    </div>
  </div>
</body>
</html>
"""

HOME_CONTENT_TEMPLATE = """
<section class="hero">
  <h1>AI 工具站数据已落到 sqlite，子页面直接由 Python 渲染</h1>
  <p>首页负责发现工具链接，详情页按顺序请求后写入数据库。页面读取 sqlite 数据，不再依赖原始 HTML 文件直接展示。</p>
  <form class="searchbar" method="get" action="{{ url_for('home') }}">
    <input type="search" name="q" value="{{ query }}" placeholder="搜索工具名、简介或分类，例如：AI 写作、Midjourney、PPT">
    <button class="button" type="submit">搜索</button>
  </form>
  <div class="meta-row">
    <span class="chip">抓取源首页：<strong>{{ source_label }}</strong></span>
    {% if query %}
      <a href="{{ url_for('home') }}" class="button ghost">清空搜索</a>
    {% endif %}
  </div>
</section>

{% if categories %}
  <nav class="quick-nav">
    {% for category in categories %}
      <a href="#{{ category.slug }}">{{ category.title }}</a>
    {% endfor %}
  </nav>
{% endif %}

{% if query %}
  <section class="section">
    <div class="section-head">
      <div>
        <h2>搜索结果</h2>
        <p>共找到 {{ search_total }} 个匹配项</p>
      </div>
    </div>
    {% if search_tools %}
      <div class="grid">
        {% for tool in search_tools %}
          <a class="card" href="{{ url_for('tool_detail', tool_key=tool.source_id) }}">
            <div class="card-top">
              <div class="card-logo">
                {% if tool.image_url %}
                  <img src="{{ tool.image_url }}" alt="{{ tool.title }}">
                {% endif %}
              </div>
              <div>
                <h3 class="card-title">{{ tool.title }}</h3>
                <div class="card-category">{{ tool.main_category_title or '未分类' }}{% if tool.subcategory_title %} / {{ tool.subcategory_title }}{% endif %}</div>
              </div>
            </div>
            <div class="card-desc">{{ tool.subtitle or tool.description or '暂无简介' }}</div>
            <div class="card-actions">
              <span class="pill">查看详情</span>
              {% if tool.official_url %}
                <span class="section-link">官网可用</span>
              {% endif %}
            </div>
          </a>
        {% endfor %}
      </div>
    {% else %}
      <div class="empty">没有匹配到结果，换一个关键词试试。</div>
    {% endif %}
  </section>
{% else %}
  {% if sections %}
    {% for section in sections %}
      <section id="{{ section.slug }}" class="section">
        <div class="section-head">
          <div>
            <h2>{{ section.title }}</h2>
            <p>{{ section.total }} 个工具，已写入 sqlite</p>
          </div>
          <div class="section-actions">
            <a class="section-link" href="{{ url_for('category_page', slug=section.slug) }}">分类页</a>
            {% if section.source_url %}
              <a class="section-link" href="{{ section.source_url }}" target="_blank" rel="noopener">源站</a>
            {% endif %}
          </div>
        </div>
        {% for group in section.groups %}
          <div class="group">
            {% if group.title %}
              <h3>{{ group.title }}</h3>
            {% endif %}
            <div class="grid">
              {% for tool in group.tools %}
                <a class="card" href="{{ url_for('tool_detail', tool_key=tool.source_id) }}">
                  <div class="card-top">
                    <div class="card-logo">
                      {% if tool.image_url %}
                        <img src="{{ tool.image_url }}" alt="{{ tool.title }}">
                      {% endif %}
                    </div>
                    <div>
                      <h3 class="card-title">{{ tool.title }}</h3>
                      <div class="card-category">{{ tool.subcategory_title or section.title }}</div>
                    </div>
                  </div>
                  <div class="card-desc">{{ tool.subtitle or tool.description or '暂无简介' }}</div>
                  <div class="card-actions">
                    <span class="pill">本地详情页</span>
                    {% if tool.official_url %}
                      <span class="section-link">访问官网</span>
                    {% endif %}
                  </div>
                </a>
              {% endfor %}
            </div>
          </div>
        {% endfor %}
      </section>
    {% endfor %}
  {% else %}
    <div class="empty">数据库中还没有工具数据。先运行 <code>python app.py sync</code>，或使用 <code>python app.py sync --home-file index.html --skip-details</code> 做本地初始化。</div>
  {% endif %}
{% endif %}
"""

CATEGORY_CONTENT_TEMPLATE = """
<section class="hero">
  <h1>{{ category.title }}</h1>
  <p>{{ category_hint }}</p>
  <form class="searchbar" method="get" action="{{ url_for('category_page', slug=category.slug) }}">
    <input type="search" name="q" value="{{ query }}" placeholder="在当前分类内搜索">
    <button class="button" type="submit">筛选</button>
  </form>
  <div class="meta-row">
    <a href="{{ url_for('home') }}" class="button secondary">返回首页</a>
    {% if category.source_url %}
      <a href="{{ category.source_url }}" class="button ghost" target="_blank" rel="noopener">查看源站分类</a>
    {% endif %}
  </div>
</section>

{% if groups %}
  {% for group in groups %}
    <section class="section">
      <div class="section-head">
        <div>
          <h2>{{ group.title }}</h2>
          <p>{{ group.tools|length }} 个工具</p>
        </div>
      </div>
      <div class="grid">
        {% for tool in group.tools %}
          <a class="card" href="{{ url_for('tool_detail', tool_key=tool.source_id) }}">
            <div class="card-top">
              <div class="card-logo">
                {% if tool.image_url %}
                  <img src="{{ tool.image_url }}" alt="{{ tool.title }}">
                {% endif %}
              </div>
              <div>
                <h3 class="card-title">{{ tool.title }}</h3>
                <div class="card-category">{{ tool.main_category_title or '未分类' }}{% if tool.subcategory_title %} / {{ tool.subcategory_title }}{% endif %}</div>
              </div>
            </div>
            <div class="card-desc">{{ tool.subtitle or tool.description or '暂无简介' }}</div>
            <div class="card-actions">
              <span class="pill">查看详情</span>
              {% if tool.official_url %}
                <span class="section-link">官网可用</span>
              {% endif %}
            </div>
          </a>
        {% endfor %}
      </div>
    </section>
  {% endfor %}
{% else %}
  <div class="empty">当前分类没有匹配到工具。</div>
{% endif %}
"""

DETAIL_CONTENT_TEMPLATE = """
<section class="hero">
  <div class="meta-row">
    <a href="{{ url_for('home') }}" class="button secondary">返回首页</a>
    {% if tool.main_category_slug %}
      <a href="{{ url_for('category_page', slug=tool.main_category_slug) }}" class="button ghost">{{ tool.main_category_title }}</a>
    {% endif %}
    {% if tool.subcategory_slug %}
      <a href="{{ url_for('category_page', slug=tool.subcategory_slug) }}" class="button ghost">{{ tool.subcategory_title }}</a>
    {% endif %}
  </div>
  <h1>{{ tool.title }}</h1>
  <p>{{ tool.subtitle or tool.description or '当前详情页已经入库，但原页面没有可抽取的简介。' }}</p>
  <div class="tag-row">
    {% for tag in tool.tags %}
      <span class="chip">{{ tag }}</span>
    {% endfor %}
    {% if not tool.tags %}
      <span class="chip">暂无标签</span>
    {% endif %}
  </div>
  <div class="action-row">
    {% if tool.official_url %}
      <a href="{{ tool.official_url }}" class="button" target="_blank" rel="noopener">访问官网</a>
    {% endif %}
    {% if tool.detail_source_url %}
      <a href="{{ tool.detail_source_url }}" class="button ghost" target="_blank" rel="noopener">查看源页</a>
    {% endif %}
  </div>
  {% if tool.image_url %}
    <div class="hero-image">
      <img src="{{ tool.image_url }}" alt="{{ tool.title }}">
    </div>
  {% endif %}
</section>

<div class="detail-layout">
  <article class="article">
    {% if tool.sections %}
      {% for section in tool.sections %}
        <section class="detail-section">
          <h2>{{ section.heading }}</h2>
          {% for block in section.blocks %}
            {% if block.kind == 'paragraph' %}
              <p>{{ block.text }}</p>
            {% elif block.kind == 'list' %}
              <ul>
                {% for item in block["items"] %}
                  <li>
                    {% if item["local_source_id"] %}
                      <a href="{{ url_for('tool_detail', tool_key=item['local_source_id']) }}">{{ item["text"] }}</a>
                    {% elif item["url"] %}
                      <a href="{{ item['url'] }}" target="_blank" rel="noopener">{{ item["text"] }}</a>
                    {% else %}
                      {{ item["text"] }}
                    {% endif %}
                  </li>
                {% endfor %}
              </ul>
            {% endif %}
          {% endfor %}
        </section>
      {% endfor %}
    {% else %}
      <section class="detail-section">
        <h2>内容说明</h2>
        <p>这个工具已经在首页发现并写入数据库，但还没有抓取到完整详情。可以重新执行 <code>python app.py sync --refresh</code> 继续补全详情内容。</p>
      </section>
    {% endif %}
  </article>

  <aside class="aside-panel">
    <h3>页面信息</h3>
    <div class="stack">
      <div class="card">
        <div class="muted">工具 ID</div>
        <strong>{{ tool.source_id }}</strong>
      </div>
      {% if tool.main_category_title %}
        <div class="card">
          <div class="muted">所属分类</div>
          <strong>{{ tool.main_category_title }}</strong>
          {% if tool.subcategory_title %}
            <div class="muted" style="margin-top: 6px;">{{ tool.subcategory_title }}</div>
          {% endif %}
        </div>
      {% endif %}
      {% if tool.fetched_at %}
        <div class="card">
          <div class="muted">详情同步时间</div>
          <strong>{{ tool.fetched_at }}</strong>
        </div>
      {% endif %}
      {% if related_tools %}
        <div class="card">
          <div class="muted" style="margin-bottom: 10px;">同分类推荐</div>
          <div class="stack">
            {% for related in related_tools %}
              <a class="card" href="{{ url_for('tool_detail', tool_key=related.source_id) }}">
                <strong>{{ related.title }}</strong>
                <div class="muted" style="margin-top: 8px;">{{ related.subtitle or related.description or '暂无简介' }}</div>
              </a>
            {% endfor %}
          </div>
        </div>
      {% endif %}
    </div>
  </aside>
</div>
"""

app = Flask(__name__)
app.config.from_mapping(
    DB_PATH=str(DEFAULT_DB_PATH),
    HOME_SOURCE=DEFAULT_HOME_URL,
    AUTO_SYNC=DEFAULT_AUTO_SYNC,
    SYNC_INTERVAL=DEFAULT_SYNC_INTERVAL,
)

SYNC_STATE_LOCK = threading.Lock()
SYNC_JOB_LOCK = threading.Lock()
SYNC_STATE: dict[str, Any] = {
    "running": False,
    "last_started_at": None,
    "last_finished_at": None,
    "last_error": None,
    "last_result": None,
}
SYNC_THREAD: threading.Thread | None = None


def utcnow() -> str:
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def normalize_space(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def slugify_title(value: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z]+", "-", value).strip("-").lower()
    return slug[:96] or "tool"


def resolve_url(base_url: str, maybe_url: str | None) -> str | None:
    if not maybe_url:
        return None
    candidate = maybe_url.strip()
    if not candidate:
        return None
    return urljoin(base_url, candidate)


def is_internal_source(url: str | None) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    if not parsed.netloc:
        return True
    return parsed.netloc.endswith("ai-bot.cn")


def extract_source_id(url: str | None, fallback: str | None = None) -> str | None:
    if fallback:
        return str(fallback)
    if not url:
        return None
    match = re.search(r"/sites/(\d+)\.html", url)
    if match:
        return match.group(1)
    parsed = urlparse(url)
    segment = parsed.path.rstrip("/").split("/")[-1]
    segment = segment.removesuffix(".html")
    segment = re.sub(r"[^0-9A-Za-z_-]+", "", segment)
    return segment or None


def connect_db(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect_db(app.config["DB_PATH"])
        init_db(g.db)
    return g.db


@app.teardown_appcontext
def close_db(_: BaseException | None) -> None:
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
    )
    return session


def fetch_html(session: requests.Session, url: str, timeout: int = DEFAULT_REQUEST_TIMEOUT) -> str:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.encoding or response.apparent_encoding or "utf-8"
    return response.text


def read_home_source(
    session: requests.Session,
    *,
    home_url: str,
    home_file: str | None,
) -> tuple[str, str]:
    if home_file:
        path = Path(home_file)
        html = path.read_text(encoding="utf-8", errors="ignore")
        return home_url, html
    return home_url, fetch_html(session, home_url)


def upsert_category(conn: sqlite3.Connection, category: dict[str, Any]) -> None:
    existing = conn.execute(
        "SELECT * FROM categories WHERE slug = ?",
        (category["slug"],),
    ).fetchone()
    now = utcnow()
    payload = dict(existing) if existing else {}
    payload.setdefault("created_at", now)
    payload["updated_at"] = now
    for key in ("slug", "title", "parent_slug", "source_url", "kind", "sort_order"):
        value = category.get(key)
        if value not in (None, ""):
            payload[key] = value
        else:
            payload.setdefault(key, None if key != "kind" else "main")
    columns = ", ".join(CATEGORY_COLUMNS)
    placeholders = ", ".join("?" for _ in CATEGORY_COLUMNS)
    updates = ", ".join(f"{column}=excluded.{column}" for column in CATEGORY_COLUMNS[1:])
    conn.execute(
        f"""
        INSERT INTO categories ({columns})
        VALUES ({placeholders})
        ON CONFLICT(slug) DO UPDATE SET {updates}
        """,
        [payload.get(column) for column in CATEGORY_COLUMNS],
    )


def upsert_tool(conn: sqlite3.Connection, tool: dict[str, Any]) -> None:
    existing = conn.execute(
        "SELECT * FROM tools WHERE source_id = ?",
        (tool["source_id"],),
    ).fetchone()
    payload = dict(existing) if existing else {}
    now = utcnow()
    payload.setdefault("created_at", now)
    payload["updated_at"] = now
    for key, value in tool.items():
        if value in (None, "", []):
            payload.setdefault(key, None if key not in {"tags_json", "sections_json", "home_order"} else value)
            continue
        payload[key] = value
    payload.setdefault("slug", slugify_title(payload.get("title", payload["source_id"])))
    payload.setdefault("tags_json", "[]")
    payload.setdefault("sections_json", "[]")
    payload.setdefault("home_order", 0)
    columns = ", ".join(TOOL_COLUMNS)
    placeholders = ", ".join("?" for _ in TOOL_COLUMNS)
    updates = ", ".join(f"{column}=excluded.{column}" for column in TOOL_COLUMNS[1:])
    conn.execute(
        f"""
        INSERT INTO tools ({columns})
        VALUES ({placeholders})
        ON CONFLICT(source_id) DO UPDATE SET {updates}
        """,
        [payload.get(column) for column in TOOL_COLUMNS],
    )


def card_name(card: Tag) -> str:
    candidates = (
        normalize_space(card.select_one("strong").get_text(" ", strip=True)) if card.select_one("strong") else "",
        normalize_space(card.select_one(".text-sm").get_text(" ", strip=True)) if card.select_one(".text-sm") else "",
        card.select_one("img").get("alt", "").strip() if card.select_one("img") else "",
    )
    return next((item for item in candidates if item), "")


def parse_card(
    card: Tag,
    *,
    base_url: str,
    main_category: dict[str, Any],
    subcategory: dict[str, Any] | None,
    home_order: int,
) -> dict[str, Any] | None:
    href = resolve_url(base_url, card.get("href"))
    source_id = extract_source_id(href, fallback=card.get("data-id"))
    if not source_id:
        return None
    detail_source_url = href if is_internal_source(href) else None
    official_url = resolve_url(base_url, card.get("data-url")) or (href if not detail_source_url else None)
    image = card.select_one("img")
    image_url = None
    if image:
        image_url = resolve_url(base_url, image.get("data-src") or image.get("src"))
    title = card_name(card) or f"工具 {source_id}"
    description = normalize_space(
        card.get("title")
        or (card.select_one("p").get_text(" ", strip=True) if card.select_one("p") else "")
    )
    return {
        "source_id": source_id,
        "slug": slugify_title(title),
        "title": title,
        "description": description,
        "image_url": image_url,
        "detail_source_url": detail_source_url,
        "official_url": official_url,
        "main_category_slug": main_category["slug"],
        "main_category_title": main_category["title"],
        "subcategory_slug": subcategory["slug"] if subcategory else None,
        "subcategory_title": subcategory["title"] if subcategory else None,
        "home_order": home_order,
    }


def header_from_node(node: Tag) -> tuple[str, str] | None:
    if node.name == "h4":
        heading = node
    else:
        heading = None
        for child in node.children:
            if isinstance(child, Tag) and child.name == "h4":
                heading = child
                break
    if not heading:
        return None
    marker = heading.find(id=re.compile(r"^term-\d+$"))
    slug = marker.get("id") if marker else None
    title = normalize_space(heading.get_text(" ", strip=True))
    if not slug or not title:
        return None
    return slug, title


def is_section_header(node: Tag) -> bool:
    if not isinstance(node, Tag):
        return False
    parsed = header_from_node(node)
    return parsed is not None


def parse_homepage(html: str, *, base_url: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    soup = BeautifulSoup(html, "html.parser")
    layout = soup.select_one(".content-layout") or soup.body or soup
    children = [child for child in layout.children if isinstance(child, Tag)]
    categories: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
    tools: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
    main_sort = 0
    index = 0

    while index < len(children):
        node = children[index]
        header = header_from_node(node)
        if not header:
            index += 1
            continue

        main_sort += 1
        main_slug, main_title = header
        source_url = None
        if node.name != "h4":
            button = node.select_one("a.btn-move[href]")
            source_url = resolve_url(base_url, button.get("href")) if button else None
        main_category = {
            "slug": main_slug,
            "title": main_title,
            "parent_slug": None,
            "source_url": source_url,
            "kind": "main",
            "sort_order": main_sort,
        }
        categories.setdefault(main_slug, main_category)

        block_nodes: list[Tag] = []
        lookahead = index + 1
        while lookahead < len(children) and not is_section_header(children[lookahead]):
            block_nodes.append(children[lookahead])
            lookahead += 1

        for block in block_nodes:
            button = block.select_one("a.btn-move[href]")
            if button and not categories[main_slug].get("source_url"):
                categories[main_slug]["source_url"] = resolve_url(base_url, button.get("href"))

        tab_map: dict[str, dict[str, Any]] = {}
        sub_sort = 0
        for block in block_nodes:
            for link in block.select("a.nav-link.tab-noajax[id][href^='#tab-']"):
                sub_sort += 1
                tab_id = link.get("href", "").lstrip("#")
                subcategory = {
                    "slug": link.get("id"),
                    "title": normalize_space(link.get_text(" ", strip=True)),
                    "parent_slug": main_slug,
                    "source_url": resolve_url(base_url, link.get("data-link")),
                    "kind": "sub",
                    "sort_order": sub_sort,
                }
                if subcategory["slug"] and subcategory["title"]:
                    categories.setdefault(subcategory["slug"], subcategory)
                    tab_map[tab_id] = subcategory

        pane_nodes = [pane for block in block_nodes for pane in block.select("div.tab-pane[id]")]
        if pane_nodes:
            for pane in pane_nodes:
                pane_id = pane.get("id")
                subcategory = tab_map.get(pane_id)
                if not subcategory:
                    inferred_slug = f"term-{pane_id.removeprefix('tab-')}"
                    subcategory = {
                        "slug": inferred_slug,
                        "title": normalize_space(pane.get("data-title") or inferred_slug),
                        "parent_slug": main_slug,
                        "source_url": None,
                        "kind": "sub",
                        "sort_order": 999,
                    }
                for card in pane.select("a.card[data-id], a.card[href]"):
                    parsed = parse_card(
                        card,
                        base_url=base_url,
                        main_category=main_category,
                        subcategory=subcategory,
                        home_order=len(tools) + 1,
                    )
                    if not parsed:
                        continue
                    existing = tools.get(parsed["source_id"])
                    if existing:
                        continue
                    tools[parsed["source_id"]] = parsed
        else:
            seen_ids: set[str] = set()
            for block in block_nodes:
                for card in block.select("a.card[data-id], a.card[href]"):
                    parsed = parse_card(
                        card,
                        base_url=base_url,
                        main_category=main_category,
                        subcategory=None,
                        home_order=len(tools) + 1,
                    )
                    if not parsed or parsed["source_id"] in seen_ids:
                        continue
                    seen_ids.add(parsed["source_id"])
                    existing = tools.get(parsed["source_id"])
                    if existing:
                        continue
                    tools[parsed["source_id"]] = parsed

        index = lookahead

    fallback_category = {
        "slug": "term-home-extra",
        "title": "首页推荐",
        "parent_slug": None,
        "source_url": base_url,
        "kind": "main",
        "sort_order": main_sort + 1,
    }
    missing_cards = 0
    for card in soup.select("a.card[data-id], a.card[href]"):
        parsed = parse_card(
            card,
            base_url=base_url,
            main_category=fallback_category,
            subcategory=None,
            home_order=len(tools) + 1,
        )
        if not parsed or parsed["source_id"] in tools:
            continue
        if missing_cards == 0:
            categories.setdefault(fallback_category["slug"], fallback_category)
        tools[parsed["source_id"]] = parsed
        missing_cards += 1

    return list(categories.values()), list(tools.values())


def extract_meta_content(soup: BeautifulSoup, key: str, attr: str = "name") -> str | None:
    tag = soup.find("meta", attrs={attr: key})
    if tag and tag.get("content"):
        return normalize_space(tag["content"])
    return None


def top_text_summary(main: Tag | None, fallback: str | None) -> str | None:
    if not main:
        return fallback
    heading = main.find("h1")
    if not heading:
        return fallback
    for sibling in heading.next_siblings:
        if isinstance(sibling, Tag) and sibling.name in {"h2", "h3"}:
            break
        if isinstance(sibling, NavigableString):
            continue
        if not isinstance(sibling, Tag):
            continue
        text = normalize_space(sibling.get_text(" ", strip=True))
        if not text:
            continue
        if "标签" in text or "访问官网" in text:
            continue
        return text
    return fallback


def extract_top_tags(main: Tag | None) -> list[str]:
    if not main:
        return []
    tags: list[str] = []
    heading = main.find("h1")
    if heading:
        for sibling in heading.next_siblings:
            if isinstance(sibling, Tag) and sibling.name in {"h2", "h3"}:
                break
            if not isinstance(sibling, Tag):
                continue
            text = normalize_space(sibling.get_text(" ", strip=True))
            if "标签" not in text:
                continue
            anchors = [normalize_space(anchor.get_text(" ", strip=True)) for anchor in sibling.find_all("a")]
            tags.extend([item for item in anchors if item])
    if tags:
        return list(dict.fromkeys(tags))
    for selector in (".breadcrumb a", "nav a", ".site-tags a"):
        candidates = [normalize_space(anchor.get_text(" ", strip=True)) for anchor in main.select(selector)]
        tags.extend([item for item in candidates if item and item not in {"首页"}])
        if tags:
            break
    return list(dict.fromkeys(tags))


def extract_official_url(main: Tag | None, page_url: str) -> str | None:
    if not main:
        return None
    heading = main.find("h1")
    search_nodes: list[Tag] = [main]
    if heading:
        buffer: list[Tag] = []
        for sibling in heading.next_siblings:
            if isinstance(sibling, Tag) and sibling.name in {"h2", "h3"}:
                break
            if isinstance(sibling, Tag):
                buffer.append(sibling)
        if buffer:
            search_nodes = buffer
    for node in search_nodes:
        for anchor in node.find_all("a", href=True):
            href = resolve_url(page_url, anchor.get("href"))
            text = normalize_space(anchor.get_text(" ", strip=True))
            if not href or is_internal_source(href):
                continue
            if any(keyword in text for keyword in ("访问官网", "官网", "产品官网", "立即访问", "立即体验")):
                return href
    for anchor in main.find_all("a", href=True):
        href = resolve_url(page_url, anchor.get("href"))
        if href and not is_internal_source(href):
            return href
    return None


def extract_list_items(list_tag: Tag, *, page_url: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in list_tag.find_all("li", recursive=False):
        text = normalize_space(item.get_text(" ", strip=True))
        if not text:
            continue
        anchor = item.find("a", href=True)
        url = resolve_url(page_url, anchor.get("href")) if anchor else None
        local_source_id = extract_source_id(url) if is_internal_source(url) else None
        items.append({"text": text, "url": url, "local_source_id": local_source_id})
    return items


def nested_blocks(node: Tag, *, page_url: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    if node.name == "p":
        text = normalize_space(node.get_text(" ", strip=True))
        if text:
            blocks.append({"kind": "paragraph", "text": text})
        return blocks
    if node.name in {"ul", "ol"}:
        items = extract_list_items(node, page_url=page_url)
        if items:
            blocks.append({"kind": "list", "items": items})
        return blocks

    child_tags = [child for child in node.children if isinstance(child, Tag)]
    direct_block_found = False
    for child in child_tags:
        if child.name in {"p", "ul", "ol"}:
            direct_block_found = True
            blocks.extend(nested_blocks(child, page_url=page_url))
        elif child.select("a.card[href]"):
            related_items = []
            for anchor in child.select("a.card[href]"):
                title = card_name(anchor)
                summary = normalize_space(
                    anchor.get("title")
                    or (anchor.select_one("p").get_text(" ", strip=True) if anchor.select_one("p") else "")
                )
                text = f"{title}：{summary}" if title and summary else (title or summary)
                href = resolve_url(page_url, anchor.get("href"))
                if not text:
                    continue
                related_items.append(
                    {
                        "text": text,
                        "url": href,
                        "local_source_id": extract_source_id(href) if is_internal_source(href) else None,
                    }
                )
            if related_items:
                direct_block_found = True
                blocks.append({"kind": "list", "items": related_items})
    if blocks or direct_block_found:
        return blocks

    text = normalize_space(node.get_text(" ", strip=True))
    if text and len(text) > 12:
        blocks.append({"kind": "paragraph", "text": text})
    return blocks


def extract_sections(main: Tag | None, *, page_url: str) -> list[dict[str, Any]]:
    if not main:
        return []
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    seen_paragraphs: set[int] = set()
    seen_lists: set[int] = set()
    seen_cards: set[int] = set()
    skip_keywords = ("评论", "comment", "回复", "留言", "发表评论")

    for node in main.descendants:
        if not isinstance(node, Tag):
            continue

        if node.name in {"h2", "h3"}:
            title = normalize_space(node.get_text(" ", strip=True))
            if not title:
                continue
            if any(kw in title.lower() for kw in skip_keywords):
                current = None
                continue
            current = {"heading": title, "blocks": []}
            sections.append(current)
            continue

        if current is None:
            continue

        if node.name == "p":
            node_id = id(node)
            if node_id in seen_paragraphs:
                continue
            if any(
                isinstance(parent, Tag) and (
                    parent.name == "li" or "card" in (parent.get("class") or [])
                )
                for parent in node.parents
            ):
                continue
            text = normalize_space(node.get_text(" ", strip=True))
            if text:
                current["blocks"].append({"kind": "paragraph", "text": text})
                seen_paragraphs.add(node_id)
            continue

        if node.name in {"ul", "ol"}:
            node_id = id(node)
            if node_id in seen_lists:
                continue
            if any(isinstance(parent, Tag) and parent.name in {"ul", "ol"} for parent in node.parents):
                continue
            items = extract_list_items(node, page_url=page_url)
            if items:
                current["blocks"].append({"kind": "list", "items": items})
                seen_lists.add(node_id)
            continue

        if node.name == "a" and "card" in (node.get("class") or []):
            node_id = id(node)
            if node_id in seen_cards:
                continue
            text = card_name(node)
            summary = normalize_space(
                node.get("title")
                or (node.select_one("p").get_text(" ", strip=True) if node.select_one("p") else "")
            )
            href = resolve_url(page_url, node.get("href"))
            if text or summary:
                item = {
                    "text": f"{text}：{summary}" if text and summary else (text or summary),
                    "url": href,
                    "local_source_id": extract_source_id(href) if is_internal_source(href) else None,
                }
                if current["blocks"] and current["blocks"][-1]["kind"] == "list":
                    current["blocks"][-1]["items"].append(item)
                else:
                    current["blocks"].append({"kind": "list", "items": [item]})
                seen_cards.add(node_id)

    for section in sections:
        merged_blocks: list[dict[str, Any]] = []
        for block in section["blocks"]:
            if not merged_blocks:
                merged_blocks.append(block)
                continue
            last = merged_blocks[-1]
            if last["kind"] == "paragraph" and block["kind"] == "paragraph":
                last["text"] = f"{last['text']} {block['text']}"
            elif last["kind"] == "list" and block["kind"] == "list":
                last["items"].extend(block["items"])
            else:
                merged_blocks.append(block)
        section["blocks"] = merged_blocks

    sections = [section for section in sections if section["blocks"]]
    return sections


def parse_detail_page(html: str, *, page_url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    candidates = [
        soup.select_one("main"),
        soup.select_one(".panel-body.single.mt-2"),
        soup.select_one(".panel-body.single"),
        soup.select_one("article"),
        soup.body,
    ]
    main = next((node for node in candidates if node and node.find("h1")), None)
    if main is None:
        main = next((node for node in candidates if node), soup.body)
    h1 = main.find("h1") if main else None
    title = normalize_space(h1.get_text(" ", strip=True)) if h1 else ""
    title = title or extract_meta_content(soup, "og:title", attr="property") or ""
    title = title.split("|", 1)[0].strip()
    subtitle = top_text_summary(main, extract_meta_content(soup, "description"))
    image_url = (
        extract_meta_content(soup, "og:image", attr="property")
        or extract_meta_content(soup, "twitter:image", attr="name")
    )
    official_url = extract_official_url(main, page_url)
    tags = extract_top_tags(main)
    sections = extract_sections(main, page_url=page_url)
    return {
        "title": title,
        "subtitle": subtitle,
        "description": subtitle,
        "image_url": image_url,
        "official_url": official_url,
        "tags_json": json_dumps(tags),
        "sections_json": json_dumps(sections),
        "raw_html": html,
        "fetched_at": utcnow(),
    }


def sync_database(
    conn: sqlite3.Connection,
    *,
    home_url: str,
    home_file: str | None = None,
    limit: int | None = None,
    refresh: bool = False,
    skip_details: bool = False,
    delay: float = DEFAULT_REQUEST_DELAY,
) -> dict[str, Any]:
    init_db(conn)
    session = build_session()
    base_url, home_html = read_home_source(session, home_url=home_url, home_file=home_file)
    categories, tools = parse_homepage(home_html, base_url=base_url)

    for category in categories:
        upsert_category(conn, category)
    for tool in tools:
        upsert_tool(conn, tool)
    conn.commit()

    detail_candidates = conn.execute(
        """
        SELECT source_id, detail_source_url, title, fetched_at
        FROM tools
        WHERE detail_source_url IS NOT NULL AND detail_source_url != ''
        ORDER BY home_order
        """
    ).fetchall()
    if not refresh:
        detail_candidates = [row for row in detail_candidates if not row["fetched_at"]]
    if limit is not None:
        detail_candidates = detail_candidates[:limit]

    fetched = 0
    failed: list[str] = []
    if not skip_details:
        for row in detail_candidates:
            try:
                html = fetch_html(session, row["detail_source_url"])
                detail = parse_detail_page(html, page_url=row["detail_source_url"])
                detail["source_id"] = row["source_id"]
                upsert_tool(conn, detail)
                conn.commit()
                fetched += 1
                if delay > 0:
                    time.sleep(delay)
            except Exception as exc:  # noqa: BLE001
                failed.append(f"{row['source_id']} {row['title']}: {exc}")

    total_tools = conn.execute("SELECT COUNT(*) AS count FROM tools").fetchone()["count"]
    total_categories = conn.execute("SELECT COUNT(*) AS count FROM categories").fetchone()["count"]
    return {
        "categories": len(categories),
        "tools": total_tools,
        "detail_attempts": len(detail_candidates) if not skip_details else 0,
        "detail_fetched": fetched,
        "detail_failed": failed,
        "category_total": total_categories,
        "source_label": home_file or home_url,
    }


def database_has_tools(db_path: str | Path) -> bool:
    with connect_db(db_path) as conn:
        init_db(conn)
        row = conn.execute("SELECT COUNT(*) AS count FROM tools").fetchone()
        return bool(row["count"])


def run_sync_job(
    *,
    refresh: bool = False,
    skip_details: bool = False,
    limit: int | None = None,
    delay: float = DEFAULT_REQUEST_DELAY,
) -> bool:
    if not SYNC_JOB_LOCK.acquire(blocking=False):
        return False
    started_at = utcnow()
    update_sync_state(
        running=True,
        last_started_at=started_at,
        last_error=None,
    )
    try:
        with connect_db(app.config["DB_PATH"]) as conn:
            result = sync_database(
                conn,
                home_url=app.config["HOME_SOURCE"],
                refresh=refresh,
                skip_details=skip_details,
                limit=limit,
                delay=delay,
            )
        update_sync_state(
            running=False,
            last_finished_at=utcnow(),
            last_result=result,
            last_error=None,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        update_sync_state(
            running=False,
            last_finished_at=utcnow(),
            last_error=str(exc),
        )
        return False
    finally:
        SYNC_JOB_LOCK.release()


def sync_worker_loop() -> None:
    if app.config.get("AUTO_SYNC", True) or not database_has_tools(app.config["DB_PATH"]):
        run_sync_job()
    while True:
        if app.config.get("AUTO_SYNC", True):
            run_sync_job()
        interval = max(int(app.config.get("SYNC_INTERVAL", DEFAULT_SYNC_INTERVAL)), 60)
        time.sleep(interval)


def start_background_sync_worker() -> None:
    global SYNC_THREAD
    if SYNC_THREAD and SYNC_THREAD.is_alive():
        return
    worker = threading.Thread(target=sync_worker_loop, name="ai-tools-sync", daemon=True)
    worker.start()
    SYNC_THREAD = worker


def row_to_tool(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    tool = dict(row)
    tool["tags"] = json_loads(tool.get("tags_json"), [])
    tool["sections"] = json_loads(tool.get("sections_json"), [])
    return tool


def rows_to_tools(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [row_to_tool(row) for row in rows if row is not None]


def stats_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM tools) AS tool_count,
            (SELECT COUNT(*) FROM categories) AS category_count,
            MAX(COALESCE(fetched_at, updated_at)) AS last_sync
        FROM tools
        """
    ).fetchone()
    return {
        "tool_count": row["tool_count"] or 0,
        "category_count": row["category_count"] or 0,
        "last_sync": row["last_sync"],
    }


def update_sync_state(**fields: Any) -> None:
    with SYNC_STATE_LOCK:
        SYNC_STATE.update(fields)


def sync_state_snapshot() -> dict[str, Any]:
    with SYNC_STATE_LOCK:
        snapshot = dict(SYNC_STATE)
    return snapshot


def compact_text(value: str | None, limit: int = 140) -> str | None:
    text = normalize_space(value)
    if not text:
        return None
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def serialize_category(category: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    data = dict(category)
    return {
        "slug": data["slug"],
        "title": data["title"],
        "parent_slug": data.get("parent_slug"),
        "source_url": data.get("source_url"),
        "kind": data.get("kind"),
        "sort_order": data.get("sort_order"),
        "path": url_for("category_page", slug=data["slug"]),
    }


def serialize_tool_summary(tool: dict[str, Any]) -> dict[str, Any]:
    summary = compact_text(tool.get("subtitle") or tool.get("description"))
    return {
        "source_id": tool["source_id"],
        "slug": tool.get("slug"),
        "title": tool.get("title"),
        "subtitle": summary,
        "description": compact_text(tool.get("description")),
        "image_url": tool.get("image_url"),
        "official_url": tool.get("official_url"),
        "detail_source_url": tool.get("detail_source_url"),
        "detail_path": url_for("tool_detail", tool_key=tool["source_id"]),
        "main_category_slug": tool.get("main_category_slug"),
        "main_category_title": tool.get("main_category_title"),
        "main_category_path": (
            url_for("category_page", slug=tool["main_category_slug"])
            if tool.get("main_category_slug")
            else None
        ),
        "subcategory_slug": tool.get("subcategory_slug"),
        "subcategory_title": tool.get("subcategory_title"),
        "subcategory_path": (
            url_for("category_page", slug=tool["subcategory_slug"])
            if tool.get("subcategory_slug")
            else None
        ),
        "fetched_at": tool.get("fetched_at"),
        "has_detail": bool(tool.get("sections") or tool.get("fetched_at")),
    }


def serialize_tool_detail(tool: dict[str, Any]) -> dict[str, Any]:
    detail = serialize_tool_summary(tool)
    detail.update(
        {
            "description": tool.get("description") or tool.get("subtitle"),
            "subtitle": tool.get("subtitle") or tool.get("description"),
            "tags": tool.get("tags", []),
            "sections": tool.get("sections", []),
            "raw_html": None,
        }
    )
    return detail


def build_home_payload(conn: sqlite3.Connection, query: str = "") -> dict[str, Any]:
    categories = conn.execute(
        """
        SELECT *
        FROM categories
        WHERE parent_slug IS NULL
        ORDER BY sort_order, slug
        """
    ).fetchall()
    payload: dict[str, Any] = {
        "view": "home",
        "query": query,
        "source_label": app.config["HOME_SOURCE"],
        "categories": [serialize_category(category) for category in categories],
        "stats": stats_snapshot(conn),
        "sync": sync_state_snapshot(),
    }
    if query:
        clause, params = search_clause(query)
        rows = conn.execute(
            f"""
            SELECT *
            FROM tools
            WHERE {clause}
            ORDER BY home_order, title
            LIMIT 120
            """,
            params,
        ).fetchall()
        search_tools = [serialize_tool_summary(tool) for tool in rows_to_tools(rows)]
        payload["search_tools"] = search_tools
        payload["search_total"] = len(search_tools)
        payload["sections"] = []
        return payload

    sections = []
    for section in build_home_sections(conn):
        sections.append(
            {
                "slug": section["slug"],
                "title": section["title"],
                "source_url": section.get("source_url"),
                "total": section["total"],
                "path": url_for("category_page", slug=section["slug"]),
                "groups": [
                    {
                        "title": group.get("title"),
                        "slug": group.get("slug"),
                        "source_url": group.get("source_url"),
                        "tools": [serialize_tool_summary(tool) for tool in group["tools"]],
                    }
                    for group in section["groups"]
                ],
            }
        )
    payload["sections"] = sections
    payload["search_tools"] = []
    payload["search_total"] = 0
    return payload


def build_category_payload(conn: sqlite3.Connection, slug: str, query: str = "") -> dict[str, Any]:
    category = conn.execute(
        "SELECT * FROM categories WHERE slug = ?",
        (slug,),
    ).fetchone()
    if category is None:
        abort(404)
    groups = []
    for group in fetch_category_groups(conn, category, query=query):
        groups.append(
            {
                "title": group["title"],
                "tools": [serialize_tool_summary(tool) for tool in group["tools"]],
            }
        )
    return {
        "view": "category",
        "query": query,
        "category": serialize_category(category),
        "groups": groups,
        "stats": stats_snapshot(conn),
        "sync": sync_state_snapshot(),
    }


def build_tool_payload(conn: sqlite3.Connection, tool_key: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT *
        FROM tools
        WHERE source_id = ? OR slug = ?
        LIMIT 1
        """,
        (tool_key, tool_key),
    ).fetchone()
    tool = row_to_tool(row)
    if tool is None:
        abort(404)
    related_rows = conn.execute(
        """
        SELECT *
        FROM tools
        WHERE source_id != ?
          AND (
            (subcategory_slug IS NOT NULL AND subcategory_slug != '' AND subcategory_slug = ?)
            OR main_category_slug = ?
          )
        ORDER BY fetched_at DESC, home_order
        LIMIT 6
        """,
        (tool["source_id"], tool.get("subcategory_slug"), tool.get("main_category_slug")),
    ).fetchall()
    return {
        "view": "tool",
        "tool": serialize_tool_detail(tool),
        "related_tools": [serialize_tool_summary(item) for item in rows_to_tools(related_rows)],
        "stats": stats_snapshot(conn),
        "sync": sync_state_snapshot(),
    }


def build_home_sections(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    main_categories = conn.execute(
        """
        SELECT *
        FROM categories
        WHERE parent_slug IS NULL
        ORDER BY sort_order, slug
        """
    ).fetchall()
    for category in main_categories:
        child_rows = conn.execute(
            """
            SELECT *
            FROM categories
            WHERE parent_slug = ?
            ORDER BY sort_order, slug
            """,
            (category["slug"],),
        ).fetchall()
        groups: list[dict[str, Any]] = []
        ungrouped = rows_to_tools(
            conn.execute(
                """
                SELECT *
                FROM tools
                WHERE main_category_slug = ?
                  AND (subcategory_slug IS NULL OR subcategory_slug = '')
                ORDER BY home_order, title
                """,
                (category["slug"],),
            ).fetchall()
        )
        if ungrouped:
            groups.append({"title": None, "slug": category["slug"], "tools": ungrouped})
        for child in child_rows:
            child_tools = rows_to_tools(
                conn.execute(
                    """
                    SELECT *
                    FROM tools
                    WHERE subcategory_slug = ?
                    ORDER BY home_order, title
                    """,
                    (child["slug"],),
                ).fetchall()
            )
            if child_tools:
                groups.append(
                    {
                        "title": child["title"],
                        "slug": child["slug"],
                        "source_url": child["source_url"],
                        "tools": child_tools,
                    }
                )
        total = sum(len(group["tools"]) for group in groups)
        if total:
            sections.append(
                {
                    "slug": category["slug"],
                    "title": category["title"],
                    "source_url": category["source_url"],
                    "groups": groups,
                    "total": total,
                }
            )
    return sections


def search_clause(query: str) -> tuple[str, list[str]]:
    like = f"%{query}%"
    return (
        """
        (
            title LIKE ?
            OR COALESCE(subtitle, '') LIKE ?
            OR COALESCE(description, '') LIKE ?
            OR COALESCE(main_category_title, '') LIKE ?
            OR COALESCE(subcategory_title, '') LIKE ?
        )
        """,
        [like, like, like, like, like],
    )


def fetch_category_groups(
    conn: sqlite3.Connection,
    category: sqlite3.Row,
    *,
    query: str = "",
) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    filter_sql = ""
    params: list[Any] = []
    if query:
        filter_sql, params = search_clause(query)
        filter_sql = f"AND {filter_sql}"

    if category["parent_slug"]:
        rows = conn.execute(
            f"""
            SELECT *
            FROM tools
            WHERE subcategory_slug = ?
            {filter_sql}
            ORDER BY home_order, title
            """,
            [category["slug"], *params],
        ).fetchall()
        groups.append({"title": category["title"], "tools": rows_to_tools(rows)})
        return groups

    child_rows = conn.execute(
        """
        SELECT *
        FROM categories
        WHERE parent_slug = ?
        ORDER BY sort_order, slug
        """,
        (category["slug"],),
    ).fetchall()
    direct_rows = conn.execute(
        f"""
        SELECT *
        FROM tools
        WHERE main_category_slug = ?
          AND (subcategory_slug IS NULL OR subcategory_slug = '')
        {filter_sql}
        ORDER BY home_order, title
        """,
        [category["slug"], *params],
    ).fetchall()
    if direct_rows:
        groups.append({"title": "全部工具", "tools": rows_to_tools(direct_rows)})
    for child in child_rows:
        rows = conn.execute(
            f"""
            SELECT *
            FROM tools
            WHERE subcategory_slug = ?
            {filter_sql}
            ORDER BY home_order, title
            """,
            [child["slug"], *params],
        ).fetchall()
        if rows:
            groups.append({"title": child["title"], "tools": rows_to_tools(rows)})
    return groups


def serve_shell() -> Any:
    return send_file(INDEX_SHELL_PATH)


@app.route("/")
@app.route("/index.html")
def home() -> Any:
    return serve_shell()


@app.route("/category/<slug>")
def category_page(slug: str) -> Any:
    return serve_shell()


@app.route("/sites/<tool_key>.html")
def tool_detail(tool_key: str) -> Any:
    return serve_shell()


@app.route("/api/home")
def api_home() -> Any:
    conn = get_db()
    query = normalize_space(request.args.get("q"))
    return jsonify(build_home_payload(conn, query=query))


@app.route("/api/category/<slug>")
def api_category(slug: str) -> Any:
    conn = get_db()
    query = normalize_space(request.args.get("q"))
    return jsonify(build_category_payload(conn, slug=slug, query=query))


@app.route("/api/tools/<tool_key>")
def api_tool(tool_key: str) -> Any:
    conn = get_db()
    return jsonify(build_tool_payload(conn, tool_key=tool_key))


@app.route("/api/status")
def api_status() -> Any:
    conn = get_db()
    return jsonify(
        {
            "stats": stats_snapshot(conn),
            "sync": sync_state_snapshot(),
            "home_source": app.config["HOME_SOURCE"],
        }
    )


@app.route("/api/admin/sync", methods=["POST"])
def api_trigger_sync() -> Any:
    if sync_state_snapshot().get("running"):
        return jsonify({"started": False, "sync": sync_state_snapshot()})
    worker = threading.Thread(target=run_sync_job, name="ai-tools-sync-manual", daemon=True)
    worker.start()
    return jsonify(
        {
            "started": True,
            "sync": sync_state_snapshot(),
        }
    )


@app.errorhandler(404)
def handle_404(_: Any) -> Any:
    if request.path.startswith("/api/"):
        return jsonify({"error": "not_found", "path": request.path}), 404
    return "Not Found", 404


@app.errorhandler(500)
def handle_500(_: Any) -> Any:
    if request.path.startswith("/api/"):
        return jsonify({"error": "internal_error", "path": request.path}), 500
    return "Internal Server Error", 500


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI 工具站 sqlite 抓取与渲染程序")
    subparsers = parser.add_subparsers(dest="command")

    sync_parser = subparsers.add_parser("sync", help="抓取首页和详情页并写入 sqlite")
    sync_parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="sqlite 数据库路径")
    sync_parser.add_argument("--home-url", default=DEFAULT_HOME_URL, help="首页抓取 URL")
    sync_parser.add_argument("--home-file", help="使用本地首页 HTML 初始化，而不是请求首页")
    sync_parser.add_argument("--limit", type=int, help="最多抓取多少个详情页")
    sync_parser.add_argument("--refresh", action="store_true", help="即使已抓取过，也重新刷新详情页")
    sync_parser.add_argument("--skip-details", action="store_true", help="只解析首页，不请求详情页")
    sync_parser.add_argument("--delay", type=float, default=DEFAULT_REQUEST_DELAY, help="详情页请求间隔秒数")

    serve_parser = subparsers.add_parser("serve", help="启动本地 Web 服务")
    serve_parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="sqlite 数据库路径")
    serve_parser.add_argument("--home-url", default=DEFAULT_HOME_URL, help="记录在页面中的源首页地址")
    serve_parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    serve_parser.add_argument("--port", type=int, default=8000, help="监听端口")
    serve_parser.add_argument("--debug", action="store_true", help="开启 Flask debug")
    serve_parser.add_argument(
        "--sync-interval",
        type=int,
        default=DEFAULT_SYNC_INTERVAL,
        help="后台自动同步间隔，单位秒",
    )
    serve_parser.add_argument(
        "--no-auto-sync",
        action="store_true",
        help="关闭后台自动同步，只提供服务",
    )

    return parser


def command_sync(args: argparse.Namespace) -> int:
    conn = connect_db(args.db)
    try:
        result = sync_database(
            conn,
            home_url=args.home_url,
            home_file=args.home_file,
            limit=args.limit,
            refresh=args.refresh,
            skip_details=args.skip_details,
            delay=args.delay,
        )
    finally:
        conn.close()

    print(f"首页来源: {result['source_label']}")
    print(f"分类写入: {result['categories']}")
    print(f"工具总数: {result['tools']}")
    if not args.skip_details:
        print(f"详情尝试抓取: {result['detail_attempts']}")
        print(f"详情成功抓取: {result['detail_fetched']}")
        print(f"详情抓取失败: {len(result['detail_failed'])}")
        if result["detail_failed"]:
            print("失败明细:")
            for item in result["detail_failed"][:20]:
                print(f"  - {item}")
    return 0


def command_serve(args: argparse.Namespace) -> int:
    app.config["DB_PATH"] = args.db
    app.config["HOME_SOURCE"] = args.home_url
    app.config["AUTO_SYNC"] = not args.no_auto_sync
    app.config["SYNC_INTERVAL"] = args.sync_interval
    with connect_db(args.db) as conn:
        init_db(conn)
    if not args.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        start_background_sync_worker()
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)
    return 0


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    if args.command == "sync":
        return command_sync(args)
    if args.command == "serve":
        return command_serve(args)
    return command_serve(
        argparse.Namespace(
            db=str(DEFAULT_DB_PATH),
            home_url=DEFAULT_HOME_URL,
            host="127.0.0.1",
            port=8000,
            debug=False,
            sync_interval=DEFAULT_SYNC_INTERVAL,
            no_auto_sync=False,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
