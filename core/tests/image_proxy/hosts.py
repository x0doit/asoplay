"""
Хосты картинок: правило имени из панели (punycode по UTS 46, зона с буквы, не IP), общие суффиксы, список панели,
самая точная запись хоста, проверка адреса, как его разберёт httpx, via по строке панели; /proxy/img принимает хосты
каталога и панели.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/tests/image_proxy: вход (core/tests/test_image_proxy.py) собирает части по порядку.
"""
import re
import unittest

from core.config import config
from core.functions import image_hosts, public_suffix, site_settings
from core.tests.image_proxy.lib import MAL, ProxyCase, image


class PanelHosts(unittest.TestCase):
    def test_a_name_is_brought_to_its_compared_form_as_browsers_do(self):
        for raw, host in ((" Img.Example.COM. ", "img.example.com"), ("пример.рф", "xn--e1afmkfd.xn--p1ai"),
                          ("xn--e1afmkfd.xn--p1ai", "xn--e1afmkfd.xn--p1ai"), ("a.b1", "a.b1"),
                          ("Straße.de", "xn--strae-oqa.de"), ("faß.de", "xn--fa-hia.de"), ("ςa.gr", "xn--a-xmb.gr"),
                          ("s4.anilist.co", "s4.anilist.co")):
            self.assertEqual(image_hosts.normalize_host(raw), host, raw)

    def test_anything_but_a_name_is_refused(self):
        for raw in ("", None, "localhost", "https://a.com", "a.com/x", "a.com:80", "user@a.com", "a.com?x", "a com",
                    "127.0.0.1", "0x7f.1", "2130706433", "a.0x7f", "a.1com", "[::1]", "::1", "a..com", "-a.com",
                    "a-.com", "a_b.com", "evil%2ecom.example.com", "x" * 64 + ".com", ("a." * 127) + "com"):
            with self.assertRaises(ValueError, msg=raw):
                image_hosts.normalize_host(raw)
        # 200 знаков кириллицей проходят до перевода, а в punycode их 284 — больше 253
        cyrillic = ".".join(["абвгдежзийклмнопрстуфхцчшщъыьэюя"] * 6) + ".рф"
        self.assertEqual((len(cyrillic), len(cyrillic.encode("idna"))), (200, 284))
        with self.assertRaises(ValueError):
            image_hosts.normalize_host(cyrillic)

    def test_a_host_takes_its_most_exact_row(self):
        hosts = ("myanimelist.net", "cdn.myanimelist.net", "example.com")
        self.assertEqual(image_hosts.entry_for("cdn.myanimelist.net", hosts), "cdn.myanimelist.net")
        self.assertEqual(image_hosts.entry_for("x.cdn.myanimelist.net.", hosts), "cdn.myanimelist.net")
        self.assertEqual(image_hosts.entry_for("MyAnimeList.net", hosts), "myanimelist.net")
        for host in ("evilmyanimelist.net", "myanimelist.net.evil.test", "", "com"):
            self.assertIsNone(image_hosts.entry_for(host, hosts), host)

    def test_an_address_is_checked_as_httpx_will_connect(self):
        hosts = image_hosts.IMG_HOSTS
        for url in (MAL, "http://cdn.myanimelist.net/a.jpg", "https://CDN.MyAnimeList.net./a.jpg",
                    "https://x.cdn.myanimelist.net:443/a.jpg", "https://shikimori.io/system/a.jpg"):
            self.assertIsNone(image_hosts.url_refusal(url, hosts), url)
        for url, why in (("ftp://cdn.myanimelist.net/a.jpg", "scheme"), ("javascript:alert(1)", "scheme"),
                         ("https://u:p@cdn.myanimelist.net/a.jpg", "credentials"),
                         ("https://cdn.myanimelist.net@evil.test/a.jpg", "credentials"),
                         ("https://evil.test\\@cdn.myanimelist.net/", "credentials"),
                         ("https://cdn.myanimelist.net:22/a.jpg", "port"), ("https://127.0.0.1/a.jpg", "ip address"),
                         ("https://[::1]/a.jpg", "ip address"), ("https://shikimori.io.evil.test/a.jpg", "host not allowed"),
                         ("https://evil%2ecom.shikimori.io/a.jpg", "bad host"),
                         ("https://2130706433/a.jpg", "host not allowed"), ("https://[::1/a.jpg", "bad url")):
            self.assertEqual(image_hosts.url_refusal(url, hosts), why, url)


class PanelRows(ProxyCase):
    def test_a_public_suffix_is_refused_with_its_reason_and_a_site_under_it_is_taken(self):
        for raw in ("co.uk", "github.io", "S3.AmazonAWS.com", "r2.dev", "duckdns.org", "foo.ck", "рф"):
            with self.assertRaises(ValueError, msg=raw) as caught:
                image_hosts.parse_proxy_hosts([{"host": raw}], strict=True)
            self.assertEqual(caught.exception.args[0], raw)
            if raw != "рф":  # одна метка — не имя вовсе, без пояснения
                self.assertIn("общий суффикс", caught.exception.args[1])
        taken = image_hosts.parse_proxy_hosts([{"host": h} for h in ("example.co.uk", "user.github.io", "www.ck")],
                                              strict=True)
        self.assertEqual([row["host"] for row in taken], ["example.co.uk", "user.github.io", "www.ck"])
        for raw in ("nip.io", "127.0.0.1.nip.io", "x.sslip.io", "lvh.me"):  # любой IP в имени
            with self.assertRaises(ValueError, msg=raw) as caught:
                image_hosts.parse_proxy_hosts([{"host": raw}], strict=True)
            self.assertIn("любой IP в имени", caught.exception.args[1])
        stored = image_hosts.parse_proxy_hosts([{"host": "github.io"}], strict=False)  # чтение из базы — без списка
        self.assertEqual(stored, [{"host": "github.io", "via": "site"}])

    def test_without_the_list_the_panel_cannot_save_hosts_but_stored_ones_still_load(self):
        config.PUBLIC_SUFFIX_FILE.unlink()
        public_suffix._rules.cache_clear()
        with self.assertRaises(RuntimeError):
            image_hosts.parse_proxy_hosts([{"host": "img.example.com"}], strict=True)
        self.assertEqual(image_hosts.parse_proxy_hosts([{"host": "img.example.com"}], strict=False),
                         [{"host": "img.example.com", "via": "site"}])
        config.PUBLIC_SUFFIX_FILE.write_text("", encoding="utf-8")

    def test_the_stored_list_keeps_the_first_row_of_a_host_and_at_most_a_hundred(self):
        rows = [{"host": "A.example.com", "via": "vpn"}, {"host": "a.example.com"}, {"host": "b.example.com"}]
        self.assertEqual(image_hosts.parse_proxy_hosts(rows, strict=True),
                         [{"host": "a.example.com", "via": "vpn"}, {"host": "b.example.com", "via": "site"}])
        broken = [{"host": "bad host"}, {"host": "c.example.com", "via": "tor"}, "d.example.com", {"host": "e.example.com"}]
        self.assertEqual(image_hosts.parse_proxy_hosts(broken, strict=False), [{"host": "e.example.com", "via": "site"}])
        with self.assertRaises(ValueError):
            image_hosts.parse_proxy_hosts(broken, strict=True)
        many = [{"host": f"h{n}.example.com"} for n in range(image_hosts.PROXY_HOSTS_MAX + 5)]
        self.assertEqual(len(image_hosts.parse_proxy_hosts(many, strict=False)), image_hosts.PROXY_HOSTS_MAX)
        with self.assertRaises(ValueError):
            image_hosts.parse_proxy_hosts(many, strict=True)

    def test_via_comes_from_the_most_exact_panel_row(self):
        self.panel(("example.com", "vpn"), ("img.example.com", "site"), ("myanimelist.net", "vpn"))
        self.assertEqual(site_settings.proxy_via("a.img.example.com"), "site")
        self.assertEqual(site_settings.proxy_via("static.example.com"), "vpn")
        self.assertEqual(site_settings.proxy_via("cdn.myanimelist.net"), "vpn")
        self.assertEqual(site_settings.proxy_via("s4.anilist.co"), "site")
        self.assertEqual(site_settings.img_hosts(), [*image_hosts.IMG_HOSTS, "example.com", "img.example.com"])

    async def test_a_panel_host_is_taken_while_it_is_listed(self):
        url = "https://img.example.com/a.png"
        self.direct.answers[url] = image
        self.assertEqual((await self.get(url)).status_code, 403)
        self.panel(("example.com", "site"))
        self.assertEqual((await self.get(url)).body[:4], b"\x89PNG")
        self.panel()
        self.assertEqual((await self.get("https://img.example.com/b.png")).status_code, 403)
        self.assertEqual(self.direct.seen, [url])


class RealList(unittest.TestCase):
    def test_the_list_is_in_the_repo_with_its_version(self):
        self.assertTrue(config.PUBLIC_SUFFIX_FILE.is_file(),
                        f"нет {config.PUBLIC_SUFFIX_FILE.name}: файл ждёт «да» владельца на загрузку с publicsuffix.org")
        head = config.PUBLIC_SUFFIX_FILE.read_text(encoding="utf-8")[:2000]
        self.assertRegex(head, re.compile(r"VERSION: \d{4}-\d{2}-\d{2}"))
        public_suffix._rules.cache_clear()
        self.assertTrue(all(public_suffix.is_public_suffix(h) for h in ("co.uk", "github.io", "s3.amazonaws.com",
                                                                        "r2.dev", "duckdns.org", "xn--p1ai")))
        self.assertFalse(public_suffix.is_public_suffix("myanimelist.net"))
        for parent in ("amazonaws.com", "compute.amazonaws.com"):  # под ними s3.amazonaws.com и *.compute…
            with self.assertRaises(ValueError) as caught:
                image_hosts.parse_proxy_hosts([{"host": parent}], strict=True)
            self.assertIn("под ним есть общие суффиксы", caught.exception.args[1])
        catalog_hosts = [{"host": h} for h in image_hosts.IMG_HOSTS]
        self.assertEqual(len(image_hosts.parse_proxy_hosts(catalog_hosts, strict=True)), len(image_hosts.IMG_HOSTS))
