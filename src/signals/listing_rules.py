from urllib.parse import urlparse
from ..schemas import Listing, Signal, RedFlag

URGENCY_TERMS = ("act now", "last chance", "limited time", "buy now or lose", "only today", "urgent payment", "pay immediately")
RISKY_PAYMENT_TERMS = ("gift card only", "pay with gift cards", "wire transfer only", "crypto only", "cryptocurrency only", "friends and family only")
INCONSISTENCY_TERMS = ("100% authentic" , "genuine guaranteed", "official store")

def collect_listing_signals(listing: Listing, domain_result: dict, image_result: dict) -> tuple[list[Signal], list[RedFlag]]:
    signals, flags = [], []
    text = f"{listing.title} {listing.description} {listing.raw_text}".lower()
    if any(term in text for term in RISKY_PAYMENT_TERMS):
        phrase = next(term for term in RISKY_PAYMENT_TERMS if term in text)
        signals.append(Signal(id="payment_pressure", title="Potentially unsafe payment method", status="flagged", severity="high", evidence=f"Listing text contains the phrase: “{phrase}”.", score_contribution=10))
        flags.append(RedFlag(title="Potentially unsafe payment method", explanation="Payment methods with limited buyer protection can make disputes difficult.", evidence=f"Observed listing text: “{phrase}”.", confidence="high"))
    elif any(term in text for term in URGENCY_TERMS):
        phrase = next(term for term in URGENCY_TERMS if term in text)
        signals.append(Signal(id="urgency_language", title="Urgency language", status="flagged", severity="medium", evidence=f"Listing text contains the phrase: “{phrase}”.", score_contribution=10))
        flags.append(RedFlag(title="Pressure to buy quickly", explanation="Urgency language can discourage independent checks; it is a warning sign, not proof of fraud.", evidence=f"Observed listing text: “{phrase}”.", confidence="medium"))
    else:
        signals.append(Signal(id="pressure_language", title="Payment and urgency language", status="clear", severity="low", evidence="No configured high-risk payment or urgency phrases were found in the available text."))
    if not listing.contact_info_found:
        signals.append(Signal(id="contact_information", title="Contact information not detected", status="flagged", severity="low", evidence="No supported contact indicators were found in extracted page text.", score_contribution=5))
        flags.append(RedFlag(title="Contact details not detected", explanation="A shopper could not readily verify contact details from the extracted content. Some sites place them on separate pages, so this may be a false positive.", evidence="Contact indicators absent from the extracted text; extraction may be incomplete.", confidence="low"))
    else:
        signals.append(Signal(id="contact_information", title="Contact information", status="clear", evidence="Contact-related text was found in the extracted page."))
    if not listing.return_policy_found:
        signals.append(Signal(id="return_policy", title="Return policy not detected", status="flagged", severity="low", evidence="No supported return/refund-policy phrases were found in extracted page text.", score_contribution=5))
        flags.append(RedFlag(title="Return policy not detected", explanation="The extracted content did not reveal a return or refund policy. Check the full site before ordering.", evidence="Return-policy indicators absent from extracted text; this check may be incomplete.", confidence="low"))
    else:
        signals.append(Signal(id="return_policy", title="Return policy", status="clear", evidence="Return/refund-policy wording was found in extracted text."))
    if listing.price is not None and listing.price == 0:
        signals.append(Signal(id="price_zero", title="Zero price displayed", status="flagged", severity="medium", evidence="The extracted price is 0; this can also reflect a metadata or extraction issue.", score_contribution=5))
        flags.append(RedFlag(title="Unusual price metadata", explanation="A zero price may be misleading or may be an extraction artifact. Verify the checkout price independently.", evidence="Extracted price: 0.", confidence="low"))
    else:
        signals.append(Signal(id="price_plausibility", title="Price plausibility", status="unknown", severity="low", evidence="No verified comparable market price is configured. The listed price was not scored as a market-price discrepancy."))
    age = domain_result.get("age_days")
    if age is not None and age < 180:
        signals.append(Signal(id="domain_age", title="Recently created domain", status="flagged", severity="low", evidence=domain_result.get("message", ""), score_contribution=10))
        flags.append(RedFlag(title="Recently created domain", explanation="A newer domain provides less operating history to review, but domain age alone does not establish fraud.", evidence=domain_result.get("message", ""), confidence="low"))
    elif age is not None:
        signals.append(Signal(id="domain_age", title="Domain age", status="clear", evidence=domain_result.get("message", "")))
    else:
        signals.append(Signal(id="domain_age", title="Domain age", status="unknown", evidence=domain_result.get("message", "WHOIS unavailable.")))
    matches = image_result.get("matches", [])
    if image_result.get("status") == "available" and matches:
        signals.append(Signal(id="image_search", title="Reverse-image results found", status="unknown", severity="low", evidence=f"{len(matches)} source result(s) returned; source context was not automatically verified."))
    else:
        signals.append(Signal(id="image_search", title="Reverse-image search", status="unknown", evidence=image_result.get("message", "No image-search evidence.")))
    if listing.title and len(listing.title) < 8:
        signals.append(Signal(id="short_title", title="Very short product title", status="flagged", severity="low", evidence=f"Extracted title is only {len(listing.title)} characters.", score_contribution=5))
        flags.append(RedFlag(title="Uninformative product title", explanation="A very short title gives shoppers little detail to verify; some legitimate listings also use short names.", evidence=f"Extracted title: {listing.title!r}.", confidence="low"))
    return signals, flags
