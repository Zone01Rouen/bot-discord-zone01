"""Retours apprenants → hub (utils/models.py).

Lancer depuis la racine du dépôt : python -m unittest discover tests
"""
import os
import unittest
from datetime import datetime

from aiohttp import web

# config_loader lit ces variables au chargement : valeurs de test, jamais de vrai secret.
os.environ.setdefault("CONNECT_PORT", "8080")
os.environ["LEARNER_FEEDBACK_SECRET"] = "secret-de-test"
os.environ["HUB_FEEDBACK_URL"] = "http://127.0.0.1:1/inutilise"

from utils import models  # noqa: E402


class PayloadTest(unittest.TestCase):
    def test_periode_premier_lundi_du_mois(self):
        p = models.build_feedback_payload("alan.lbrt", "R.A.S", now=datetime(2026, 11, 20, 9, 30))
        self.assertEqual(p["period"], "Mois du 02/11/2026")
        self.assertEqual(p["pseudo"], "alan.lbrt")
        self.assertEqual(p["text"], "R.A.S")
        self.assertEqual(p["receivedAt"], "2026-11-20T09:30:00Z")

    def test_meme_cle_que_notion_avant_le_premier_lundi(self):
        # Le 1er novembre 2026 est un dimanche : même colonne que Notion, le lundi 2.
        p = models.build_feedback_payload("x", "y", now=datetime(2026, 11, 1, 8, 0))
        self.assertEqual(p["period"], "Mois du 02/11/2026")


class ResultatHubTest(unittest.TestCase):
    def ok(self, **result):
        return {"success": True, "data": {"results": [{"index": 0, **result}]}}

    def test_rattache(self):
        self.assertTrue(models.check_hub_result(200, self.ok(ok=True, attributed=True)))

    def test_non_rattache_est_enregistre(self):
        self.assertFalse(models.check_hub_result(200, self.ok(ok=True, attributed=False)))

    def test_refus_dans_un_200(self):
        with self.assertRaisesRegex(RuntimeError, "apprenant inconnu"):
            models.check_hub_result(200, self.ok(ok=False, error="apprenant inconnu du hub (x)"))

    def test_statut_http(self):
        for status, body in [(401, {"success": False}), (500, None), (502, None)]:
            with self.assertRaisesRegex(RuntimeError, f"HTTP {status}"):
                models.check_hub_result(status, body)


class EnvoiHubTest(unittest.IsolatedAsyncioTestCase):
    """Appel réel contre un faux hub local : en-tête, corps, lecture de la réponse."""

    async def asyncSetUp(self):
        self.recu = []
        self.statut = 200

        async def handler(request):
            self.recu.append((request.headers.get("Authorization"), await request.json()))
            if self.statut != 200:
                return web.Response(status=self.statut, text="<html>Bad Gateway</html>")
            return web.json_response(
                {"success": True, "data": {"results": [{"index": 0, "ok": True, "attributed": True}]}}
            )

        app = web.Application()
        app.router.add_post("/api/learner-feedback", handler)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self._url = models.HUB_FEEDBACK_URL
        models.HUB_FEEDBACK_URL = f"http://127.0.0.1:{port}/api/learner-feedback"

    async def asyncTearDown(self):
        models.HUB_FEEDBACK_URL = self._url
        await self.runner.cleanup()

    async def test_envoi(self):
        self.assertTrue(await models.add_response_to_hub("alan.lbrt", "Projet fini"))
        auth, corps = self.recu[0]
        self.assertEqual(auth, "Bearer secret-de-test")
        self.assertEqual(corps["pseudo"], "alan.lbrt")
        self.assertEqual(corps["text"], "Projet fini")
        self.assertRegex(corps["period"], r"^Mois du \d{2}/\d{2}/\d{4}$")

    async def test_page_html_du_proxy(self):
        self.statut = 502
        with self.assertRaisesRegex(RuntimeError, "HTTP 502"):
            await models.add_response_to_hub("alan.lbrt", "Projet fini")


if __name__ == "__main__":
    unittest.main()
