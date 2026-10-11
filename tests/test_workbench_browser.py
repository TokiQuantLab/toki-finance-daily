"""Instrument identity and embedded controls without third-party requests."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, SkipTest
import importlib.util

from toki_finance_daily.cli import workbench



class WorkbenchBrowserTests(TestCase):
    @classmethod
    def setUpClass(cls):
        if importlib.util.find_spec("playwright") is None:
            raise SkipTest("Optional Playwright runtime is unavailable")
        from playwright.sync_api import sync_playwright
        cls.runtime = sync_playwright().start()
        try:
            cls.browser = cls.runtime.chromium.launch(headless=True)
        except Exception as exc:
            cls.runtime.stop()
            raise SkipTest("Optional Chromium unavailable: " + str(exc).splitlines()[0])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.runtime.stop()

    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "technical.html"
        workbench(self.temp.name)
        self.page = self.browser.new_page(viewport={"width": 540, "height": 1000})
        self.addCleanup(self.page.close)
        self.page.route("http://**/*", lambda route: route.abort())
        self.page.route("https://**/*", lambda route: route.abort())
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.goto(self.path.as_uri() + "?embedded=1")

    def test_four_actual_instruments_periods_and_original_source_links(self):
        symbols = ["OANDA:XAUUSD", "TVC:USOIL", "BITSTAMP:BTCUSD", "NASDAQ:NDX"]
        self.assertEqual(self.page.locator(".symbol-button").evaluate_all("nodes=>nodes.map(n=>n.dataset.symbol)"), symbols)
        for symbol in symbols:
            self.page.locator('button[data-symbol="' + symbol + '"]').click()
            self.assertEqual(self.page.locator("#instrument-code").inner_text(), symbol)
            self.assertEqual(self.page.locator('.symbol-button[aria-pressed="true"]').get_attribute("data-symbol"), symbol)
            self.page.locator("#interval").select_option("60")
            self.assertIn("1 小时", self.page.locator("#selected-title").inner_text())
            widget = self.page.locator("#technical-host tv-technical-analysis")
            if widget.count():
                self.assertEqual(widget.get_attribute("symbol"), symbol)
                self.assertEqual(widget.get_attribute("interval"), "60")
            else:
                self.assertEqual(self.page.locator("#technical-host .source-observation").count(), 1)
                self.assertEqual(self.page.locator("#technical-host a").get_attribute("href"), self.page.locator("#technical-source").get_attribute("href"))
                self.assertIn("当前没有可显示的嵌入评级", self.page.locator("#technical-host p").inner_text())
        self.assertIn("纳斯达克100现金指数", self.page.locator("#instrument-description").inner_text())
        self.assertIn("NASDAQ-NDX", self.page.locator("#technical-source").get_attribute("href"))
        self.assertNotIn("SPX", self.page.locator(".symbol-bar").inner_text())
        self.page.locator('button[data-symbol="TVC:USOIL"]').click()
        self.assertIn("CFD", self.page.locator("#instrument-type").inner_text())
        self.assertIn("具体月份", self.page.locator("#instrument-description").inner_text())
        self.assertFalse(self.errors)

    def test_source_cards_keep_actual_instrument_and_link_across_period_and_theme(self):
        for symbol, source in (("TVC:USOIL", "TVC-USOIL"), ("NASDAQ:NDX", "NASDAQ-NDX")):
            self.page.locator('button[data-symbol="' + symbol + '"]').click()
            for period in ("60", "1D", "1W"):
                self.page.locator("#interval").select_option(period)
                self.assertEqual(self.page.locator("#technical-host tv-technical-analysis").count(), 0)
                self.assertEqual(self.page.locator("#instrument-code").inner_text(), symbol)
                link = self.page.locator("#technical-host a").get_attribute("href")
                self.assertIn(source, link)
                self.assertEqual(link, self.page.locator("#technical-source").get_attribute("href"))
            self.page.locator("#theme-toggle-embedded").click()
            self.assertIn("当前没有可显示的嵌入评级", self.page.locator("#technical-host p").inner_text())
            self.assertEqual(self.page.locator("#technical-host tv-technical-analysis").count(), 0)
        self.assertFalse(self.errors)

    def test_embedded_theme_controls_mobile_height_and_ink_contrast(self):
        self.assertEqual(self.page.locator(".masthead").evaluate("n=>getComputedStyle(n).display"), "none")
        self.assertEqual(self.page.locator(".hero").evaluate("n=>getComputedStyle(n).display"), "none")
        self.assertEqual(self.page.locator("tv-technical-analysis").evaluate("n=>getComputedStyle(n).height"), "800px")
        for theme in ("dark", "light"):
            if theme == "light":
                self.page.locator("#theme-toggle-embedded").click()
            self.assertEqual(self.page.locator("html").get_attribute("data-theme"), theme)
            labels = self.page.locator("[data-theme-toggle]").evaluate_all("nodes=>nodes.map(n=>n.textContent)")
            self.assertEqual(labels[0], labels[1])
            self.assertEqual(self.page.locator("tv-technical-analysis").get_attribute("theme"), theme)
            contrast = self.page.locator("body").evaluate("""n=>{
                const vars=getComputedStyle(document.documentElement);
                const luminance=hex=>{let c=hex.trim().replace('#','');if(c.length===3)c=[...c].map(v=>v+v).join('');const parts=[0,2,4].map(i=>parseInt(c.slice(i,i+2),16)/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4);return .2126*parts[0]+.7152*parts[1]+.0722*parts[2];};
                const background=luminance(vars.getPropertyValue('--panel')),foreground=luminance(vars.getPropertyValue('--muted'));
                return (Math.max(background,foreground)+.05)/(Math.min(background,foreground)+.05);
            }""")
            self.assertGreaterEqual(contrast, 4.5)
            for width in (1440, 540, 390):
                self.page.set_viewport_size({"width": width, "height": 1000})
                self.assertEqual(self.page.evaluate("document.documentElement.scrollWidth"), width)
        self.assertFalse(self.errors)
