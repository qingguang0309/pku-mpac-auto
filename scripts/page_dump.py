"""连接到 open_browser.sh 打开的 Chrome，打印当前页面的 URL、标题和可点击元素。"""
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
    for page in browser.contexts[0].pages:
        print("==", page.url, "|", page.title())
    page = browser.contexts[0].pages[-1]
    for el in page.locator("a, button, input, select, [role=button], [onclick]").all()[:200]:
        try:
            if not el.is_visible():
                continue
            tag = el.evaluate("e => e.tagName.toLowerCase()")
            txt = (el.inner_text() or el.get_attribute("value") or el.get_attribute("placeholder") or "").strip().replace("\n", " ")[:60]
            print(f"{tag:7} id={el.get_attribute('id')!s:20} {txt}")
        except Exception:
            pass
