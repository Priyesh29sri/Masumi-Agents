import asyncio
import re
import json
import requests
import os
from urllib.parse import urlparse

from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
from crawl4ai.deep_crawling import (
    BestFirstCrawlingStrategy,
    DomainFilter,
    FilterChain,
    KeywordRelevanceScorer,
    URLPatternFilter,
)

# Your Together AI Key
TOGETHER_API_KEY = os.getenv("TOGETHER_API_KEY", "")
TOGETHER_API_URL = "https://api.together.xyz/v1/chat/completions"

# Basic Regex for explicit fallback extraction
LINKEDIN_RE = re.compile(r"https?://(?:www\.)?linkedin\.com/in/[a-zA-Z0-9_-]+", re.I)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", re.I)

def extract_people_llm(markdown_content: str) -> list[dict]:
    """Use Together AI to extract structured personal data from text."""
    if not markdown_content or len(markdown_content) < 50:
        return []
    
    # Take up to ~8000 characters to stay within free tier context limits
    text = markdown_content[:8000]
    
    prompt = f"""
Extract a list of people mentioned in the following webpage text. 
For each person, provide their name, role or title, email address (if present or guessable), and LinkedIn profile URL (if present).
Return ONLY a valid JSON array of objects with keys "name", "role", "email", and "linkedin". If a field is missing, use null.
If no people are found, return []. Do not include markdown formatting or explanations, just the JSON array.

Text:
{text}
"""
    headers = {
        "Authorization": f"Bearer {TOGETHER_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "meta-llama/Llama-3-8b-chat-hf",  # Free tier friendly model
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
    }
    
    try:
        r = requests.post(TOGETHER_API_URL, json=payload, headers=headers, timeout=20)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        
        # Cleanup potential markdown ticks around JSON
        content = content.strip()
        if content.startswith("```json"):
            content = content[7:]
        if content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
            
        return json.loads(content.strip())
    except Exception as e:
        # Fallback to regex if LLM fails
        return []

def build_strategy(start_url: str) -> BestFirstCrawlingStrategy:
    host = urlparse(start_url).netloc
    filters = FilterChain([
        DomainFilter(allowed_domains=[host]),
        URLPatternFilter(patterns=["*.jpg", "*.jpeg", "*.png", "*.gif", "*.pdf"], reverse=True),
    ])
    # Give massive priority to pages explicitly listing teams
    scorer = KeywordRelevanceScorer(
        keywords=["team", "about", "people", "faculty", "staff", "leadership", "authors"],
        weight=2.0,
    )
    return BestFirstCrawlingStrategy(
        max_depth=2,          # Look slightly deep for the /team page
        filter_chain=filters,
        url_scorer=scorer,
        include_external=False,
        max_pages=5,         # Just check top 5 most relevant pages to save time/API
    )

async def search_leads(start_url: str):
    browser_config = BrowserConfig(headless=True, verbose=False)
    run_config = CrawlerRunConfig(
        deep_crawl_strategy=build_strategy(start_url),
        cache_mode=CacheMode.BYPASS,
        stream=False,
        verbose=False,
    )

    all_leads = []
    fallback_emails = set()
    fallback_linkedins = set()
    
    print(f"\n🚀 Crawling {start_url} for leads...")
    
    async with AsyncWebCrawler(config=browser_config) as crawler:
        try:
            results = await asyncio.wait_for(crawler.arun(url=start_url, config=run_config), timeout=120)
            
            for i, res in enumerate(results, 1):
                url = res.url
                text = str(getattr(res, "markdown", "") or "")
                print(f"📄 [{i}/{len(results)}] Scanning {url} ({len(text)} chars)")
                
                # 1. Regex Fallback
                fallback_emails |= set(match.group(0) for match in EMAIL_RE.finditer(text))
                fallback_linkedins |= set(match.group(0) for match in LINKEDIN_RE.finditer(text))
                
                # 2. LLM Extraction
                llm_people = extract_people_llm(text)
                if llm_people:
                    print(f"   🤖 LLM found {len(llm_people)} people on this page.")
                    all_leads.extend(llm_people)
                    
        except Exception as e:
            print(f"❌ Error during crawl: {e}")

    # Remove generic/privacy emails from fallback
    generic = {"info","support","help","hello","contact","admin","sales","careers","privacy","legal"}
    fallback_emails = {e for e in fallback_emails if e.split('@')[0].lower() not in generic}

    print("\n\n🎯 === EXTRACTION RESULTS ===")
    
    # Deduplicate LLM leads by name
    seen_names = set()
    unique_leads = []
    for lead in all_leads:
        name = lead.get("name")
        if name and name not in seen_names:
            unique_leads.append(lead)
            seen_names.add(name)

    if unique_leads:
        print(f"\n🧑‍💼 PEOPLE EXTRACTED ({len(unique_leads)}):")
        for lead in unique_leads:
            print(f" - {lead.get('name', 'N/A')}")
            print(f"   Role:     {lead.get('role', 'N/A')}")
            if lead.get('email'):
                print(f"   Email:    {lead.get('email')}")
            if lead.get('linkedin'):
                print(f"   LinkedIn: {lead.get('linkedin')}")
    else:
        print("\n🧑‍💼 PEOPLE EXTRACTED: none via LLM")

    print(f"\n✉️ FALLBACK EMAILS DETECTED ({len(fallback_emails)}):")
    for e in sorted(fallback_emails)[:10]:
        print(f" - {e}")
        
    print(f"\n🔗 FALLBACK LINKEDINS DETECTED ({len(fallback_linkedins)}):")
    for l in sorted(fallback_linkedins)[:10]:
        print(f" - {l}")

if __name__ == "__main__":
    # Test on a site with open directory / LinkedIn listings
    asyncio.run(search_leads("https://www.retool.com/about"))