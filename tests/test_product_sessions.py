"""The example's product pages can share a browser on different local ports."""
import unittest

from aiohttp import ClientSession, CookieJar, web
from aiohttp.test_utils import TestServer

from openavc_oem.manager import ProductManager


class ProductSessions(unittest.IsolatedAsyncioTestCase):
    async def test_two_products_keep_independent_sessions_and_logout(self):
        products = []
        login = {'username': 'engineer', 'password': 'example-test-password'}
        for identity in ('first-product', 'second-product'):
            manager = ProductManager.__new__(ProductManager)
            manager.identity = identity
            manager.config = {'example_ui': True, 'login': login}
            manager.sessions = set()
            manager.management_token = 'test-backend-token'
            manager.status = lambda: {'available': True}
            manager.check_policy = lambda: True
            app = web.Application()
            app.router.add_post('/oem/login', manager.login)
            app.router.add_post('/oem/logout', manager.logout)
            app.router.add_get('/oem/settings', manager.settings)
            server = TestServer(app)
            await server.start_server()
            self.addAsyncCleanup(server.close)
            products.append((manager, server))

        async with ClientSession(cookie_jar=CookieJar(unsafe=True)) as browser:
            for manager, server in products:
                origin = str(server.make_url('/')).rstrip('/')
                async with browser.post(server.make_url('/oem/login'), json=login,
                                        headers={'Origin': origin}) as response:
                    self.assertEqual(response.status, 200)
                    self.assertTrue(response.cookies[manager.session_cookie]['httponly'])
                    self.assertEqual(response.cookies[manager.session_cookie]['samesite'], 'Strict')
            for _, server in products:
                async with browser.get(server.make_url('/oem/settings')) as response:
                    self.assertEqual(response.status, 200)
            _, first = products[0]
            async with browser.post(first.make_url('/oem/logout'), headers={
                    'Origin': str(first.make_url('/')).rstrip('/')}) as response:
                self.assertEqual(response.status, 200)
            for (_, server), expected in zip(products, (401, 200)):
                async with browser.get(server.make_url('/oem/settings')) as response:
                    self.assertEqual(response.status, expected)
