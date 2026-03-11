#!/usr/bin/env python3
"""HTML→PDF変換（WeasyPrintラッパー）"""

import subprocess
import sys


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


def convert_html_to_pdf(html_path: str, pdf_path: str) -> bool:
    """HTMLファイルをPDFに変換"""
    if not ensure_weasyprint():
        return False

    from weasyprint import HTML

    try:
        HTML(filename=html_path).write_pdf(pdf_path)
        print(f"PDF生成完了: {pdf_path}", file=sys.stderr)
        return True
    except Exception as e:
        print(f"PDF変換エラー: {e}", file=sys.stderr)
        return False


def main():
    if len(sys.argv) < 3:
        print("使い方: html_to_pdf.py <入力HTML> <出力PDF>")
        sys.exit(1)

    html_path = sys.argv[1]
    pdf_path = sys.argv[2]

    success = convert_html_to_pdf(html_path, pdf_path)
    if success:
        print(pdf_path)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
