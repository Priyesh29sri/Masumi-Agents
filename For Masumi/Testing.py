"""
Intelligent Web Crawling System
Finds company emails and contact information based on a search query.
Uses Crawl4AI + regex (primary) + OpenRouter LLM (fallback).
"""

import asyncio
import json
import re
import time
import sys
from urllib.parse import urlencode, urljoin, urlparse

import aiohttp
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

if sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

OPENROUTER_API_KEY = "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"  # Replace with your key
OPENROUTER_MODEL   = "mistralai/mistral-7b-instruct"  # Free/cheap model on OpenRouter

MAX_SITES          = 8      # Max company sites from search results
MAX_LINKS_PER_SITE = 5      # Max sub-links to crawl per site
REQUEST_DELAY      = 1.0    # Seconds between requests
CONTACT_KEYWORDS   = ["contact", "about", "email", "company", "team", "careers",
                       "hire", "support", "reach", "connect", "business"]
GENERIC_EMAIL_DOMAINS = {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com",
                          "protonmail.com", "icloud.com", "live.com", "aol.com"}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

EMAIL_REGEX = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
)

# ─────────────────────────────────────────────
# UTILITIES
# ─────────────────────────────────────────────

def log(msg: str) -> None:
    print(f"  [DEBUG] {msg}")


def build_search_url(query: str) -> str:
    params = urlencode({"q": query})
    return f"https://duckduckgo.com/html/?{params}"


def get_domain(url: str) -> str:
    try:
        parsed = urlparse(url)
        return parsed.netloc.lower().replace("www.", "")
    except Exception:
        return ""


def company_name_from_domain(domain: str) -> str:
    """Best-effort company name from domain (e.g. 'openai.com' → 'Openai')."""
    name = domain.split(".")[0] if domain else "Unknown"
    return name.replace("-", " ").replace("_", " ").title()


def is_valid_external_url(url: str, base_domain: str = "") -> bool:
    """Returns True for crawlable external http/https URLs."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        netloc = parsed.netloc.lower()
        # Filter junk / search-internal links
        junk = ["duckduckgo.com", "google.com", "bing.com", "facebook.com",
                "twitter.com", "linkedin.com", "youtube.com", "instagram.com",
                "t.co", "bit.ly", "amazon.com", "wikipedia.org"]
        if any(j in netloc for j in junk):
            return False
        if base_domain and netloc == base_domain:
            return False
        return bool(netloc)
    except Exception:
        return False


def score_link(url: str) -> int:
    """Score a URL higher if it likely contains contact/company information."""
    url_lower = url.lower()
    return sum(1 for kw in CONTACT_KEYWORDS if kw in url_lower)


def extract_emails(text: str) -> list[str]:
    """Extract all emails from raw text using regex."""
    found = EMAIL_REGEX.findall(text)
    # Deduplicate while preserving order
    seen = set()
    unique = []
    for e in found:
        e_clean = e.strip().lower()
        if e_clean not in seen:
            seen.add(e_clean)
            unique.append(e_clean)
    return unique


def classify_emails(emails: list[str], site_domain: str) -> dict:
    """Separate company emails from generic ones; pick primary."""
    company_emails = []
    generic_emails  = []

    for email in emails:
        domain = email.split("@")[-1] if "@" in email else ""
        if domain in GENERIC_EMAIL_DOMAINS:
            generic_emails.append(email)
        else:
            company_emails.append(email)

    all_emails  = company_emails + generic_emails
    primary     = company_emails[0] if company_emails else (generic_emails[0] if generic_emails else None)
    confidence  = "high" if company_emails else ("medium" if generic_emails else "low")

    return {
        "all_emails": all_emails,
        "primary_email": primary,
        "confidence": confidence,
    }


# ─────────────────────────────────────────────
# SEARCH RESULT PARSING
# ─────────────────────────────────────────────

def parse_search_results(html: str) -> list[str]:
    """
    Extract result URLs from DuckDuckGo HTML search page.
    DDG wraps results in <a class="result__a" href="..."> or via redirect.
    """
    urls = []

    # DDG HTML results: href="/l/?uddg=<encoded-url>&..."
    uddg_pattern = re.compile(r'href="[^"]*uddg=([^&"]+)', re.IGNORECASE)
    for match in uddg_pattern.finditer(html):
        from urllib.parse import unquote
        url = unquote(match.group(1))
        if is_valid_external_url(url):
            urls.append(url)

    # Fallback: plain href https links
    if not urls:
        plain_pattern = re.compile(r'href="(https?://[^"]+)"', re.IGNORECASE)
        for match in plain_pattern.finditer(html):
            url = match.group(1)
            if is_valid_external_url(url):
                urls.append(url)

    # Deduplicate by domain
    seen_domains: set[str] = set()
    unique: list[str] = []
    for url in urls:
        d = get_domain(url)
        if d and d not in seen_domains:
            seen_domains.add(d)
            unique.append(url)

    return unique[:MAX_SITES]


# ─────────────────────────────────────────────
# LINK EXTRACTION FROM PAGE
# ─────────────────────────────────────────────

def extract_links_from_html(html: str, base_url: str) -> list[str]:
    """Extract all href links from HTML and resolve relative URLs."""
    href_pattern = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
    links = []
    base_domain = get_domain(base_url)
    parsed_base = urlparse(base_url)

    for match in href_pattern.finditer(html):
        href = match.group(1).strip()
        if not href or href.startswith("#") or href.startswith("mailto:"):
            continue
        # Resolve relative →absolute
        if href.startswith("http"):
            full_url = href
        elif href.startswith("//"):
            full_url = f"{parsed_base.scheme}:{href}"
        else:
            full_url = urljoin(base_url, href)
        # Only same-domain sub-pages for depth-1 crawl
        if get_domain(full_url) == base_domain:
            links.append(full_url)

    # Deduplicate
    return list(dict.fromkeys(links))


def best_first_links(links: list[str], max_links: int = MAX_LINKS_PER_SITE) -> list[str]:
    """Sort links by contact-keyword score, return top N."""
    scored = sorted(links, key=score_link, reverse=True)
    if scored:
        log(f"Top scored sub-links:")
        for lnk in scored[:max_links]:
            log(f"  score={score_link(lnk):2d}  {lnk}")
    return scored[:max_links]


# ─────────────────────────────────────────────
# LLM FALLBACK (OpenRouter)
# ─────────────────────────────────────────────

async def llm_extract_contact(text: str, site_url: str) -> dict | None:
    """
    Call OpenRouter LLM to extract structured contact info.
    Used only when regex finds no emails.
    """
    if not OPENROUTER_API_KEY or OPENROUTER_API_KEY == "your-openrouter-api-key-here" or OPENROUTER_API_KEY == "":
        log("No OpenRouter API key configured — skipping LLM fallback.")
        return None

    # Truncate text to avoid token limits
    snippet = text[:4000].strip()
    if not snippet:
        return None

    prompt = f"""You are a web data extractor. From the webpage content below, extract:
- company_name: the name of the company
- service: what the company does (1-2 sentences)
- emails: list of email addresses found
- contact_info: any phone numbers or contact details

Return ONLY valid JSON with keys: company_name, service, emails, contact_info.
If something is not found, use null or [].

Website: {site_url}
Content:
{snippet}"""

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://github.com/your-crawler",
                    "X-Title": "WebCrawlerBot",
                },
                json={
                    "model": OPENROUTER_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": 512,
                },
                timeout=aiohttp.ClientTimeout(total=20)
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                raw = data["choices"][0]["message"]["content"]
                
                # Try to parse JSON from LLM response
                json_match = re.search(r"\{.*\}", raw, re.DOTALL)
                if json_match:
                    return json.loads(json_match.group())
    except Exception as e:
        log(f"LLM fallback error: {e}")

    return None


# ─────────────────────────────────────────────
# CORE CRAWLER
# ─────────────────────────────────────────────

async def crawl_page(crawler: AsyncWebCrawler, url: str) -> tuple[str, str]:
    """
    Crawl a single URL. Returns (markdown_text, raw_html).
    """
    run_cfg = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        page_timeout=20000,
        wait_until="domcontentloaded",
    )
    try:
        result = await crawler.arun(url=url, config=run_cfg)
        if result.success:
            return result.markdown or "", result.html or ""
        else:
            log(f"Failed: {url} → {result.error_message}")
    except Exception as e:
        log(f"Exception crawling {url}: {e}")
    return "", ""


async def process_site(crawler: AsyncWebCrawler, site_url: str) -> dict:
    """
    Full pipeline for one company site:
    1. Crawl homepage
    2. Extract + score sub-links
    3. Crawl top sub-links (depth=1)
    4. Combine content
    5. Regex email extraction
    6. LLM fallback if needed
    """
    domain      = get_domain(site_url)
    company     = company_name_from_domain(domain)

    log(f"━━━ Processing: {site_url} ({company}) ━━━")

    # Step 1: Homepage
    homepage_md, homepage_html = await crawl_page(crawler, site_url)
    await asyncio.sleep(REQUEST_DELAY)

    combined_text = homepage_md

    # Step 2: Extract & score sub-links
    all_links = extract_links_from_html(homepage_html, site_url)
    log(f"Found {len(all_links)} internal links on homepage")

    top_links = best_first_links(all_links)

    # Step 3: Crawl top sub-pages (depth = 1)
    for sub_url in top_links:
        log(f"Crawling sub-page: {sub_url}")
        sub_md, _ = await crawl_page(crawler, sub_url)
        if sub_md:
            combined_text += "\n" + sub_md
        await asyncio.sleep(REQUEST_DELAY)

    # Step 4: Regex email extraction
    emails = extract_emails(combined_text)
    log(f"Emails found via regex: {emails}")

    # Step 5: Classify emails
    classified = classify_emails(emails, domain)

    # Step 6: LLM fallback
    llm_data = None
    if not classified["all_emails"]:
        log("No emails found via regex → attempting LLM fallback …")
        llm_data = await llm_extract_contact(combined_text, site_url)
        if llm_data:
            log(f"LLM response: {llm_data}")
            llm_emails = llm_data.get("emails") or []
            if isinstance(llm_emails, list):
                classified = classify_emails(
                    [e for e in llm_emails if isinstance(e, str)], domain
                )
            # Override company name if LLM found one
            if llm_data.get("company_name"):
                company = llm_data["company_name"]

    result = {
        "company":       company,
        "website":       site_url,
        "emails":        classified["all_emails"],
        "primary_email": classified["primary_email"],
        "confidence":    classified["confidence"],
    }

    if llm_data:
        result["llm_service"]      = llm_data.get("service")
        result["llm_contact_info"] = llm_data.get("contact_info")

    return result


# ─────────────────────────────────────────────
# SEARCH CRAWL
# ─────────────────────────────────────────────

async def get_search_sites(crawler: AsyncWebCrawler, query: str) -> list[str]:
    """Crawl DuckDuckGo and return a list of company site URLs."""
    search_url = build_search_url(query)
    log(f"Search URL: {search_url}")

    _, search_html = await crawl_page(crawler, search_url)
    await asyncio.sleep(REQUEST_DELAY)

    if not search_html:
        print("[ERROR] Could not fetch search results. Try again later.")
        return []

    sites = parse_search_results(search_html)
    log(f"Extracted {len(sites)} sites from search: {sites}")
    return sites


# ─────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────

async def main() -> None:
    query = input("Enter search query: ").strip()
    if not query:
        print("[ERROR] Query cannot be empty.")
        return

    print(f"\n🔍 Searching for: {query}\n")

    browser_cfg = BrowserConfig(
        headless=True,
        headers=HEADERS,
    )

    async with AsyncWebCrawler(config=browser_cfg) as crawler:

        # 1. Get target sites from search
        sites = await get_search_sites(crawler, query)

        if not sites:
            print("[ERROR] No sites found. Try a different query.")
            return

        print(f"\n📋 Found {len(sites)} sites to crawl:\n")
        for i, s in enumerate(sites, 1):
            print(f"  {i}. {s}")
        print()

        # 2. Process each site
        results = []
        for site_url in sites:
            try:
                result = await process_site(crawler, site_url)
                results.append(result)
            except Exception as e:
                log(f"Unhandled error for {site_url}: {e}")
                results.append({
                    "company":       company_name_from_domain(get_domain(site_url)),
                    "website":       site_url,
                    "emails":        [],
                    "primary_email": None,
                    "confidence":    "low",
                    "error":         str(e),
                })

    # 3. Print final results
    print("\n" + "═" * 60)
    print("  RESULTS")
    print("═" * 60 + "\n")

    # Sort: high → medium → low confidence
    confidence_order = {"high": 0, "medium": 1, "low": 2}
    results.sort(key=lambda r: confidence_order.get(r.get("confidence", "low"), 2))

    for r in results:
        print(json.dumps(r, indent=2, ensure_ascii=False))
        print()

    # Summary
    found_count = sum(1 for r in results if r.get("emails"))
    print(f"📊 Summary: {found_count}/{len(results)} sites had emails.")


if __name__ == "__main__":
    asyncio.run(main())