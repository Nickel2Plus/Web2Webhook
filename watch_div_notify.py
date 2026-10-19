#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
watch_div_notify.py

指定したWebページ内の特定の要素（例: <div id="hogehoge">〜</div>）の
内容が更新されたことを検知し、Discordに Webhook で通知するBot。

更新が検知された場合、対象範囲内に含まれる <a href> タグのリンク先と
リンク文字列を抽出し、Discordの埋め込みメッセージに一覧として載せる。

前回通知時のリンク一覧を状態ファイルに保存しており、次回以降は
同じURLのリンクで文字列だけが変わった場合（例:「〇〇を開催します」→
「【終了】〇〇を開催します」）は「更新されたリンク」として、
新しく追加されたリンクは「新しく追加されたリンク」として、
無くなったリンクは「無くなったリンク」として、それぞれ区別して通知する。
リンクに変化が無く、対象範囲内の他の部分（テキストなど）だけが
変わった場合は、シンプルに「ページが更新されました」という通知になる。

【想定環境】
- さくらのVPS (CentOS) 上で cron により定期実行する
- Python 3.8+ / requests / beautifulsoup4 が必要

【インストール】
    pip3 install requests beautifulsoup4

【設定】
    このファイル下部の CONFIG を編集してください。
    複数サイトを監視したい場合は CONFIG のリストに項目を追加してください。

【selector（監視対象要素）の指定方法】
    selector には CSS セレクタを指定します。ID・class どちらでも指定可能です。

        指定したいもの         | HTML例                              | selectorの書き方
        ----------------------|--------------------------------------|-------------------
        ID                    | <div id="hogehoge">                  | "#hogehoge"
        class                 | <div class="update-area">            | ".update-area"
        タグ名                 | <article>                            | "article"
        タグ+class            | <div class="update-area">            | "div.update-area"
        複数classの組み合わせ    | <div class="news card">              | ".news.card"
        親子関係               | <div id="main"><div class="body">    | "#main .body"

    ※ select_one() を使用しているため、同じ class を持つ要素が複数存在する
      場合は「最初に見つかった1つ」だけが監視対象になります。
      2番目以降の要素を指定したい場合は :nth-of-type() などが利用できます。
      例: ".update-area:nth-of-type(2)"

【ID・classが無い要素（例: <table></table>）を指定したい場合】
    タグ名だけでは複数一致してしまうことがあるため、WatchTarget に
    nth（1始まりの出現順）を指定してください。

        selector="table", nth=2   → ページ内の2番目の <table> を監視

    ページ内にそのタグが1つしかない場合は nth=1（デフォルト）のままでOKです。
    親要素に目印がある場合は "div.content table" のように絞り込むことも可能です。

【cron設定例】(5分おきにチェック)
    */5 * * * * /usr/bin/python3 /path/to/watch_div_notify.py >> /path/to/watch.log 2>&1
"""

import hashlib
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from typing import List, Optional

import requests
from bs4 import BeautifulSoup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "watch_state.json")
REQUEST_TIMEOUT = 15  # seconds
USER_AGENT = "Mozilla/5.0 (compatible; WatchDivNotifyBot/1.0)"


@dataclass
class WatchTarget:
    """監視対象の設定"""
    name: str                # 通知に表示する識別名
    url: str                 # 監視対象ページのURL
    selector: str            # 監視対象のCSSセレクタ (ID指定: "#hogehoge" / class指定: ".update-area" など。詳細はファイル冒頭の説明を参照)
    webhook_url: str         # 通知先のDiscord Webhook URL
    base_url: Optional[str] = None  # 相対リンクを絶対URLに変換する際の基準URL（省略時は url を使用）
    nth: int = 1              # selectorに一致する要素が複数ある場合、何番目を対象にするか(1始まり)。ID/classがないtableなどを"table"のような広いセレクタ+nthで特定する際に使用


# ==========================================================================
# CONFIG: 監視したい対象をここに追加してください
# ==========================================================================
CONFIG: List[WatchTarget] = [
    WatchTarget(
        name="サンプルサイト お知らせ欄",
        url="https://example.com/news/",
        selector="#hogehoge",  # <div id="hogehoge"> を監視する場合
        webhook_url="https://discord.com/api/webhooks/XXXXXXXXXX/YYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYY",
    ),
    # 複数サイトを監視する場合はここに追加
    # WatchTarget(
    #     name="別サイト",
    #     url="https://example.org/updates/",
    #     selector="div.update-area",
    #     webhook_url="https://discord.com/api/webhooks/ZZZZZZZZZZ/WWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWW",
    # ),
    # IDやclassが無い <table> をページ内の出現順（2番目）で指定する例
    # WatchTarget(
    #     name="価格表サイト",
    #     url="https://example.net/prices/",
    #     selector="table",
    #     nth=2,
    #     webhook_url="https://discord.com/api/webhooks/AAAAAAAAAA/BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
    # ),
]
# ==========================================================================


def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("状態ファイルの読み込みに失敗しました。空の状態から開始します: %s", e)
        return {}


def save_state(state: dict) -> None:
    tmp_path = STATE_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, STATE_FILE)


def fetch_target_element(target: WatchTarget) -> Optional[BeautifulSoup]:
    """指定URLを取得し、CSSセレクタに一致する最初の要素を返す"""
    try:
        resp = requests.get(
            target.url,
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("[%s] ページの取得に失敗しました: %s", target.name, e)
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    elements = soup.select(target.selector)
    if not elements:
        logger.error(
            "[%s] セレクタ '%s' に一致する要素が見つかりませんでした",
            target.name, target.selector,
        )
        return None
    if target.nth < 1 or target.nth > len(elements):
        logger.error(
            "[%s] セレクタ '%s' は %d 件見つかりましたが、nth=%d は範囲外です",
            target.name, target.selector, len(elements), target.nth,
        )
        return None
    return elements[target.nth - 1]


def compute_hash(element: BeautifulSoup) -> str:
    """要素の内容（テキスト正規化済み）からハッシュ値を計算する"""
    normalized = " ".join(element.get_text(separator=" ", strip=True).split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def extract_links(element: BeautifulSoup, base_url: str) -> List[dict]:
    """要素内の <a href> をすべて抽出する（重複は除去、順序は維持）"""
    links = []
    seen = set()
    for a_tag in element.find_all("a", href=True):
        href = a_tag["href"].strip()
        text = a_tag.get_text(strip=True) or href
        if not href or href.startswith("#"):
            continue
        absolute_url = requests.compat.urljoin(base_url, href)
        key = (absolute_url, text)
        if key in seen:
            continue
        seen.add(key)
        links.append({"text": text, "url": absolute_url})
    return links


def diff_links(previous_links: dict, current_links: List[dict]) -> dict:
    """
    前回のリンク一覧（{url: text} の辞書）と今回のリンク一覧を比較する。

    戻り値:
        {
            "changed": [{"url":..., "old_text":..., "new_text":...}, ...],  # 同じURLでリンク文字列が変わったもの
            "added":   [{"url":..., "text":...}, ...],                     # 新しく追加されたリンク
            "removed": [{"url":..., "text":...}, ...],                     # 無くなったリンク
        }
    """
    current_map = {l["url"]: l["text"] for l in current_links}

    changed = []
    added = []
    for url, text in current_map.items():
        if url in previous_links:
            if previous_links[url] != text:
                changed.append({"url": url, "old_text": previous_links[url], "new_text": text})
        else:
            added.append({"url": url, "text": text})

    removed = [
        {"url": url, "text": text}
        for url, text in previous_links.items()
        if url not in current_map
    ]

    return {"changed": changed, "added": added, "removed": removed}


DISCORD_MAX_FIELDS = 25
DISCORD_EMBED_TOTAL_LIMIT = 5500  # Discord上限は6000だが安全のため余裕を持たせる
DISCORD_FIELD_VALUE_LIMIT = 1000  # Discord上限は1024だが余裕を持たせる


def _embed_size(embed: dict) -> int:
    """embed全体の合計文字数を概算する（title + description + 全fieldのname/value）"""
    size = len(embed.get("title", "")) + len(embed.get("description", ""))
    for f in embed.get("fields", []):
        size += len(f.get("name", "")) + len(f.get("value", ""))
    return size


def _enforce_field_limit(embed: dict) -> None:
    """
    Discordのembedは
      - fieldsが最大25件まで
      - title/description/fields等の合計が6000文字まで
    という制限があるため、超える場合は末尾のfieldを間引いて要約する。
    """
    fields = embed["fields"]
    omitted_summary_field = {
        "name": "他",
        "value": "",  # 件数は後で埋める
        "inline": False,
    }

    def build_kept(n: int) -> List[dict]:
        omitted = len(fields) - n
        if omitted <= 0:
            return fields
        kept = fields[:n] + [{
            **omitted_summary_field,
            "value": f"表示しきれない項目が他に{omitted}件あります。元ページをご確認ください。",
        }]
        return kept

    # まずfields数の上限に合わせる
    n = min(len(fields), DISCORD_MAX_FIELDS if len(fields) <= DISCORD_MAX_FIELDS else DISCORD_MAX_FIELDS - 1)
    kept = build_kept(n)
    embed["fields"] = kept

    # 合計文字数が上限を超えている間、末尾のfieldから間引いていく
    while _embed_size(embed) > DISCORD_EMBED_TOTAL_LIMIT and n > 0:
        n -= 1
        kept = build_kept(n)
        embed["fields"] = kept

    # それでも超える場合（description自体が長すぎる等）はdescriptionも切り詰める
    if _embed_size(embed) > DISCORD_EMBED_TOTAL_LIMIT:
        overflow = _embed_size(embed) - DISCORD_EMBED_TOTAL_LIMIT
        desc = embed.get("description", "")
        embed["description"] = desc[:max(0, len(desc) - overflow - 3)] + "..."


def _append_link_list_field(embed: dict, field_name: str, lines: List[str]) -> None:
    """行のリストをDiscordのフィールド値制限（1024文字）に合わせて分割して追加する"""
    # 1行だけで上限を超える場合は、その行自体を強制的に切り詰める
    safe_lines = []
    for line in lines:
        if len(line) > DISCORD_FIELD_VALUE_LIMIT:
            safe_lines.append(line[:DISCORD_FIELD_VALUE_LIMIT - 3] + "...")
        else:
            safe_lines.append(line)

    chunk = ""
    chunk_index = 1
    for line in safe_lines:
        # 追加すると上限を超え、かつ既に何か入っている場合のみフィールドを確定する
        if chunk and len(chunk) + len(line) + 1 > DISCORD_FIELD_VALUE_LIMIT:
            embed["fields"].append({
                "name": f"{field_name} ({chunk_index})",
                "value": chunk,
                "inline": False,
            })
            chunk = ""
            chunk_index += 1
        chunk += (line + "\n")
    if chunk:
        embed["fields"].append({
            "name": f"{field_name} ({chunk_index})" if chunk_index > 1 else field_name,
            "value": chunk,
            "inline": False,
        })




def build_discord_payload(target: WatchTarget, links: List[dict], diff: Optional[dict] = None) -> dict:
    """
    Discord Webhook用のペイロードを組み立てる。

    diff が指定され、かつ changed/added/removed のいずれかがある場合は差分を表示する。
    diff が None、または差分が何も無い場合（リンク以外の部分が更新された場合など）は、
    シンプルに「ページが更新されました」という内容にする。
    """
    description_lines = [f"**{target.name}** に更新がありました。", f"元ページ: {target.url}"]

    embed = {
        "title": "更新を検知しました",
        "description": "\n".join(description_lines),
        "color": 0x00B0F4,
        "fields": [],
    }

    has_diff = diff is not None and (diff["changed"] or diff["added"] or diff["removed"])

    if has_diff:
        if diff["changed"]:
            lines = [f"[{c['old_text']}] → [{c['new_text']}]\n{c['url']}" for c in diff["changed"]]
            _append_link_list_field(embed, "更新されたリンク", lines)
        if diff["added"]:
            lines = [f"[{a['text']}]({a['url']})" for a in diff["added"]]
            _append_link_list_field(embed, "新しく追加されたリンク", lines)
        if diff["removed"]:
            lines = [f"[{r['text']}]({r['url']})" for r in diff["removed"]]
            _append_link_list_field(embed, "無くなったリンク", lines)
    elif links:
        # 差分情報が無い（初回相当）が、とりあえず現在のリンクを一覧表示したいケース向けのフォールバック
        link_lines = [f"[{l['text']}]({l['url']})" for l in links]
        _append_link_list_field(embed, "検出されたリンク", link_lines)
    # リンクの変化が無い場合はフィールドを追加せず、descriptionのみで
    # 「ページが更新されました」という表示にとどめる

    _enforce_field_limit(embed)

    return {"embeds": [embed]}


def send_discord_notification(webhook_url: str, payload: dict) -> bool:
    try:
        resp = requests.post(webhook_url, json=payload, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        logger.error("Discordへの通知送信に失敗しました: %s", e)
        # Discordのエラーレスポンス本文には具体的な原因（どのfieldが不正か等）が
        # 含まれているため、デバッグのために出力しておく
        if e.response is not None:
            logger.error("Discordからのレスポンス内容: %s", e.response.text)
        return False


def check_target(target: WatchTarget, state: dict) -> None:
    element = fetch_target_element(target)
    if element is None:
        return

    current_hash = compute_hash(element)
    target_state = state.get(target.name, {})
    previous_hash = target_state.get("hash")
    previous_links = target_state.get("links", {})  # {url: text}

    if previous_hash is None:
        # 初回実行時は通知せず、状態だけ保存する
        logger.info("[%s] 初回チェックのため状態を保存し、通知はスキップします", target.name)
        base_url = target.base_url or target.url
        links = extract_links(element, base_url)
        state[target.name] = {
            "hash": current_hash,
            "links": {l["url"]: l["text"] for l in links},
        }
        return

    if current_hash == previous_hash:
        logger.info("[%s] 更新なし", target.name)
        return

    logger.info("[%s] 更新を検知しました", target.name)
    base_url = target.base_url or target.url
    links = extract_links(element, base_url)
    diff = diff_links(previous_links, links)
    payload = build_discord_payload(target, links, diff)
    if send_discord_notification(target.webhook_url, payload):
        logger.info(
            "[%s] Discordへの通知に成功しました（変更%d件 / 追加%d件 / 削除%d件）",
            target.name, len(diff["changed"]), len(diff["added"]), len(diff["removed"]),
        )
        state[target.name] = {
            "hash": current_hash,
            "links": {l["url"]: l["text"] for l in links},
        }
    else:
        # 送信に失敗した場合は次回また通知を試みるため、状態は更新しない
        logger.warning("[%s] 通知失敗のため状態は更新しません（次回再試行されます）", target.name)


def main() -> int:
    if not CONFIG:
        logger.error("CONFIG が空です。監視対象を設定してください。")
        return 1

    state = load_state()
    for target in CONFIG:
        check_target(target, state)
    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
