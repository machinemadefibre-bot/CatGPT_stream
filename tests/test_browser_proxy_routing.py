from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from src.browser.manager import _resolve_domains_for_chrome


class BrowserProxyRoutingTests(unittest.TestCase):
    def test_proxy_skips_local_provider_domain_resolution(self) -> None:
        with (
            patch.dict(
                os.environ,
                {"BROWSER_PROXY_SERVER": "socks5://tailscale-egress:1055"},
                clear=False,
            ),
            patch("src.browser.manager._is_docker_runtime", return_value=True),
            patch("src.browser.manager.socket.gethostbyname") as resolve,
        ):
            self.assertEqual(_resolve_domains_for_chrome(), "")
            resolve.assert_not_called()


if __name__ == "__main__":
    unittest.main()
