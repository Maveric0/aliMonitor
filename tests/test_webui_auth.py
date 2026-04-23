import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import failover_webui_app as app


class WebUiAuthTests(unittest.TestCase):
    def setUp(self):
        self._old_password = os.environ.get("ALIMONITOR_WEBUI_PASSWORD")
        os.environ["ALIMONITOR_WEBUI_PASSWORD"] = "test-password"
        with app.AUTH_SESSION_LOCK:
            app.AUTH_ACTIVE_NONCES.clear()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.base_url = f"http://{host}:{port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        if self._old_password is None:
            os.environ.pop("ALIMONITOR_WEBUI_PASSWORD", None)
        else:
            os.environ["ALIMONITOR_WEBUI_PASSWORD"] = self._old_password

    def request(self, path, method="GET", payload=None, cookie=None):
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if cookie:
            headers["Cookie"] = cookie
        req = urllib.request.Request(f"{self.base_url}{path}", data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as response:
            body = json.loads(response.read().decode("utf-8"))
            return response, body

    def test_signed_token_validates_and_password_change_invalidates_it(self):
        token = app.make_auth_token("test-password", now=1000)

        self.assertTrue(app.verify_auth_token("test-password", token, now=1001))
        self.assertFalse(app.verify_auth_token("changed-password", token, now=1001))
        self.assertFalse(app.verify_auth_token("test-password", token + "x", now=1001))

    def test_protected_api_requires_login(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/api/logs")

        self.assertEqual(caught.exception.code, 401)

    def test_login_cookie_allows_protected_api(self):
        response, body = self.request("/api/auth/login", method="POST", payload={"password": "test-password"})
        cookie = response.headers["Set-Cookie"].split(";", 1)[0]

        self.assertTrue(body["ok"])
        response, body = self.request("/api/logs", cookie=cookie)

        self.assertEqual(response.status, 200)
        self.assertTrue(body["ok"])
        self.assertIn("content", body["data"])

    def test_logout_clear_cookie_removes_access(self):
        login_response, _body = self.request("/api/auth/login", method="POST", payload={"password": "test-password"})
        cookie = login_response.headers["Set-Cookie"].split(";", 1)[0]
        logout_response, logout_body = self.request("/api/auth/logout", method="POST", payload={}, cookie=cookie)
        cleared_cookie = logout_response.headers["Set-Cookie"].split(";", 1)[0]

        self.assertTrue(logout_body["ok"])
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/api/logs", cookie=cookie)
        self.assertEqual(caught.exception.code, 401)

        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/api/logs", cookie=cleared_cookie)

        self.assertEqual(caught.exception.code, 401)


if __name__ == "__main__":
    unittest.main()
