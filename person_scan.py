import asyncio
import re
from test1 import run_crawl

URLS = [
    "https://wise.com",
    "https://razorpay.com",
]

GENERIC = {
    "info", "support", "help", "hello", "contact", "admin", "sales", "careers", "career", "jobs",
    "privacy", "legal", "security", "press", "media", "team", "billing", "finance", "complaints",
    "compliance", "hr", "marketing", "partnerships", "partners", "noreply", "no-reply", "donotreply",
    "investor", "investors", "feedback", "office", "enquiry", "inquiries", "query", "queries", "api",
}

ARTIFACT_RE = re.compile(r"^(u003e|gt|lt)+", re.I)


def clean_email(email: str) -> str:
    return ARTIFACT_RE.sub("", email.strip().lower())


def looks_personal(email: str) -> bool:
    local = email.split("@", 1)[0]
    if not local or local in GENERIC:
        return False
    if any(token in local for token in ("first", "last", "name", "example", "test")):
        return False
    if "." in local or "_" in local:
        return True
    if "-" in local and local not in GENERIC:
        return True
    return False


async def main() -> None:
    for url in URLS:
        print(f"\n=== {url} ===", flush=True)
        try:
            emails = await asyncio.wait_for(run_crawl(url), timeout=180)
            cleaned = sorted({clean_email(e) for e in emails if "@" in e})
            personal = sorted([e for e in cleaned if looks_personal(e)])

            print(f"total_extracted={len(cleaned)}", flush=True)
            print(f"likely_personal={len(personal)}", flush=True)
            if personal:
                for e in personal:
                    print(f"PERSONAL: {e}", flush=True)
            else:
                print("PERSONAL: none", flush=True)
        except Exception as ex:
            print(f"ERROR: {ex}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
