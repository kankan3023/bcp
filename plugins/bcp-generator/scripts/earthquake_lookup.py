#!/usr/bin/env python3
"""地震リスク分析 - J-SHIS APIを使用した地震ハザード・表層地盤情報の取得

防災科学技術研究所 J-SHIS（地震ハザードステーション）Web APIから
指定座標の地震発生確率・想定震度・地盤特性・液状化リスクを取得・分析する。
"""

import json
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# J-SHIS API設定
JSHIS_BASE = "https://www.j-shis.bosai.go.jp/map/api"
PSHM_VERSION = "Y2024"  # 最新版
SSTRCT_VERSION = "V4"   # 表層地盤データ最新版
EPSG = "4326"            # WGS84

# 震度確率の属性名
INTENSITY_ATTRS = {
    "T30_I45_PS": {"intensity": "5弱", "label": "30年以内に震度5弱以上"},
    "T30_I50_PS": {"intensity": "5強", "label": "30年以内に震度5強以上"},
    "T30_I55_PS": {"intensity": "6弱", "label": "30年以内に震度6弱以上"},
    "T30_I60_PS": {"intensity": "6強", "label": "30年以内に震度6強以上"},
}

# 微地形分類と液状化リスクの対応表
# 参考: 内閣府 液状化判定基準、J-SHIS微地形分類コード
LIQUEFACTION_RISK_BY_LANDFORM = {
    # 高リスク
    "埋立地": "高い",
    "干拓地": "高い",
    "旧河道": "高い",
    "後背湿地": "高い",
    "三角州・海岸低地": "高い",
    "砂州・砂丘間低地": "高い",
    "砂丘末端緩斜面": "やや高い",
    # 中リスク
    "自然堤防": "やや高い",
    "扇状地": "やや高い",
    "谷底平野": "やや高い",
    "砂礫質台地": "低い",
    "砂丘": "やや高い",
    # 低リスク
    "ローム台地": "非常に低い",
    "台地": "低い",
    "砂礫質台地II": "低い",
    "岩盤": "非常に低い",
    "山地": "非常に低い",
    "丘陵": "非常に低い",
    "火山地": "低い",
    "火山山麓地": "低い",
    "火山性丘陵": "低い",
    "大起伏山地": "非常に低い",
    "小起伏山地": "非常に低い",
    "山麓地": "非常に低い",
}


def fetch_json(url):
    """URLからJSONを取得"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "bcp-generator-skill/0.1"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"  API取得失敗: {e}", file=sys.stderr)
        return None


def get_earthquake_hazard(lat, lng):
    """地震ハザード情報（確率論的地震動予測）を取得"""
    url = (
        f"{JSHIS_BASE}/pshm/{PSHM_VERSION}/AVR/TTL_MTTL/meshinfo.geojson"
        f"?position={lng},{lat}&epsg={EPSG}"
    )
    print(f"  地震ハザード情報を取得中...", file=sys.stderr)
    data = fetch_json(url)

    if not data or data.get("status") != "Success" or not data.get("features"):
        return None

    props = data["features"][0]["properties"]
    meshcode = props.get("meshcode", "")

    probabilities = {}
    for attr_key, attr_info in INTENSITY_ATTRS.items():
        val = props.get(attr_key)
        if val is not None:
            prob = float(val)
            probabilities[attr_key] = {
                "intensity": attr_info["intensity"],
                "label": attr_info["label"],
                "probability": round(prob, 6),
                "percent": round(prob * 100, 1),
            }

    return {
        "meshcode": meshcode,
        "probabilities": probabilities,
    }


def get_surface_ground(lat, lng):
    """表層地盤情報を取得"""
    url = (
        f"{JSHIS_BASE}/sstrct/{SSTRCT_VERSION}/meshinfo.geojson"
        f"?position={lng},{lat}&epsg={EPSG}"
    )
    print(f"  表層地盤情報を取得中...", file=sys.stderr)
    data = fetch_json(url)

    if not data or data.get("status") != "Success" or not data.get("features"):
        return None

    props = data["features"][0]["properties"]

    avs = float(props["AVS"]) if props.get("AVS") else None
    arv = float(props["ARV"]) if props.get("ARV") else None
    jname = props.get("JNAME", "")
    jcode = props.get("JCODE", "")

    # AVS30に基づく地盤の硬さ判定
    ground_firmness = "不明"
    if avs is not None:
        if avs >= 600:
            ground_firmness = "非常に硬い（岩盤級）"
        elif avs >= 400:
            ground_firmness = "硬い（工学的基盤級）"
        elif avs >= 300:
            ground_firmness = "やや硬い"
        elif avs >= 200:
            ground_firmness = "やや軟らかい"
        elif avs >= 150:
            ground_firmness = "軟らかい"
        else:
            ground_firmness = "非常に軟らかい"

    # 増幅率に基づく揺れやすさ判定
    shaking_ease = "不明"
    if arv is not None:
        if arv >= 2.0:
            shaking_ease = "非常に揺れやすい"
        elif arv >= 1.6:
            shaking_ease = "揺れやすい"
        elif arv >= 1.4:
            shaking_ease = "やや揺れやすい"
        elif arv >= 1.2:
            shaking_ease = "普通"
        else:
            shaking_ease = "揺れにくい"

    return {
        "landform": jname,
        "landform_code": jcode,
        "avs30": avs,
        "amplification_ratio": arv,
        "ground_firmness": ground_firmness,
        "shaking_ease": shaking_ease,
    }


def assess_liquefaction(surface_ground):
    """液状化リスクの簡易判定（微地形分類 + AVS30）"""
    if surface_ground is None:
        return {"risk_level": "判定不能", "basis": "表層地盤データが取得できませんでした"}

    landform = surface_ground.get("landform", "")
    avs = surface_ground.get("avs30")

    # 微地形による判定
    landform_risk = LIQUEFACTION_RISK_BY_LANDFORM.get(landform, "不明")

    # AVS30による判定
    avs_risk = "不明"
    if avs is not None:
        if avs < 150:
            avs_risk = "高い"
        elif avs < 200:
            avs_risk = "やや高い"
        elif avs < 300:
            avs_risk = "低い"
        else:
            avs_risk = "非常に低い"

    # 総合判定（厳しい方を採用）
    risk_order = ["非常に低い", "低い", "やや高い", "高い"]
    final_risk = "不明"
    risks = []
    if landform_risk in risk_order:
        risks.append(landform_risk)
    if avs_risk in risk_order:
        risks.append(avs_risk)

    if risks:
        final_risk = max(risks, key=lambda r: risk_order.index(r))

    basis_parts = []
    if landform:
        basis_parts.append(f"微地形分類「{landform}」→ {landform_risk}")
    if avs is not None:
        basis_parts.append(f"AVS30={avs}m/s → {avs_risk}")

    return {
        "risk_level": final_risk,
        "landform_risk": landform_risk,
        "avs_risk": avs_risk,
        "basis": "、".join(basis_parts) if basis_parts else "判定根拠なし",
    }


def estimate_seismic_intensity(probabilities):
    """確率データから想定震度レンジを推定"""
    if not probabilities:
        return {"estimated_intensity": "不明", "basis": "データなし"}

    # 最も高い確率で起きる震度を推定
    # 30年以内に26%以上 = 「発生の可能性が高い」
    estimated = "5弱未満"
    for attr_key in ["T30_I60_PS", "T30_I55_PS", "T30_I50_PS", "T30_I45_PS"]:
        if attr_key in probabilities:
            prob = probabilities[attr_key]["probability"]
            if prob >= 0.26:  # 30年で26%以上 ≈ 年1%
                estimated = f"{probabilities[attr_key]['intensity']}以上"
                break

    # 最も影響が大きい想定（3%以上の確率がある最大震度）
    max_possible = "5弱未満"
    for attr_key in ["T30_I60_PS", "T30_I55_PS", "T30_I50_PS", "T30_I45_PS"]:
        if attr_key in probabilities:
            prob = probabilities[attr_key]["probability"]
            if prob >= 0.03:  # 3%以上で「可能性あり」
                max_possible = f"{probabilities[attr_key]['intensity']}以上"
                break

    return {
        "likely_intensity": estimated,
        "max_possible_intensity": max_possible,
    }


def analyze_earthquake_risk(lat, lng):
    """指定座標の地震リスク総合分析"""
    print(f"地点 ({lat}, {lng}) の地震リスク分析を開始...", file=sys.stderr)

    # 1+2. 地震ハザード情報と表層地盤情報を並列取得
    with ThreadPoolExecutor(max_workers=2) as executor:
        hazard_future = executor.submit(get_earthquake_hazard, lat, lng)
        ground_future = executor.submit(get_surface_ground, lat, lng)
        hazard = hazard_future.result()
        ground = ground_future.result()

    # 3. 液状化リスク判定
    liquefaction = assess_liquefaction(ground)

    # 4. 想定震度の推定
    intensity_estimate = {}
    if hazard and hazard.get("probabilities"):
        intensity_estimate = estimate_seismic_intensity(hazard["probabilities"])

    # 結果構築
    result = {
        "center": {"lat": lat, "lng": lng},
        "earthquake_hazard": None,
        "surface_ground": None,
        "liquefaction": liquefaction,
        "intensity_estimate": intensity_estimate,
        "summary": "",
    }

    if hazard:
        result["earthquake_hazard"] = {
            "meshcode": hazard["meshcode"],
            "data_version": PSHM_VERSION,
            "probabilities": hazard["probabilities"],
        }
        # 確率のログ出力
        for attr_key, info in hazard["probabilities"].items():
            level = "⚠" if info["percent"] >= 26 else "  "
            print(f"  {level} {info['label']}: {info['percent']}%", file=sys.stderr)

    if ground:
        result["surface_ground"] = {
            "data_version": SSTRCT_VERSION,
            **ground,
        }
        print(f"  地盤: {ground['landform']} / AVS30={ground['avs30']}m/s / "
              f"増幅率={ground['amplification_ratio']} / {ground['shaking_ease']}", file=sys.stderr)

    print(f"  液状化リスク: {liquefaction['risk_level']}（{liquefaction['basis']}）", file=sys.stderr)

    # サマリー生成
    summary_parts = []
    if intensity_estimate.get("likely_intensity"):
        summary_parts.append(f"想定震度: {intensity_estimate['likely_intensity']}（発生確率26%超）")
    if intensity_estimate.get("max_possible_intensity"):
        summary_parts.append(f"最大想定: {intensity_estimate['max_possible_intensity']}（発生確率3%超）")
    if ground:
        summary_parts.append(f"地盤: {ground['landform']}（{ground['ground_firmness']}、{ground['shaking_ease']}）")
    summary_parts.append(f"液状化リスク: {liquefaction['risk_level']}")

    result["summary"] = " / ".join(summary_parts)
    print(f"\nサマリー: {result['summary']}", file=sys.stderr)

    return result


def main():
    import argparse

    parser = argparse.ArgumentParser(description="地震リスク分析（J-SHIS API）")
    parser.add_argument("--lat", type=float, required=True, help="緯度")
    parser.add_argument("--lng", type=float, required=True, help="経度")
    parser.add_argument("--output", type=str, default=None, help="出力ファイルパス")

    args = parser.parse_args()

    result = analyze_earthquake_risk(args.lat, args.lng)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"\n結果を {args.output} に保存しました", file=sys.stderr)

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
