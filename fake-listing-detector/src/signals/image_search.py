import os
import requests

def reverse_image_search(image_url: str, timeout: int = 8) -> dict:
    key = os.getenv("SERPAPI_API_KEY", "").strip()
    if not key:
        return {"status": "unavailable", "message": "SERPAPI_API_KEY is not configured.", "matches": []}
    if not image_url:
        return {"status": "unknown", "message": "No product image URL was extracted or supplied.", "matches": []}
    try:
        response = requests.get("https://serpapi.com/search.json", params={"engine": "google_reverse_image", "image_url": image_url, "api_key": key}, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
        matches = []
        for item in payload.get("image_results", [])[:5]:
            link = item.get("link") or item.get("source")
            if link:
                matches.append({"title": str(item.get("title", "Image result"))[:180], "url": link, "snippet": str(item.get("snippet", ""))[:300]})
        return {"status": "available", "message": "Reverse-image results returned. Matches require human context review; stock imagery alone is not proof of fraud.", "matches": matches}
    except Exception as exc:
        return {"status": "unavailable", "message": f"Reverse-image search failed ({type(exc).__name__}).", "matches": []}
