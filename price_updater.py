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

def register_callback(cb):
    global _gradio_callback
    _gradio_callback = cb

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
    if _gradio_callback:
        try:
            _gradio_callback(formatted)
        except Exception:
            pass

def log_error(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{timestamp}] [✘] ERROR: {msg}"
    print(formatted)
    if _gradio_callback:
        try:
            _gradio_callback(formatted)
        except Exception:
            pass

def log_warning(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{timestamp}] [⚠] WARNING: {msg}"
    print(formatted)
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

def scrape_live_price_and_status(url):
    """
    Fetches product URL and extracts:
    - price: live scraped price string
    - status: 'active', 'sold_out', or 'not_found'
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        
        # 1. 404 Explicit dead link check
        if response.status_code == 404:
            return None, "not_found"
            
        # If server returns error codes (like rate blocks 403, 503), do not delete/block! Skip safely.
        if response.status_code in [403, 429, 500, 503]:
            return None, "skipped"
            
        soup = BeautifulSoup(response.content, "html.parser")
        
        # --- A. SEMANTIC AVAILABILITY CHECK (JSON-LD & Meta Tags) ---
        explicit_instock = False
        
        # 1. Check Meta Tags
        avail_meta = (
            soup.find("meta", attrs={"property": "og:availability"}) or 
            soup.find("meta", attrs={"name": "availability"}) or
            soup.find("meta", attrs={"property": "product:availability"})
        )
        if avail_meta and avail_meta.get("content"):
            content = avail_meta["content"].strip().lower()
            if "outofstock" in content or "out of stock" in content:
                return None, "sold_out"
            if "instock" in content or "in stock" in content:
                explicit_instock = True
                
        # 2. Check JSON-LD Schema
        if not explicit_instock:
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
                            if "instock" in avail or "in_stock" in avail:
                                explicit_instock = True
                                break
                except Exception:
                    continue

        # --- B. SHORTENED TEXT AVAILABILITY CHECK ---
        # Limit search to first 5000 characters and ONLY run if we don't have explicit semantic InStock status!
        if not explicit_instock:
            page_text = soup.get_text()
            short_text = page_text[:5000].lower()
            
            # Check for typical "Sold Out" or "Product Removed" patterns
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
            
            # Specifically for Depop/Grailed: check if sold flags are prominent
            platform = extract_platform_from_link(url)
            if platform in ["Depop", "Grailed"]:
                if re.search(r"\bsold\b", short_text):
                    return None, "sold_out"
            else:
                # General out of stock or product not found keywords
                for keyword in sold_out_keywords:
                    if keyword in short_text:
                        return None, "sold_out"
                    
        # 3. Extract Price
        scraped_price = ""
        
        # A. Search meta elements
        meta_selectors = [
            ("property", "og:price:amount"),
            ("property", "product:price:amount"),
            ("name", "twitter:data1"),
            ("itemprop", "price")
        ]
        for attr, val in meta_selectors:
            meta = soup.find("meta", attrs={attr: val})
            if meta and meta.get("content"):
                scraped_price = meta["content"].strip()
                curr_meta = soup.find("meta", attrs={"property": "og:price:currency"}) or soup.find("meta", attrs={"property": "product:price:currency"})
                curr = curr_meta["content"].strip() if (curr_meta and curr_meta.get("content")) else "$"
                if curr == "USD" or curr == "$":
                    scraped_price = f"${scraped_price}"
                elif curr == "INR" or curr == "Rs.":
                    scraped_price = f"₹{scraped_price}"
                else:
                    scraped_price = f"{curr} {scraped_price}"
                break
        
        # B. Search JSON-LD
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
                    
        # C. Regex patterns on price tags
        if not scraped_price:
            price_elements = soup.find_all(class_=re.compile("price|amount|val", re.IGNORECASE))
            for el in price_elements:
                text = el.get_text().strip()
                match = re.search(r"([$₹£€]\s*\d+([.,]\d{2})?)", text)
                if match:
                    scraped_price = match.group(1).replace(" ", "")
                    break
                    
        if scraped_price:
            return scraped_price, "active"
        return None, "active"
        
    except Exception as e:
        log_warning(f"URL connection issue: {e}. Skipping safely to avoid accidental block.")
        return None, "skipped"


# ---------------------------------------------------------
# MAIN ROTATING PRICE UPDATER
# ---------------------------------------------------------
def main():
    print("=" * 60)
    print(" ZAALS AUTOMATED PRICE & STATUS UPDATER AGENT ".center(60, "■"))
    print("=" * 60)
    
    # Send telegram starting notification
    send_telegram_message("🔄 *ZAALS Price & Stock Update Sweep Shuru Ho Rahi Hai...*")
    
    client = get_google_sheet_client()
    if not client:
        log_error("Google Sheets authenticate nahi ho saka! Exiting.")
        send_telegram_message("❌ *ZAALS Price Updater Error:* Google Sheets authenticate nahi ho saka!")
        sys.exit(1)
        
    try:
        log_step(f"Database connect ho raha hai: '{SPREADSHEET_NAME}'...")
        sheet = client.open(SPREADSHEET_NAME)
        worksheet = sheet.get_worksheet(0) # Tab 1 (Sheet1)
        
        all_records = worksheet.get_all_values()
        if len(all_records) < 2:
            log_success("Sheet me koi active product rows nahi hain. Exiting.")
            send_telegram_message("✅ *ZAALS Price Updater:* Sheet empty hai, check complete.")
            sys.exit(0)
            
        headers = [h.strip().lower() for h in all_records[0]]
        
        # Resolve column indexes dynamically
        id_idx = -1
        title_idx = -1
        price_idx = -1
        link_idx = -1
        status_idx = -1
        
        for idx, h in enumerate(headers):
            if h == "id": id_idx = idx
            elif h == "title": title_idx = idx
            elif "price" in h: price_idx = idx
            elif "link" in h or "affiliate" in h: link_idx = idx
            elif "status" in h: status_idx = idx
            
        # PROACTIVE Status Column Creation
        if status_idx == -1:
            log_warning("Status column sheet headers me nahi mili! Automatic append ki ja rahi hai...")
            # gspread append header cell to the first row (headers)
            new_col_letter = chr(ord('A') + len(headers))
            # If columns exceed Z, we fall back to a safe gspread operation
            worksheet.update_cell(1, len(headers) + 1, "Status")
            status_idx = len(headers)
            headers.append("status")
            log_success("Status header dynamically added to Column!")
            
        if id_idx == -1 or price_idx == -1 or link_idx == -1:
            log_error("Required headers (ID, Price, AffiliateLink) sheet me nahi mile! Exiting.")
            send_telegram_message("❌ *ZAALS Price Updater Error:* ID, Price, ya Link headers missing hain!")
            sys.exit(1)
            
        total_products = len(all_records) - 1
        log_step(f"Total {total_products} products paye gaye. Verification shuru ho rahi hai...")
        
        updates_count = 0
        blocked_count = 0
        reactivated_count = 0
        unchanged_count = 0
        skipped_count = 0
        
        # Determine 10% progress milestones for telegram alerts
        # e.g., if 100 items, milestone = 10. Update triggers every 10 steps.
        milestone = max(1, total_products // 10)
        
        # Process rows in reverse order to keep sync clean
        for idx, i in enumerate(range(len(all_records), 1, -1)):
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
            f"_Database successfully refreshed & synchronized!_"
        )
        send_telegram_message(summary_msg)
        print("=" * 60)
        log_success("AUTOMATED DATABASE REFRESH COMPLETED & TELEGRAM NOTIFICATION SENT!")
        print("=" * 60)
        
    except Exception as e:
        log_error(f"Price Updater failed: {e}")
        send_telegram_message(f"❌ *ZAALS Price Updater Fatal Error:* {e}")

if __name__ == "__main__":
    main()
