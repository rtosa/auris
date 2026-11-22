"""Public headline observations from Proskauer's private-credit index releases."""

from __future__ import annotations

import asyncio
import calendar
import re
from datetime import date
from html.parser import HTMLParser

import httpx

from app.collectors.fred import CollectorError, Observation
from app.collectors.definitions import IndicatorDefinition

LIST_URL = "https://www.proskauer.com/browse-insights?primary_type=8"
BASE_URL = "https://www.proskauer.com"


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(str(href))

    def handle_data(self, data: str) -> None:
        self.text.append(data)


class ProskauerCollector:
    def __init__(self, client: httpx.AsyncClient | None = None, timeout_seconds: float = 25.0) -> None:
        self.client = client
        self.timeout = timeout_seconds

    async def fetch_series(self, definition: IndicatorDefinition, observation_start: date) -> list[Observation]:
        own = self.client is None
        client = self.client or httpx.AsyncClient(timeout=self.timeout, follow_redirects=True,
            headers={"User-Agent": "Auris AI Bubble Risk Monitor"})
        try:
            listing = await client.get(LIST_URL)
            listing.raise_for_status()
            parser = _PageParser()
            parser.feed(listing.text)
            urls = []
            for href in parser.links:
                if "private-credit-default-index" not in href.lower():
                    continue
                url = href if href.startswith("http") else f"{BASE_URL}{href if href.startswith('/') else '/' + href}"
                if url not in urls:
                    urls.append(url)
            output: dict[date, Observation] = {}
            for url in urls[:16]:
                response = await client.get(url)
                response.raise_for_status()
                article = _PageParser()
                article.feed(response.text)
                content = " ".join(article.text)
                title_match = re.search(r"Private Credit Default Index.{0,120}?([0-9]+(?:\.[0-9]+)?)%.*?\b(Q[1-4])\s+(20\d{2})", content, re.I)
                if not title_match:
                    title_match = re.search(r"([0-9]+(?:\.[0-9]+)?)%\s+(?:for|in)\s+(Q[1-4])\s+(20\d{2})", content, re.I)
                if not title_match:
                    continue
                value, quarter, year = title_match.groups()
                month = int(quarter[1]) * 3
                end_date = date(int(year), month, calendar.monthrange(int(year), month)[1])
                if end_date < observation_start:
                    continue
                output[end_date] = Observation(
                    indicator_id=definition.id,
                    observation_date=end_date,
                    value=float(value),
                    raw_payload={"quarter": quarter, "year": int(year), "article_url": url,
                                 "source": "Proskauer Private Credit Default Index headline rate"},
                    source_reference=url,
                )
                await asyncio.sleep(0.1)
            if not output:
                raise CollectorError("Proskauer listing did not yield any parseable quarterly index releases")
            return [output[key] for key in sorted(output)]
        except httpx.HTTPError as exc:
            raise CollectorError(f"Proskauer request failed ({type(exc).__name__})") from exc
        finally:
            if own:
                await client.aclose()
