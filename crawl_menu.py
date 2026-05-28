"""
全自動 Google 餐廳菜單爬蟲 v3.0
====================================
特點：
1. 完全自動化 - 無需手動點擊（除非失敗）
2. 自動啟動 Chrome 遠端調試
3. 智慧按鈕定位 - 多策略查找菜單按鈕
4. 搜尋不加「菜單」關鍵字
5. CSS 選擇器模組化 - 方便維護
"""

import asyncio
import base64
import json
import sys
import subprocess
import time
import socket
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import quote_plus
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# ============================================================================
# CSS 選擇器常數
# ============================================================================

class Selectors:
    """Google 搜尋結果頁面的 CSS 選擇器"""
    INFO_PANEL = "#rhs"
    MENU_BTN_CLASS = ".aep93e"
    MENU_BTN_ROLE = "[role='button']"
    MENU_BTN_DIV = "div[role='button']"
    MENU_ITEM_NAME = ".bWZFsc"
    MENU_ITEM_PRICE = ".OCfJnf"

class Config:
    """爬蟲配置"""
    CDP_PORT = 9222
    CDP_URL = f"http://localhost:{CDP_PORT}"
    WAIT_PAGE_LOAD = 2000
    WAIT_BTN_CLICK = 1500
    WAIT_DATA_CHECK = 500
    MAX_CHECK_ATTEMPTS = 10
    CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    ENABLE_MANUAL_ASSIST = False

# ============================================================================
# 資料結構
# ============================================================================

@dataclass
class MenuItem:
    name: str
    price: str = "價格未提供"

@dataclass
class Restaurant:
    name: str
    menu_items: list = None
    error: str = ""

# ============================================================================
# 輔助函數
# ============================================================================

def check_port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    """檢查端口是否開啟"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except:
        return False

def start_chrome_debug_mode():
    """啟動 Chrome 遠端調試模式"""
    print("\n[自動啟動] 嘗試啟動 Chrome 遠端調試模式...")
    
    if check_port_open('localhost', Config.CDP_PORT):
        print("  [OK] Chrome 遠端調試已在運行")
        return True
    
    try:
        # Use a stable profile so manual Google verification can persist across retries.
        user_data_dir = Path(__file__).resolve().parent / ".chrome_debug_profile"
        user_data_dir.mkdir(exist_ok=True)
        
        chrome_cmd = [
            Config.CHROME_PATH,
            f"--remote-debugging-port={Config.CDP_PORT}",
            f"--user-data-dir={str(user_data_dir)}",
            "--no-first-run",
            "--no-default-browser-check"
        ]
        
        print(f"  => 啟動 Chrome...")
        subprocess.Popen(
            chrome_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == 'win32' else 0
        )
        
        print("  => 等待 Chrome 就緒...")
        for i in range(30):  # 增加到 30 秒
            time.sleep(1)
            if check_port_open('localhost', Config.CDP_PORT):
                print(f"  [OK] Chrome 已啟動（耗時 {i+1} 秒）")
                time.sleep(2)  # 額外等待 Chrome 完全就緒
                return True
            if i % 5 == 4:  # 每 5 秒顯示一次
                print(f"     等待中... {i+1}/30 秒")
        
        print("\n  [FAIL] 啟動超時")
        print("  提示：Chrome 可能已啟動但端口未就緒，請手動檢查")
        return False
    
    except Exception as e:
        print(f"  [FAIL] 啟動失敗: {e}")
        return False

async def wait_with_feedback(page, delay_ms: int, message: str = None):
    """等待並顯示進度反饋"""
    if message:
        print(f"  => {message}")
    await page.wait_for_timeout(delay_ms)

async def find_and_click_menu_button(page) -> bool:
    """【Phase 2: 智慧尋找並點擊菜單】"""
    print("\n" + "="*70)
    print("【Phase 2】智慧尋找菜單按鈕")
    print("="*70)
    
    await page.wait_for_load_state('domcontentloaded', timeout=10000)
    await wait_with_feedback(page, 1500, "等待 JavaScript 渲染完成...")
    
    # 策略 1: 檢查右側資訊欄
    print("\n[策略 1] 檢查右側資訊欄...")
    try:
        rhs = page.locator(Selectors.INFO_PANEL)
        
        if await rhs.count() > 0:
            print("  [OK] 找到右側資訊欄 (#rhs)")
            
            menu_btn = rhs.locator(Selectors.MENU_BTN_CLASS).filter(has_text="菜單")
            if await menu_btn.count() > 0 and await menu_btn.first.is_visible():
                print("  [OK] 找到 .aep93e 菜單按鈕")
                await menu_btn.first.click()
                await wait_with_feedback(page, Config.WAIT_BTN_CLICK, "點擊成功，等待內容載入...")
                return True
            
            menu_btn = rhs.locator(Selectors.MENU_BTN_DIV).filter(has_text="菜單")
            if await menu_btn.count() > 0 and await menu_btn.first.is_visible():
                print("  [OK] 找到 div[role='button'] 菜單按鈕")
                await menu_btn.first.evaluate("el => el.click()")
                await wait_with_feedback(page, Config.WAIT_BTN_CLICK, "JS 點擊成功，等待內容載入...")
                return True
            
            print("  [FAIL] 資訊欄內未找到菜單按鈕")
        else:
            print("  [FAIL] 未找到右側資訊欄")
    except Exception as e:
        print(f"  [FAIL] 策略 1 失敗: {str(e)[:80]}")
    
    # 策略 2: 全頁面搜尋
    print("\n[策略 2] 全頁面搜尋 role=button...")
    try:
        menu_btns = page.locator(Selectors.MENU_BTN_ROLE).filter(has_text="菜單")
        
        if await menu_btns.count() > 0:
            for i in range(await menu_btns.count()):
                btn = menu_btns.nth(i)
                if await btn.is_visible():
                    print(f"  [OK] 找到第 {i+1} 個菜單按鈕")
                    await btn.scroll_into_view_if_needed()
                    await btn.click()
                    await wait_with_feedback(page, Config.WAIT_BTN_CLICK, "點擊成功，等待內容載入...")
                    return True
        
        print("  [FAIL] 未找到可見的菜單按鈕")
    except Exception as e:
        print(f"  [FAIL] 策略 2 失敗: {str(e)[:80]}")
    
    # 策略 3: 導航列
    print("\n[策略 3] 檢查導航列...")
    try:
        nav_menu = page.get_by_text("菜單", exact=True)
        
        if await nav_menu.count() > 0 and await nav_menu.first.is_visible():
            print("  [OK] 找到導航列的「菜單」連結")
            await nav_menu.first.click()
            await wait_with_feedback(page, Config.WAIT_BTN_CLICK, "點擊成功，等待內容載入...")
            return True
        
        print("  [FAIL] 導航列無菜單連結")
    except Exception as e:
        print(f"  [FAIL] 策略 3 失敗: {str(e)[:80]}")
    
    print("\n" + "="*70)
    print("[WARNING] 所有自動點擊策略均失敗")
    print("="*70)
    
    try:
        await page.screenshot(path='debug_no_menu_button.png')
        print("[SCREENSHOT] 已儲存除錯截圖: debug_no_menu_button.png")
    except:
        pass
    
    return False

async def check_menu_loaded(page) -> bool:
    """檢查菜單內容是否已載入"""
    print("\n[檢查] 偵測菜單內容...")
    
    for attempt in range(Config.MAX_CHECK_ATTEMPTS):
        count = await page.locator(Selectors.MENU_ITEM_NAME).count()
        
        if count > 0:
            print(f"  [OK] 已偵測到 {count} 個菜單項目")
            return True
        
        dots = "." * (attempt + 1)
        print(f"  => 等待中{dots} ({attempt + 1}/{Config.MAX_CHECK_ATTEMPTS})")
        await page.wait_for_timeout(Config.WAIT_DATA_CHECK)
    
    print("  [FAIL] 未偵測到菜單內容")
    return False

async def extract_menu_data(page, restaurant_name: str) -> Restaurant:
    """【Phase 3: 資料抓取】"""
    print("\n" + "="*70)
    print("【Phase 3】資料抓取")
    print("="*70)
    
    menu_items = []
    seen_names = set()
    
    try:
        await page.wait_for_selector(Selectors.MENU_ITEM_NAME, timeout=10000)
        
        name_elements = page.locator(Selectors.MENU_ITEM_NAME)
        item_count = await name_elements.count()
        
        print(f"\n開始抓取 {item_count} 個菜單項目...")
        print("-" * 70)
        
        for i in range(item_count):
            try:
                name_elem = name_elements.nth(i)
                name = await name_elem.inner_text()
                name = name.strip()
                
                if not name or len(name) < 2 or name in seen_names:
                    continue
                
                price = "價格未提供"
                
                try:
                    parent = name_elem.locator('xpath=..')
                    next_sibling = parent.locator('xpath=following-sibling::*[1]')
                    
                    if await next_sibling.count() > 0:
                        class_name = await next_sibling.get_attribute('class')
                        
                        if class_name and 'OCfJnf' in class_name:
                            aria_label = await next_sibling.get_attribute('aria-label')
                            if aria_label:
                                price = aria_label.strip().rstrip('.')
                            else:
                                price_text = await next_sibling.inner_text()
                                if price_text:
                                    price = price_text.strip()
                except:
                    try:
                        all_prices = page.locator(Selectors.MENU_ITEM_PRICE)
                        if i < await all_prices.count():
                            price_elem = all_prices.nth(i)
                            aria_label = await price_elem.get_attribute('aria-label')
                            if aria_label:
                                price = aria_label.strip().rstrip('.')
                            else:
                                price_text = await price_elem.inner_text()
                                if price_text:
                                    price = price_text.strip()
                    except:
                        pass
                
                menu_items.append(MenuItem(name=name, price=price))
                seen_names.add(name)
                
                print(f"  {len(menu_items):3d}. {name[:45]:45s} │ {price}")
                
            except Exception as e:
                continue
        
        print("-" * 70)
        print(f"[SUCCESS] 成功抓取 {len(menu_items)} 道菜\n")
        
        return Restaurant(name=restaurant_name, menu_items=menu_items)
    
    except PlaywrightTimeout:
        print("[ERROR] 等待菜單元素超時")
        return Restaurant(name=restaurant_name, menu_items=[])
    except Exception as e:
        print(f"[ERROR] 抓取失敗: {e}")
        import traceback
        traceback.print_exc()
        return Restaurant(name=restaurant_name, menu_items=[])


def _extract_json_object(text: str) -> Any:
    """Extract a JSON object from model output."""
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except Exception:
            return None
    return None


def _normalize_vision_menu_items(raw_items: Any) -> List[MenuItem]:
    """Convert model JSON into crawler MenuItem objects."""
    if not isinstance(raw_items, list):
        return []

    def clean_price(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return "價格未標示"
        if "未" in text or "時價" in text:
            return "價格未標示"
        match = re.search(r"\$?\s*(\d{2,5})(?:\.0+)?", text.replace(",", ""))
        if match:
            return match.group(1)
        return text

    items: List[MenuItem] = []
    seen = set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or raw.get("dish") or "").strip()
        if len(name) < 2 or name in seen:
            continue
        price = clean_price(raw.get("price") or raw.get("amount"))
        items.append(MenuItem(name=name, price=price))
        seen.add(name)
    return items


MIN_VISION_MENU_ITEMS = 8


class GoogleVerificationRequired(RuntimeError):
    """Google asked for human verification; do not continue automation."""


def is_usable_menu_result(items: List[MenuItem]) -> bool:
    return len(items) >= MIN_VISION_MENU_ITEMS


async def is_google_verification_page(page) -> bool:
    url = (page.url or "").lower()
    if any(marker in url for marker in ("google.com/sorry", "captcha", "recaptcha")):
        return True

    try:
        body_text = (await page.locator("body").inner_text(timeout=2000)).lower()
    except Exception:
        body_text = ""

    verification_markers = (
        "unusual traffic",
        "captcha",
        "recaptcha",
        "not a robot",
        "verify you are human",
        "人機驗證",
        "驗證",
        "確認你不是機器人",
    )
    return any(marker in body_text for marker in verification_markers)


async def stop_if_google_verification(page):
    if await is_google_verification_page(page):
        raise GoogleVerificationRequired(
            "Google 要求人機驗證，請在 Chrome 完成驗證後重新爬取。"
        )


async def open_google_image_preview(page, candidate: Dict[str, Any]) -> str:
    """Click a Google Images result and return the largest preview image URL."""
    index = candidate.get("index")
    if not isinstance(index, int):
        return ""

    try:
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(200)
        except Exception:
            pass
        images = page.locator("img")
        if index >= await images.count():
            return ""
        await images.nth(index).scroll_into_view_if_needed(timeout=3000)
        await images.nth(index).click(timeout=5000)
        await page.wait_for_timeout(1200)
        return await page.evaluate(
            """() => {
                const imgs = Array.from(document.images).map((img) => {
                    const rect = img.getBoundingClientRect();
                    const url = img.currentSrc || img.src || img.getAttribute('data-src') || '';
                    return {
                        url,
                        width: img.naturalWidth || img.width || rect.width || 0,
                        height: img.naturalHeight || img.height || rect.height || 0,
                        renderedWidth: rect.width || 0,
                        renderedHeight: rect.height || 0
                    };
                }).filter((item) =>
                    item.url.startsWith('http') &&
                    item.renderedWidth >= 250 &&
                    item.renderedHeight >= 160
                );
                imgs.sort((a, b) =>
                    (b.width * b.height + b.renderedWidth * b.renderedHeight) -
                    (a.width * a.height + a.renderedWidth * a.renderedHeight)
                );
                return imgs[0] ? imgs[0].url : '';
            }"""
        )
    except Exception as e:
        print(f"  [WARN] 無法開啟圖片預覽: {e}")
        return ""


async def find_menu_image_candidates(page, restaurant_name: str) -> List[Dict[str, Any]]:
    """Search Google Images for menu-like image URLs."""
    print("\n" + "=" * 70)
    print("[Fallback] 搜尋菜單圖片")
    print("=" * 70)

    queries = [
        f"{restaurant_name} photos",
        f"{restaurant_name} 菜單",
        f"{restaurant_name} 價格",
    ]
    keywords = ("菜單", "menu", "價目表", "餐牌", "價格", "price", "相片", "photo")
    candidates: List[Dict[str, Any]] = []
    seen_urls = set()

    for query in queries:
        search_url = f"https://www.google.com/search?tbm=isch&q={quote_plus(query)}"
        print(f"  => {search_url}")
        try:
            await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2500)
            await stop_if_google_verification(page)
        except GoogleVerificationRequired:
            raise
        except Exception as e:
            print(f"  [WARN] 圖片搜尋失敗: {e}")
            continue

        try:
            page_candidates = await page.evaluate(
                """() => {
                    const originalUrl = (el) => {
                        const a = el.closest('a');
                        const href = a ? a.href || '' : '';
                        if (!href) return '';
                        try {
                            const parsed = new URL(href);
                            return parsed.searchParams.get('imgurl') ||
                                   parsed.searchParams.get('mediaurl') ||
                                   parsed.searchParams.get('url') ||
                                   '';
                        } catch {
                            return '';
                        }
                    };
                    const cardText = (img) => {
                        const card = img.closest('div[data-ri], div[jscontroller], a, div') || img.parentElement;
                        return [
                            img.alt || '',
                            img.title || '',
                            img.getAttribute('aria-label') || '',
                            card ? card.innerText || '' : '',
                            card ? card.getAttribute('aria-label') || '' : ''
                        ].join(' ');
                    };
                    return Array.from(document.images).map((img, index) => {
                        const rect = img.getBoundingClientRect();
                        return {
                            url: originalUrl(img) || img.currentSrc || img.src || img.getAttribute('data-src') || '',
                            text: cardText(img),
                            width: img.naturalWidth || img.width || Math.round(rect.width) || 0,
                            height: img.naturalHeight || img.height || Math.round(rect.height) || 0,
                            renderedWidth: Math.round(rect.width),
                            renderedHeight: Math.round(rect.height),
                            left: Math.round(rect.left),
                            top: Math.round(rect.top),
                            index
                        };
                    }).filter((item) =>
                        item.url.startsWith('http') &&
                        item.renderedWidth >= 80 &&
                        item.renderedHeight >= 60
                    );
                }"""
            )
        except Exception as e:
            print(f"  [WARN] 無法擷取圖片候選: {e}")
            continue

        query_candidates: List[Dict[str, Any]] = []
        for item in page_candidates:
            url = str(item.get("url", ""))
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            text = str(item.get("text", ""))
            score = 0
            lower_text = text.lower()
            lower_url = url.lower()
            if any(k.lower() in lower_text for k in keywords):
                score += 5
            if any(k.lower() in lower_url for k in keywords):
                score += 2
            if "facebook" in lower_text or "facebook" in lower_url:
                score += 3
            rendered_width = int(item.get("renderedWidth", 0) or 0)
            rendered_height = int(item.get("renderedHeight", 0) or 0)
            natural_width = int(item.get("width", 0) or 0)
            natural_height = int(item.get("height", 0) or 0)
            aspect = (rendered_width or natural_width or 1) / max(rendered_height or natural_height or 1, 1)
            if rendered_width >= 240 and rendered_height >= 150:
                score += 3
            if 1.2 <= aspect <= 2.6:
                score += 2
            if int(item.get("top", 9999) or 9999) < 700:
                score += 2
            score += min(natural_width // 500, 3)
            score += min(natural_height // 400, 3)
            candidate = {
                "url": url,
                "text": text[:300],
                "score": score,
                "width": natural_width,
                "height": natural_height,
                "index": item.get("index"),
            }
            candidates.append(candidate)
            query_candidates.append(candidate)

        for candidate in sorted(query_candidates, key=lambda x: x.get("score", 0), reverse=True)[:5]:
            large_url = await open_google_image_preview(page, candidate)
            if large_url and large_url not in seen_urls:
                seen_urls.add(large_url)
                enriched = dict(candidate)
                enriched["url"] = large_url
                enriched["score"] = int(enriched.get("score", 0)) + 6
                candidates.append(enriched)

    candidates.sort(key=lambda x: x.get("score", 0), reverse=True)
    print(f"  [INFO] 找到 {len(candidates)} 張候選圖片")
    return candidates


def verify_menu_items_with_vision(
    vision_chat_func,
    base_prompt: str,
    image_url: str,
    restaurant_name: str,
    items: List[MenuItem],
) -> List[MenuItem]:
    """Ask the vision model to verify names/prices against the same image."""
    draft = {
        "menu_items": [
            {"name": item.name, "price": item.price}
            for item in items
        ]
    }
    verify_prompt = f"""{base_prompt}

Now verify this draft against the same image:
{json.dumps(draft, ensure_ascii=False)}

Fix wrong item names, wrong prices, and missing visible items.
Especially check prices carefully: do not output $300 for an item whose printed price is $230 or $330.
Handwritten or temporary marks such as blue X marks, red strike-throughs, circles, stickers, or pen marks must not remove a printed menu item.
Return the corrected strict JSON only.
"""
    try:
        response = vision_chat_func(verify_prompt, image_url=image_url, timeout=180.0)
        parsed = _extract_json_object(response)
        if not isinstance(parsed, dict):
            return items
        verified = _normalize_vision_menu_items(parsed.get("menu_items"))
        return verified or items
    except Exception as e:
        print(f"  [Vision] 二次校對失敗，保留第一輪結果: {e}")
        return items


async def parse_menu_from_search_screenshots(page, restaurant_name: str, vision_chat_func) -> Restaurant:
    """Fallback: let the vision model inspect Google Images result screenshots directly."""
    print("\n[Fallback] 直接分析 Google 圖片搜尋結果畫面")
    queries = [
        f"{restaurant_name} photos",
        f"{restaurant_name} 菜單",
        f"{restaurant_name} 價格",
    ]

    prompt = f"""This image is a screenshot of Google Images search results for "{restaurant_name}".
Your task is to find any visible restaurant menu image within the screenshot and extract menu items from that visible menu image.

Return strict JSON only:
{{"menu_items":[{{"name":"exact Chinese item name","price":"integer price only, e.g. 120"}}]}}

How to decide which visible image is the menu:
- It may look like a printed price board, menu board, red/black table, dense text grid, or a photo of a menu page.
- It may come from Facebook, Google reviews, blog posts, or restaurant photos.
- It does NOT need to have the word "菜單" or "menu" near it.
- Ignore storefront photos, food photos, logos, interiors, people photos, UberEats/Foodpanda product cards, and single-dish product photos unless a full menu with many item names and prices is readable.

Extraction rules:
- Extract only from the visible menu image in the screenshot.
- Use printed item names and nearby printed prices.
- Handwritten or temporary marks can mean sold out that day; do not remove printed items because of them.
- Return only digits for prices; if unreadable use "價格未標示".
- If fewer than 8 readable menu items are visible, return {{"menu_items":[]}}.
- If no readable menu image is visible, return {{"menu_items":[]}}.
"""

    for query in queries:
        search_url = f"https://www.google.com/search?tbm=isch&q={quote_plus(query)}"
        print(f"  [Screenshot] {search_url}")
        try:
            await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2500)
            await stop_if_google_verification(page)
            visible_count = await page.evaluate(
                """() => Array.from(document.images).filter((img) => {
                    const rect = img.getBoundingClientRect();
                    return rect.width >= 80 && rect.height >= 60;
                }).length"""
            )
            if visible_count < MIN_VISION_MENU_ITEMS:
                print(f"  [Screenshot] 可見圖片只有 {visible_count} 張，略過這個 query")
                continue
            try:
                await page.evaluate("window.scrollTo(0, 0)")
            except Exception:
                pass
            png = await page.screenshot(type="png", full_page=False)
            data_url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
            response = vision_chat_func(prompt, image_url=data_url, timeout=180.0)
            parsed = _extract_json_object(response)
            if not isinstance(parsed, dict):
                continue
            items = _normalize_vision_menu_items(parsed.get("menu_items"))
            if is_usable_menu_result(items):
                print(f"  [Screenshot Vision] 成功從搜尋結果畫面解析 {len(items)} 道菜")
                return Restaurant(name=restaurant_name, menu_items=items)
            if items:
                print(
                    f"  [Screenshot Vision] 只解析到 {len(items)} 道菜，低於 {MIN_VISION_MENU_ITEMS} 項門檻，繼續嘗試"
                )
        except GoogleVerificationRequired:
            raise
        except Exception as e:
            print(f"  [Screenshot Vision] 解析失敗: {e}")

    return Restaurant(name=restaurant_name, menu_items=[])


async def parse_menu_from_images(page, restaurant_name: str) -> Restaurant:
    """Fallback: find menu images and parse them with the configured vision model."""
    try:
        src_dir = Path(__file__).resolve().parent / "src"
        if src_dir.exists() and str(src_dir) not in sys.path:
            sys.path.insert(0, str(src_dir))
        from ollama_fuc import vision_chat
    except Exception as e:
        print(f"  [ERROR] 無法匯入 vision_chat: {e}")
        return Restaurant(name=restaurant_name, menu_items=[])

    screenshot_result = await parse_menu_from_search_screenshots(page, restaurant_name, vision_chat)
    if screenshot_result.menu_items:
        return screenshot_result

    candidates = await find_menu_image_candidates(page, restaurant_name)
    if not candidates:
        return Restaurant(name=restaurant_name, menu_items=[])

    prompt = f"""You are doing high-precision OCR transcription of a restaurant menu image for "{restaurant_name}".
Return strict JSON only, no markdown and no explanation.
Schema:
{{"menu_items":[{{"name":"exact Chinese item name","price":"integer price only, e.g. 330"}}]}}

Critical rules:
- Transcribe every visible menu item and its nearby printed price.
- Handwritten or temporary markings may indicate the item was sold out or unavailable only on the day the photo was taken. These markings can be blue X marks, red strike-throughs, circles, stickers, pen marks, or other manual annotations. DO NOT remove the printed menu item because of these markings. Still extract the printed item name and printed price.
- Use the price printed immediately next to the item name. Do not infer or adjust prices.
- Prices are usually shown as $230, $300, $430, etc. Return only the digits, without "$" and without ".00".
- Keep Chinese item names exactly as shown. Do not translate.
- Ignore weight, doneness, origin, checkboxes, descriptions, and availability marks.
- Ignore UberEats/Foodpanda product cards, food photos, storefront photos, logos, and single-dish pictures; they are not complete menus.
- Do not invent items that are not visible.
- If fewer than 8 readable menu items are visible, return {{"menu_items":[]}}.
- If an item name is visible but the price cannot be read, use "價格未標示".
- For wide two-page menus, scan left-to-right and top-to-bottom across the entire image.
"""

    for idx, candidate in enumerate(candidates[:3], start=1):
        image_url = candidate["url"]
        if "encrypted-tbn" in image_url:
            large_url = await open_google_image_preview(page, candidate)
            if not large_url or "encrypted-tbn" in large_url:
                print(f"  [Vision] 第 {idx} 張仍是 Google 縮圖，略過")
                continue
            image_url = large_url

        print(f"  [Vision] 嘗試解析第 {idx} 張菜單圖片: {image_url[:120]}")
        try:
            response = vision_chat(prompt, image_url=image_url, timeout=180.0)
            parsed = _extract_json_object(response)
            if not isinstance(parsed, dict):
                print("  [Vision] 回覆不是 JSON，跳過")
                continue
            items = _normalize_vision_menu_items(parsed.get("menu_items"))
            if items:
                items = verify_menu_items_with_vision(vision_chat, prompt, image_url, restaurant_name, items)
                if is_usable_menu_result(items):
                    print(f"  [Vision] 成功解析 {len(items)} 道菜")
                    return Restaurant(name=restaurant_name, menu_items=items)
                print(
                    f"  [Vision] 只解析到 {len(items)} 道菜，低於 {MIN_VISION_MENU_ITEMS} 項門檻，略過"
                )
                continue
            print("  [Vision] JSON 中沒有有效菜單項目，跳過")
        except Exception as e:
            print(f"  [Vision] 圖片解析失敗: {e}")

    return Restaurant(name=restaurant_name, menu_items=[])

async def crawl_google_menu(restaurant_name: str) -> Restaurant:
    """【主流程】全自動爬取 Google 餐廳菜單"""
    
    print("\n" + "="*70)
    print("全自動 Google 餐廳菜單爬蟲 v3.0")
    print("="*70)
    print(f"目標餐廳: {restaurant_name}")
    print(f"CDP 端口: {Config.CDP_PORT}")
    print("="*70)
    
    async with async_playwright() as p:
        try:
            # ================================================================
            # Phase 1: 連接 Chrome & 搜尋
            # ================================================================
            print("\n【Phase 1】連接 Chrome 並搜尋餐廳")
            print("="*70)
            
            # 確保 Chrome 遠端調試模式已啟動
            if not check_port_open('localhost', Config.CDP_PORT):
                print("[自動化] Chrome 遠端調試未運行，嘗試自動啟動...")
                if not start_chrome_debug_mode():
                    print("\n[ERROR] 無法自動啟動 Chrome")
                    print("\n請手動啟動 Chrome 遠端調試模式：")
                    print(f"  步驟 1: 關閉所有 Chrome 視窗")
                    print(f"  步驟 2: 在命令提示字元執行：")
                    print(f'    cd "C:\\Program Files\\Google\\Chrome\\Application"')
                    print(f'    chrome.exe --remote-debugging-port={Config.CDP_PORT}')
                    print("\n  或者直接執行：")
                    print(f"  '{Config.CHROME_PATH}' --remote-debugging-port={Config.CDP_PORT}")
                    return None
            else:
                print("[自動化] Chrome 遠端調試已在運行")
            
            # 連接到本機 Chrome
            print(f"\n[1/3] 連接到 Chrome (CDP: {Config.CDP_URL})...")
            try:
                browser = await p.chromium.connect_over_cdp(Config.CDP_URL)
                print("  [OK] 連接成功")
            except Exception as e:
                print(f"  [FAIL] 連接失敗: {e}")
                print("\n可能原因：")
                print("  1. Chrome 啟動中但尚未完全就緒")
                print("  2. 端口被其他程式佔用")
                print("  3. 防火牆阻擋連接")
                print("\n建議：請手動啟動 Chrome 後重試")
                return None
            
            # 取得或創建頁面
            print("\n[2/3] 取得瀏覽器頁面...")
            contexts = browser.contexts
            if not contexts:
                print("  [FAIL] 沒有可用的瀏覽器上下文")
                return None
            
            if contexts[0].pages:
                page = contexts[0].pages[0]
                print("  [OK] 使用現有頁面")
            else:
                page = await contexts[0].new_page()
                print("  [OK] 創建新頁面")
            
            # 搜尋餐廳（不加「菜單」關鍵字）
            print(f"\n[3/3] 搜尋餐廳: {restaurant_name}")
            print("  [NOTE] 搜尋參數不包含「菜單」關鍵字")
            
            search_url = f"https://www.google.com/search?q={restaurant_name}"
            print(f"  => 導航至: {search_url}")
            
            try:
                await page.goto(search_url, wait_until='domcontentloaded', timeout=30000)
                print("  [OK] 頁面載入成功")
            except Exception as e:
                print(f"  [FAIL] 頁面載入失敗: {e}")
                print("  => 嘗試重新載入...")
                try:
                    await page.goto(search_url, wait_until='networkidle', timeout=30000)
                    print("  [OK] 重新載入成功")
                except:
                    print("  [FAIL] 重新載入失敗")
                    return None
            
            await wait_with_feedback(page, Config.WAIT_PAGE_LOAD, "等待搜尋結果完全載入...")
            await stop_if_google_verification(page)
            
            # 驗證是否在正確的頁面
            current_url = page.url
            if 'google.com/search' in current_url:
                print(f"  [OK] 確認在搜尋結果頁面")
            else:
                print(f"  [WARNING] 當前頁面: {current_url}")
            
            print("  [OK] Phase 1 完成\n")
            
            # ================================================================
            # Phase 2: 智慧點擊菜單按鈕
            # ================================================================
            click_success = await find_and_click_menu_button(page)
            
            # ================================================================
            # Phase 4: 沒有 Google 菜單按鈕時改走圖片備援
            # ================================================================
            if not click_success:
                print("\n" + "="*70)
                print("[WARNING] 找不到 Google 菜單按鈕，改從搜尋結果找菜單圖片")
                print("="*70)
                image_result = await parse_menu_from_images(page, restaurant_name)
                if image_result.menu_items:
                    return image_result
                if not Config.ENABLE_MANUAL_ASSIST:
                    return image_result
                print("圖片備援也失敗，切換至【手動輔助模式】")
                print("請在瀏覽器中手動點擊「菜單」標籤，完成後按 Enter 繼續抓取")
                input("\n按 Enter 繼續...")
            
            # 檢查菜單是否載入
            menu_loaded = await check_menu_loaded(page)
            
            if not menu_loaded:
                print("\n" + "="*70)
                print("[ERROR] 最終檢查失敗：無法偵測到菜單內容")
                print("="*70)
                print("[Fallback] 改從 Google 搜尋結果尋找菜單圖片")
                return await parse_menu_from_images(page, restaurant_name)
            
            # ================================================================
            # Phase 3: 資料抓取
            # ================================================================
            restaurant = await extract_menu_data(page, restaurant_name)
            if not restaurant.menu_items:
                print("[Fallback] 文字菜單抽取為空，改從 Google 搜尋結果尋找菜單圖片")
                return await parse_menu_from_images(page, restaurant_name)

            return restaurant
        
        except GoogleVerificationRequired as e:
            print(f"\n[Google Verification] {e}")
            return Restaurant(
                name=restaurant_name,
                menu_items=[],
                error="google_verification_required",
            )
        except Exception as e:
            print(f"\n[ERROR] 爬蟲執行失敗: {e}")
            import traceback
            traceback.print_exc()
            return Restaurant(name=restaurant_name, menu_items=[])

# ============================================================================
# 對外介面
# ============================================================================

async def quick_crawl(restaurant_name: str) -> Restaurant:
    """快速爬取介面（供後端 API 調用）"""
    return await crawl_google_menu(restaurant_name)

# ============================================================================
# 命令列執行入口
# ============================================================================

async def main():
    """命令列執行主程式"""
    
    print("\n" + "="*70)
    print("Google 餐廳菜單爬蟲（全自動化版本）")
    print("="*70)
    
    if len(sys.argv) > 1:
        restaurant_name = sys.argv[1]
    else:
        restaurant_name = input("\n請輸入餐廳名稱（例如：麥當勞大甲）: ").strip()
    
    if not restaurant_name:
        print("[ERROR] 未輸入餐廳名稱，程式結束")
        return
    
    restaurant = await crawl_google_menu(restaurant_name)
    
    if restaurant and restaurant.menu_items and len(restaurant.menu_items) > 0:
        print("\n" + "="*70)
        print("💾 儲存結果")
        print("="*70)
        
        filename = f"menu_{restaurant.name.replace(' ', '_')}.json"
        file_path = Path(filename)
        
        file_path.write_text(
            json.dumps(asdict(restaurant), ensure_ascii=False, indent=2),
            encoding='utf-8'
        )
        
        print(f"[SUCCESS] 已儲存: {filename}")
        print(f"[INFO] 菜單項目數: {len(restaurant.menu_items)}")
        print("="*70)
    else:
        print("\n" + "="*70)
        print("[ERROR] 爬取失敗或無資料")
        print("="*70)

if __name__ == '__main__':
    if sys.platform.startswith('win32'):
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    
    asyncio.run(main())
