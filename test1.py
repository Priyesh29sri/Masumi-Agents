import asyncio
import re
from urllib.parse import urlparse

from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
from crawl4ai.deep_crawling import (
    BestFirstCrawlingStrategy,
    DomainFilter,
    FilterChain,
    KeywordRelevanceScorer,
    URLPatternFilter,
)

EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.(?:com|org|net|edu|io|dev|ai|in)",
    re.I,
)


def extract_emails(text: str) -> set[str]:
    if not text:
        return set()
    return set(match.group(0) for match in EMAIL_RE.finditer(text))


def build_strategy(start_url: str) -> BestFirstCrawlingStrategy:
    host = urlparse(start_url).netloc
    filters = FilterChain(
        [
            DomainFilter(allowed_domains=[host]),
            URLPatternFilter(patterns=["*.jpg", "*.jpeg", "*.png", "*.gif", "*.pdf", "*.zip"], reverse=True),
        ]
    )
    scorer = KeywordRelevanceScorer(
        keywords=["contact", "email", "about", "team", "people"],
        weight=1.0,
    )
    return BestFirstCrawlingStrategy(
        max_depth=2,
        filter_chain=filters,
        url_scorer=scorer,
        include_external=False,
        max_pages=25,
    )


async def run_crawl(start_url: str) -> set[str]:
    browser_config = BrowserConfig(headless=True, verbose=False)
    run_config = CrawlerRunConfig(
        deep_crawl_strategy=build_strategy(start_url),
        cache_mode=CacheMode.BYPASS,
        stream=False,
        verbose=False,
    )

    found_emails: set[str] = set()
    async with AsyncWebCrawler(config=browser_config) as crawler:
        results = await crawler.arun(url=start_url, config=run_config)
        for result in results:
            text = "\n".join(
                [
                    str(getattr(result, "markdown", "") or ""),
                    getattr(result, "cleaned_html", "") or "",
                    getattr(result, "html", "") or "",
                ]
            )
            found_emails |= extract_emails(text)

    return found_emails


async def main() -> None:
    target = "https://example.com"
    emails = await run_crawl(target)
    print(f"Target: {target}")
    print(f"Emails found: {len(emails)}")
    for email in sorted(emails):
        print("-", email)


if __name__ == "__main__":
    asyncio.run(main())