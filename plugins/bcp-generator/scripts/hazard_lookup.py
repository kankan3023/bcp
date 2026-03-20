#!/usr/bin/env python3
"""地点ハザード分析 - 指定座標周辺のハザードマップデータを収集・分析
国土交通省ハザードマップポータルのタイルデータを使用。
kitaku-routeプロジェクトのhazard.pyをベースに地点分析版として再構築。
"""

import hashlib
import io
import json
import math
import os
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

try:
    from PIL import Image
except ImportError:
    print("Pillowがインストールされていません: pip install Pillow", file=sys.stderr)
    sys.exit(1)

# ハザードマップタイルURL（国土交通省 ハザードマップポータル）
HAZARD_LAYERS = {
    "flood": {
        "name": "洪水浸水想定区域",
        "url": "https://disaportaldata.gsi.go.jp/raster/01_flood_l2_shinsuishin_data/{z}/{x}/{y}.png",
        "description": "想定最大規模の降雨による浸水想定",
        "severity_colors": {
            "light_yellow": {"range": "0.5m未満", "depth": "床下浸水程度"},
            "yellow": {"range": "0.5-3.0m", "depth": "1階天井まで浸水"},
            "orange": {"range": "3.0-5.0m", "depth": "2階まで浸水"},
            "red": {"range": "5.0-10.0m", "depth": "3階まで浸水"},
            "purple": {"range": "10.0m以上", "depth": "3階以上が浸水"},
        },
    },
    "landslide_debris": {
        "name": "土砂災害警戒区域（土石流）",
        "url": "https://disaportaldata.gsi.go.jp/raster/05_dosekiryukeikaikuiki/{z}/{x}/{y}.png",
        "description": "土石流による土砂災害警戒区域",
    },
    "landslide_slope": {
        "name": "土砂災害警戒区域（急傾斜地）",
        "url": "https://disaportaldata.gsi.go.jp/raster/05_kyukeishakeikaikuiki/{z}/{x}/{y}.png",
        "description": "急傾斜地の崩壊による土砂災害警戒区域",
    },
    "tsunami": {
        "name": "津波浸水想定区域",
        "url": "https://disaportaldata.gsi.go.jp/raster/04_tsunami_newlegend_data/{z}/{x}/{y}.png",
        "description": "津波による浸水想定区域",
    },
}

ANALYSIS_ZOOM = 14

TILE_CACHE_DIR = "/tmp/bcp_tile_cache"

_tile_cache = {}


def latlng_to_tile(lat, lng, zoom):
    """緯度経度→タイル座標"""
    n = 2 ** zoom
    x = int((lng + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


def latlng_to_pixel_in_tile(lat, lng, zoom):
    """緯度経度→タイル内ピクセル座標"""
    n = 2 ** zoom
    px = int(((lng + 180.0) / 360.0 * n) % 1.0 * 256)
    lat_rad = math.radians(lat)
    py = int(((1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n) % 1.0 * 256)
    return px, py


def fetch_tile(url):
    """タイルを取得（メモリ+ファイルキャッシュ付き）"""
    if url in _tile_cache:
        return _tile_cache[url]

    # ファイルキャッシュチェック
    cache_key = hashlib.md5(url.encode()).hexdigest()
    cache_path = os.path.join(TILE_CACHE_DIR, cache_key)
    try:
        if os.path.exists(cache_path):
            with open(cache_path, "rb") as f:
                data = f.read()
            _tile_cache[url] = data
            return data
    except Exception:
        pass

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "bcp-generator-skill/0.1"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read()
        _tile_cache[url] = data
        # ファイルキャッシュに保存
        try:
            os.makedirs(TILE_CACHE_DIR, exist_ok=True)
            with open(cache_path, "wb") as f:
                f.write(data)
        except Exception:
            pass
        return data
    except Exception:
        _tile_cache[url] = None
        return None


def check_hazard_at_point(lat, lng, layer_key):
    """指定地点のハザード有無をチェック。色情報も返す。"""
    layer = HAZARD_LAYERS[layer_key]
    tx, ty = latlng_to_tile(lat, lng, ANALYSIS_ZOOM)
    px, py = latlng_to_pixel_in_tile(lat, lng, ANALYSIS_ZOOM)

    url = layer["url"].format(z=ANALYSIS_ZOOM, x=tx, y=ty)
    tile_data = fetch_tile(url)

    if tile_data is None:
        return {"has_risk": False}

    try:
        img = Image.open(io.BytesIO(tile_data))
        if img.mode != "RGBA":
            img = img.convert("RGBA")

        px = min(px, img.width - 1)
        py = min(py, img.height - 1)
        r, g, b, a = img.getpixel((px, py))

        if a <= 30:
            return {"has_risk": False}

        # 洪水レイヤーの場合、色から浸水深を推定
        severity = "unknown"
        if layer_key == "flood":
            if r > 200 and g > 200 and b < 150:
                severity = "0.5m未満"
            elif r > 230 and g > 180 and b < 100:
                severity = "0.5-3.0m"
            elif r > 230 and g > 100 and g < 180 and b < 80:
                severity = "3.0-5.0m"
            elif r > 200 and g < 100:
                severity = "5.0-10.0m"
            elif b > r:
                severity = "10.0m以上"
            else:
                severity = "浸水あり（深さ不明）"
        elif layer_key == "tsunami":
            if r > 200 and g > 200 and b < 150:
                severity = "0.3m未満"
            elif r > 230 and g > 180 and b < 100:
                severity = "0.3-1.0m"
            elif r > 230 and g > 100 and b < 80:
                severity = "1.0-2.0m"
            elif r > 200 and g < 100:
                severity = "2.0m以上"
            else:
                severity = "浸水あり（深さ不明）"

        return {"has_risk": True, "rgba": [r, g, b, a], "severity": severity}
    except Exception:
        return {"has_risk": False}


def analyze_point(lat, lng, grid_size=5):
    """指定地点周辺のハザード分析（グリッドサンプリング）

    Args:
        lat: 緯度
        lng: 経度
        grid_size: グリッドサイズ（grid_size x grid_size）。デフォルト5（約1km四方）

    Returns:
        dict: 各ハザードレイヤーの分析結果
    """
    # 全レイヤーの中心タイルを並列プリフェッチ（後続のcheck_hazard_at_pointでキャッシュヒット）
    cx, cy = latlng_to_tile(lat, lng, ANALYSIS_ZOOM)
    prefetch_urls = [
        layer_info["url"].format(z=ANALYSIS_ZOOM, x=cx, y=cy)
        for layer_info in HAZARD_LAYERS.values()
    ]
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(fetch_tile, prefetch_urls))

    # グリッド間隔: zoom14のタイルは約10km四方、256px
    # 1ピクセル ≈ 40m、5x5グリッドで200m間隔 → 約1km四方
    offset_deg = 0.001  # 約100m間隔

    results = {}

    for layer_key, layer_info in HAZARD_LAYERS.items():
        print(f"  {layer_info['name']}を分析中...", file=sys.stderr)

        risk_points = []
        total_points = 0
        max_severity = None

        half = grid_size // 2
        for dy in range(-half, half + 1):
            for dx in range(-half, half + 1):
                check_lat = lat + dy * offset_deg
                check_lng = lng + dx * offset_deg
                total_points += 1

                result = check_hazard_at_point(check_lat, check_lng, layer_key)
                if result["has_risk"]:
                    risk_points.append({
                        "lat": round(check_lat, 6),
                        "lng": round(check_lng, 6),
                        "severity": result.get("severity", "あり"),
                    })
                    if result.get("severity") and result["severity"] != "unknown":
                        max_severity = result["severity"]

        risk_ratio = len(risk_points) / total_points if total_points > 0 else 0

        # リスクレベル判定
        if risk_ratio == 0:
            risk_level = "なし"
        elif risk_ratio < 0.2:
            risk_level = "低い（周辺部のみ）"
        elif risk_ratio < 0.5:
            risk_level = "中程度"
        elif risk_ratio < 0.8:
            risk_level = "高い"
        else:
            risk_level = "非常に高い（全域）"

        layer_result = {
            "name": layer_info["name"],
            "description": layer_info["description"],
            "has_risk": len(risk_points) > 0,
            "risk_point_count": len(risk_points),
            "total_points_checked": total_points,
            "risk_ratio": round(risk_ratio, 2),
            "risk_level": risk_level,
        }
        if max_severity:
            layer_result["max_severity"] = max_severity

        results[layer_key] = layer_result

        if risk_points:
            severity_info = f"（最大: {max_severity}）" if max_severity else ""
            print(f"    ⚠ リスク{risk_level} - {len(risk_points)}/{total_points}地点{severity_info}",
                  file=sys.stderr)
        else:
            print(f"    ✓ リスクなし", file=sys.stderr)

    # サマリー
    risk_layers = [r for r in results.values() if r["has_risk"]]
    if risk_layers:
        summary_parts = []
        for r in risk_layers:
            severity_info = f"（{r['max_severity']}）" if r.get("max_severity") else ""
            summary_parts.append(f"{r['name']}: {r['risk_level']}{severity_info}")
        summary = "⚠ " + "、".join(summary_parts)
    else:
        summary = "分析対象のハザードマップ上にリスクは検出されませんでした"

    output = {
        "center": {"lat": lat, "lng": lng},
        "grid_size": grid_size,
        "analysis_zoom": ANALYSIS_ZOOM,
        "layers": results,
        "has_any_risk": any(r["has_risk"] for r in results.values()),
        "summary": summary,
    }

    return output


def main():
    import argparse

    parser = argparse.ArgumentParser(description="地点ハザード分析")
    parser.add_argument("--lat", type=float, required=True, help="緯度")
    parser.add_argument("--lng", type=float, required=True, help="経度")
    parser.add_argument("--grid", type=int, default=5, help="グリッドサイズ（デフォルト: 5）")
    parser.add_argument("--output", type=str, default=None, help="出力ファイルパス")

    args = parser.parse_args()

    print(f"地点 ({args.lat}, {args.lng}) のハザード分析を開始...", file=sys.stderr)
    result = analyze_point(args.lat, args.lng, grid_size=args.grid)

    output_path = args.output
    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"\n結果を {output_path} に保存しました", file=sys.stderr)

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
