#!/usr/bin/env python3
"""ハザードマップ画像生成 - 指定座標周辺のハザードマップをPNG画像として出力

国土地理院の標準地図タイルを背景に、国土交通省ハザードマップポータルの
洪水・土砂災害・津波のハザードレイヤーを重ね合わせた地図画像を生成する。
BCP文書のPDFに埋め込むための画像として使用。
"""

import argparse
import base64
import hashlib
import io
import json
import math
import os
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("Pillowがインストールされていません: pip install Pillow", file=sys.stderr)
    sys.exit(1)

# 同一ディレクトリの hazard_lookup.py から関数・定数をインポート
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hazard_lookup import latlng_to_tile, latlng_to_pixel_in_tile, fetch_tile, HAZARD_LAYERS, TILE_CACHE_DIR

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
    """日本語フォントを探す。3段階フォールバック: fc-list → 既知パス → Web自動DL。"""

    # --- 1. fc-list で動的検索（Linux / fontconfig導入済み環境） ---
    try:
        import subprocess
        result = subprocess.run(
            ["fc-list", ":lang=ja", "-f", "%{file}\n"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                line = line.strip()
                if line and os.path.exists(line):
                    try:
                        return ImageFont.truetype(line, size)
                    except Exception:
                        continue
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass  # fc-list が無い環境（macOS標準等）→ 次へ

    # --- 2. OS別の既知パスを順に試行 ---
    font_paths = [
        # Linux (Noto CJK)
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/OTF/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
        # macOS
        "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
        "/System/Library/Fonts/ヒラギノ角ゴ ProN W3.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/Supplemental/Hiragino Sans GB W3.otf",
        "/Library/Fonts/Arial Unicode.ttf",
        # Windows
        "C:/Windows/Fonts/YuGothR.ttc",
        "C:/Windows/Fonts/YuGothM.ttc",
        "C:/Windows/Fonts/meiryo.ttc",
        "C:/Windows/Fonts/msgothic.ttc",
        "C:/Windows/Fonts/msmincho.ttc",
        "C:/Windows/Fonts/BIZ-UDGothicR.ttc",
    ]
    for p in font_paths:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue

    # --- 3. Google Fonts から Noto Sans JP を自動DL（最終手段） ---
    cache_font_path = os.path.join(TILE_CACHE_DIR, "NotoSansJP-Regular.ttf")
    if os.path.exists(cache_font_path):
        try:
            return ImageFont.truetype(cache_font_path, size)
        except Exception:
            pass

    font_url = "https://github.com/googlefonts/noto-cjk/raw/main/Sans/Variable/TTF/NotoSansCJKjp-VF.ttf"
    print("日本語フォントが見つかりません。Noto Sans JP をダウンロード中...", file=sys.stderr)
    try:
        os.makedirs(TILE_CACHE_DIR, exist_ok=True)
        req = urllib.request.Request(font_url, headers={"User-Agent": "BCP-Generator/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            font_data = resp.read()
        with open(cache_font_path, "wb") as f:
            f.write(font_data)
        print(f"フォントを保存: {cache_font_path}", file=sys.stderr)
        return ImageFont.truetype(cache_font_path, size)
    except Exception as e:
        print(f"フォントのダウンロードに失敗: {e}", file=sys.stderr)

    return ImageFont.load_default()


def tile_to_latlng(tile_x, tile_y, zoom):
    """タイル座標→緯度経度（左上隅）。latlng_to_tileの逆関数。"""
    n = 2 ** zoom
    lng = tile_x / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * tile_y / n)))
    lat = math.degrees(lat_rad)
    return lat, lng


def get_grid_bbox(lat, lng, zoom):
    """3x3タイルグリッドのbbox (south, west, north, east) を返す。"""
    cx, cy = latlng_to_tile(lat, lng, zoom)
    north, west = tile_to_latlng(cx - 1, cy - 1, zoom)
    south, east = tile_to_latlng(cx + 2, cy + 2, zoom)
    return south, west, north, east


def fetch_shelters(south, west, north, east):
    """Overpass APIで避難所を検索。キャッシュ付き。失敗時は空リスト。"""
    bbox_str = f"{south},{west},{north},{east}"
    cache_key = "shelters_" + hashlib.md5(bbox_str.encode()).hexdigest()
    cache_path = os.path.join(TILE_CACHE_DIR, cache_key)

    # キャッシュ確認
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r") as f:
                return json.load(f)
        except Exception:
            pass

    query = f"""[out:json][timeout:15];
(
  node["emergency"="assembly_point"]({bbox_str});
  way["emergency"="assembly_point"]({bbox_str});
  node["amenity"="shelter"]({bbox_str});
  way["amenity"="shelter"]({bbox_str});
);
out center 20;"""

    try:
        data = urllib.parse.urlencode({"data": query}).encode("utf-8")
        req = urllib.request.Request(
            "https://overpass-api.de/api/interpreter",
            data=data,
            headers={"User-Agent": "BCP-Generator/1.0"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"  避難所検索失敗（続行）: {e}", file=sys.stderr)
        return []

    shelters = []
    for elem in result.get("elements", []):
        lat_val = elem.get("lat") or elem.get("center", {}).get("lat")
        lng_val = elem.get("lon") or elem.get("center", {}).get("lon")
        if lat_val is None or lng_val is None:
            continue
        name = elem.get("tags", {}).get("name", "避難所")
        shelters.append({"lat": float(lat_val), "lng": float(lng_val), "name": name})

    # キャッシュ保存
    try:
        os.makedirs(TILE_CACHE_DIR, exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump(shelters, f)
    except Exception:
        pass

    return shelters


def select_nearest_shelters(shelters, lat, lng, max_count=10):
    """避難所を近い順にソートしてmax_count件返す。"""
    def dist(s):
        return (s["lat"] - lat) ** 2 + (s["lng"] - lng) ** 2
    return sorted(shelters, key=dist)[:max_count]


def latlng_to_grid_pixel(lat, lng, center_lat, center_lng, zoom):
    """緯度経度を3x3グリッド画像上のピクセル座標に変換。"""
    # 中心タイル
    ccx, ccy = latlng_to_tile(center_lat, center_lng, zoom)
    # 対象のタイル座標とタイル内ピクセル
    tx, ty = latlng_to_tile(lat, lng, zoom)
    px, py = latlng_to_pixel_in_tile(lat, lng, zoom)
    # グリッド上のピクセル座標
    gx = (tx - ccx + 1) * TILE_SIZE + px
    gy = (ty - ccy + 1) * TILE_SIZE + py
    return gx, gy


def draw_shelter_markers(img, shelters, center_lat, center_lng, zoom):
    """避難所を緑三角マーカーで描画。"""
    result = img.copy()
    draw = ImageDraw.Draw(result)

    for s in shelters:
        gx, gy = latlng_to_grid_pixel(s["lat"], s["lng"], center_lat, center_lng, zoom)

        # 画像範囲外はスキップ
        if gx < 0 or gx >= img.width or gy < 0 or gy >= img.height:
            continue

        # 上向き三角形（幅12px × 高10px）
        half_w = 6
        h = 10
        top = (gx, gy - h // 2)
        left = (gx - half_w, gy + h // 2)
        right = (gx + half_w, gy + h // 2)

        # 白縁
        draw.polygon([top, left, right], outline=(255, 255, 255, 255))
        # 緑塗り（日本の防災標識の緑）
        draw.polygon([
            (top[0], top[1] + 1),
            (left[0] + 1, left[1] - 1),
            (right[0] - 1, right[1] - 1),
        ], fill=(0, 160, 80, 255))

    return result


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


def draw_legend(img, has_any_risk=True, has_shelters=False):
    """右下に凡例を描画"""
    result = img.copy()
    draw = ImageDraw.Draw(result)

    font = _find_cjk_font(13)
    font_small = _find_cjk_font(10)

    items = LEGEND_ITEMS if has_any_risk else [("リスク検出なし", (200, 200, 200, 255))]

    # 凡例ボックスの寸法（避難所ありなら1行追加）
    item_h = 18
    padding = 8
    box_w = 160
    extra_rows = 1 + (1 if has_shelters else 0)  # 対象地点 + 避難所
    box_h = padding * 2 + len(items) * item_h + 20 + extra_rows * item_h
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

    # マーカー凡例（対象地点 - 赤丸）
    marker_base_y = y0 + padding + 20 + len(items) * item_h
    draw.ellipse([x0 + padding + 3, marker_base_y + 4, x0 + padding + 11, marker_base_y + 12],
                 fill=(220, 38, 38, 255))
    draw.text((x0 + padding + 20, marker_base_y), "対象地点",
              fill=(30, 30, 30, 255), font=font_small)

    # 避難所凡例（緑三角）
    if has_shelters:
        shelter_y = marker_base_y + item_h
        sx = x0 + padding + 7
        sy = shelter_y + 3
        draw.polygon([(sx, sy), (sx - 5, sy + 9), (sx + 5, sy + 9)],
                     fill=(0, 160, 80, 255), outline=(255, 255, 255, 255))
        draw.text((x0 + padding + 20, shelter_y), "避難所",
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


def generate_hazard_map(lat, lng, output_path, zoom=14, show_shelters=True):
    """ハザードマップ画像を生成してPNGで保存（全タイル並列取得）"""
    print(f"ハザードマップ画像を生成中 ({lat}, {lng})...", file=sys.stderr)

    cx, cy = latlng_to_tile(lat, lng, zoom)

    # 1. 全タイルURL収集 (背景9 + ハザード4層×9 = 45枚)
    tile_requests = []  # (layer_key, dx, dy, url)
    for dy in range(-1, 2):
        for dx in range(-1, 2):
            tx, ty = cx + dx, cy + dy
            tile_requests.append(("base", dx, dy, GSI_STD_URL.format(z=zoom, x=tx, y=ty)))
            for layer_key, layer_info in HAZARD_LAYERS.items():
                tile_requests.append((layer_key, dx, dy, layer_info["url"].format(z=zoom, x=tx, y=ty)))

    print(f"  全{len(tile_requests)}タイルを並列取得中...", file=sys.stderr)

    # 2. ThreadPoolExecutorで一括並列取得（避難所検索も同時実行）
    tile_data = {}
    shelter_future = None
    with ThreadPoolExecutor(max_workers=16) as executor:
        future_map = {
            executor.submit(fetch_tile, url): (key, dx, dy)
            for key, dx, dy, url in tile_requests
        }

        # 避難所検索をタイル取得と並列実行
        if show_shelters:
            bbox = get_grid_bbox(lat, lng, zoom)
            shelter_future = executor.submit(fetch_shelters, *bbox)

        for future in as_completed(future_map):
            key_info = future_map[future]
            try:
                tile_data[key_info] = future.result()
            except Exception:
                tile_data[key_info] = None

    # 避難所データ取得
    shelters = []
    if shelter_future is not None:
        try:
            shelters = shelter_future.result()
            shelters = select_nearest_shelters(shelters, lat, lng)
            print(f"  避難所{len(shelters)}件検出", file=sys.stderr)
        except Exception:
            shelters = []

    # 3. 背景画像を組み立て
    base_img = Image.new("RGBA", (TILE_SIZE * GRID_SIZE, TILE_SIZE * GRID_SIZE), (0, 0, 0, 0))
    for dy in range(-1, 2):
        for dx in range(-1, 2):
            data = tile_data.get(("base", dx, dy))
            if data:
                try:
                    tile_img = Image.open(io.BytesIO(data)).convert("RGBA")
                    base_img.paste(tile_img, ((dx + 1) * TILE_SIZE, (dy + 1) * TILE_SIZE))
                except Exception:
                    pass

    # 4. ハザードレイヤー重ね合わせ（タイルデータは取得済み、組み立てのみ）
    result = base_img
    for layer_key, layer_info in HAZARD_LAYERS.items():
        print(f"  {layer_info['name']}を重ね合わせ中...", file=sys.stderr)
        grid = Image.new("RGBA", (TILE_SIZE * GRID_SIZE, TILE_SIZE * GRID_SIZE), (0, 0, 0, 0))
        for dy in range(-1, 2):
            for dx in range(-1, 2):
                data = tile_data.get((layer_key, dx, dy))
                if data:
                    try:
                        tile_img = Image.open(io.BytesIO(data)).convert("RGBA")
                        grid.paste(tile_img, ((dx + 1) * TILE_SIZE, (dy + 1) * TILE_SIZE))
                    except Exception:
                        pass

        r_band, g_band, b_band, a_band = grid.split()
        a_band = a_band.point(lambda a: min(a, HAZARD_ALPHA) if a > 30 else 0)
        grid = Image.merge("RGBA", (r_band, g_band, b_band, a_band))
        result = Image.alpha_composite(result, grid)

    # 5. マーカー描画
    result = draw_marker(result, lat, lng, zoom)

    # 5.5. 避難所マーカー描画
    if shelters:
        result = draw_shelter_markers(result, shelters, lat, lng, zoom)

    # 6. 凡例描画
    result = draw_legend(result, has_shelters=len(shelters) > 0)

    # 7. 出典テキスト
    result = draw_attribution(result)

    # 8. RGBA→RGB変換（白背景）して保存。ファイルサイズ削減＋API互換性向上
    rgb_img = Image.new("RGB", result.size, (255, 255, 255))
    rgb_img.paste(result, mask=result.split()[3])
    rgb_img.save(output_path, "PNG", optimize=True)
    print(f"ハザードマップ画像を保存: {output_path}", file=sys.stderr)
    return output_path


def png_to_base64_data_uri(png_path):
    """PNGファイルをBase64 data URIに変換"""
    with open(png_path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def main():
    parser = argparse.ArgumentParser(description="ハザードマップ画像生成")
    parser.add_argument("--lat", type=float, required=True, help="緯度")
    parser.add_argument("--lng", type=float, required=True, help="経度")
    parser.add_argument("--output", type=str, required=True, help="出力PNGファイルパス")
    parser.add_argument("--zoom", type=int, default=14, help="ズームレベル（デフォルト: 14）")
    parser.add_argument("--base64", action="store_true",
                        help="Base64 data URIをファイルに書き出す（{output}.b64.txt）。stdoutにはファイルパスを出力")
    parser.add_argument("--no-shelters", action="store_true", help="避難所マーカーを非表示")
    args = parser.parse_args()

    output = generate_hazard_map(args.lat, args.lng, args.output, args.zoom,
                                 show_shelters=not args.no_shelters)
    if args.base64:
        b64_path = args.output + ".b64.txt"
        data_uri = png_to_base64_data_uri(args.output)
        with open(b64_path, "w") as f:
            f.write(data_uri)
        print(f"Base64 data URI saved to: {b64_path}", file=sys.stderr)
        print(b64_path)
    else:
        print(output)


if __name__ == "__main__":
    main()
