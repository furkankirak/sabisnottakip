# -*- coding: utf-8 -*-
import json
import os
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")


# =========================
# ENV / SETTINGS
# =========================
SABIS_USERNAME = os.getenv("SABIS_USERNAME")
SABIS_PASSWORD = os.getenv("SABIS_PASSWORD")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

GROUP_CHAT_ID = os.getenv("GROUP_CHAT_ID") or None
GROUP_TOPIC_ID = os.getenv("GROUP_TOPIC_ID") or None

LOGIN_URL = (
    "https://login.sabis.sakarya.edu.tr/Account/Login"
    "?ReturnUrl=%2Fconnect%2Fauthorize%2Fcallback%3Fclient_id%3Dobs.sabis.sakarya.edu.tr"
    "%26redirect_uri%3Dhttps%253A%252F%252Fobs.sabis.sakarya.edu.tr%252Fsignin-oidc"
    "%26response_type%3Dcode%26scope%3Dopenid%2520obsapi%2520baumapi%2520offline_access"
    "%2520sauid%2520profile%26code_challenge%3Dtest%26code_challenge_method%3DS256"
    "%26response_mode%3Dform_post%26nonce%3Dtest%26state%3Dtest"
)
DERS_URL = "https://obs.sabis.sakarya.edu.tr/Ders"

STATE_FILE = Path("state_local.json")
DEBUG_HTML_FILE = Path("live_ders.html")

CHECK_INTERVAL_SECONDS = 10
MAX_RUNTIME_SECONDS = 5 * 60 * 60 + 50 * 60   # 5 saat 50 dk
HEADLESS = True


# =========================
# HELPERS
# =========================
def validate_config():
    required = {
        "SABIS_USERNAME": SABIS_USERNAME,
        "SABIS_PASSWORD": SABIS_PASSWORD,
        "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
        "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise RuntimeError(f"Eksik secret/env var: {', '.join(missing)}")


def temiz_yazi(text: str) -> str:
    return " ".join(text.split()).strip()


def duzelt_mojibake(text: str) -> str:
    try:
        return text.encode("latin1").decode("utf-8")
    except Exception:
        return text


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


def parse_sabis_html(html: str):
    soup = BeautifulSoup(html, "html.parser")
    results = []

    cards = soup.select("div.card.card-custom.card-stretch")

    for card in cards:
        code_el = card.select_one(".symbol-label")
        ders_kodu = temiz_yazi(code_el.get_text()) if code_el else ""

        name_el = card.select_one("a.text-dark.font-weight-bolder")
        ders_adi = temiz_yazi(name_el.get_text()) if name_el else ""
        ders_adi = duzelt_mojibake(ders_adi)

        group_el = card.select_one("span.text-muted.font-weight-bold.font-size-lg")
        grup = temiz_yazi(group_el.get_text()) if group_el else ""
        grup = duzelt_mojibake(grup)

        rows = card.select("table tbody tr")
        for row in rows:
            cols = row.select("td")
            if len(cols) < 3:
                continue

            oran = temiz_yazi(cols[0].get_text())
            calisma_tipi = temiz_yazi(cols[1].get_text())
            calisma_tipi = duzelt_mojibake(calisma_tipi)

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


def send_telegram_message(chat_id: str, text: str, message_thread_id=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": chat_id,
        "text": text,
    }
    if message_thread_id is not None:
        data["message_thread_id"] = int(message_thread_id)

    response = requests.post(url, data=data, timeout=30)
    if response.status_code != 200:
        print("Telegram hata:", response.status_code, response.text)
    response.raise_for_status()


def build_private_message(added, removed):
    lines = []

    if added:
        lines.append("Sabis Not Guncellemesi:")
        for item in sorted(added):
            ders_adi = duzelt_mojibake(item[1])
            calisma_tipi = duzelt_mojibake(item[4])
            notu = item[5]
            lines.append(f" - {ders_adi} {calisma_tipi} notu: {notu}")

    if removed:
        if lines:
            lines.append("")
        lines.append("Artik gorunmeyen eski notlar:")
        for item in sorted(removed):
            ders_adi = duzelt_mojibake(item[1])
            calisma_tipi = duzelt_mojibake(item[4])
            notu = item[5]
            lines.append(f" - {ders_adi} {calisma_tipi} notu: {notu}")

    return "\n".join(lines)


def build_group_message(added, removed):
    lines = []

    if added:
        lines.append("Sabis Not Guncellemesi:")
        for item in sorted(added):
            ders_adi = duzelt_mojibake(item[1])
            calisma_tipi = duzelt_mojibake(item[4])
            lines.append(f" - {ders_adi} {calisma_tipi} Sinav Notu Aciklandi")

    if removed:
        if lines:
            lines.append("")
        lines.append("Artik gorunmeyen eski notlar:")
        for item in sorted(removed):
            ders_adi = duzelt_mojibake(item[1])
            calisma_tipi = duzelt_mojibake(item[4])
            lines.append(f" - {ders_adi} {calisma_tipi} Sinav notu silindi")

    return "\n".join(lines)


def safe_goto(page, url: str, timeout: int = 60000):
    page.goto(url, wait_until="domcontentloaded", timeout=timeout)
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except PlaywrightTimeoutError:
        pass


def is_login_page(page) -> bool:
    try:
        url = page.url.lower()
        html = page.content().lower()
    except Exception:
        return True

    return (
        "account/login" in url
        or 'type="password"' in html
        or "giriş yap" in html
        or "giris yap" in html
    )


def is_ders_page(page) -> bool:
    try:
        html = page.content().lower()
    except Exception:
        return False

    return (
        "seçilen dersler" in html
        or "secilen dersler" in html
        or "card-stretch" in html
        or "/ders/grup/" in html
    )


def do_login(page):
    print("Login yapiliyor...")
    safe_goto(page, LOGIN_URL)

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
        try:
            if page.locator(sel).count() > 0:
                page.locator(sel).first.fill(SABIS_USERNAME)
                username_filled = True
                break
        except Exception:
            continue

    if not username_filled:
        raise RuntimeError("Kullanici adi inputu bulunamadi.")

    password_filled = False
    for sel in password_selectors:
        try:
            if page.locator(sel).count() > 0:
                page.locator(sel).first.fill(SABIS_PASSWORD)
                password_filled = True
                break
        except Exception:
            continue

    if not password_filled:
        raise RuntimeError("Sifre inputu bulunamadi.")

    login_button_selectors = [
        'button[type="submit"]',
        'input[type="submit"]',
        'button:has-text("Giriş Yap")',
        'button:has-text("Giris Yap")',
        'text=Giriş Yap',
        'text=Giris Yap',
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
        raise RuntimeError("Giris butonu bulunamadi.")

    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except PlaywrightTimeoutError:
        pass

    fresh_url = f"{DERS_URL}?t={int(time.time())}"
    safe_goto(page, fresh_url)

    if is_login_page(page):
        raise RuntimeError("Login basarisiz; hala login sayfasindasin.")

    if not is_ders_page(page):
        print("Uyari: ders sayfasi gibi gorunmuyor ama devam ediliyor.")


def fetch_ders_html(page) -> str:
    fresh_url = f"{DERS_URL}?t={int(time.time())}"
    safe_goto(page, fresh_url)

    if is_login_page(page):
        raise RuntimeError("Oturum dusmus, login sayfasina gidildi.")

    html = page.content()
    DEBUG_HTML_FILE.write_text(html, encoding="utf-8")
    return html


def main():
    validate_config()
    start_time = time.time()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context()

        context.set_extra_http_headers({
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        })

        page = context.new_page()

        try:
            do_login(page)

            while True:
                if time.time() - start_time > MAX_RUNTIME_SECONDS:
                    print("Maksimum calisma suresi doldu, cikiliyor...")
                    break

                old_items = load_state()

                try:
                    print("=" * 60)
                    print("Calisma zamani:", time.strftime("%Y-%m-%d %H:%M:%S"))

                    html = fetch_ders_html(page)
                    current_url = page.url
                    new_items = parse_sabis_html(html)

                    print("Final URL:", current_url)

                    if not old_items:
                        save_state(new_items)
                        print("Ilk calisma: state dosyasi olusturuldu, mesaj atilmadi.")
                    else:
                        added, removed = compare(old_items, new_items)

                        if not added and not removed:
                            print("Degisiklik yok.")
                        else:
                            private_message = build_private_message(added, removed)
                            if private_message:
                                print(private_message)
                                send_telegram_message(TELEGRAM_CHAT_ID, private_message)
                                print("Telegram ozel mesaj gonderildi.")

                            if GROUP_CHAT_ID:
                                group_message = build_group_message(added, removed)
                                if group_message:
                                    print(group_message)
                                    send_telegram_message(
                                        GROUP_CHAT_ID,
                                        group_message,
                                        message_thread_id=GROUP_TOPIC_ID
                                    )
                                    print("Telegram grup mesaji gonderildi.")

                            save_state(new_items)
                            print("state dosyasi guncellendi.")

                except KeyboardInterrupt:
                    print("Program kullanici tarafindan durduruldu.")
                    break

                except Exception as e:
                    print("HATA:", e)

                    try:
                        if is_login_page(page):
                            print("Login sayfasi algilandi, yeniden giris yapiliyor...")
                            do_login(page)
                        else:
                            print("Sayfa yenilenip tekrar denenecek...")
                            try:
                                do_login(page)
                            except Exception as relogin_error:
                                print("Yeniden login hatasi:", relogin_error)
                    except Exception as e2:
                        print("Oturum toparlama hatasi:", e2)

                print(f"{CHECK_INTERVAL_SECONDS} saniye bekleniyor...")
                time.sleep(CHECK_INTERVAL_SECONDS)

        finally:
            browser.close()


if __name__ == "__main__":
    main()