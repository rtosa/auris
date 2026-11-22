from datetime import date
import asyncio

import httpx

from app.collectors.definitions import IndicatorDefinition
from app.collectors.proskauer import ProskauerCollector


def test_collects_public_quarterly_headline_releases():
    article = "<html><body><h1>Proskauer's Private Credit Default Index Reveals Rate of 2.41% for Q3 2026</h1></body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        if "browse-insights" in str(request.url):
            return httpx.Response(200, text='<a href="/release/private-credit-default-index-test">release</a>')
        return httpx.Response(200, text=article)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    definition = IndicatorDefinition(
        id="private_credit_default_rate", name="Private credit default rate", category="credit",
        unit="percent", frequency="quarterly", direction="higher_is_worse",
        normalization_method="rolling_percentile", source="proskauer", source_id="headline_default_rate",
        staleness_days=150, scenarios={}, minimum_observations=4,
    )
    async def collect():
        try:
            return await ProskauerCollector(client).fetch_series(definition, date(2025, 1, 1))
        finally:
            await client.aclose()
    observations = asyncio.run(collect())

    assert len(observations) == 1
    assert observations[0].observation_date == date(2026, 9, 30)
    assert observations[0].value == 2.41
    assert observations[0].source_reference.endswith("private-credit-default-index-test")
