"""低价提醒 + 微信推送（带去抖）"""
import logging
import json
from typing import List, Optional

from .models import FlightPrice, Route


class Alerter:
    def __init__(self, logger: logging.Logger, notifier=None, storage=None,
                 push_drop_min: float = 30, push_rise_min: float = 50):
        """
        notifier:        推送器（None 表示不推送）
        storage:         用于读写 alert_state（去抖）
        push_drop_min:   触发"再次推送"的最小降幅(¥)
        push_rise_min:   触发"再次推送"的最小涨幅(¥)
        """
        self.logger = logger
        self.notifier = notifier
        self.storage = storage
        self.push_drop_min = push_drop_min
        self.push_rise_min = push_rise_min

    def check_and_alert(self, route: Route, prices: List[FlightPrice]):
        if not prices:
            return
        # 控制台/日志：三平台对比 + 多日期对比
        self._compare_platforms(route, prices)
        self._compare_dates(route, prices)

        # 低价阈值提醒
        if route.alert_on_drop or (route.alert_threshold and route.alert_threshold > 0):
            self._handle_threshold(route, prices)

    # ---------- 控制台对比 ----------
    def _compare_platforms(self, route: Route, prices: List[FlightPrice]):
        by_date = {}
        for p in prices:
            by_date.setdefault(p.depart_date, []).append(p)
        for date, lst in sorted(by_date.items()):
            lst = sorted(lst, key=lambda x: x.price)
            best = lst[0]
            line = " | ".join(f"{p.platform}: ¥{p.price:.0f}" for p in lst)
            self.logger.info(
                "[对比] %s->%s %s 最低=%s ¥%.0f  (%s)",
                route.from_name, route.to_name, date,
                best.platform, best.price, line,
            )

    def _compare_dates(self, route: Route, prices: List[FlightPrice]):
        if len(route.dates) <= 1 or not prices:
            return
        cheapest_p = min(prices, key=lambda p: p.price)
        self.logger.info(
            "[多日期] %s->%s 最便宜日期=%s ¥%.0f (%s)",
            route.from_name, route.to_name, cheapest_p.depart_date,
            cheapest_p.price, cheapest_p.platform,
        )

    # ---------- 阈值提醒 + 推送 ----------
    def _handle_threshold(self, route: Route, prices: List[FlightPrice]):
        # 按日期取最低
        best_by_date = {}
        for p in prices:
            cur = best_by_date.get(p.depart_date)
            if cur is None or p.price < cur.price:
                best_by_date[p.depart_date] = p

        for date, bp in sorted(best_by_date.items()):
            route_key = f"{route.alert_group or (route.from_code + '-' + route.to_code)}-{date}"

            if not route.alert_on_drop and bp.price > route.alert_threshold:
                # 涨出阈值：清除去抖状态，下次跌破按"首次"重新推
                if self.storage:
                    self.storage.clear_alert_state(route_key)
                continue

            last = self.storage.get_alert_state(route_key) if self.storage else None
            if route.alert_on_drop:
                should_push = last is None or bp.price < last
                reason = "首次或降价" if should_push else "未低于上次提醒价格"
            else:
                should_push, reason = self._should_push(bp.price, last)

            # 控制台始终打印当前命中低价的状态
            self.logger.warning(
                "[低价] %s->%s %s ¥%.0f (阈值¥%.0f, 上次推送¥%s) -> %s",
                route.from_name, route.to_name, date,
                bp.price, route.alert_threshold,
                f"{last:.0f}" if last else "-",
                "推送" if should_push else f"跳过({reason})",
            )

            if should_push and self.notifier:
                ok = self._push(route, date, bp, last)
                if ok and self.storage:
                    self.storage.set_alert_state(route_key, bp.price)

    def _should_push(self, cur: float, last: Optional[float]):
        if last is None:
            return True, "首次"
        diff = cur - last
        if diff <= -self.push_drop_min:
            return True, f"降¥{-diff:.0f}"
        if diff >= self.push_rise_min:
            return True, f"涨¥{diff:.0f}"
        return False, f"波动¥{diff:+.0f}(<阈值)"

    def _push(self, route: Route, date: str, p: FlightPrice,
              last: Optional[float]) -> bool:
        diff_txt = ""
        emoji = "✈️"
        if last is not None:
            diff = p.price - last
            if diff <= 0:
                emoji = "📉"
                diff_txt = f"（较上次降 ¥{-diff:.0f}）"
            else:
                emoji = "📈"
                diff_txt = f"（较上次涨 ¥{diff:.0f}）"

        title = (f"{emoji} {route.from_name}→{route.to_name} {date} "
                 f"¥{p.price:.0f}{diff_txt}")

        # markdown 详细
        extra = json.loads(p.extra) if p.extra else {}
        view_url = extra.get("url") or self._build_view_url(route, date, p.platform)
        desp = (
            f"## 机票低价提醒\n\n"
            f"- **航线**：{route.from_name}（{route.from_code}） → "
            f"{route.to_name}（{route.to_code}）\n"
            f"- **日期**：{date}\n"
            f"- **当前最低价**：**¥{p.price:.0f}**\n"
            + ("" if route.alert_on_drop else f"- **设定阈值**：¥{route.alert_threshold:.0f}\n")
            +
            f"- **来源**：{p.platform}\n"
            f"- **抓取时间**：{p.fetched_at}\n"
        )
        if last is not None:
            desp += f"- **上次推送价**：¥{last:.0f}\n"
        if route.alert_on_drop:
            desp += "- **提醒规则**：首次找到合格航班及之后降价\n"
        verified = extra.get("filters_verified", {})
        if verified.get("airlines"):
            desp += f"- **航班号航司**：{' / '.join(verified['airlines'])}\n"
        if verified.get("depart_after"):
            desp += f"- **起飞要求**：严格晚于出发机场当地 {verified['depart_after']}\n"
        if verified.get("max_stops") is not None:
            desp += f"- **中转限制**：最多 {verified['max_stops']} 次\n"
        cabins = {1: "经济舱", 2: "超经舱", 3: "公务舱", 4: "头等舱"}
        for segment in extra.get("segments", []):
            desp += (f"- {segment['from']}→{segment['to']} {segment['flight_no']} "
                     f"{segment['depart_date']} {segment['depart_time']} "
                     f"{cabins.get(segment['cabin'], '舱位待确认')}\n")
        if extra.get("hidden_city"):
            desp += (f"\n**甩尾候选**：在 {extra['actual_destination']} 下机，票面终点为 "
                     f"{extra['ticket_destination']}；票价为整张票价格。托运行李可能直挂终点，"
                     "未乘后续航段可能影响剩余行程，购票前需确认票规。\n")
        desp += f"\n[👉 在 {p.platform} 查看详情]({view_url})\n"
        return self.notifier.send(title, desp)

    def _build_view_url(self, route: Route, date: str, platform: str) -> str:
        if platform == "google_flights":
            from .google_flights import build_google_flights_url
            return build_google_flights_url(route.from_code, route.to_code, date)
        if platform == "ctrip":
            return (
                "https://m.ctrip.com/html5/flight/taro/first?from=inner"
                "&tripType=ONE_WAY"
                f"&dcity={route.from_code}&acity={route.to_code}&ddate={date}"
            )
        # fliggy 默认走飞猪 H5
        return (
            "https://outfliggys.m.taobao.com/app/trip/rx-flight-eco/pages/listing"
            f"?depCityCode={route.from_code}&arrCityCode={route.to_code}"
            f"&leaveDate={date}&adultPassengerNum=1&searchType=1"
        )
