import sys
sys.stdout.reconfigure(encoding="utf-8")
import json
import os
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

SABIS_USERNAME = os.getenv("SABIS_USERNAME")
SABIS_PASSWORD = os.getenv("SABIS_PASSWORD")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GROUP_CHAT_ID = "-1003825108733"

LOGIN_URL = "https://login.sabis.sakarya.edu.tr/Account/Login?ReturnUrl=%2Fconnect%2Fauthorize%2Fcallback%3Fclient_id%3Dobs.sabis.sakarya.edu.tr%26redirect_uri%3Dhttps%253A%252F%252Fobs.sabis.sakarya.edu.tr%252Fsignin-oidc%26response_type%3Dcode%26scope%3Dopenid%2520obsapi%2520baumapi%2520offline_access%2520sauid%2520profile%26code_challenge%3Dtest%26code_challenge_method%3DS256%26response_mode%3Dform_post%26nonce%3Dtest%26state%3Dtest"
DERS_URL = "https://obs.sabis.sakarya.edu.tr/Ders"

STATE_FILE = Path("state.json")
DEBUG_HTML_FILE = Path("live_ders.html")


def temiz_yazi(text: str) -> str:
    return " ".join(text.split()).strip()


def parse_sabis_html(html: str):
    soup = BeautifulSoup(html, "html.parser")
    results = []

    cards = soup.select("div.card.card-custom.card-stretch")

    for card in cards:
        code_el = card.select_one(".symbol-label")
        ders_kodu = temiz_yazi(code_el.get_text()) if code_el else ""

        name_el = card.select_one("a.text-dark.font-weight-bolder")
        ders_adi = temiz_yazi(name_el.get_text()) if name_el else ""

        group_el = card.select_one("span.text-muted.font-weight-bold.font-size-lg")
        grup = temiz_yazi(group_el.get_text()) if group_el else ""

        rows = card.select("table tbody tr")
        for row in rows:
            cols = row.select("td")
            if len(cols) < 3:
                continue

            oran = temiz_yazi(cols[0].get_text())
            calisma_tipi = temiz_yazi(cols[1].get_text())
            not_text = temiz_yazi(cols[2].get_text())

            if not not_text:
                continue

            results.append({
                "ders_kodu": ders_kodu,
                "ders_adi": ders_adi,
                "grup": grup,
                "oran": oran,
                "calisma_tipi": calisma_tipi,
                "not": not_text,
            })

    return sorted(
        results,
        key=lambda x: (
            x["ders_kodu"],
            x["ders_adi"],
            x["grup"],
            x["oran"],
            x["calisma_tipi"],
            x["not"],
        )
    )


def load_state():
    if not STATE_FILE.exists():
        return []
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_state(items):
    STATE_FILE.write_text(
        json.dumps(items, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def compare(old_items, new_items):
    old_set = {
        (
            x["ders_kodu"],
            x["ders_adi"],
            x["grup"],
            x["oran"],
            x["calisma_tipi"],
            x["not"],
        )
        for x in old_items
    }

    new_set = {
        (
            x["ders_kodu"],
            x["ders_adi"],
            x["grup"],
            x["oran"],
            x["calisma_tipi"],
            x["not"],
        )
        for x in new_items
    }

    added = new_set - old_set
    removed = old_set - new_set
    return added, removed


def send_telegram_message(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    response = requests.post(
        url,
        data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text
        },
        timeout=30
    )
    response.raise_for_status()
def send_telegram_message2(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    response = requests.post(
        url,
        data={
            "chat_id": GROUP_CHAT_ID,
            "text": text
        },
        timeout=30
    )
    response.raise_for_status()

def validate_config():
    required = {
        "SABIS_USERNAME": SABIS_USERNAME,
        "SABIS_PASSWORD": SABIS_PASSWORD,
        "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
        "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID,
    }

    missing = [k for k, v in required.items() if not v]
    if missing:
        raise RuntimeError(f"Eksik ayar var: {', '.join(missing)}")

def login_and_fetch_html():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        try:
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)

            # Kullanıcı adı ve şifre kutularını birkaç olası selector ile bulmaya çalış
            username_selectors = [
                'input[name="Username"]',
                'input[name="username"]',
                'input[type="text"]',
                'input[type="email"]',
            ]
            password_selectors = [
                'input[name="Password"]',
                'input[name="password"]',
                'input[type="password"]',
            ]

            username_filled = False
            for sel in username_selectors:
                if page.locator(sel).count() > 0:
                    page.fill(sel, SABIS_USERNAME)
                    username_filled = True
                    break

            if not username_filled:
                raise RuntimeError("Kullanıcı adı inputu bulunamadı.")

            password_filled = False
            for sel in password_selectors:
                if page.locator(sel).count() > 0:
                    page.fill(sel, SABIS_PASSWORD)
                    password_filled = True
                    break

            if not password_filled:
                raise RuntimeError("Şifre inputu bulunamadı.")

            login_button_selectors = [
                'button[type="submit"]',
                'input[type="submit"]',
                'button:has-text("Giriş Yap")',
                'text=Giriş Yap',
            ]

            clicked = False
            for sel in login_button_selectors:
                try:
                    if page.locator(sel).count() > 0:
                        page.locator(sel).first.click()
                        clicked = True
                        break
                except Exception:
                    continue

            if not clicked:
                raise RuntimeError("Giriş butonu bulunamadı.")

            try:
                page.wait_for_load_state("networkidle", timeout=30000)
            except PlaywrightTimeoutError:
                pass

            page.goto(DERS_URL, wait_until="domcontentloaded", timeout=60000)
            try:
                page.wait_for_load_state("networkidle", timeout=30000)
            except PlaywrightTimeoutError:
                pass

            html = page.content()
            DEBUG_HTML_FILE.write_text(html, encoding="utf-8")

            current_url = page.url
            browser.close()

            if "Account/Login" in current_url or "Giriş Yap" in html:
                raise RuntimeError("Login başarısız görünüyor; hâlâ giriş sayfasındasın.")

            return html, current_url

        finally:
            try:
                browser.close()
            except Exception:
                pass


def main():
    validate_config()

    old_items = load_state()
    html, current_url = login_and_fetch_html()
    new_items = parse_sabis_html(html)

    print("Final URL:", current_url)
    print("Eski dolu not:", len(old_items))
    print("Yeni dolu not:", len(new_items))
    print("HTML kaydedildi:", DEBUG_HTML_FILE.name)

    if not old_items:
        save_state(new_items)
        print("Ilk calisma: state.json olusturuldu, mesaj atilmadi.")
        return

    added, removed = compare(old_items, new_items)

    if not added and not removed:
        print("Degisiklik yok.")
        return

    lines = []
    
    if added:
        lines.append("Sabis Not Güncellemesi:")
        for item in sorted(added):
            ders_adi = item[1]
            calisma_tipi = item[4]
            notu = item[5]
            lines.append(f" - {ders_adi} {calisma_tipi} notu: {notu}")
    if removed:
        if lines:
            lines.append("")
        lines.append("Artik gorunmeyen eski notlar:")
        for item in sorted(removed):
            ders_adi = item[1]
            calisma_tipi = item[4]
            notu = item[5]
            lines.append(f" - {ders_adi} {calisma_tipi} notu: {notu}")
    message = "\n".join(lines)

    print(message)
    send_telegram_message(message)
    print("Telegram mesaji gonderildi.")
    lines2 = []
    if added:
        lines2.append("Sabis Not Güncellemesi:")
        for item in sorted(added):
            ders_adi = item[1]
            calisma_tipi = item[4]
            lines2.append(f" - {ders_adi} {calisma_tipi} Sinav Notu Aciklandi")
    if removed:
        if lines2:
            lines2.append("")
        lines2.append("Artik gorunmeyen eski notlar:")
        for item in sorted(removed):
            ders_adi = item[1]
            calisma_tipi = item[4]
            lines2.append(f" - {ders_adi} {calisma_tipi} Sinav notu silindi")

    message2 = "\n".join(lines2)

    print(message2)
    send_telegram_message2(message2)
    print("Telegram2 mesaji gonderildi.")
    
    save_state(new_items)
    print("state.json guncellendi.")


if __name__ == "__main__":
    main()