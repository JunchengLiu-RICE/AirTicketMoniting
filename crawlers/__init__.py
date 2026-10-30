"""爬虫模块"""
from .base import BaseCrawler
from .ctrip import CtripCrawler
from .fliggy import FliggyCrawler
from .google_flights import GoogleFlightsCrawler

REGISTRY = {
    "ctrip": CtripCrawler,
    "fliggy": FliggyCrawler,
    "google_flights": GoogleFlightsCrawler,
}

__all__ = ["BaseCrawler", "CtripCrawler", "FliggyCrawler", "GoogleFlightsCrawler", "REGISTRY"]
