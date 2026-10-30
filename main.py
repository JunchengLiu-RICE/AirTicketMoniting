"""AirTicketMoniting: run trip.yaml once or on the local schedule.

    python main.py --once
    python main.py
    python main.py -c .runtime/config.yaml --once
"""
import argparse
import sys
from pathlib import Path

import yaml

from core.logger import setup_logger
from core.config import build_config
from core.models import Route
from core.platforms import DEFAULT_PLATFORMS, validate_platforms
from core.storage import PriceStorage
from core.alerter import Alerter
from core.notifier import build_notifier
from crawlers import REGISTRY


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        settings = yaml.safe_load(f)
    return settings if isinstance(settings, dict) and "routes" in settings else build_config(settings)


def build_routes(cfg: dict):
    routes = []
    for r in cfg.get("routes", []):
        routes.append(Route(
            from_code=r["from"],
            from_name=r.get("from_name", r["from"]),
            to_code=r["to"],
            to_name=r.get("to_name", r["to"]),
            dates=list(r.get("dates", [])),
            alert_threshold=float(r.get("alert_threshold", 0) or 0),
            alert_on_drop=bool(r.get("alert_on_drop", False)),
            alert_group=r.get("alert_group", ""),
        ))
    return routes


def make_job(cfg: dict, logger, storage: PriceStorage, alerter: Alerter):
    crawler_cfg = cfg.get("crawler", {})
    platforms = validate_platforms(cfg.get("platforms", DEFAULT_PLATFORMS))
    routes = build_routes(cfg)

    crawlers = [REGISTRY[name](crawler_cfg, logger) for name in platforms]

    def job():
        logger.info("===== 开始一轮抓取 =====")
        grouped = {}
        batched = (len(crawlers) == 1 and hasattr(crawlers[0], "fetch_routes")
                   and int(crawler_cfg.get("google_flights", {}).get("origin_batch_size", 1)) > 1)
        by_route = {}
        if batched:
            prices = crawlers[0].fetch_routes(routes)
            storage.save_many(prices)
            for price in prices:
                by_route.setdefault((price.from_city, price.to_city), []).append(price)
        for route in routes:
            all_prices = [p for p in by_route.get((route.from_code, route.to_code), [])
                          if p.depart_date in route.dates]
            if not batched:
                for c in crawlers:
                    prices = c.safe_fetch(route.from_code, route.to_code, route.dates)
                    all_prices.extend(prices)
                storage.save_many(all_prices)
            if route.alert_group:
                best_by_date = grouped.setdefault(route.alert_group, {})
                for price in all_prices:
                    current = best_by_date.get(price.depart_date)
                    if current is None or price.price < current[1].price:
                        best_by_date[price.depart_date] = (route, price)
            else:
                alerter.check_and_alert(route, all_prices)
        for best_by_date in grouped.values():
            for route, price in best_by_date.values():
                alerter.check_and_alert(route, [price])
        logger.info("===== 本轮抓取结束 =====")

    return job


def main():
    ap = argparse.ArgumentParser(description="机票价格监控工具")
    ap.add_argument("-c", "--config", default="trip.yaml", help="配置文件路径")
    ap.add_argument("--once", action="store_true", help="只运行一次后退出")
    ap.add_argument("--login", metavar="PLATFORM",
                    help="登录指定平台(ctrip/fliggy)，弹出可见浏览器，登录完成后回车保存会话")
    args = ap.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"配置文件不存在: {cfg_path}", file=sys.stderr)
        sys.exit(1)
    cfg = load_config(str(cfg_path))

    out = cfg.get("output", {})
    logger = setup_logger(out.get("log_path", "logs/monitor.log"))
    storage = PriceStorage(out.get("db_path", "data/prices.db"))
    notifier = build_notifier(cfg.get("notifier"), logger)
    if notifier:
        logger.info("已启用推送: %s", type(notifier).__name__)
    notify_cfg = cfg.get("notifier") or {}
    alerter = Alerter(
        logger,
        notifier=notifier,
        storage=storage,
        push_drop_min=float(notify_cfg.get("push_drop_min", 30)),
        push_rise_min=float(notify_cfg.get("push_rise_min", 50)),
    )

    # ---- 登录模式 ----
    if args.login:
        name = args.login.strip().lower()
        cls = REGISTRY.get(name)
        if cls is None:
            print(f"未知平台: {name}，可选: {list(REGISTRY)}", file=sys.stderr)
            sys.exit(2)
        crawler = cls(cfg.get("crawler", {}), logger)
        crawler.interactive_login()
        return

    job = make_job(cfg, logger, storage, alerter)

    if args.once:
        job()
        return

    sched_cfg = cfg.get("schedule", {})
    interval = int(sched_cfg.get("interval_minutes", 30))
    jitter = int(sched_cfg.get("jitter_minutes", 0))
    run_on_start = bool(sched_cfg.get("run_on_start", True))
    logger.info("启动定时调度，每 %d 分钟一次 (±%d 分钟随机扰动, 首次立即运行=%s)",
                interval, jitter, run_on_start)
    try:
        from core.scheduler import run_scheduler

        run_scheduler(job, interval, run_on_start, jitter_minutes=jitter)
    except (KeyboardInterrupt, SystemExit):
        logger.info("收到退出信号，停止监控")


if __name__ == "__main__":
    main()
