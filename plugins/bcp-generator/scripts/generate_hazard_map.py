#!/usr/bin/env python3
"""ハザードマップ画像生成 - 指定座標周辺のハザードマップをPNG画像として出力

国土地理院の標準地図タイルを背景に、国土交通省ハザードマップポータルの
洪水・土砂災害・津波のハザードレイヤーを重ね合わせた地図画像を生成する。
BCP文書のPDFに埋め込むための画像として使用。
"""

import argparse
import io
import math
import os
import sys

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("Pillowがインストールされていません: pip install Pillow", file=sys.stderr)
    sys.exit(1)

# 同一ディレクトリの hazard_lookup.py から関数・定数をインポート
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hazard_lookup import latlng_to_tile, latlng_to_pixel_in_tile, fetch_tile, HAZARD_LAYERS

# 国土地理院 標準地図タイル
GSI_STD_URL = "https://cyberjapandata.gsi.go.jp/xyz/std/{z}/{x}/{y}.png"

TILE_SIZE = 256
GRID_SIZE = 3  # 3x3タイル

# ハザードレイヤーの重ね合わせ透明度 (0-255)
HAZARD_ALPHA = 160

# 凡例の色定義
LEGEND_ITEMS = [
    ("洪水 0.5m未満", (255, 255, 179, 255)),
    ("洪水 0.5-3.0m", (255, 200, 50, 255)),
    ("洪水 3.0-5.0m", (255, 150, 50, 255)),
    ("洪水 5.0m以上", (255, 50, 50, 255)),
    ("土砂災害警戒", (255, 200, 0, 255)),
    ("津波浸水想定", (0, 150, 255, 255)),
]

ATTRIBUTION = "出典: 国土地理院, 国土交通省ハザードマップポータルサイト"


def _find_cjk_font(size=12):
    """日本語フォントを探す。見つからなければデフォルトフォント。"""
    font_paths = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/OTF/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",  # macOS
        "/System/Library/Fonts/Supplemental/Hiragino Sans GB W3.otf",
        "C:/Windows/Fonts/msgothic.ttc",  # Windows
        "C:/Windows/Fonts/BIZ-UDGothicR.ttc",
    ]
    for p in font_paths:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def download_tile_grid(lat, lng, zoom, url_template):
    """中心座標の周囲3×3タイルをダウンロードして結合画像を返す"""
    cx, cy = latlng_to_tile(lat, lng, zoom)
    grid_img = Image.new("RGBA", (TILE_SIZE * GRID_SIZE, TILE_SIZE * GRID_SIZE), (0, 0, 0, 0))

    for dy in range(-1, 2):
        for dx in range(-1, 2):
            tx = cx + dx
            ty = cy + dy
            url = url_template.format(z=zoom, x=tx, y=ty)
            tile_data = fetch_tile(url)

            gx = (dx + 1) * TILE_SIZE
            gy = (dy + 1) * TILE_SIZE

            if tile_data is not None:
                try:
                    tile_img = Image.open(io.BytesIO(tile_data))
                    if tile_img.mode != "RGBA":
                        tile_img = tile_img.convert("RGBA")
                    grid_img.paste(tile_img, (gx, gy))
                except Exception:
                    pass  # タイル読み込み失敗 → 透明のまま

    return grid_img


def overlay_hazard_layers(base_img, lat, lng, zoom):
    """ハザードレイヤー4種を半透明で重ね合わせる"""
    result = base_img.copy()

    for layer_key, layer_info in HAZARD_LAYERS.items():
        print(f"  {layer_info['name']}を重ね合わせ中...", file=sys.stderr)
        hazard_grid = download_tile_grid(lat, lng, zoom, layer_info["url"])

        # 非透明ピクセルのアルファ値を調整（半透明化）- numpy不要のバンド演算
        r_band, g_band, b_band, a_band = hazard_grid.split()
        # アルファが30以上のピクセルをHAZARD_ALPHAに制限
        a_band = a_band.point(lambda a: min(a, HAZARD_ALPHA) if a > 30 else 0)
        hazard_grid = Image.merge("RGBA", (r_band, g_band, b_band, a_band))

        result = Image.alpha_composite(result, hazard_grid)

    return result


def draw_marker(img, lat, lng, zoom):
    """企業所在地に赤いマーカーを描画"""
    result = img.copy()
    draw = ImageDraw.Draw(result)

    cx, cy = latlng_to_tile(lat, lng, zoom)
    px, py = latlng_to_pixel_in_tile(lat, lng, zoom)

    # 3×3グリッド上の座標（中央タイルのオフセット）
    mx = TILE_SIZE + px
    my = TILE_SIZE + py

    # 外側の白縁 → 赤い丸 → 中心の白点
    r_outer = 10
    r_inner = 7
    r_center = 3
    draw.ellipse([mx - r_outer, my - r_outer, mx + r_outer, my + r_outer],
                 fill=(255, 255, 255, 220))
    draw.ellipse([mx - r_inner, my - r_inner, mx + r_inner, my + r_inner],
                 fill=(220, 38, 38, 255))
    draw.ellipse([mx - r_center, my - r_center, mx + r_center, my + r_center],
                 fill=(255, 255, 255, 255))

    return result


def draw_legend(img, has_any_risk=True):
    """右下に凡例を描画"""
    result = img.copy()
    draw = ImageDraw.Draw(result)

    font = _find_cjk_font(13)
    font_small = _find_cjk_font(10)

    items = LEGEND_ITEMS if has_any_risk else [("リスク検出なし", (200, 200, 200, 255))]

    # 凡例ボックスの寸法
    item_h = 18
    padding = 8
    box_w = 160
    box_h = padding * 2 + len(items) * item_h + 20  # +20 for title
    x0 = img.width - box_w - 10
    y0 = img.height - box_h - 10

    # 半透明白背景
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.rounded_rectangle([x0, y0, x0 + box_w, y0 + box_h],
                                   radius=4, fill=(255, 255, 255, 210))
    result = Image.alpha_composite(result, overlay)
    draw = ImageDraw.Draw(result)

    # タイトル
    draw.text((x0 + padding, y0 + padding), "凡例", fill=(30, 30, 30, 255), font=font)

    # 各項目
    for i, (label, color) in enumerate(items):
        iy = y0 + padding + 20 + i * item_h
        # 色ブロック
        draw.rectangle([x0 + padding, iy + 2, x0 + padding + 14, iy + 14],
                       fill=color, outline=(100, 100, 100, 255))
        # テキスト
        draw.text((x0 + padding + 20, iy), label,
                  fill=(30, 30, 30, 255), font=font_small)

    # マーカー凡例
    marker_y = y0 + box_h - item_h - 2
    draw.ellipse([x0 + padding + 3, marker_y + 4, x0 + padding + 11, marker_y + 12],
                 fill=(220, 38, 38, 255))
    draw.text((x0 + padding + 20, marker_y), "対象地点",
              fill=(30, 30, 30, 255), font=font_small)

    return result


def draw_attribution(img):
    """下部に出典テキストを描画"""
    result = img.copy()
    draw = ImageDraw.Draw(result)
    font = _find_cjk_font(10)

    bar_h = 22
    y0 = img.height - bar_h

    # 半透明の暗い帯
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.rectangle([0, y0, img.width, img.height], fill=(0, 0, 0, 150))
    result = Image.alpha_composite(result, overlay)

    draw = ImageDraw.Draw(result)
    draw.text((6, y0 + 4), ATTRIBUTION, fill=(255, 255, 255, 255), font=font)

    return result


def generate_hazard_map(lat, lng, output_path, zoom=14):
    """ハザードマップ画像を生成してPNGで保存"""
    print(f"ハザードマップ画像を生成中 ({lat}, {lng})...", file=sys.stderr)

    # 1. 背景地図（国土地理院 標準地図）
    print("  背景地図を取得中...", file=sys.stderr)
    base_img = download_tile_grid(lat, lng, zoom, GSI_STD_URL)
    base_img = base_img.convert("RGBA")

    # 2. ハザードレイヤー重ね合わせ
    result = overlay_hazard_layers(base_img, lat, lng, zoom)

    # 3. マーカー描画
    result = draw_marker(result, lat, lng, zoom)

    # 4. 凡例描画
    result = draw_legend(result)

    # 5. 出典テキスト
    result = draw_attribution(result)

    # 6. 保存
    result.save(output_path, "PNG", optimize=True)
    print(f"ハザードマップ画像を保存: {output_path}", file=sys.stderr)
    return output_path


def main():
    parser = argparse.ArgumentParser(description="ハザードマップ画像生成")
    parser.add_argument("--lat", type=float, required=True, help="緯度")
    parser.add_argument("--lng", type=float, required=True, help="経度")
    parser.add_argument("--output", type=str, required=True, help="出力PNGファイルパス")
    parser.add_argument("--zoom", type=int, default=14, help="ズームレベル（デフォルト: 14）")
    args = parser.parse_args()

    output = generate_hazard_map(args.lat, args.lng, args.output, args.zoom)
    print(output)


if __name__ == "__main__":
    main()
