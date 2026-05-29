import os
import sys
import json
import re
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup
import time
from datetime import datetime

# Thread-safe log registry for Gradio status
_gradio_callback = None
_telegram_console_buffer = []

def register_callback(cb):
    global _gradio_callback
    _gradio_callback = cb

def log_to_telegram_buffer(msg):
    _telegram_console_buffer.append(msg)

# Direct print utility for clean console logging
def log_step(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{timestamp}] [➔] {msg}"
    print(formatted)
    if _gradio_callback:
        try:
            _gradio_callback(formatted)
        except Exception:
            pass

def log_success(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{timestamp}] [✔] SUCCESS: {msg}"
    print(formatted)
    log_to_telegram_buffer(f"🟢 {msg}")
    if _gradio_callback:
        try:
            _gradio_callback(formatted)
        except Exception:
            pass

def log_error(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{timestamp}] [✘] ERROR: {msg}"
    print(formatted)
    log_to_telegram_buffer(f"❌ *ERROR:* {msg}")
    if _gradio_callback:
        try:
            _gradio_callback(formatted)
        except Exception:
            pass

def log_warning(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{timestamp}] [⚠] WARNING: {msg}"
    print(formatted)
    log_to_telegram_buffer(f"⚠️ *WARNING:* {msg}")
    if _gradio_callback:
        try:
            _gradio_callback(formatted)
        except Exception:
            pass


# Initialize setup and config checks
CONFIG_FILE = "config.json"
CREDENTIALS_FILE = "credentials.json"

config = {}
if os.path.exists(CONFIG_FILE):
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
    except Exception as e:
        log_warning(f"Config file load nahi ho saka: {e}")

SPREADSHEET_NAME = os.getenv("SPREADSHEET_NAME", config.get("spreadsheet_name", "urban threads")).strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", config.get("telegram_bot_token", "")).strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", config.get("telegram_chat_id", "")).strip()
GROQ_API_KEYS_RAW = os.getenv("GROQ_API_KEY", config.get("groq_api_key", "")).strip()
GROQ_API_KEYS = [k.strip() for k in re.split(r'[,\s;]+', GROQ_API_KEYS_RAW) if k.strip()]
_current_groq_key_index = 0

def get_active_groq_key():
    global _current_groq_key_index
    if not GROQ_API_KEYS:
        return ""
    if _current_groq_key_index >= len(GROQ_API_KEYS):
        _current_groq_key_index = 0
    return GROQ_API_KEYS[_current_groq_key_index]

def rotate_groq_key():
    global _current_groq_key_index
    if not GROQ_API_KEYS or len(GROQ_API_KEYS) <= 1:
        return False
    _current_groq_key_index = (_current_groq_key_index + 1) % len(GROQ_API_KEYS)
    log_warning(f"🔄 Groq API Key limit/error detected! Rotating to Key #{_current_groq_key_index + 1}: ...{get_active_groq_key()[-6:]}")
    return True


# Dynamic Multi-Key ScraperAPI Rotation System Setup
SCRAPER_API_KEYS_RAW = os.getenv("SCRAPER_API_KEY", config.get("scraper_api_key", "")).strip()
# Support comma-separated, space-separated or semicolon-separated keys
SCRAPER_API_KEYS = [k.strip() for k in re.split(r'[,\s;]+', SCRAPER_API_KEYS_RAW) if k.strip()]
_current_key_index = 0

def get_active_scraper_key():
    global _current_key_index
    if not SCRAPER_API_KEYS:
        return ""
    if _current_key_index >= len(SCRAPER_API_KEYS):
        _current_key_index = 0
    return SCRAPER_API_KEYS[_current_key_index]

def rotate_scraper_key():
    global _current_key_index
    if not SCRAPER_API_KEYS or len(SCRAPER_API_KEYS) <= 1:
        return False
    _current_key_index = (_current_key_index + 1) % len(SCRAPER_API_KEYS)
    log_warning(f"🔄 ScraperAPI Key limit/error detected! Rotating to Key #{_current_key_index + 1}: ...{get_active_scraper_key()[-6:]}")
    return True


try:
    import gspread
    from google.oauth2.service_account import Credentials
except ImportError:
    log_error("Required libraries missing! Please run: pip install gspread google-auth")
    sys.exit(1)



# ---------------------------------------------------------
# TELEGRAM BOT NOTIFICATION SERVICE
# ---------------------------------------------------------
def send_telegram_message(message):
    """
    Sends a styled markdown notification to the user's Telegram bot.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log_warning("Telegram Bot Token ya Chat ID config me set nahi hai. Message skipped.")
        return False
        
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            return True
        else:
            log_error(f"Telegram API fail: {response.text}")
            return False
    except Exception as e:
        log_error(f"Telegram notification error: {e}")
        return False


# ---------------------------------------------------------
# GOOGLE SHEETS CLIENT INITIALIZATION
# ---------------------------------------------------------
def get_google_sheet_client():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]
    try:
        # Check environment variable first (HF secret)
        env_creds = os.getenv("GOOGLE_CREDENTIALS_JSON")
        if env_creds:
            info = json.loads(env_creds)
            creds = Credentials.from_service_account_info(info, scopes=scope)
            client = gspread.authorize(creds)
            return client
            
        # Fallback to local file
        if os.path.exists(CREDENTIALS_FILE):
            creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scope)
            client = gspread.authorize(creds)
            return client
            
        log_error(f"Google Credentials nahi mile! Na to '{CREDENTIALS_FILE}' mila, aur na hi 'GOOGLE_CREDENTIALS_JSON' env var.")
        return None
    except Exception as e:
        log_error(f"Google Service Account Authentication failed: {e}")
        return None



# ---------------------------------------------------------
# PRICE SCRAPING ENGINE
# ---------------------------------------------------------
def extract_platform_from_link(url):
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
        
        platform_mapping = {
            "depop.com": "Depop",
            "grailed.com": "Grailed",
            "zara.com": "Zara",
            "hm.com": "H&M",
            "urbanoutfitters.com": "Urban Outfitters",
            "asos.com": "ASOS",
            "farfetch.com": "Farfetch",
            "nike.com": "Nike",
            "adidas.com": "Adidas",
            "uniqlo.com": "Uniqlo",
            "stockx.com": "StockX",
            "goat.com": "GOAT",
            "pacsun.com": "PacSun",
            "ssense.com": "SSENSE",
            "ebay.com": "eBay",
            "amazon.com": "Amazon"
        }
        
        for key, val in platform_mapping.items():
            if key in domain:
                return val
        
        parts = domain.split(".")
        if len(parts) >= 2:
            return parts[-2].capitalize()
        return domain.capitalize()
    except Exception:
        return "Unknown"

def find_prices_in_dict(d, found=None):
    if found is None:
        found = []
    if isinstance(d, dict):
        # Highly precise Shein price targets to avoid matching dimensions, weight, height, or sizes!
        for key in ["salePrice", "retailPrice", "priceInfo", "usdPrice", "usd_price", "originalPrice", "original_price", "goods_ga_price", "ga_price"]:
            val = d.get(key)
            if val and isinstance(val, dict):
                amount = val.get("amount") or val.get("usdAmount") or val.get("usd_amount") or val.get("value") or val.get("price")
                if amount and isinstance(amount, (int, float, str)) and not isinstance(amount, bool):
                    symbol = val.get("amountWithSymbol") or val.get("symbol") or val.get("currency") or val.get("priceCurrency") or "$"
                    found.append((str(amount), str(symbol)))
            elif val and isinstance(val, (int, float, str)) and not isinstance(val, bool):
                found.append((str(val), key))
            
        for k, v in d.items():
            if isinstance(v, (dict, list)):
                find_prices_in_dict(v, found)
    elif isinstance(d, list):
        for item in d:
            find_prices_in_dict(item, found)
    return found

def get_active_groq_text_model():
    active_key = get_active_groq_key()
    if not active_key:
        return "llama-3.3-70b-specdec"
    try:
        headers = {
            "Authorization": f"Bearer {active_key}"
        }
        response = requests.get("https://api.groq.com/openai/v1/models", headers=headers, timeout=10)
        if response.status_code == 200:
            models_data = response.json()
            model_ids = [m["id"] for m in models_data.get("data", [])]
            for m_id in model_ids:
                if "llama-3.3-70b" in m_id.lower():
                    return m_id
            for m_id in model_ids:
                if "llama-3.1-70b" in m_id.lower():
                    return m_id
            for m_id in model_ids:
                if "llama-3.1-8b" in m_id.lower():
                    return m_id
            if model_ids:
                sensible = [m for m in model_ids if "llama" in m.lower() or "scout" in m.lower()]
                if sensible:
                    return sensible[0]
        return "llama-3.3-70b-specdec"
    except Exception as e:
        log_warning(f"Failed to fetch active Groq models: {e}. Using default fallback.")
        return "llama-3.3-70b-specdec"

def scrape_live_price_and_status(url):
    """
    Fetches product URL and extracts:
    - price: live scraped price string
    - status: 'active', 'sold_out', or 'not_found'
    """
    # Strip all tracking/affiliate parameters from Shein URL to prevent anti-bot redirect loops!
    if "shein." in url.lower():
        if "?" in url:
            url = url.split("?")[0]
            
    original_url = url
    # Convert mobile Shein links to desktop links to bypass Shein's aggressive mobile-to-home-page redirect loops for Phase 1!
    if "m.shein.com" in url.lower():
        url = re.sub(r"m\.shein\.com", "www.shein.com", url, flags=re.IGNORECASE)
        log_step(f"Auto-converted Mobile Shein URL to Desktop for Phase 1: {url}")
        
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9"
    }
    
    is_shein = "shein." in url.lower()
    is_protected = any(domain in url.lower() for domain in ["shein.", "zara.", "hm.", "asos.", "amazon."])
    
    # ---------------------------------------------------------
    # PHASE 1: Try Direct Text/Script Parser First (Extremely Fast & Accurate!)
    # ---------------------------------------------------------
    log_step("Phase 1: Fetching page via high-speed public CORS proxies for direct text/JSON parsing...")
    html_content = ""
    active_key = get_active_scraper_key()
    
    try:
        import urllib.parse
        # Prioritize ScraperAPI / Scrape.do with US Geo to guarantee US pricing ($6.64)!
        # Since render=true is disabled, these static fetches are extremely fast and won't time out.
        proxies_to_try = []
        if is_protected and active_key:
            clean_key = active_key.replace("scrapedo:", "").replace("SCRAPEDO:", "")
            is_scrape_do = len(active_key) != 32 or active_key.lower().startswith("scrapedo:")
            if is_scrape_do:
                proxies_to_try.append(f"https://api.scrape.do/?token={clean_key}&url={urllib.parse.quote(url)}&geo=us")
            else:
                proxies_to_try.append(f"https://api.scraperapi.com?api_key={clean_key}&url={urllib.parse.quote(url)}&country_code=us")
                
        # Free public proxies as backups (they default to European server IPs so pricing might vary)
        proxies_to_try.extend([
            f"https://corsproxy.io/?url={urllib.parse.quote(url)}",
            f"https://api.allorigins.win/raw?url={urllib.parse.quote(url)}"
        ])
                
        response = None
        for p_idx, proxy_url in enumerate(proxies_to_try):
            p_name = "ScraperAPI" if "scraperapi" in proxy_url else "Scrape.do" if "scrape.do" in proxy_url else "corsproxy.io" if "corsproxy" in proxy_url else "allorigins"
            log_step(f"Attempting Phase 1 fetch via Proxy #{p_idx+1} ({p_name})...")
            try:
                resp = requests.get(proxy_url, headers=headers, timeout=12)
                if resp.status_code == 200 and len(resp.text) > 5000:
                    response = resp
                    log_success(f"Successfully fetched Shein HTML via {p_name}!")
                    break
                else:
                    log_warning(f"Proxy {p_name} returned status code {resp.status_code} or empty response.")
            except Exception as proxy_err:
                log_warning(f"Proxy {p_name} failed: {proxy_err}")
                
        # Final fallback to direct request if all proxies failed
        if response is None:
            log_warning("All proxies failed in Phase 1. Attempting direct request fallback...")
            try:
                resp = requests.get(url, headers=headers, timeout=12)
                if resp.status_code == 200:
                    response = resp
            except Exception as direct_err:
                log_warning(f"Direct request failed: {direct_err}")
                
        if response is not None and response.status_code == 200:
            html_content = response.text
            soup = BeautifulSoup(response.content, "html.parser")
            page_text = soup.get_text()
            
            scraped_price = ""
            
            # 1. Shein Specific Script JSON Parser
            if is_shein:
                for script in soup.find_all("script"):
                    script_content = script.text or ""
                    if script_content and any(x in script_content for x in ["goodsDetailV3SsrData", "goodsDetail", "goodsInfo", "productIntroData", "gbOrderDetailV3SsrData", "SsrData"]):
                        try:
                            var_idx = -1
                            for var_name in ["goodsDetailV3SsrData", "goodsDetail", "goodsInfo", "productIntroData", "gbOrderDetailV3SsrData", "SsrData"]:
                                var_idx = script_content.find(var_name)
                                if var_idx != -1:
                                    break
                            
                            start_idx = script_content.find("{", max(0, var_idx))
                            if start_idx != -1:
                                depth = 0
                                for idx in range(start_idx, len(script_content)):
                                    char = script_content[idx]
                                    if char == "{":
                                        depth += 1
                                    elif char == "}":
                                        depth -= 1
                                        if depth == 0:
                                            json_str = script_content[start_idx:idx+1]
                                            json_data = json.loads(json_str)
                                            candidates = find_prices_in_dict(json_data)
                                            prices_found = []
                                            for amt, sym in candidates:
                                                if sym in ["productId", "goodsId", "id", "skuId"]:
                                                    continue
                                                amt_clean = re.sub(r"[^\d.]", "", amt)
                                                if amt_clean:
                                                    try:
                                                        val_float = float(amt_clean)
                                                        if 0.1 < val_float < 10000.0:
                                                            curr = "$"
                                                            if "₹" in sym or "inr" in sym.lower():
                                                                curr = "₹"
                                                            elif "£" in sym or "gbp" in sym.lower():
                                                                curr = "£"
                                                            elif "€" in sym or "eur" in sym.lower():
                                                                curr = "€"
                                                            prices_found.append((val_float, curr))
                                                    except ValueError:
                                                        continue
                                            if prices_found:
                                                prices_found.sort(key=lambda x: x[0])
                                                min_val, min_curr = prices_found[0]
                                                scraped_price = f"{min_curr}{min_val:.2f}"
                                            break
                                if scraped_price:
                                    break
                        except Exception as parse_err:
                            log_warning(f"Error parsing Shein JSON in Phase 1: {parse_err}")
                            
            # 2. Direct Attribute Search
            if not scraped_price:
                attr_prices = []
                for el in soup.find_all(lambda tag: tag.has_attr('data-goods_ga_price') or tag.has_attr('data-price') or tag.has_attr('data-goods-price') or tag.has_attr('data-goods_price')):
                    val = el.get('data-goods_ga_price') or el.get('data-price') or el.get('data-goods-price') or el.get('data-goods_price')
                    if val:
                        val_str = str(val).strip()
                        amt_clean = re.sub(r"[^\d.]", "", val_str)
                        if amt_clean:
                            try:
                                val_float = float(amt_clean)
                                if 0.1 < val_float < 10000.0:
                                    curr = "$"
                                    if "₹" in page_text or "inr" in page_text.lower():
                                        curr = "₹"
                                    elif "£" in page_text or "gbp" in page_text.lower():
                                        curr = "£"
                                    elif "€" in page_text or "eur" in page_text.lower():
                                        curr = "€"
                                    attr_prices.append((val_float, curr))
                            except ValueError:
                                continue
                if attr_prices:
                    attr_prices.sort(key=lambda x: x[0])
                    scraped_price = f"{attr_prices[0][1]}{attr_prices[0][0]:.2f}"
                            
            # 3. Meta Selectors & itemprop prices (Prioritizing lowPrice first)
            if not scraped_price:
                for attr, val in [("itemprop", "lowPrice"), ("property", "og:price:amount"), ("property", "product:price:amount"), ("name", "twitter:data1")]:
                    meta = soup.find("meta", attrs={attr: val})
                    if meta and meta.get("content"):
                        amt_str = meta["content"].strip()
                        amt_clean = re.sub(r"[^\d.]", "", amt_str)
                        if amt_clean:
                            curr_meta = soup.find("meta", attrs={"property": "og:price:currency"}) or soup.find("meta", attrs={"property": "product:price:currency"})
                            curr = curr_meta["content"].strip() if (curr_meta and curr_meta.get("content")) else "$"
                            if curr == "USD" or curr == "$":
                                curr = "$"
                            elif curr == "INR":
                                curr = "₹"
                            scraped_price = f"{curr}{amt_clean}"
                            break
                            
            # 4. Direct Itemprop search on any tags (div, span, p, etc.)
            if not scraped_price:
                for el in soup.find_all(attrs={"itemprop": "lowPrice"}):
                    val = el.get("content") or el.get_text()
                    if val:
                        amt_clean = re.sub(r"[^\d.]", "", val)
                        if amt_clean and len(amt_clean) < 10:
                            scraped_price = f"${amt_clean}"
                            break
                            
            if not scraped_price:
                itemprop_prices = []
                for el in soup.find_all(attrs={"itemprop": "price"}):
                    val = el.get("content") or el.get_text()
                    if val:
                        amt_clean = re.sub(r"[^\d.]", "", val)
                        if amt_clean:
                            try:
                                val_float = float(amt_clean)
                                if 0.1 < val_float < 10000.0:
                                    itemprop_prices.append(val_float)
                            except ValueError:
                                continue
                if itemprop_prices:
                    min_price = min(itemprop_prices)
                    scraped_price = f"${min_price:.2f}"
                        
            if scraped_price:
                clean_num = re.sub(r"[^\d.]", "", scraped_price)
                try:
                    num_val = float(clean_num) if clean_num else 0.0
                except Exception:
                    num_val = 0.0
                if num_val > 0.0:
                    log_success(f"Phase 1 successfully extracted price from HTML: {scraped_price}")
                    return scraped_price, "active"
                    
    except Exception as e:
        log_warning(f"Phase 1 Parser failed: {e}. Falling back to Phase 2 Groq Vision.")
        
    # ---------------------------------------------------------
    # PHASE 2: SHEIN SPECIAL PLAYWRIGHT CLEAN-TEXT + GROQ TEXT LLM FLOW
    # ---------------------------------------------------------
    active_groq_key = get_active_groq_key()
    if is_shein and active_groq_key:
        log_step(f"Shein detected! Starting Playwright Clean-Text + Groq Text LLM flow for: {url}")
        
        try:
            from playwright.sync_api import sync_playwright
            page_text_raw = ""
            
            with sync_playwright() as p:
                proxy_config = None
                # For Shein or when HTML was not fetched, we must always use the premium proxy in Playwright if available!
                if (is_shein or not html_content) and active_key:
                    clean_key = active_key.replace("scrapedo:", "").replace("SCRAPEDO:", "")
                    is_scrape_do = len(active_key) != 32 or active_key.lower().startswith("scrapedo:")
                    if is_scrape_do:
                        proxy_config = {
                            "server": "http://api.scrape.do:8080",
                            "username": clean_key,
                            "password": "geo-us"
                        }
                    else:
                        proxy_config = {
                            "server": "http://proxy-server.scraperapi.com:8001",
                            "username": "scraperapi.country_code=us",
                            "password": clean_key
                        }
                
                browser = p.chromium.launch(headless=True, proxy=proxy_config)
                if is_shein:
                    # Emulate a premium desktop Chrome browser to load Shein desktop page stably!
                    context = browser.new_context(
                        viewport={"width": 1440, "height": 900},
                        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                        ignore_https_errors=True
                    )
                    # Inject targeting cookies to guarantee US/USD localized styling and avoid country redirects!
                    try:
                        context.add_cookies([
                            {"name": "cookieRegion", "value": "US", "domain": ".shein.com", "path": "/"},
                            {"name": "cookieLanguage", "value": "en", "domain": ".shein.com", "path": "/"},
                            {"name": "cookieCurrency", "value": "USD", "domain": ".shein.com", "path": "/"},
                            {"name": "site_code", "value": "us", "domain": ".shein.com", "path": "/"},
                            {"name": "language", "value": "en", "domain": ".shein.com", "path": "/"},
                            {"name": "region_code", "value": "US", "domain": ".shein.com", "path": "/"},
                            {"name": "currency", "value": "USD", "domain": ".shein.com", "path": "/"}
                        ])
                    except Exception as cookie_err:
                        log_warning(f"Failed to inject regional cookies: {cookie_err}")
                else:
                    context = browser.new_context(
                        viewport={"width": 1280, "height": 800},
                        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                        ignore_https_errors=True
                    )
                page = context.new_page()
                
                # Abort heavy images, ads, media, and tracking to load page 10x faster!
                try:
                    def handle_route(route):
                        route_url = route.request.url.lower()
                        if any(x in route_url for x in ["google-analytics", "doubleclick", "facebook.net", "analytics", "tracking", "adsystem", "quantserve", "hotjar"]):
                            route.abort()
                        elif any(route_url.endswith(ext) or (ext + "?") in route_url for ext in [".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".mp4", ".webm", ".ogg", ".woff", ".woff2", ".ttf"]):
                            route.abort()
                        else:
                            route.continue_()
                    page.route("**/*", handle_route)
                except Exception as route_err:
                    log_warning(f"Failed to setup fast page routing: {route_err}")
                    
                try:
                    from playwright_stealth import Stealth
                    Stealth().apply_stealth_sync(page)
                except Exception as e1:
                    try:
                        from playwright_stealth import stealth_sync
                        stealth_sync(page)
                    except Exception as e2:
                        try:
                            from playwright_stealth import stealth
                            stealth(page)
                        except Exception as e3:
                            log_warning(f"Stealth bypass failed: {e1} | {e2} | {e3}")
                
                if is_shein:
                    # For Shein, we ALWAYS navigate to the desktop URL directly under desktop emulation!
                    # This avoids mobile-to-homepage redirect loops and guarantees page stability.
                    log_step(f"Browser desktop page loading directly: {url}")
                    try:
                        page.goto(url, wait_until="load", timeout=30000)
                    except Exception as e:
                        log_warning(f"Page load timed out ({e}), settling...")
                        try:
                            page.wait_for_timeout(6000)
                        except Exception:
                            time.sleep(6)
                elif html_content:
                    log_step("Loading pre-fetched rendered HTML into Playwright...")
                    page.set_content(html_content)
                    page.wait_for_timeout(3000)
                else:
                    log_step("Browser page loading directly...")
                    try:
                        page.goto(url, wait_until="load", timeout=30000)
                    except Exception as e:
                        log_warning(f"Page load timed out ({e}), settling...")
                        try:
                            page.wait_for_timeout(5000)
                        except Exception:
                            time.sleep(5)
                
                # Auto scroll to ensure price is in viewport
                try:
                    page.evaluate("window.scrollTo(0, 400);")
                    time.sleep(5)
                except Exception as eval_err:
                    log_warning(f"Scroll skipped or context destroyed: {eval_err}")
                
                # Double check if we redirected to the homepage
                current_url = page.url.lower()
                is_prod_page = any(x in current_url for x in ["-p-", "product-", "goods-", "goods-p-", "/p/"])
                if not is_prod_page and "shein" in current_url:
                    browser.close()
                    raise Exception(f"Shein redirected the page to the homepage: {page.url}")

                # Double check if we hit a robot page/access denied
                page_text_raw = page.locator("body").inner_text()
                page_text = page_text_raw.lower()
                if len(page_text_raw.strip()) < 100:
                    browser.close()
                    raise Exception("Playwright loaded a blank or empty page.")
                    
                if any(x in page_text for x in ["robot check", "captcha", "enter the characters", "verify you are human", "access denied"]):
                    browser.close()
                    raise Exception("Playwright browser blocked by Captcha/Access Denied.")
                
                # Try to dismiss any active modals/popups (e.g. cookie banners, new user discounts)
                try:
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(500)
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(500)
                except Exception:
                    pass
                
                # Extract clean inner text from fully rendered page instead of taking screenshot!
                browser.close()
                
            # Clean and truncate page text to protect context limit while keeping all product info card data
            page_text_clean = page_text_raw[:12000].strip()
            
            # Call Groq Text Completions API with key rotation
            max_groq_retries = len(GROQ_API_KEYS)
            response = None
            
            for groq_attempt in range(max_groq_retries):
                active_groq_key = get_active_groq_key()
                if not active_groq_key:
                    break
                
                groq_headers = {
                    "Authorization": f"Bearer {active_groq_key}",
                    "Content-Type": "application/json"
                }
                
                payload = {
                    "model": get_active_groq_text_model(),
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are a highly precise streetwear e-commerce pricing expert.\n"
                                "Your task is to identify the absolute LOWEST price a customer can buy this item for from the raw page text.\n"
                                "Ignore crossed-out original retail prices.\n"
                                "Look for promotional coupon prices showing a dynamic discount (such as 'with code' or 'after discount' e.g., $6.64).\n"
                                "Return ONLY the price string with its currency symbol (e.g., $6.64 or US$6.64) and absolutely nothing else. No explanation, no extra words.\n"
                                "CRITICAL SAFEGUARD: Do NOT hallucinate or copy the example prices (like $29.99, $14.99, or $9.80) in this prompt. You MUST extract the actual, real price visible in the text. If no price is visible or the text is empty, return $0.00."
                            )
                        },
                        {
                            "role": "user",
                            "content": f"Product Page Raw Text Snippet:\n\n{page_text_clean}"
                        }
                    ],
                    "temperature": 0.0
                }
                
                log_step(f"Sending cleaned page text ({len(page_text_clean)} chars) to Groq Text LLM (Attempt {groq_attempt + 1}/{max_groq_retries})...")
                try:
                    resp = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=groq_headers, json=payload, timeout=30)
                    if resp.status_code in [429, 403]:
                        if rotate_groq_key():
                            continue
                    response = resp
                    break
                except Exception as groq_err:
                    log_warning(f"Groq API call attempt {groq_attempt + 1} failed: {groq_err}")
                    if rotate_groq_key():
                        continue
                    
            if response is not None and response.status_code == 200:
                result = response.json()
                scraped_price = result['choices'][0]['message']['content'].strip()
                match = re.search(r"([$₹£€]\s*\d+([.,]\d{2})?)", scraped_price)
                if match:
                    final_price = match.group(1).replace(" ", "")
                    clean_num = re.sub(r"[^\d.]", "", final_price)
                    try:
                        num_val = float(clean_num) if clean_num else 0.0
                    except Exception:
                        num_val = 0.0
                    if num_val > 0.0:
                        log_success(f"Groq Text LLM successfully extracted price: {final_price}")
                        return final_price, "active"
                    else:
                        log_warning(f"Groq Text LLM returned a zero or empty price ({final_price}). Falling back to Phase 3.")
                else:
                    log_warning(f"Groq Text LLM returned text but no valid price format: '{scraped_price}'")
            else:
                status_code = response.status_code if response is not None else "Unknown"
                log_warning(f"Groq Text completions failed with status: {status_code}")
                
        except Exception as text_llm_err:
            log_warning(f"Groq Text LLM flow failed: {text_llm_err}")
                


    try:
        import urllib.parse
        fetch_url = url
        active_key = get_active_scraper_key()
        
        # Route through premium ScraperAPI / Scrape.do if key is available
        if is_protected and active_key:
            max_retries = len(SCRAPER_API_KEYS)
            for attempt in range(max_retries):
                current_key = get_active_scraper_key()
                is_scrape_do = len(current_key) != 32 or current_key.lower().startswith("scrapedo:")
                clean_key = current_key.replace("scrapedo:", "").replace("SCRAPEDO:", "")
                
                if is_scrape_do:
                    fetch_url = f"https://api.scrape.do/?token={clean_key}&url={urllib.parse.quote(url)}"
                    if "shein." in url.lower():
                        # Force US proxies, JS rendering, and 5s load delay for Shein!
                        fetch_url += "&geo=us&render=true&customWait=5000"
                else:
                    fetch_url = f"https://api.scraperapi.com?api_key={clean_key}&url={urllib.parse.quote(url)}"
                    if "shein." in url.lower():
                        # Force US proxies, JS rendering, and 5s load delay for Shein!
                        fetch_url += "&country_code=us&render=true&wait_for=5000"
                    
                try:
                    # Premium rotating proxies need more time (up to 60s) to bypass strict anti-bot protections!
                    response = requests.get(fetch_url, headers=headers, timeout=60)
                    resp_lower = response.text.lower() if response.text else ""
                    has_block = any(term in resp_lower for term in ["captcha", "robot check", "slide to verify", "verify that you are a human", "verify you are human", "security check", "access denied", "please verify"])
                    
                    # ScraperAPI/Scrape.do returns 403 (exceeded limits), 429 (rate limits/concurrency), or 410 (gone) for exhausted keys
                    if response.status_code in [403, 410, 429] or has_block:
                        if rotate_scraper_key():
                            continue # Try again with the next key!
                    break
                except Exception as req_err:
                    if attempt < max_retries - 1:
                        rotate_scraper_key()
                        continue
                    raise req_err
        elif is_shein:
            # Route Shein requests through a free high-reputation CORS proxy to bypass geo-blocks!
            fetch_url = f"https://corsproxy.io/?url={urllib.parse.quote(url)}"
            response = requests.get(fetch_url, headers=headers, timeout=15)
        else:
            response = requests.get(fetch_url, headers=headers, timeout=15)
            
        # If the proxy/API failed or returned an error, fallback to direct request
        if (is_shein or (is_protected and active_key)) and response.status_code != 200:
            fetch_url = url
            response = requests.get(fetch_url, headers=headers, timeout=15)
            
        # 1. 404 Explicit dead link check
        if response.status_code == 404:
            return None, "not_found"
            
        # Automatic Proxy Fallback for 403, 503, or CAPTCHAs on other e-commerce sites!
        if response.status_code in [403, 503, 429] or "captcha" in response.text.lower() or "robot check" in response.text.lower() or "slide to verify" in response.text.lower() or "verify that you are a human" in response.text.lower():
            try:
                # A. Try AllOrigins raw proxy first (high reputation, raw content, no landing page redirects)
                proxy_url = f"https://api.allorigins.win/raw?url={urllib.parse.quote(url)}"
                proxy_response = requests.get(proxy_url, headers=headers, timeout=15)
                if proxy_response.status_code == 200 and "corsproxy" not in proxy_response.text.lower() and "allorigins" not in proxy_response.text.lower():
                    response = proxy_response
                else:
                    # B. Fallback to corsproxy.io
                    proxy_url = f"https://corsproxy.io/?url={urllib.parse.quote(url)}"
                    proxy_response = requests.get(proxy_url, headers=headers, timeout=15)
                    if proxy_response.status_code == 200 and "corsproxy" not in proxy_response.text.lower():
                        response = proxy_response
            except Exception:
                pass
                
        # Re-check status after fallback
        if response.status_code in [403, 429, 500, 503]:
            return None, "skipped"
            
        soup = BeautifulSoup(response.content, "html.parser")
        page_text = soup.get_text()
        
        # Shein specific diagnostic
        if "shein." in url.lower():
            title_str = (soup.title.text or soup.title.string or "No Title").strip()
            has_ssr = any(x in response.text for x in ["goodsDetailV3SsrData", "goodsDetail", "goodsInfo"])
            log_warning(f"Shein Diagnostic: Page Title: '{title_str}' | SSR Data: {has_ssr} | Status Code: {response.status_code}")
            
            # If the proxy returned its landing page, skip it safely!
            if "corsproxy" in title_str.lower() or "corsproxy" in page_text.lower() or "allorigins" in title_str.lower():
                log_warning("CORS Proxy landing page returned instead of Shein page. Skipping safely.")
                return None, "skipped"
            
        # --- A. BOT / CAPTCHA CHECK ---
        # If we hit Amazon's robot check or captcha page, skip stock block and keep active!
        if any(term in page_text.lower() for term in ["robot check", "captcha", "enter the characters you see below", "slide to verify", "verify that you are a human", "verify you are human", "security check", "access denied", "please verify"]):
            log_warning("Bot verification/CAPTCHA page detected. Skipping price update safely to avoid block.")
            return None, "active"
            
        # --- B. PRICE EXTRACTION ---
        scraped_price = ""
        
        # 1. Shein Specific Script JSON Parser
        if "shein." in url.lower():
            for script in soup.find_all("script"):
                script_content = script.text or ""
                if script_content and any(x in script_content for x in ["goodsDetailV3SsrData", "goodsDetail", "goodsInfo"]):
                    try:
                        # Find the first '{' after the variable name
                        var_idx = -1
                        for var_name in ["goodsDetailV3SsrData", "goodsDetail", "goodsInfo"]:
                            var_idx = script_content.find(var_name)
                            if var_idx != -1:
                                break
                        
                        start_idx = script_content.find("{", max(0, var_idx))
                        if start_idx != -1:
                            depth = 0
                            for idx in range(start_idx, len(script_content)):
                                char = script_content[idx]
                                if char == "{":
                                    depth += 1
                                elif char == "}":
                                    depth -= 1
                                    if depth == 0:
                                        json_str = script_content[start_idx:idx+1]
                                        json_data = json.loads(json_str)
                                        candidates = find_prices_in_dict(json_data)
                                        prices_found = []
                                        for amt, sym in candidates:
                                            if sym in ["productId", "goodsId", "id", "skuId"]:
                                                continue
                                            amt_clean = re.sub(r"[^\d.]", "", amt)
                                            if amt_clean:
                                                try:
                                                    val_float = float(amt_clean)
                                                    if 0.1 < val_float < 10000.0:
                                                        curr = "$"
                                                        if "₹" in sym or "inr" in sym.lower():
                                                            curr = "₹"
                                                        elif "£" in sym or "gbp" in sym.lower():
                                                            curr = "£"
                                                        elif "€" in sym or "eur" in sym.lower():
                                                            curr = "€"
                                                        prices_found.append((val_float, curr))
                                                except ValueError:
                                                    continue
                                        if prices_found:
                                            prices_found.sort(key=lambda x: x[0])
                                            min_val, min_curr = prices_found[0]
                                            scraped_price = f"{min_curr}{min_val:.2f}"
                                        break
                            if scraped_price:
                                break
                    except Exception as parse_err:
                        log_warning(f"Error parsing Shein JSON: {parse_err}")
        
        # 2. Direct Tracking Attribute Search (e.g. data-goods_ga_price in Shein, data-price)
        if not scraped_price:
            attr_prices = []
            for el in soup.find_all(lambda tag: tag.has_attr('data-goods_ga_price') or tag.has_attr('data-price') or tag.has_attr('data-goods-price') or tag.has_attr('data-goods_price')):
                val = el.get('data-goods_ga_price') or el.get('data-price') or el.get('data-goods-price') or el.get('data-goods_price')
                if val:
                    val_str = str(val).strip()
                    amt_clean = re.sub(r"[^\d.]", "", val_str)
                    if amt_clean:
                        try:
                            val_float = float(amt_clean)
                            if 0.1 < val_float < 10000.0:
                                curr = "$"
                                if "₹" in page_text or "INR" in page_text:
                                    curr = "₹"
                                elif "£" in page_text or "GBP" in page_text:
                                    curr = "£"
                                elif "€" in page_text or "EUR" in page_text:
                                    curr = "€"
                                attr_prices.append((val_float, curr))
                        except ValueError:
                            continue
            if attr_prices:
                attr_prices.sort(key=lambda x: x[0])
                scraped_price = f"{attr_prices[0][1]}{attr_prices[0][0]:.2f}"
        
        # 3. Platform Specific Selectors (Amazon)
        if not scraped_price and "amazon." in url.lower():
            for selector in ["span.a-price span.a-offscreen", "span#price_inside_buybox", "span.apexPriceToPay span.a-offscreen", "span#price"]:
                el = soup.select_one(selector)
                if el:
                    text = el.get_text().strip()
                    match = re.search(r"([$₹£€]\s*\d+([.,]\d{2})?)", text)
                    if match:
                        scraped_price = match.group(1).replace(" ", "")
                        break
                        
        # 4. General Meta Selectors (Prioritizing lowPrice first)
        if not scraped_price:
            for attr, val in [("itemprop", "lowPrice"), ("property", "og:price:amount"), ("property", "product:price:amount"), ("name", "twitter:data1"), ("itemprop", "price")]:
                meta = soup.find("meta", attrs={attr: val})
                if meta and meta.get("content"):
                    amt_str = meta["content"].strip()
                    amt_clean = re.sub(r"[^\d.]", "", amt_str)
                    if amt_clean:
                        curr_meta = soup.find("meta", attrs={"property": "og:price:currency"}) or soup.find("meta", attrs={"property": "product:price:currency"})
                        curr = curr_meta["content"].strip() if (curr_meta and curr_meta.get("content")) else "$"
                        if curr == "USD" or curr == "$":
                            curr = "$"
                        elif curr == "INR" or curr == "Rs.":
                            curr = "₹"
                        scraped_price = f"{curr}{amt_clean}"
                        break
                        
        # Direct Itemprop search on any tags (div, span, p, etc.)
        if not scraped_price:
            for el in soup.find_all(attrs={"itemprop": "lowPrice"}):
                val = el.get("content") or el.get_text()
                if val:
                    amt_clean = re.sub(r"[^\d.]", "", val)
                    if amt_clean and len(amt_clean) < 10:
                        scraped_price = f"${amt_clean}"
                        break
                        
        if not scraped_price:
            itemprop_prices = []
            for el in soup.find_all(attrs={"itemprop": "price"}):
                val = el.get("content") or el.get_text()
                if val:
                    amt_clean = re.sub(r"[^\d.]", "", val)
                    if amt_clean:
                        try:
                            val_float = float(amt_clean)
                            if 0.1 < val_float < 10000.0:
                                itemprop_prices.append(val_float)
                        except ValueError:
                            continue
            if itemprop_prices:
                min_price = min(itemprop_prices)
                scraped_price = f"${min_price:.2f}"
                    
        # 4. General JSON-LD
        if not scraped_price:
            json_ld = soup.find_all("script", type="application/ld+json")
            for script in json_ld:
                try:
                    data = json.loads(script.string)
                    offers = None
                    if isinstance(data, dict):
                        offers = data.get("offers")
                    elif isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict) and "offers" in item:
                                offers = item["offers"]
                                break
                    if offers:
                        if isinstance(offers, dict) and "price" in offers:
                            price_val = str(offers["price"])
                            curr_val = offers.get("priceCurrency", "$")
                            scraped_price = f"${price_val}" if curr_val == "USD" else f"₹{price_val}" if curr_val == "INR" else f"{curr_val} {price_val}"
                            break
                except Exception:
                    continue
                    
        # 5. General Regex price extraction
        if not scraped_price:
            price_elements = soup.find_all(class_=re.compile("price|amount|val", re.IGNORECASE))
            for el in price_elements:
                text = el.get_text().strip()
                match = re.search(r"([$₹£€]\s*\d+([.,]\d{2})?)", text)
                if match:
                    scraped_price = match.group(1).replace(" ", "")
                    break
                    
        # --- C. STOCK STATUS RESOLVER ---
        # RULE 1: If we successfully extracted a price, the item MUST be active!
        if scraped_price:
            return scraped_price, "active"
            
        # RULE 2: If no price is found, check semantic metadata for sold-out flags
        avail_meta = (
            soup.find("meta", attrs={"property": "og:availability"}) or 
            soup.find("meta", attrs={"name": "availability"}) or
            soup.find("meta", attrs={"property": "product:availability"})
        )
        if avail_meta and avail_meta.get("content"):
            content = avail_meta["content"].strip().lower()
            if "outofstock" in content or "out of stock" in content:
                return None, "sold_out"
                
        # RULE 3: Check JSON-LD offers availability
        json_ld = soup.find_all("script", type="application/ld+json")
        for script in json_ld:
            try:
                data = json.loads(script.string)
                offers = None
                if isinstance(data, dict):
                    offers = data.get("offers")
                elif isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and "offers" in item:
                            offers = item["offers"]
                            break
                if offers:
                    if isinstance(offers, dict) and "availability" in offers:
                        avail = str(offers["availability"]).lower()
                        if "outofstock" in avail or "out_of_stock" in avail:
                            return None, "sold_out"
            except Exception:
                continue
                
        # RULE 4: Fallback to text checks (first 5,000 chars)
        short_text = page_text[:5000].lower()
        sold_out_keywords = [
            "product not found", 
            "item no longer available", 
            "page not found",
            "this item is sold", 
            "sold out", 
            "out of stock",
            "currently unavailable",
            "temporarily out of stock"
        ]
        
        platform = extract_platform_from_link(url)
        if platform in ["Depop", "Grailed"]:
            if re.search(r"\bsold\b", short_text):
                return None, "sold_out"
        else:
            for keyword in sold_out_keywords:
                if keyword in short_text:
                    return None, "sold_out"
                    
        # If no sold out keywords matched and we just couldn't parse the price, keep active!
        return None, "active"
        
    except Exception as e:
        log_warning(f"URL connection issue: {e}. Skipping safely to avoid accidental block.")
        return None, "skipped"


def auto_install_playwright_browsers():
    if not get_active_groq_key():
        return
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
        log_step("Playwright Chromium browser is already installed.")
    except Exception:
        log_step("Playwright Chromium browser not found. Installing automatically now (Please wait)...")
        import subprocess
        try:
            subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
            log_success("Playwright Chromium browser installed successfully!")
        except Exception as install_err:
            log_error(f"Failed to auto-install Playwright browser: {install_err}")

# ---------------------------------------------------------
# MAIN ROTATING PRICE UPDATER
# ---------------------------------------------------------
def main():
    auto_install_playwright_browsers()
    print("=" * 60)
    print(" ZAALS AUTOMATED PRICE & STATUS UPDATER AGENT ".center(60, "■"))
    print("=" * 60)
    
    # Send telegram starting notification
    send_telegram_message("🔄 *ZAALS Price & Stock Update Sweep Shuru Ho Rahi Hai...*")
    
    client = get_google_sheet_client()
    if not client:
        log_error("Google Sheets authenticate nahi ho saka! Exiting.")
        send_telegram_message("❌ *ZAALS Price Updater Error:* Google Sheets authenticate nahi ho saka!")
        return
        
    try:
        log_step(f"Database connect ho raha hai: '{SPREADSHEET_NAME}'...")
        sheet = client.open(SPREADSHEET_NAME)
        worksheet = sheet.get_worksheet(0)
        all_records = worksheet.get_all_values()
        
        if len(all_records) < 1:
            log_success("Sheet me koi active product rows nahi hain. Exiting.")
            send_telegram_message("✅ *ZAALS Price Updater:* Sheet empty hai, check complete.")
            return
            
        # Dynamically find the headers row (check first 10 rows to handle any empty/title rows at the top!)
        header_row_idx = -1
        id_idx = -1
        title_idx = -1
        price_idx = -1
        link_idx = -1
        status_idx = -1
        headers = []
        
        # Keep track of what we saw for detailed diagnostics on failure
        seen_rows_diagnostics = []
        
        def clean_norm(s):
            return re.sub(r'[^a-z0-9]', '', s.strip().lower())
            
        for row_i in range(min(10, len(all_records))):
            raw_row = all_records[row_i]
            curr_headers = [h.strip() for h in raw_row]
            curr_headers_norm = [clean_norm(h) for h in raw_row]
            
            seen_rows_diagnostics.append(f"Row {row_i + 1}: {curr_headers}")
            
            temp_id = -1
            temp_title = -1
            temp_price = -1
            temp_link = -1
            temp_status = -1
            
            for idx, (raw_h, norm_h) in enumerate(zip(curr_headers, curr_headers_norm)):
                # 1. ID Column: matches 'id', 'sno', 's.no', 'srno', 'sr.no', 'serialno', 'serial', 'code', 'no'
                if any(x in norm_h for x in ["id", "sno", "srno", "serial", "code"]) or norm_h == "no" or norm_h == "n":
                    temp_id = idx
                # 2. Title Column: matches 'title', 'name', 'product'
                elif any(x in norm_h for x in ["title", "name", "product"]):
                    temp_title = idx
                # 3. Price Column: matches 'price', 'mrp', 'rate', 'cost'
                elif any(x in norm_h for x in ["price", "mrp", "rate", "cost"]):
                    temp_price = idx
                # 4. Link Column: matches 'link', 'url', 'affiliate', 'website', 'source', 'shein'
                elif any(x in norm_h for x in ["link", "url", "affiliate", "website", "source", "shein"]):
                    temp_link = idx
                # 5. Status Column: matches 'status'
                elif "status" in norm_h:
                    temp_status = idx
                    
            # Fallbacks: If we found a link and a price, but not a clear ID column, 
            # we can fallback to the first column (idx 0) as the ID/Serial number column!
            if temp_link != -1 and temp_price != -1 and temp_id == -1:
                if len(curr_headers) > 0:
                    temp_id = 0
                    
            if temp_id != -1 and temp_price != -1 and temp_link != -1:
                header_row_idx = row_i
                headers = curr_headers
                id_idx = temp_id
                title_idx = temp_title
                price_idx = temp_price
                link_idx = temp_link
                status_idx = temp_status
                break
                
        if header_row_idx == -1:
            log_error("Required headers (ID, Price, Affiliate Link) sheet me nahi mile!")
            log_error("--- SHEET DIAGNOSTICS (First few rows found): ---")
            for diag_line in seen_rows_diagnostics:
                log_error(diag_line)
            send_telegram_message("❌ *ZAALS Price Updater Error:* ID, Price, ya Link headers missing hain! Check logs on HF.")
            return
            
        # PROACTIVE Status Column Creation
        if status_idx == -1:
            log_warning("Status column sheet headers me nahi mili! Automatic append ki ja rahi hai...")
            worksheet.update_cell(header_row_idx + 1, len(headers) + 1, "Status")
            status_idx = len(headers)
            headers.append("status")
            log_success("Status header dynamically added to Column!")
            
        total_products = len(all_records) - (header_row_idx + 1)
        log_step(f"Total {total_products} products paye gaye. Verification shuru ho rahi hai...")
        
        updates_count = 0
        blocked_count = 0
        reactivated_count = 0
        unchanged_count = 0
        skipped_count = 0
        
        # Determine 10% progress milestones for telegram alerts
        milestone = max(1, total_products // 10)
        
        # Process rows from top to bottom (starting from first data row down to the last row)
        for idx, i in enumerate(range(header_row_idx + 2, len(all_records) + 1)):
            row_data = all_records[i - 1]
            prod_id = row_data[id_idx] if len(row_data) > id_idx else "UnknownID"
            prod_title = row_data[title_idx] if (title_idx != -1 and len(row_data) > title_idx) else "No Title"
            prod_price = row_data[price_idx] if len(row_data) > price_idx else ""
            prod_link = row_data[link_idx] if len(row_data) > link_idx else ""
            
            # Fetch current status, default to Active if column value is blank/missing
            prod_status = "active"
            if len(row_data) > status_idx and row_data[status_idx]:
                prod_status = row_data[status_idx].strip().lower()
                
            if not prod_link or not prod_link.startswith("http"):
                log_warning(f"Row {i} (ID: {prod_id}) me valid link nahi hai. Skipping.")
                skipped_count += 1
                continue
                
            log_step(f"Verifying ({idx + 1}/{total_products}) [{prod_id}] - {prod_title} (Current: {prod_status})...")
            
            live_price, status = scrape_live_price_and_status(prod_link)
            
            if status in ["not_found", "sold_out"]:
                # Item is dead or sold out! Do NOT delete, instead set Status to "Inactive" (Temporary Block)
                if prod_status != "inactive":
                    log_warning(f"🔴 ITEM BLOCKED (Out of Stock): '{prod_title}' ({status}). Status: Inactive set kiya ja raha hai...")
                    worksheet.update_cell(i, status_idx + 1, "Inactive")
                    blocked_count += 1
                else:
                    log_step("Item already Inactive. No change.")
                    unchanged_count += 1
                    
            elif status == "active":
                # Page is back active! Update price & set status to "Active" if it was Inactive (Reactivation!)
                price_to_update = live_price if live_price else prod_price
                
                # Check if we need to reactivate
                reactivated = False
                if prod_status == "inactive":
                    log_success(f"🟢 ITEM BACK IN STOCK (Reactivated!): '{prod_title}'. Status set to Active.")
                    worksheet.update_cell(i, status_idx + 1, "Active")
                    reactivated_count += 1
                    reactivated = True
                    
                # Verify price updates
                clean_old = re.sub(r"[^\d.]", "", prod_price)
                clean_new = re.sub(r"[^\d.]", "", price_to_update)
                
                if clean_old != clean_new and live_price:
                    log_success(f"🟢 PRICE CHANGED: '{prod_title}' ({prod_price} ➔ {live_price}). Updating cell...")
                    worksheet.update_cell(i, price_idx + 1, live_price)
                    if not reactivated:
                        updates_count += 1
                else:
                    log_step(f"🔵 Price unchanged ({prod_price}). Ok.")
                    if not reactivated:
                        unchanged_count += 1
            else:
                log_step("⚪ System connection skipped. Safe.")
                skipped_count += 1
                
            # Periodic 10% milestone progress telegram notifications
            current_processed = idx + 1
            if current_processed % milestone == 0 or current_processed == total_products:
                percentage = int((current_processed / total_products) * 100)
                send_telegram_message(
                    f"⏳ *ZAALS Price Update Progress:* {percentage}% completed\n"
                    f"Processed: `{current_processed}/{total_products}` items\n"
                    f"Updates: `{updates_count}` | Blocked: `{blocked_count}` | Reactivated: `{reactivated_count}`"
                )
                
            # Respectful delay between scraping requests (2 seconds)
            time.sleep(2)
            
        # Final Sweep Summary Report
        summary_msg = (
            f"✅ *ZAALS AUTOMATED SWEEP COMPLETED!*\n\n"
            f"*Final Report Details:*\n"
            f"✦ Total Items Scanned: `{total_products}`\n"
            f"🟢 Live Price Updates: `{updates_count}`\n"
            f"🔴 Temporarily Blocked (Out of Stock): `{blocked_count}`\n"
            f"⚡ Back in Stock (Reactivated): `{reactivated_count}`\n"
            f"🔵 Active & Unchanged: `{unchanged_count}`\n"
            f"⚪ Connection Skipped/Safe: `{skipped_count}`\n\n"
        )
        
        if _telegram_console_buffer:
            summary_msg += "*📋 Live Action Logs:*\n"
            # Join up to last 20 logs to avoid character overflow
            summary_msg += "\n".join(_telegram_console_buffer[-20:])
            summary_msg += "\n\n"
            
        summary_msg += "_Database successfully refreshed & synchronized!_"
        
        send_telegram_message(summary_msg)
        print("=" * 60)
        # Clear buffer to avoid double messages in Gradio forced loops
        _telegram_console_buffer.clear()
        print("AUTOMATED DATABASE REFRESH COMPLETED & TELEGRAM NOTIFICATION SENT!")
        print("=" * 60)
        
    except Exception as e:
        log_error(f"Price Updater failed: {e}")
        send_telegram_message(f"❌ *ZAALS Price Updater Fatal Error:* {e}")

if __name__ == "__main__":
    main()
