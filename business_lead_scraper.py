import argparse
import asyncio
import re
import sys
from urllib.parse import urlparse

from ddgs import DDGS
from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
from crawl4ai.deep_crawling import (
    BestFirstCrawlingStrategy,
    DomainFilter,
    FilterChain,
    KeywordRelevanceScorer,
    URLPatternFilter,
)

# Broad regex for catching generic business emails
EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
    re.I,
)

ARTIFACT_RE = re.compile(r"^(u003e|gt|lt|x22)+", re.I)

# List of typical business/role-based local parts we WANT to keep
BUSINESS_PREFIXES = {
    "info", "support", "help", "hello", "contact", "admin", "sales", "careers", "career", "jobs",
    "privacy", "legal", "security", "press", "media", "team", "billing", "finance", "complaints",
    "compliance", "hr", "marketing", "partnerships", "partners", "investor", "investors", 
    "feedback", "office", "enquiry", "inquiries", "query", "queries", "hello", "hi", "mail", "general"
}

def clean_email(email: str) -> str:
    """Strip obvious artifacts and trailing garbage."""
    clean = ARTIFACT_RE.sub("", email.strip().lower())
    # remove trailing punctuations that might sneak into regex
    return clean.rstrip(".,;")

def is_business_email(email: str) -> bool:
    """
    Returns True if it leans toward being a business/department email
    rather than a 'first.last@' personal email.
    """
    parts = email.split("@", 1)
    if len(parts) != 2:
        return False
        
    local, domain = parts
    
    # E.g. exclude standard image attachments or auto-removals.
    if domain in ("sentry.io", "example.com"):
        return False
    if any(ext in email for ext in [".png", ".jpg", ".jpeg", ".gif"]):
        return False

    # Standard known role accounts => absolute keep
    if local in BUSINESS_PREFIXES:
        return True

    # Heavily personal patterns => drop
    if "." in local or "_" in local:
        return False
    if any(token in local for token in ("first", "last", "name", "test", "demo")):
        return False

    # Short generic, not overtly personal (e.g. 'hr', 'it', 'pr') -> keep
    return True


def get_company_url(company_name: str) -> str:
    """Find the likely homepage for the company using DuckDuckGo search."""
    print(f"Searching web for: {company_name}")
    try:
        results = DDGS().text(company_name, max_results=5)
        for r in results:
            url = r.get("href")
            if not url:
                continue
                
            host = urlparse(url).netloc.lower()
            
            # Skip massive generic directory/proxy domains we don't want to crawl into
            skip_domains = [
                "linkedin.com", "justdial.com", "zaubacorp.com", "glassdoor.com", 
                "indiamart.com", "tofler.in", "ambitionbox.com", "quickcompany.in",
                "zoominfo.com", "crunchbase.com", "bloomberg.com", "facebook.com",
                "twitter.com", "x.com", "instagram.com"
            ]
            if any(d in host for d in skip_domains):
                continue
                
            return url
    except Exception as e:
        print(f"Search failed: {e}")
        
    return None


def build_strategy(start_url: str) -> BestFirstCrawlingStrategy:
    """Scorer strategy to hunt contact pages inside the domain."""
    host = urlparse(start_url).netloc
    filters = FilterChain([
        DomainFilter(allowed_domains=[host]),
        URLPatternFilter(patterns=["*.jpg", "*.jpeg", "*.png", "*.gif", "*.pdf", "*.zip"], reverse=True),
    ])
    scorer = KeywordRelevanceScorer(
        keywords=["contact", "about", "team", "support", "contact-us"],
        weight=1.0,
    )
    return BestFirstCrawlingStrategy(
        max_depth=2,
        filter_chain=filters,
        url_scorer=scorer,
        include_external=False,
        max_pages=20,  # increased pages heavily focused on 'contact'
    )


async def extract_business_emails(start_url: str) -> set[str]:
    """Crawl the target URL looking for business emails."""
    browser_config = BrowserConfig(headless=True, verbose=False)
    run_config = CrawlerRunConfig(
        deep_crawl_strategy=build_strategy(start_url),
        cache_mode=CacheMode.BYPASS,
        stream=False,
        verbose=False,
    )

    found_emails: set[str] = set()
    
    try:
        async with AsyncWebCrawler(config=browser_config) as crawler:
            results = await crawler.arun(url=start_url, config=run_config)
            
            for result in results:
                # Combine markdown/html representation of the page
                text_content = "\n".join([
                    str(getattr(result, "markdown", "") or ""),
                    getattr(result, "cleaned_html", "") or "",
                    getattr(result, "html", "") or "",
                ])
                
                # Match everything email-like
                for match in EMAIL_RE.finditer(text_content):
                    e = clean_email(match.group(0))
                    # Only keep department/role-based business addresses
                    if is_business_email(e):
                        found_emails.add(e)
    except Exception as ex:
        print(f"Crawl failed: {ex}")
        
    return found_emails


async def main():
    parser = argparse.ArgumentParser(description="Find business contact emails for a company.")
    parser.add_argument("company", type=str, help="Name of the company (e.g., 'nasiwak services pvt limited bengaluru')")
    args = parser.parse_args()

    company_name = args.company
    
    # 1. Map Company Name -> Website
    url = get_company_url(company_name)
    if not url:
        print(f"\nCould not confidently find a website for '{company_name}'.")
        return
        
    print(f"\nTarget Website Confirmed: {url}")
    print("Crawling internally for contact emails... (Takes 30-60 seconds)\n")
    
    # 2. Crawl & Extract
    emails = await extract_business_emails(url)
    
    print(f"=== BUSINESS EMAILS FOUND ({len(emails)}) ===")
    if not emails:
        print("No business emails could be found.")
    else:
        for e in sorted(emails):
            print(f"- {e}")

if __name__ == "__main__":
    asyncio.run(main())
