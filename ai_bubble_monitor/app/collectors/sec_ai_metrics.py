"""Conservative extraction of issuer-reported AI and data-center revenue evidence."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any


class _Text(HTMLParser):
    BLOCKS = {"br", "p", "div", "li", "tr", "td", "th", "h1", "h2", "h3"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, _attrs) -> None:
        if tag.lower() in {"script", "style", "ix:header"}:
            self.hidden += 1
        if tag.lower() in self.BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "ix:header"} and self.hidden:
            self.hidden -= 1
        if tag.lower() in self.BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


_AMOUNT = re.compile(r"\$\s*(\d[\d,]*(?:\.\d+)?)\s*(trillion|tn|t|billion|bn|b|million|mn|m)\b", re.I)
_SCALES = {"trillion": 1e12, "tn": 1e12, "t": 1e12, "billion": 1e9, "bn": 1e9, "b": 1e9,
           "million": 1e6, "mn": 1e6, "m": 1e6}


def extract_ai_revenue_evidence(document: str, company: dict[str, str], filing: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract explicit AI business or Data Center revenue amounts, preserving quotes.

    This produces evidence only. It deliberately does not convert broad cloud revenue
    or CAPEX into AI revenue and does not feed the scenario score.
    """
    parser = _Text()
    parser.feed(document)
    parser.close()
    visible = re.sub(r"[\t\xa0 ]+", " ", "".join(parser.parts))
    sentences = re.split(r"(?<=[.!?;])\s+|\n+", visible)
    records: list[dict[str, Any]] = []
    for sentence in sentences:
        lower = sentence.lower()
        ai_business = bool(re.search(r"\b(?:ai business|artificial intelligence business|ai revenue|generative ai revenue)\b", lower))
        compute_segment = bool(re.search(r"\bdata[ -]center revenue\b", lower))
        annualized = bool(re.search(r"\b(?:annual(?:ized)? revenue run rate|run rate)\b", lower))
        if not (ai_business or compute_segment) or len(sentence) > 700:
            continue
        amounts = list(_AMOUNT.finditer(sentence))
        if len(amounts) != 1:
            continue
        match = amounts[0]
        value = float(match.group(1).replace(",", "")) * _SCALES[match.group(2).lower()]
        category = "ai_monetization" if ai_business else "ai_compute_revenue"
        period = str(filing.get("report_date") or filing.get("filed") or "")
        title = f"{company['ticker']} reported {'AI business' if ai_business else 'Data Center'} revenue"
        if annualized:
            title += " run rate"
        records.append({
            "title": title,
            "summary": sentence,
            "company_id": company["id"],
            "category": category,
            "published_at": filing["filed"],
            "source_url": filing["source_url"],
            "confidence": 0.85,
            "guidance_period": period,
            "guidance_lower": value,
            "guidance_upper": value,
            "guidance_unit": "USD_annualized" if annualized else "USD_reported_period",
            "source_quote": sentence,
            "source_accession": filing["accession"],
        })
    return records
