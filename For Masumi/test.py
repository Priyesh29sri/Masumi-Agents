import asyncio
import os
import requests
import time
import json
import re

from crawl4ai import AsyncWebCrawler, CrawlerRunConfig
from crawl4ai.content_scraping_strategy import LXMLWebScrapingStrategy


# =========================
# API CONFIG
# =========================
API_KEY = os.getenv("API_KEY", "")

URL = "https://openrouter.ai/api/v1/chat/completions"

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
    "HTTP-Referer": "http://localhost",
    "X-Title": "Deep Crawl Lead System"
}


# =========================
# LLM CALL
# =========================
def call_llm(prompt, max_retries=5, delay=10):
    for attempt in range(max_retries):
        try:
            response = requests.post(
                URL,
                headers=HEADERS,
                json={
                    "model": "arcee-ai/trinity-large-preview:free",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0
                },
                timeout=30
            )

            try:
                result = response.json()
            except:
                print("❌ Invalid JSON:", response.text)
                return None

            if "choices" in result:
                content = result["choices"][0]["message"]["content"]

                # Clean markdown
                if content.startswith("```"):
                    content = content.replace("```json", "").replace("```", "").strip()

                return content

            elif "error" in result:
                print(f"⚠️ {result['error']['message']}")
                time.sleep(delay)

        except Exception as e:
            print(f"❌ Error: {e}")
            time.sleep(delay)

    return None


# =========================
# LINK EXTRACTION
# =========================
def extract_links(markdown):
    pattern = r'href="(https?://[^"]+)"'
    links = re.findall(pattern, markdown)

    clean_links = set()
    for link in links:
        link = link.rstrip("/")
        if "github.com" in link:
            clean_links.add(link)

    return list(clean_links)


def is_relevant_link(link):
    link = link.lower()

    allowed = [
        "?tab=repositories",
        "?tab=projects",
        "?tab=stars",
        "/"
    ]

    return any(a in link for a in allowed)


# =========================
# DEEP CRAWLER
# =========================
async def deep_crawl(url, crawler, depth=1, visited=None):
    if visited is None:
        visited = set()

    if url in visited:
        return []

    visited.add(url)

    print(f"🔎 Crawling depth={depth}: {url}")

    config = CrawlerRunConfig(
        scraping_strategy=LXMLWebScrapingStrategy(),
        verbose=False
    )

    try:
        result = await crawler.arun(url, config=config)
    except Exception as e:
        print(f"❌ Failed {url}: {e}")
        return []

    if not result.markdown:
        return []

    pages = [(url, result.markdown)]

    if depth == 0:
        return pages

    links = extract_links(result.markdown)
    links = [l for l in links if is_relevant_link(l)]

    # LIMIT branching
    links = links[:5]

    for link in links:
        if "github.com" not in link:
            continue

        await asyncio.sleep(1)  # anti-rate-limit

        child_pages = await deep_crawl(link, crawler, depth - 1, visited)
        pages.extend(child_pages)

    return pages


# =========================
# CLEAN CONTENT
# =========================
def clean_content(text):
    lines = text.split("\n")
    cleaned = []

    skip = [
        "sign in", "login", "menu", "cookie",
        "privacy", "terms", "navigation"
    ]

    for line in lines:
        line = line.strip()

        if not line or len(line) < 5:
            continue

        if any(s in line.lower() for s in skip):
            continue

        if not any(c.isalnum() for c in line):
            continue

        cleaned.append(line)

    return "\n".join(cleaned)[:4000]


# =========================
# PROMPT
# =========================
def build_prompt(content, url):
    return f"""
Extract professional data from this GitHub profile.

URL: {url}

Return ONLY JSON:

{{
  "name": null or "Full Name",
  "role": null or "Developer role",
  "email": null or "email@domain.com",
  "open_to_work": true/false
}}

Content:
{content}
"""


# =========================
# PROFILE PIPELINE
# =========================
async def process_profile(url, crawler):
    pages = await deep_crawl(url, crawler, depth=1)

    combined = ""
    for u, content in pages:
        combined += f"\n--- {u} ---\n{content}"

    cleaned = clean_content(combined)

    prompt = build_prompt(cleaned, url)

    output = call_llm(prompt)

    if not output:
        return None

    try:
        data = json.loads(output)
        data["source"] = url
        print(f"✅ {url} → {data}")
        return data
    except:
        print(f"❌ JSON parse failed for {url}")
        return None


# =========================
# SEARCH → PROFILES
# =========================
def extract_profiles(markdown):
    pattern = r'https://github\.com/[a-zA-Z0-9_-]+'
    matches = re.findall(pattern, markdown)

    return list(set(matches))


# =========================
# MAIN PIPELINE
# =========================
async def main():
    search_url = "https://github.com/search?q=frontend+developer&type=users"

    print("\n🚀 STARTING DEEP CRAWL SYSTEM\n")

    config = CrawlerRunConfig(
        scraping_strategy=LXMLWebScrapingStrategy(),
        verbose=False
    )

    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun(search_url, config=config)

        profiles = extract_profiles(result.markdown)
        profiles = profiles[:5]

        print(f"Found {len(profiles)} profiles\n")

        leads = []

        for i, url in enumerate(profiles, 1):
            print(f"[{i}] Processing {url}")

            lead = await process_profile(url, crawler)

            if lead:
                leads.append(lead)

        print("\n🎯 FINAL RESULTS\n")

        if leads:
            print(json.dumps(leads, indent=2))
        else:
            print("❌ No valid leads found.")

        print("\n⚠️ Results NOT saved locally (testing mode)")

if __name__ == "__main__":
    asyncio.run(main())