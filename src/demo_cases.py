from .schemas import Listing

DEMO_CASES = {
    "Suspicious example — pressure and unsafe payment": Listing(
        listing_url="https://flash-deals.example/products/premium-headphones", title="Premium Wireless Headphones", price=19.99, currency="USD",
        description="Limited time! Act now. Pay with gift cards only. Official store, 100% authentic. No return policy information supplied.",
        seller_name="Flash Deals", domain="flash-deals.example", image_urls=[], contact_info_found=False, return_policy_found=False,
        raw_text="Limited time! Act now. Pay with gift cards only. Official store, 100% authentic."
    ),
    "Lower-signal example — ordinary product listing": Listing(
        listing_url="https://shop.example/products/ceramic-mug", title="Blue Ceramic Coffee Mug", price=14.50, currency="USD",
        description="A reusable ceramic coffee mug. Contact us for product questions. Return policy: returns accepted within 30 days.",
        seller_name="Example Home Shop", domain="shop.example", image_urls=[], contact_info_found=True, return_policy_found=True,
        raw_text="Blue Ceramic Coffee Mug. Contact us for product questions. Return policy: returns accepted within 30 days."
    )
}
