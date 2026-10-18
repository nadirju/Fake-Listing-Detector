"""Pre-recorded demo cases (US-09, AC-14).

Both cases use fictional brands and reserved `.example` domains so they never depend
on a live third-party page. Provider results (WHOIS, image search, AI output) are
bundled but still flow through the real grounding + scoring code, so judges see the
real evidence-to-score logic.

Expected observations (also asserted by tests/test_demo_cases.py):
  * suspicious_watch  -> score >= 60 (high suspicion): very new domain, copied imagery
    found in unrelated contexts, extreme price gap vs the manufacturer reference,
    contradictory origin/seller claims, no returns/contact info, bank-transfer/crypto
    and WhatsApp payment requests plus pressure language.
  * legitimate_mug    -> score < 30 (lower observed risk): 15-year-old domain, returns
    and contact info present, image matches are manufacturer/retailer pages, price in
    line with reference, no inconsistencies.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List

from .schemas import (
    DomainAgeResult,
    GeminiAnalysis,
    ImageMatch,
    ImageMatchAssessment,
    ImageSearchResult,
    Inconsistency,
    Listing,
    Observation,
    PriceObservation,
    ReferencePrice,
)
from .utils import registrable_domain


@dataclass
class DemoMeta:
    id: str
    title: str
    summary: str
    expected_band: str
    expected_observations: List[str] = field(default_factory=list)


@dataclass
class DemoBundle:
    meta: DemoMeta
    listing: Listing
    domain: DomainAgeResult
    images: ImageSearchResult
    ai: GeminiAnalysis
    reference: ReferencePrice


DEMO_CASES: Dict[str, DemoMeta] = {
    "suspicious_watch": DemoMeta(
        id="suspicious_watch",
        title="Suspicious example: 'Aurelian Chrono Master' watch at 91% off",
        summary="A newly registered store selling a luxury-style watch far below the manufacturer price.",
        expected_band="high_suspicion",
        expected_observations=[
            "Domain registered ~12 days before the analysis",
            "Product photos found on unrelated sites",
            "Price is under 10% of the manufacturer reference",
            "Contradictory claims (Swiss vs. Japan movement; 'official boutique' vs. a wholesale seller)",
            "No return policy or contact details on the page",
            "Bank transfer / Bitcoin / WhatsApp ordering plus pressure language",
        ],
    ),
    "legitimate_mug": DemoMeta(
        id="legitimate_mug",
        title="Legitimate example: 'Northfield Stoneware' mug set",
        summary="An established store with consistent details, policies and a normal price.",
        expected_band="lower_observed_risk",
        expected_observations=[
            "Domain registered in 2011",
            "Return policy and contact email present",
            "Image matches are manufacturer / retailer pages of the same product",
            "Price within a few percent of the reference",
        ],
    ),
}


def _suspicious(now: datetime) -> DemoBundle:
    description = (
        "100% genuine Aurelian Chrono Master Automatic, retail price $1,450. Official Aurelian Boutique clearance - "
        "Swiss made movement, sapphire crystal. Case and movement made in Japan. HURRY - only 3 left at this price, "
        "sale ends tonight! We ship worldwide. Bank transfer only or Bitcoin payment accepted; message us on WhatsApp "
        "to order."
    )
    page_text = description + "\nHome Shop Sale Cart Checkout Wishlist Compare Newsletter Follow us"
    listing = Listing(
        url="https://luxetime-outlet.example/products/chrono-master-automatic",
        final_url="https://luxetime-outlet.example/products/chrono-master-automatic",
        domain="luxetime-outlet.example",
        title="Aurelian Chrono Master Automatic Watch - 91% OFF Factory Clearance",
        price=129.0,
        currency="USD",
        price_text="$129.00",
        price_source="structured",
        description=description,
        seller="Meridian Wholesale Outlet",
        brand="Aurelian",
        image_urls=[
            "https://cdn.luxetime-outlet.example/img/chrono-master-front.jpg",
            "https://cdn.luxetime-outlet.example/img/chrono-master-side.jpg",
        ],
        has_return_policy=False,
        has_contact_info=False,
        page_text=page_text,
        page_text_length=1800,
        source="demo",
        sources=["demo"],
    )
    domain = DomainAgeResult(
        domain="luxetime-outlet.example",
        status="ok",
        creation_date=now - timedelta(days=12),
        registrar="Example Registrar, Inc.",
    )
    img = listing.image_urls[0]
    m1 = "https://bargainbazaar-shop.example/p/chrono-watch-9981"
    m2 = "https://timekeepers-forum.example/threads/same-watch-photo-on-several-shops-4412"
    images = ImageSearchResult(
        status="ok",
        searched_images=[img],
        matches=[
            ImageMatch(
                image_url=img,
                title="Men's chronograph watch - supplier catalogue photo, sold by many resellers",
                source="bargainbazaar-shop.example",
                link=m1,
                domain=registrable_domain(m1),
                snippet="Generic dropship listing using the same studio photo for an unbranded watch.",
            ),
            ImageMatch(
                image_url=img,
                title="Thread: the same watch photo appears on several unrelated shops",
                source="timekeepers-forum.example",
                link=m2,
                domain=registrable_domain(m2),
                snippet="Photographer asks where the image is being reused without credit.",
            ),
        ],
    )
    ai = GeminiAnalysis(
        summary=(
            "The listing pairs a heavily discounted luxury-style watch with contradictory origin and seller claims "
            "and asks for payment methods that offer little buyer protection."
        ),
        inconsistencies=[
            Inconsistency(
                description="The listing says the movement is Swiss made and also says the movement is made in Japan.",
                evidence_field="description",
                evidence_quote="Swiss made movement",
                confidence="medium",
            ),
            Inconsistency(
                description="The text calls itself the official brand boutique, but the seller is named as a wholesale outlet.",
                evidence_field="description",
                evidence_quote="Official Aurelian Boutique",
                confidence="high",
            ),
        ],
        image_match_assessments=[
            ImageMatchAssessment(
                source_url=m1,
                context="unrelated_context",
                reason="The same photo is used for a generic unbranded watch on an unrelated reseller site.",
            ),
            ImageMatchAssessment(
                source_url=m2,
                context="unrelated_context",
                reason="A forum thread discusses this photo being reused by several unrelated shops.",
            ),
        ],
        price_observation=PriceObservation(
            plausibility="looks_unusually_low",
            note="The price looks far below typical prices for automatic watches (general knowledge, not live market data).",
        ),
        additional_observations=[
            Observation(
                title="Strong authenticity claim next to an extreme discount",
                explanation="A '100% genuine' claim together with a very large discount proves nothing by itself but deserves independent verification.",
                evidence_quote="100% genuine",
                confidence="medium",
            )
        ],
        recommended_actions=["Ask the brand's official support whether this store is an authorised reseller."],
    )
    reference = ReferencePrice(
        amount=1450.0,
        currency="USD",
        source_url="https://aurelian-watches.example/collections/chrono-master",
    )
    return DemoBundle(DEMO_CASES["suspicious_watch"], listing, domain, images, ai, reference)


def _legitimate(now: datetime) -> DemoBundle:
    description = (
        "Hand-glazed stoneware mugs, microwave and dishwasher safe. Set of four, 12 oz each. Made in our Ohio studio "
        "with a lead-free glaze. Free returns within 30 days."
    )
    page_text = (
        description
        + "\nShipping & returns: we accept returns within 30 days of delivery for a full refund."
        + "\nContact us: support@northfield-stoneware.example or visit our studio in Columbus, Ohio."
        + "\nAbout us Care instructions Wholesale Gift cards"
    )
    listing = Listing(
        url="https://northfield-stoneware.example/products/ceramic-mug-set-of-4",
        final_url="https://northfield-stoneware.example/products/ceramic-mug-set-of-4",
        domain="northfield-stoneware.example",
        title="Northfield Stoneware 12 oz Ceramic Mug, Set of 4",
        price=36.0,
        currency="USD",
        price_text="$36.00",
        price_source="structured",
        description=description,
        seller="Northfield Stoneware Co.",
        brand="Northfield Stoneware",
        image_urls=[
            "https://northfield-stoneware.example/cdn/mug-set-hero.jpg",
            "https://northfield-stoneware.example/cdn/mug-set-detail.jpg",
        ],
        has_return_policy=True,
        has_contact_info=True,
        page_text=page_text,
        page_text_length=2400,
        source="demo",
        sources=["demo"],
    )
    domain = DomainAgeResult(
        domain="northfield-stoneware.example",
        status="ok",
        creation_date=datetime(2011, 3, 14, tzinfo=timezone.utc),
        registrar="Example Registrar, Inc.",
    )
    img = listing.image_urls[0]
    m1 = "https://stoneware-trade-guide.example/catalog/northfield-mug-set"
    m2 = "https://kitchen-supply-house.example/northfield-mug-set"
    images = ImageSearchResult(
        status="ok",
        searched_images=[img],
        matches=[
            ImageMatch(
                image_url=img,
                title="Northfield Stoneware product catalog (manufacturer press images)",
                source="stoneware-trade-guide.example",
                link=m1,
                domain=registrable_domain(m1),
                snippet="Official press image of the Northfield 12 oz mug set.",
            ),
            ImageMatch(
                image_url=img,
                title="Northfield mug set, 4 pack - Kitchen Supply House",
                source="kitchen-supply-house.example",
                link=m2,
                domain=registrable_domain(m2),
                snippet="Retailer listing for the same Northfield mug set.",
            ),
        ],
    )
    ai = GeminiAnalysis(
        summary="The listing details are consistent: the brand, seller, product description and policies agree with each other.",
        inconsistencies=[],
        image_match_assessments=[
            ImageMatchAssessment(
                source_url=m1,
                context="manufacturer_or_official",
                reason="The image comes from the manufacturer's catalogue.",
            ),
            ImageMatchAssessment(
                source_url=m2,
                context="same_product_listing",
                reason="Another retailer sells the same product with the same photo.",
            ),
        ],
        price_observation=PriceObservation(
            plausibility="looks_typical",
            note="The price looks typical for a hand-glazed four-mug set (general knowledge, not live market data).",
        ),
        additional_observations=[],
        recommended_actions=[],
    )
    reference = ReferencePrice(
        amount=38.0,
        currency="USD",
        source_url="https://kitchen-supply-house.example/northfield-mug-set",
    )
    return DemoBundle(DEMO_CASES["legitimate_mug"], listing, domain, images, ai, reference)


_BUILDERS = {"suspicious_watch": _suspicious, "legitimate_mug": _legitimate}


def load_demo(case_id: str, now: datetime) -> DemoBundle:
    if case_id not in _BUILDERS:
        raise KeyError(f"Unknown demo case: {case_id}")
    return _BUILDERS[case_id](now)
