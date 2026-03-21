#!/usr/bin/env python3
"""HTML→PDF変換（WeasyPrintラッパー）"""

import os
import re
import subprocess
import sys

# Google Fonts から Noto Sans JP / Noto Serif JP を取得する @import 宣言。
# システムに日本語フォントがなくても WeasyPrint がネットワーク経由で解決する。
GOOGLE_FONTS_IMPORT = (
    '@import url("https://fonts.googleapis.com/css2?'
    "family=Noto+Sans+JP:wght@400;700&"
    "family=Noto+Serif+JP:wght@400;700&"
    'display=swap");'
)


def ensure_weasyprint():
    """WeasyPrintがなければインストール"""
    try:
        import weasyprint  # noqa: F401
        return True
    except ImportError:
        print("WeasyPrintをインストール中...", file=sys.stderr)
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "weasyprint>=60.0"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except subprocess.CalledProcessError:
            print("WeasyPrintのインストールに失敗しました", file=sys.stderr)
            return False


def _inject_google_fonts(html_content: str) -> str:
    """HTMLの最初の<style>タグにGoogle Fonts @importを注入する"""
    return html_content.replace("<style>", f"<style>\n{GOOGLE_FONTS_IMPORT}\n", 1)


def convert_html_to_pdf(html_path: str, pdf_path: str) -> bool:
    """HTMLファイルをPDFに変換"""
    if not ensure_weasyprint():
        return False

    from weasyprint import HTML

    try:
        with open(html_path, "r", encoding="utf-8") as f:
            html_content = f.read()

        html_content = _inject_google_fonts(html_content)

        HTML(string=html_content, base_url=os.path.dirname(os.path.abspath(html_path))).write_pdf(pdf_path)
        print(f"PDF生成完了: {pdf_path}", file=sys.stderr)
        return True
    except Exception as e:
        print(f"PDF変換エラー: {e}", file=sys.stderr)
        return False


def sanitize_pdf_path(pdf_path: str) -> str:
    """PDFパスのファイル名部分をサニタイズしてパストラバーサルを防止する。

    ディレクトリは /tmp 固定。ファイル名から危険な文字を除去する。
    """
    filename = os.path.basename(pdf_path)

    # パス区切り文字・制御文字・".." を除去
    filename = filename.replace("..", "")
    filename = re.sub(r'[/\\<>:"|?*\x00-\x1f]', "", filename)
    # 空白をアンダースコアに
    filename = filename.replace(" ", "_").replace("　", "_")
    # 先頭末尾のドットを除去（隠しファイル防止）
    filename = filename.strip(".")
    # サニタイズ後が空または拡張子のみなら安全なデフォルトを使用
    if not filename or filename == ".pdf":
        filename = "BCP_output.pdf"
    # .pdf 拡張子を保証
    if not filename.lower().endswith(".pdf"):
        filename += ".pdf"

    return os.path.join("/tmp", filename)


def main():
    if len(sys.argv) < 3:
        print("使い方: html_to_pdf.py <入力HTML> <出力PDF>")
        sys.exit(1)

    html_path = sys.argv[1]
    pdf_path = sanitize_pdf_path(sys.argv[2])

    success = convert_html_to_pdf(html_path, pdf_path)
    if success:
        print(pdf_path)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
