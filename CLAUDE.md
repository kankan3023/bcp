# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

BCP（事業継続計画）自動生成スキル。企業WebサイトのURL**または社名**を入力するだけで、業務分析→ハザードマップ調査→BCP文書生成をワンストップで実行し、HTMLとして出力する。

## Architecture

```
bcp/
├── .claude-plugin/marketplace.json    ← マーケットプレイス定義
└── plugins/bcp-generator/
    ├── .claude-plugin/plugin.json     ← プラグイン定義
    ├── skills/bcp/SKILL.md            ← スキル本体（7ステップワークフロー）
    ├── scripts/
    │   ├── geocode.py                 ← 住所→座標（国土地理院+Nominatim）
    │   ├── hazard_lookup.py           ← 地点ハザード分析（国交省タイル）
    │   ├── earthquake_lookup.py       ← 地震リスク分析（J-SHIS API）
    │   └── html_to_pdf.py            ← HTML→PDF（WeasyPrint、未使用）
    ├── assets/bcp_template.css        ← BCP文書用CSSテンプレート
    ├── references/
    │   ├── bcp-sections.md            ← BCP構造・業種別ガイダンス
    │   └── sme-bcp-guidelines.md      ← 中小企業庁ガイドライン要約
    └── requirements.txt
```

## Commands

```bash
# 依存パッケージのインストール
pip install -r plugins/bcp-generator/requirements.txt

# ジオコーディングテスト
python3 plugins/bcp-generator/scripts/geocode.py "東京都渋谷区神南1-1-1"

# ハザード分析テスト
python3 plugins/bcp-generator/scripts/hazard_lookup.py --lat 35.66 --lng 139.70 --output /tmp/test_hazard.json

# 地震リスク分析テスト（J-SHIS API）
python3 plugins/bcp-generator/scripts/earthquake_lookup.py --lat 35.66 --lng 139.70 --output /tmp/test_earthquake.json

# ハザードマップ画像生成テスト
python3 plugins/bcp-generator/scripts/generate_hazard_map.py --lat 35.66 --lng 139.70 --output /tmp/test_hazard_map.png
```

## Skill Usage

```
/bcp-generator:bcp <企業WebサイトURL または 社名>
```

URLまたは社名を入力すると自律的に7ステップを完走してBCP HTMLを生成する。社名入力時は自動でWebSearch→候補が複数あればユーザーに選択を求める。

### プラグインインストール方法（利用者向け）

```bash
# マーケットプレイスを追加（GitHubリポジトリまたはローカルパス）
claude plugin marketplace add <owner>/<repo>
# プラグインをインストール
claude plugin install bcp-generator@bcp-generator-marketplace
# Claude Code を再起動すると /bcp-generator:bcp が使えるようになる
```

## Key Design Decisions

- **ClaudeがHTML直接生成**: テンプレートエンジンを使わず、CSSを読み込んでClaudeがHTML全体を生成する。ブラウザ印刷対応CSSにより、Cmd+P → PDF保存も可能
- **多重情報源戦略**: WebFetch + WebSearch を並行実行し、片方失敗しても続行
- **3つの自律判断ポイント**: リスク優先順位、重要業務/RTO、BCP文書生成でAIが推論を示す
- **自己回復優先**: エラー時はフォールバックで完走。ユーザーへの質問は最小限
