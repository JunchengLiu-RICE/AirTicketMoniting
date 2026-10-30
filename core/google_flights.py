"""Google Flights 精确查询链接（单程、1 位成人、经济舱、人民币）。"""
import base64
import re
from datetime import date
from urllib.parse import urlencode


def airport_codes(value):
    codes = [value] if isinstance(value, str) else list(value)
    codes = list(dict.fromkeys(code.strip().upper() for code in codes))
    if not 1 <= len(codes) <= 7 or not all(re.fullmatch(r"[A-Z]{3}", code) for code in codes):
        raise ValueError("Google Flights 每侧需为 1 至 7 个 IATA 三字码")
    return codes


def build_google_flights_url(from_city, to_city, depart_date: str,
                             cabin: str = "economy", connecting_airport="", *,
                             airlines=None, max_stops=None, earliest_departure_hour=None) -> str:
    origins, destinations = airport_codes(from_city), airport_codes(to_city)
    if date.fromisoformat(depart_date).isoformat() != depart_date:
        raise ValueError("Google Flights 日期必须为 YYYY-MM-DD")

    # Multi-airport legs exceed 127 bytes, so lengths use real Protobuf varints.
    def varint(value):
        result = bytearray()
        while value >= 128:
            result.append((value & 127) | 128)
            value >>= 7
        result.append(value)
        return bytes(result)

    def field(tag: int, value: bytes) -> bytes:
        return varint(tag) + varint(len(value)) + value

    leg = field(0x12, depart_date.encode("ascii"))
    for code in origins:
        leg += field(0x6A, field(0x12, code.encode("ascii")))
    for code in destinations:
        leg += field(0x72, field(0x12, code.encode("ascii")))
    if max_stops is not None:
        if isinstance(max_stops, bool) or not isinstance(max_stops, int) or max_stops < 0:
            raise ValueError("中转次数必须为非负整数")
        leg += varint(0x28) + varint(max_stops)
    for airline in airlines or []:
        if not re.fullmatch(r"[A-Z0-9]{2}", airline):
            raise ValueError("航司必须为 IATA 两位代码")
        leg += field(0x32, airline.encode("ascii"))
    if earliest_departure_hour is not None:
        if isinstance(earliest_departure_hour, bool) or not isinstance(earliest_departure_hour, int) or not 0 <= earliest_departure_hour <= 23:
            raise ValueError("最早出发小时必须为 0 至 23")
        leg += varint(0x40) + varint(earliest_departure_hour)
    if connecting_airport:
        for code in airport_codes(connecting_airport):
            leg += field(0x7A, code.encode("ascii"))
    # 成人乘客 (8)、经济舱 (9)、单程 (19)。
    cabins = {"economy": 1, "premium_economy": 2, "business": 3, "first": 4}
    if cabin not in cabins:
        raise ValueError("不支持的舱位")
    query = field(0x1A, leg) + b"\x42\x01\x01\x48" + bytes([cabins[cabin]]) + b"\x98\x01\x02"
    return "https://www.google.com/travel/flights/search?" + urlencode({
        "tfs": base64.urlsafe_b64encode(query).decode("ascii"),
        "hl": "en",
        "curr": "CNY",
        "tfu": "EgQIABABIgA",  # 展示全部航班
    })
