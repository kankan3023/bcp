#!/usr/bin/env python3
"""ジオコーディング（住所→座標変換）
国土地理院APIをメインに、Nominatim（OSM）をフォールバックとして使用。
kitaku-routeプロジェクトから流用・汎用化。
"""

import json
import re
import sys
import urllib.request
import urllib.parse


def geocode_gsi(query: str) -> dict | None:
    """国土地理院APIで検索（住所に強い）"""
    encoded = urllib.parse.quote(query)
    url = f"https://msearch.gsi.go.jp/address-search/AddressSearch?q={encoded}"

    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    if not data:
        return None

    result = data[0]
    coordinates = result["geometry"]["coordinates"]
    label = result["properties"]["title"]

    return {"lat": coordinates[1], "lng": coordinates[0], "label": label}


def geocode_nominatim(query: str) -> dict | None:
    """Nominatim（OSM）で検索（ランドマーク・施設名に強い）"""
    params = urllib.parse.urlencode({
        "q": query,
        "format": "json",
        "limit": 5,
        "countrycodes": "jp",
        "accept-language": "ja",
    })
    url = f"https://nominatim.openstreetmap.org/search?{params}"

    req = urllib.request.Request(url, headers={"User-Agent": "bcp-generator-skill/0.1"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    if not data:
        return None

    result = data[0]
    return {
        "lat": float(result["lat"]),
        "lng": float(result["lon"]),
        "label": result["display_name"].split(",")[0],
    }


def _is_postalcode(query: str) -> str | None:
    """郵便番号パターンを検出してハイフンなし7桁で返す"""
    cleaned = query.replace("〒", "").replace(" ", "").replace("　", "").strip()
    m = re.match(r"^(\d{3})-?(\d{4})$", cleaned)
    if m:
        return m.group(1) + m.group(2)
    return None


def geocode_postalcode(zipcode: str) -> dict | None:
    """郵便番号→座標変換（Nominatim優先、zipcloud+GSIフォールバック）"""
    params = urllib.parse.urlencode({
        "postalcode": zipcode,
        "country": "jp",
        "format": "json",
        "limit": 1,
        "accept-language": "ja",
    })
    url = f"https://nominatim.openstreetmap.org/search?{params}"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "bcp-generator-skill/0.1"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        if data:
            r = data[0]
            label = f"〒{zipcode[:3]}-{zipcode[3:]}付近"
            return {"lat": float(r["lat"]), "lng": float(r["lon"]), "label": label}
    except Exception:
        pass

    # フォールバック: zipcloud（郵便番号→住所）→ 国土地理院（住所→座標）
    try:
        url = f"https://zipcloud.ibsnet.co.jp/api/search?zipcode={zipcode}"
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        results = data.get("results")
        if results:
            r = results[0]
            address = f"{r['address1']}{r['address2']}{r['address3']}"
            gsi_result = geocode_gsi(address)
            if gsi_result:
                gsi_result["label"] = f"〒{zipcode[:3]}-{zipcode[3:]}付近"
                return gsi_result
    except Exception:
        pass

    return None


def geocode(query: str) -> dict | None:
    """住所・場所名・郵便番号から座標を取得する（多段フォールバック）"""
    # 郵便番号の場合は専用処理
    zipcode = _is_postalcode(query)
    if zipcode:
        return geocode_postalcode(zipcode)

    # 住所系は国土地理院を優先
    try:
        result = geocode_gsi(query)
        if result:
            return result
    except Exception:
        pass

    # フォールバック: Nominatim
    try:
        result = geocode_nominatim(query)
        if result:
            return result
    except Exception:
        pass

    return None


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "使い方: geocode.py <住所または場所名>"}))
        sys.exit(1)

    query = " ".join(sys.argv[1:])
    result = geocode(query)

    if result is None:
        print(json.dumps({"error": f"'{query}' の座標が見つかりませんでした"}, ensure_ascii=False))
        sys.exit(1)

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
