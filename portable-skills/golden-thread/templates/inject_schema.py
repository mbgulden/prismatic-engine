#!/usr/bin/env python3
"""Batch-inject schema.org JSON-LD into static HTML pages missing it.

CUSTOMIZE BEFORE USE:
  1. Set BUSINESS dict with real name, phone, address, social URLs
  2. Update SITE_DIR to point to the target HTML directory
  3. If you have an audit JSON (from a prior scan), set audit_path.
     Otherwise, the script will scan all .html files directly.
  4. The detect_page_type() function uses URL path heuristics — adjust
     patterns for your site's URL structure.

Usage: python3 inject_schema.py
Output: Injects JSON-LD before </head> in every page without existing schema.
"""

import re, json, sys, os
from pathlib import Path
from bs4 import BeautifulSoup

# ── CONFIGURE THESE ──────────────────────────────────────────
SITE_DIR = Path(os.environ.get('SITE_DIR', '/path/to/site'))
BUSINESS = {
    "name": "Your Business Name",
    "url": "https://example.com",
    "telephone": "+1-555-555-5555",
    "address": {
        "@type": "PostalAddress",
        "streetAddress": "123 Main St",
        "addressLocality": "City",
        "addressRegion": "ST",
        "postalCode": "12345",
        "addressCountry": "US"
    },
    "priceRange": "$$",
    "sameAs": [
        "https://www.facebook.com/yourbusiness",
        "https://www.instagram.com/yourbusiness"
    ]
}
# ──────────────────────────────────────────────────────────────

TOUR_OPERATOR = {
    "@type": "TravelAgency",
    "name": BUSINESS["name"],
    "url": BUSINESS["url"],
    "telephone": BUSINESS["telephone"],
}

def detect_page_type(rel_path: str, soup: BeautifulSoup) -> str:
    """Detect page type from URL path and content.
    
    CUSTOMIZE: Adjust path patterns to match your site's URL structure.
    """
    p = rel_path.lower()
    
    if p == 'index.html':
        return 'homepage'
    if 'faq' in p:
        return 'faq'
    if '/activities/' in p and '/page/' not in p:
        return 'activity'
    if 'rental' in p or 'equipment' in p:
        return 'rental'
    if 'contact' in p:
        return 'contact'
    if '/guides/' in p or 'guide/' in p or 'blog' in p:
        return 'guide'
    if 'about' in p or 'review' in p or 'award' in p:
        return 'organization'
    if 'jobs' in p or 'job/' in p:
        return 'organization'
    if 'storefront' in p or 'partner' in p:
        return 'organization'
    if 'privacy' in p or 'cancellation' in p or 'terms' in p or 'policy' in p:
        return 'organization'
    if '/page/' in p:
        return 'itemlist'
    
    return 'organization'  # default

def get_page_title(soup: BeautifulSoup) -> str:
    title_tag = soup.find('title')
    if title_tag:
        return title_tag.get_text(strip=True)
    h1 = soup.find('h1')
    if h1:
        return h1.get_text(strip=True)
    return BUSINESS["name"]

def get_meta_description(soup: BeautifulSoup) -> str:
    meta = soup.find('meta', attrs={'name': 'description'})
    if meta and meta.get('content'):
        return meta['content']
    return f"Description for {BUSINESS['name']}"

def get_canonical_url(soup: BeautifulSoup) -> str:
    link = soup.find('link', rel='canonical')
    if link and link.get('href'):
        return link['href']
    return BUSINESS["url"]

def build_schema(rel_path: str, page_type: str, soup: BeautifulSoup) -> dict:
    title = get_page_title(soup)
    desc = get_meta_description(soup)
    url = get_canonical_url(soup)
    
    if page_type == 'homepage':
        return {
            "@context": "https://schema.org",
            "@type": ["TravelAgency", "LocalBusiness"],
            "@id": f"{url}#organization",
            "name": BUSINESS["name"],
            "description": desc,
            "url": url,
            "telephone": BUSINESS["telephone"],
            "address": BUSINESS["address"],
            "priceRange": BUSINESS["priceRange"],
            "sameAs": BUSINESS.get("sameAs", [])
        }
    
    elif page_type == 'activity':
        return {
            "@context": "https://schema.org",
            "@type": "TouristTrip",
            "name": title,
            "description": desc,
            "url": url,
            "tourOperator": TOUR_OPERATOR,
            "touristType": ["Adventure Travelers", "Families", "Couples", "Groups"],
            "offers": {
                "@type": "Offer",
                "priceCurrency": "USD",
                "availability": "https://schema.org/InStock",
                "url": url
            }
        }
    
    elif page_type == 'rental':
        return {
            "@context": "https://schema.org",
            "@type": "Product",
            "name": title,
            "description": desc,
            "url": url,
            "brand": {"@type": "Brand", "name": BUSINESS["name"]},
            "offers": {
                "@type": "Offer",
                "priceCurrency": "USD",
                "availability": "https://schema.org/InStock",
                "url": url
            }
        }
    
    elif page_type == 'faq':
        qas = []
        headings = soup.find_all(['h2', 'h3'])
        for h in headings[:10]:
            q_text = h.get_text(strip=True)
            if len(q_text) < 10 or len(q_text) > 200:
                continue
            answer_parts = []
            for sibling in h.find_next_siblings():
                if sibling.name in ('h2', 'h3'):
                    break
                if sibling.name in ('p', 'li', 'div'):
                    text = sibling.get_text(strip=True)
                    if text:
                        answer_parts.append(text)
                if len(' '.join(answer_parts)) > 500:
                    break
            if answer_parts:
                qas.append({
                    "@type": "Question",
                    "name": q_text,
                    "acceptedAnswer": {
                        "@type": "Answer",
                        "text": ' '.join(answer_parts)[:500]
                    }
                })
        
        schema = {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": qas[:10] if qas else []
        }
        if not qas:
            schema = {
                "@context": "https://schema.org",
                "@type": "FAQPage",
                "name": title,
                "description": desc,
                "url": url
            }
        return schema
    
    elif page_type == 'guide':
        return {
            "@context": "https://schema.org",
            "@type": "Article",
            "headline": title,
            "description": desc,
            "url": url,
            "publisher": {
                "@type": "Organization",
                "name": BUSINESS["name"],
                "url": BUSINESS["url"]
            }
        }
    
    elif page_type == 'contact':
        return {
            "@context": "https://schema.org",
            "@type": "ContactPage",
            "name": title,
            "description": desc,
            "url": url,
            "about": TOUR_OPERATOR
        }
    
    elif page_type == 'itemlist':
        return {
            "@context": "https://schema.org",
            "@type": "ItemList",
            "name": title,
            "description": desc,
            "url": url
        }
    
    else:  # organization / default
        return {
            "@context": "https://schema.org",
            "@type": "Organization",
            "@id": f"{url}#organization",
            "name": BUSINESS["name"],
            "description": desc,
            "url": url,
            "telephone": BUSINESS["telephone"],
            "address": BUSINESS["address"]
        }

def has_schema(html: str) -> bool:
    return 'application/ld+json' in html

def inject_schema(html: str, schema: dict) -> str:
    """Inject JSON-LD schema before </head>."""
    schema_str = json.dumps(schema, indent=2, ensure_ascii=False)
    script_tag = f'\n<script type="application/ld+json">\n{schema_str}\n</script>\n'
    
    if '</head>' in html:
        return html.replace('</head>', script_tag + '</head>', 1)
    elif '<body' in html:
        idx = html.index('<body')
        return html[:idx] + script_tag + html[idx:]
    else:
        return script_tag + '\n' + html

def scan_pages(site_dir: Path) -> list:
    """Discover all pages missing schema."""
    missing = []
    for html_file in site_dir.rglob('*.html'):
        rel = html_file.relative_to(site_dir)
        rel_path = str(rel)
        # Skip template/partial files
        if '_templates' in rel_path or 'wp-content' in rel_path or 'wp-includes' in rel_path:
            continue
        try:
            html = html_file.read_text(encoding='utf-8', errors='replace')
            if not has_schema(html):
                missing.append((rel_path, html_file, html))
        except Exception:
            continue
    return missing

def main():
    # Option 1: Use an audit JSON if available
    audit_json = SITE_DIR / 'seo_audit_report.json'
    if audit_json.exists():
        with open(audit_json) as f:
            pages = json.load(f)
        missing = [(p['rel_path'], SITE_DIR / p['rel_path'], None) 
                   for p in pages if not p.get('schemas') or len(p.get('schemas', [])) == 0]
    else:
        # Option 2: Scan all HTML files directly
        print("No audit JSON found — scanning all HTML files...")
        missing = scan_pages(SITE_DIR)
    
    print(f"Pages without schema: {len(missing)}")
    
    injected = 0
    skipped = 0
    errors = []
    
    for rel_path, file_path, cached_html in missing:
        if not file_path.exists():
            print(f"  SKIP (not found): {rel_path}")
            skipped += 1
            continue
        
        try:
            html = cached_html or file_path.read_text(encoding='utf-8', errors='replace')
            
            # Double-check it still has no schema
            if has_schema(html):
                print(f"  SKIP (now has schema): {rel_path}")
                skipped += 1
                continue
            
            soup = BeautifulSoup(html, 'html.parser')
            page_type = detect_page_type(rel_path, soup)
            schema = build_schema(rel_path, page_type, soup)
            new_html = inject_schema(html, schema)
            
            file_path.write_text(new_html, encoding='utf-8')
            print(f"  INJECTED [{page_type:12s}]: {rel_path}")
            injected += 1
            
        except Exception as e:
            print(f"  ERROR: {rel_path}: {e}")
            errors.append((rel_path, str(e)))
    
    print(f"\n=== SUMMARY ===")
    print(f"Injected: {injected}")
    print(f"Skipped: {skipped}")
    print(f"Errors: {len(errors)}")
    if errors:
        for path, err in errors[:5]:
            print(f"  {path}: {err}")

if __name__ == '__main__':
    main()
