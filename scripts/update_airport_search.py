"""Build an offline airport picker catalog from public-domain OurAirports data."""
import csv
from datetime import datetime, timezone
import io
import json
from pathlib import Path

import httpx


SOURCE = "https://raw.githubusercontent.com/davidmegginson/ourairports-data/main/airports.csv"
ALIASES = {
    "PEK": "北京 北京首都 首都", "PKX": "北京 北京大兴 大兴", "SHA": "上海 上海虹桥 虹桥",
    "PVG": "上海 上海浦东 浦东", "CAN": "广州 广州白云 白云", "SZX": "深圳 深圳宝安 宝安",
    "CTU": "成都 成都双流 双流", "TFU": "成都 成都天府 天府", "HKG": "香港 赤鱲角",
    "MFM": "澳门", "TPE": "台北 台湾 桃园", "TSA": "台北 台湾 松山", "KHH": "高雄 台湾",
    "CKG": "重庆 江北", "HGH": "杭州 萧山", "NKG": "南京 禄口", "WUH": "武汉 天河",
    "XIY": "西安 咸阳", "KMG": "昆明 长水", "XMN": "厦门 高崎", "FOC": "福州 长乐",
    "CSX": "长沙 黄花", "CGO": "郑州 新郑", "TAO": "青岛 胶东", "TNA": "济南 遥墙",
    "DLC": "大连 周水子", "SHE": "沈阳 桃仙", "HRB": "哈尔滨 太平", "CGQ": "长春 龙嘉",
    "TSN": "天津 滨海", "SJW": "石家庄 正定", "TYN": "太原 武宿", "HET": "呼和浩特 白塔",
    "URC": "乌鲁木齐 地窝堡 天山", "LHW": "兰州 中川", "INC": "银川 河东", "XNN": "西宁 曹家堡",
    "LXA": "拉萨 贡嘎", "KWE": "贵阳 龙洞堡", "NNG": "南宁 吴圩", "HAK": "海口 美兰",
    "SYX": "三亚 凤凰", "NGB": "宁波 栎社", "WNZ": "温州 龙湾", "HFE": "合肥 新桥",
    "KHN": "南昌 昌北", "WUX": "无锡 苏南硕放", "NTG": "南通 兴东", "LZO": "泸州 云龙",
    "JFK": "纽约 肯尼迪", "EWR": "纽约 纽瓦克", "LGA": "纽约 拉瓜迪亚",
    "LAX": "洛杉矶", "SFO": "旧金山 三藩市", "SJC": "圣何塞", "OAK": "奥克兰 旧金山湾区",
    "IAH": "休斯敦 乔治布什", "HOU": "休斯敦 霍比", "SEA": "西雅图 塔科马",
    "ORD": "芝加哥 奥黑尔", "MDW": "芝加哥 中途", "DFW": "达拉斯 沃斯堡", "DAL": "达拉斯",
    "ATL": "亚特兰大", "BOS": "波士顿 洛根", "IAD": "华盛顿 杜勒斯", "DCA": "华盛顿 里根",
    "YYZ": "多伦多 皮尔逊", "YVR": "温哥华", "YUL": "蒙特利尔",
    "LHR": "伦敦 希思罗", "LGW": "伦敦 盖特威克", "STN": "伦敦 斯坦斯特德",
    "CDG": "巴黎 戴高乐", "ORY": "巴黎 奥利", "AMS": "阿姆斯特丹 史基浦", "FRA": "法兰克福",
    "MUC": "慕尼黑", "ZRH": "苏黎世", "FCO": "罗马", "MAD": "马德里", "BCN": "巴塞罗那",
    "IST": "伊斯坦布尔", "DXB": "迪拜", "DOH": "多哈", "AUH": "阿布扎比",
    "SIN": "新加坡 樟宜", "BKK": "曼谷 素万那普", "DMK": "曼谷 廊曼", "KUL": "吉隆坡",
    "NRT": "东京 成田", "HND": "东京 羽田", "KIX": "大阪 关西", "ITM": "大阪 伊丹",
    "ICN": "首尔 仁川", "GMP": "首尔 金浦", "PUS": "釜山", "CJU": "济州",
    "SYD": "悉尼", "MEL": "墨尔本", "BNE": "布里斯班", "AKL": "奥克兰 新西兰",
}


def main():
    response = httpx.get(SOURCE, timeout=60, follow_redirects=True)
    response.raise_for_status()
    airports = {}
    for row in csv.DictReader(io.StringIO(response.text)):
        code = row["iata_code"]
        if (len(code) != 3 or row["scheduled_service"] != "yes"
                or row["type"] not in {"large_airport", "medium_airport", "small_airport"}):
            continue
        airports[code] = {"code": code, "name": row["name"], "city": row["municipality"],
                          "country": row["iso_country"], "aliases": ALIASES.get(code, "")}
    if not {"JFK", "LHR", "PEK", "PKX", "HKG"} <= airports.keys():
        raise ValueError("Airport source is incomplete; existing catalog was not replaced")
    output = Path(__file__).resolve().parents[1] / "core/airport_search.json"
    output.write_text(json.dumps({"source": SOURCE, "license": "Public Domain",
                                  "updated_at": datetime.now(timezone.utc).isoformat(),
                                  "airports": sorted(airports.values(), key=lambda a: a["code"])},
                                 ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"Airport picker catalog: {len(airports)} airports")


if __name__ == "__main__":
    main()
