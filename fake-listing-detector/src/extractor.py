from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin
import json
import re
from .schemas import Listing
from .url_safety import safe_fetch

PRICE_RE = re.compile(r"(?:\$|USD\s?)([0-9][0-9,]*(?:\.[0-9]{1,2})?)", re.I)

def _meta(soup, *keys):
    for key in keys:
        tag = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
        if tag and tag.get("content"):
            return tag["content"].strip()
    return ""

def extract_listing(url: str) -> Listing:
    final_url, body, _ = safe_fetch(url)
    soup = BeautifulSoup(body, "html.parser")
    domain = urlparse(final_url).hostname or ""
    title = _meta(soup, "og:title", "twitter:title") or (soup.title.get_text(" ", strip=True) if soup.title else "")
    description = _meta(soup, "og:description", "description", "twitter:description")
    image = _meta(soup, "og:image", "twitter:image")
    images = [urljoin(final_url, image)] if image else []
    price, currency = None, ""
    price_text = _meta(soup, "product:price:amount", "og:price:amount")
    currency = _meta(soup, "product:price:currency", "og:price:currency")
    if not price_text:
        match = PRICE_RE.search(soup.get_text(" ", strip=True))
        if match:
            price_text = match.group(1).replace(",", "")
            currency = "USD" if "$" in match.group(0) or "USD" in match.group(0).upper() else ""
    try:
        price = float(price_text) if price_text else None
    except ValueError:
        price = None
    seller = _meta(soup, "product:brand", "og:site_name")
    raw_text = soup.get_text(" ", strip=True)[:12000]
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or script.get_text())
            objects = data if isinstance(data, list) else [data]
            for obj in objects:
                if not isinstance(obj, dict):
                    continue
                typ = obj.get("@type", "")
                if isinstance(typ, list):
                    typ = " ".join(typ)
                if "Product" in typ:
                    title = title or str(obj.get("name", ""))
                    description = description or str(obj.get("description", ""))
                    seller_obj = obj.get("brand") or obj.get("seller") or {}
                    if isinstance(seller_obj, dict):
                        seller = seller or str(seller_obj.get("name", ""))
                    offers = obj.get("offers", {})
                    if isinstance(offers, list):
                        offers = offers[0] if offers else {}
                    if isinstance(offers, dict):
                        if price is None:
                            try: price = float(offers.get("price"))
                            except (TypeError, ValueError): pass
                        currency = currency or str(offers.get("priceCurrency", ""))
                    obj_images = obj.get("image", [])
                    if isinstance(obj_images, str): obj_images = [obj_images]
                    if isinstance(obj_images, list):
                        images.extend(urljoin(final_url, str(x)) for x in obj_images if isinstance(x, str))
        except (ValueError, TypeError):
            continue
    images = list(dict.fromkeys(x for x in images if x.startswith(("http://", "https://"))))[:8]
    lower = raw_text.lower()
    contact = any(x in lower for x in ("contact us", "contact information", "support@", "customer service"))
    returns = any(x in lower for x in ("return policy", "returns and refunds", "refund policy", "30-day returns"))
    notes = []
    if not title: notes.append("Title was not found in page metadata.")
    if price is None: notes.append("Price was not found in supported metadata or visible text.")
    if not description: notes.append("Description was not found in page metadata.")
    if not seller: notes.append("Seller/store name was not found in supported metadata.")
    return Listing(listing_url=final_url, title=title[:300], price=price, currency=currency[:12], description=description[:3000], seller_name=seller[:200], domain=domain, image_urls=images, contact_info_found=contact, return_policy_found=returns, raw_text=raw_text, extraction_notes=notes)

def manual_listing(listing_url: str, title: str, price: str, currency: str, description: str, seller_name: str, image_urls: str) -> Listing:
    from .url_safety import validate_url
    safe_url = validate_url(listing_url, resolve_dns=False) if listing_url.strip() else ""
    parsed_price = None
    if price.strip():
        try: parsed_price = float(price.replace(",", "").strip())
        except ValueError: raise ValueError("Price must be a number, without currency symbols.")
        if parsed_price < 0: raise ValueError("Price cannot be negative.")
    images = [x.strip() for x in image_urls.splitlines() if x.strip()]
    for image in images:
        validate_url(image, resolve_dns=False)
    domain = urlparse(safe_url).hostname or ""
    return Listing(listing_url=safe_url, title=title.strip()[:300], price=parsed_price, currency=currency.strip()[:12], description=description.strip()[:3000], seller_name=seller_name.strip()[:200], domain=domain, image_urls=images[:8], raw_text=(title + " " + description)[:12000], extraction_notes=["Listing details were supplied manually by the user."])
