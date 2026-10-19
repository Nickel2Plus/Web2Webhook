# Web2Webhook
指定したHTMLタグ範囲でウェブサイトを監視し、変更があればWebhookで通知するBotです。

## 概要
指定したWebページ内の特定の要素（例: "<div id="hogehoge">〜</div>"）の内容が更新されたことを検知し、Discordに Webhook で通知するBotです。

更新が検知された場合、対象範囲内に含まれる "<a href>" タグのリンク先とリンク文字列を抽出し、Discordの埋め込みメッセージに一覧として載せます。

前回通知時のリンク一覧を状態ファイルに保存しており、次回以降は同じURLのリンクで文字列だけが変わった場合（例:「〇〇を開催します」→「【終了】〇〇を開催します」）は「更新されたリンク」として、新しく追加されたリンクは「新しく追加されたリンク」として、無くなったリンクは「無くなったリンク」として、それぞれ区別して通知します。
リンクに変化が無く、対象範囲内の他の部分（テキストなど）だけが変わった場合は、シンプルに「ページが更新されました」という通知になります。

## 必要な環境
* Python 3.8+
* requests
* beautifulsoup4

## インストール手順
```bash
pip3 install requests beautifulsoup4
```

## 設定
### selector（監視対象要素）の指定方法
selector には CSS セレクタを指定します。ID・class どちらでも指定可能です。

        指定したいもの         | HTML例                              | selectorの書き方
        ----------------------|--------------------------------------|-------------------
        ID                    | <div id="hogehoge">                  | "#hogehoge"
        class                 | <div class="update-area">            | ".update-area"
        タグ名                 | <article>                            | "article"
        タグ+class            | <div class="update-area">            | "div.update-area"
        複数classの組み合わせ    | <div class="news card">              | ".news.card"
        親子関係               | <div id="main"><div class="body">    | "#main .body"

 ※ select_one() を使用しているため、同じ class を持つ要素が複数存在する場合は「最初に見つかった1つ」だけが監視対象になります。
2番目以降の要素を指定したい場合は :nth-of-type() などが利用できます。（例： ".update-area:nth-of-type(2)"）

【注】ID・classが無い要素（例: <table></table>）を指定したい場合
    タグ名だけでは複数一致してしまうことがあるため、WatchTarget に
    nth（1始まりの出現順）を指定してください。
        （例：selector="table", nth=2   → ページ内の2番目の <table> を監視）

ページ内にそのタグが1つしかない場合は nth=1（デフォルト）のままでOKです。
親要素に目印がある場合は "div.content table" のように絞り込むことも可能です。

### cron設定例
（5分おきにチェックする場合）
    */5 * * * * /usr/bin/python3 /path/to/watch_div_notify.py >> /path/to/watch.log 2>&1