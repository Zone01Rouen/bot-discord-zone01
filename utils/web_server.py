"""
Serveur web embarqué pour le flux d'authentification OAuth2-like.

Flux :
  1. L'utilisateur lance /connect dans Discord.
  2. Le bot génère un token UUID lié au discord_id et envoie un lien éphémère.
  3. L'utilisateur clique → ce serveur affiche un formulaire web sécurisé.
  4. L'utilisateur soumet ses identifiants Zone01 (dans le navigateur, jamais dans Discord).
  5. Le serveur authentifie contre Zone01, lie les comptes, puis notifie l'utilisateur par DM.
"""
from __future__ import annotations

import asyncio
import base64
import os
import time
import uuid

import aiohttp
import discord
from aiohttp import web

from utils import interaction_store
from utils.logger import logger

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ZONE01_DOMAIN = "zone01normandie.org"
ZONE01_API_URL = "https://api-zone01-rouen.deno.dev/api/v1"
# Clé partagée pour autoriser les mutations de l'API Deno (doit être identique
# à API_ADMIN_KEY côté API). Injectée au runtime via l'environnement.
API_ADMIN_KEY = os.getenv("API_ADMIN_KEY", "")

TOKEN_TTL = 600  # secondes (10 minutes)

# Stockage en mémoire des tokens en attente : {token: {discord_id, expires_at}}
_pending_tokens: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Gestion des tokens
# ---------------------------------------------------------------------------

def create_connect_token(discord_id: str) -> str:
    """Génère un token one-time et l'associe au discord_id."""
    _cleanup_expired_tokens()
    token = str(uuid.uuid4())
    _pending_tokens[token] = {
        "discord_id": discord_id,
        "expires_at": time.time() + TOKEN_TTL,
    }
    return token


def _cleanup_expired_tokens() -> None:
    now = time.time()
    expired = [k for k, v in _pending_tokens.items() if v["expires_at"] < now]
    for k in expired:
        del _pending_tokens[k]


def _consume_token(token: str) -> dict | None:
    """Valide et consomme un token (one-time use). Retourne les données ou None."""
    _cleanup_expired_tokens()
    data = _pending_tokens.get(token)
    if not data:
        return None
    if data["expires_at"] < time.time():
        del _pending_tokens[token]
        return None
    del _pending_tokens[token]
    return data


# ---------------------------------------------------------------------------
# HTML Templates
# ---------------------------------------------------------------------------

_BASE_CSS = """
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        background: #0d1117;
        color: #e6edf3;
        display: flex;
        align-items: center;
        justify-content: center;
        min-height: 100vh;
        padding: 1rem;
    }
    .card {
        background: #161b22;
        border: 1px solid #30363d;
        border-radius: 12px;
        padding: 2.5rem 2rem;
        width: 100%;
        max-width: 420px;
        box-shadow: 0 8px 32px rgba(0,0,0,.4);
    }
    .logo {
        display: flex;
        align-items: center;
        gap: .75rem;
        margin-bottom: 1.5rem;
    }
    .logo-icon {
        width: 42px;
        height: 42px;
        background: linear-gradient(135deg, #00b4d8, #0077b6);
        border-radius: 10px;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 1.4rem;
    }
    .logo-text { font-size: 1.25rem; font-weight: 700; color: #fff; }
    .logo-sub { font-size: .8rem; color: #8b949e; }
    h2 { font-size: 1.1rem; font-weight: 600; margin-bottom: .4rem; color: #fff; }
    .subtitle { font-size: .85rem; color: #8b949e; margin-bottom: 1.5rem; line-height: 1.5; }
    label { display: block; font-size: .8rem; font-weight: 500; color: #8b949e; margin-bottom: .35rem; }
    input {
        width: 100%;
        padding: .65rem .9rem;
        background: #0d1117;
        border: 1px solid #30363d;
        border-radius: 8px;
        color: #e6edf3;
        font-size: .9rem;
        margin-bottom: 1rem;
        outline: none;
        transition: border-color .15s;
    }
    input:focus { border-color: #00b4d8; }
    button {
        width: 100%;
        padding: .7rem;
        background: linear-gradient(135deg, #00b4d8, #0077b6);
        border: none;
        border-radius: 8px;
        color: #fff;
        font-size: .95rem;
        font-weight: 600;
        cursor: pointer;
        transition: opacity .15s;
    }
    button:hover { opacity: .88; }
    .alert {
        background: #2d1b1b;
        border: 1px solid #6e2727;
        border-radius: 8px;
        padding: .75rem 1rem;
        font-size: .85rem;
        color: #f87171;
        margin-bottom: 1.2rem;
    }
    .notice {
        font-size: .75rem;
        color: #6e7681;
        text-align: center;
        margin-top: 1.2rem;
        line-height: 1.5;
    }
    .success-icon { font-size: 3rem; text-align: center; margin-bottom: 1rem; }
    .success-title { font-size: 1.3rem; font-weight: 700; color: #4ade80; text-align: center; margin-bottom: .5rem; }
    .success-sub { font-size: .9rem; color: #8b949e; text-align: center; line-height: 1.6; }
    .timer { font-size: .75rem; color: #6e7681; text-align: center; margin-top: 1.5rem; }
"""


def _render_form(token: str, error: str = "") -> str:
    error_html = f'<div class="alert">{error}</div>' if error else ""
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Connexion Zone01 — Lier mon compte Discord</title>
  <style>{_BASE_CSS}</style>
</head>
<body>
  <div class="card">
    <div class="logo">
      <div class="logo-icon">🎓</div>
      <div>
        <div class="logo-text">Zone01</div>
        <div class="logo-sub">Liaison de compte Discord</div>
      </div>
    </div>
    <h2>Connectez-vous à Zone01</h2>
    <p class="subtitle">
      Entrez vos identifiants Zone01 pour lier votre compte Discord.
      Vos identifiants transitent directement vers Zone01 et ne sont jamais stockés.
    </p>
    {error_html}
    <form method="POST" action="/connect">
      <input type="hidden" name="token" value="{token}">
      <label for="login">Login Zone01</label>
      <input type="text" id="login" name="login" placeholder="prenom.nom" autocomplete="username" required>
      <label for="password">Mot de passe</label>
      <input type="password" id="password" name="password" placeholder="••••••••" autocomplete="current-password" required>
      <button type="submit">🔗 Lier mon compte</button>
    </form>
    <p class="notice">
      Ce lien est à usage unique et expire dans 10 minutes.<br>
      Si le lien a expiré, relancez <strong>/connect</strong> dans Discord.
    </p>
  </div>
</body>
</html>"""


_SUCCESS_PAGE = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Compte lié — Zone01</title>
  <style>{_BASE_CSS}</style>
</head>
<body>
  <div class="card">
    <div class="logo">
      <div class="logo-icon">🎓</div>
      <div>
        <div class="logo-text">Zone01</div>
        <div class="logo-sub">Liaison de compte Discord</div>
      </div>
    </div>
    <div class="success-icon">✅</div>
    <div class="success-title">Compte lié avec succès !</div>
    <p class="success-sub">
      Votre compte Discord est maintenant lié à votre compte Zone01.<br>
      Un message de confirmation vous a été envoyé par le bot sur Discord.
    </p>
    <p class="timer">Vous pouvez fermer cette page.</p>
  </div>
</body>
</html>"""

_EXPIRED_PAGE = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Lien expiré — Zone01</title>
  <style>{_BASE_CSS}</style>
</head>
<body>
  <div class="card">
    <div class="logo">
      <div class="logo-icon">🎓</div>
      <div>
        <div class="logo-text">Zone01</div>
        <div class="logo-sub">Liaison de compte Discord</div>
      </div>
    </div>
    <div class="success-icon">⏱️</div>
    <div class="success-title" style="color:#f87171">Lien invalide ou expiré</div>
    <p class="success-sub">
      Ce lien est à usage unique et a expiré ou a déjà été utilisé.<br>
      Relancez <strong>/connect</strong> dans Discord pour obtenir un nouveau lien.
    </p>
  </div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Handlers HTTP
# ---------------------------------------------------------------------------

async def _handle_get(request: web.Request) -> web.Response:
    """Affiche le formulaire de connexion si le token est valide."""
    token = request.rel_url.query.get("token", "")
    _cleanup_expired_tokens()

    if not token or token not in _pending_tokens:
        return web.Response(text=_EXPIRED_PAGE, content_type="text/html", status=400)

    return web.Response(text=_render_form(token), content_type="text/html")


async def _handle_post(request: web.Request, bot: discord.Client) -> web.Response:
    """Traite la soumission du formulaire."""
    data = await request.post()
    token = data.get("token", "")
    login = data.get("login", "").strip()
    # SECURITY: ne jamais logger password/credentials (collecte par conception, cf. sec-vulnerabilites V13)
    password = data.get("password", "")

    if not login or not password:
        _cleanup_expired_tokens()
        if token not in _pending_tokens:
            return web.Response(text=_EXPIRED_PAGE, content_type="text/html", status=400)
        return web.Response(
            text=_render_form(token, "Veuillez remplir tous les champs."),
            content_type="text/html",
        )

    token_data = _consume_token(token)
    if not token_data:
        return web.Response(text=_EXPIRED_PAGE, content_type="text/html", status=400)

    discord_id = token_data["discord_id"]

    # --- Étape 1 : Authentification Zone01 ---
    credentials = base64.b64encode(f"{login}:{password}".encode()).decode()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"https://{ZONE01_DOMAIN}/api/auth/signin",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Basic {credentials}",
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as auth_resp:
                if not auth_resp.ok:
                    logger.warning(
                        f"Échec d'auth Zone01 — login: {login} (HTTP {auth_resp.status})",
                        category="connect",
                    )
                    # Remettre un token frais pour que l'utilisateur puisse réessayer
                    new_token = str(uuid.uuid4())
                    _pending_tokens[new_token] = {
                        "discord_id": discord_id,
                        "expires_at": time.time() + TOKEN_TTL,
                    }
                    return web.Response(
                        text=_render_form(new_token, "Login ou mot de passe incorrect."),
                        content_type="text/html",
                    )

            # --- Étape 2 : Liaison Discord ↔ Zone01 ---
            async with session.put(
                f"{ZONE01_API_URL}/discord-users",
                json={"login": login, "discord_id": discord_id},
                headers={"X-Api-Key": API_ADMIN_KEY},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as api_resp:
                if not api_resp.ok:
                    logger.error(
                        f"Erreur API liaison — login: {login} (HTTP {api_resp.status})",
                        category="connect",
                    )
                    return web.Response(
                        text="<p>Erreur lors de l'enregistrement. Réessayez plus tard.</p>",
                        content_type="text/html",
                        status=500,
                    )

    except asyncio.TimeoutError:
        logger.error("Timeout lors de l'authentification Zone01", category="connect")
        return web.Response(
            text="<p>Le serveur Zone01 ne répond pas. Réessayez plus tard.</p>",
            content_type="text/html",
            status=503,
        )
    except aiohttp.ClientError as e:
        logger.error(f"Erreur réseau : {e}", category="connect")
        return web.Response(
            text="<p>Erreur réseau. Réessayez plus tard.</p>",
            content_type="text/html",
            status=503,
        )

    # --- Succès : notifier l'utilisateur par DM ---
    logger.success(
        f"Liaison réussie : Zone01={login} ↔ Discord ID={discord_id}",
        category="connect",
    )
    asyncio.create_task(_notify_discord(bot, int(discord_id), login))

    return web.Response(text=_SUCCESS_PAGE, content_type="text/html")


async def _notify_discord(bot: discord.Client, discord_id: int, login: str) -> None:
    """Envoie un DM de confirmation à l'utilisateur Discord."""
    try:
        user = await bot.fetch_user(discord_id)
        embed = discord.Embed(
            title="✅ Compte lié avec succès !",
            description=f"Votre compte Discord est maintenant lié à votre compte Zone01 **{login}**.",
            color=discord.Color.green(),
        )
        embed.set_footer(text="Relancez /connect à tout moment pour mettre à jour la liaison.")
        await user.send(embed=embed)
    except discord.Forbidden:
        logger.warning(f"Impossible d'envoyer un DM à l'utilisateur {discord_id}", category="connect")
    except Exception as e:
        logger.error(f"Erreur lors de l'envoi du DM de confirmation : {e}", category="connect")


# ---------------------------------------------------------------------------
# Endpoint /api/notify : Dashboard → Bot (relances interactives)
# ---------------------------------------------------------------------------

def _build_notify_embed(payload: dict) -> discord.Embed:
    """Construit l'embed de relance à partir du payload /api/notify."""
    embed = discord.Embed(
        title=str(payload.get("title", "Notification Zone01")),
        description=str(payload.get("body", "")),
        color=discord.Color.blurple(),
    )
    facts = payload.get("facts") or []
    if isinstance(facts, list):
        for fact in facts:
            if not isinstance(fact, dict):
                continue
            name = str(fact.get("name", "​"))
            value = str(fact.get("value", "​"))
            embed.add_field(name=name, value=value, inline=True)
    embed.set_footer(text="Zone01 — relances automatiques")
    return embed


async def _handle_notify(request: web.Request, bot: discord.Client) -> web.Response:
    """
    POST /api/notify — envoie un embed DM interactif au destinataire.

    Auth : header X-Api-Key == API_ADMIN_KEY.
    Voir le contrat §1 pour le schéma de la requête.
    """
    # --- Auth ---
    api_key = request.headers.get("X-Api-Key", "")
    if not API_ADMIN_KEY or api_key != API_ADMIN_KEY:
        logger.warning("Appel /api/notify avec clé invalide", category="cr_relance")
        return web.json_response({"ok": False, "error": "forbidden"}, status=403)

    # --- Parse ---
    try:
        payload = await request.json()
    except Exception:
        return web.json_response(
            {"ok": False, "error": "invalid_json"}, status=400
        )

    recipient_raw = payload.get("recipient_discord_id")
    if not recipient_raw:
        return web.json_response(
            {"ok": False, "error": "missing recipient_discord_id"}, status=400
        )
    try:
        recipient_id = int(recipient_raw)
    except (TypeError, ValueError):
        return web.json_response(
            {"ok": False, "error": "invalid recipient_discord_id"}, status=400
        )

    notif_type = str(payload.get("type", "")) or "cr_rdv"
    actions = payload.get("actions") or {}
    if not isinstance(actions, dict):
        actions = {}
    url = payload.get("url")
    coach_discord_id = payload.get("coach_discord_id")
    context = payload.get("context") or {}

    # --- Construction de l'embed + de la View ---
    embed = _build_notify_embed(payload)

    # Import local pour éviter une dépendance circulaire au chargement.
    from cogs.cr_relance_cog import CRReplyView

    view: discord.ui.View | None = None
    if actions.get("reply_button") or url:
        view = CRReplyView() if actions.get("reply_button") else discord.ui.View(timeout=None)
        if url:
            try:
                view.add_item(
                    discord.ui.Button(
                        label="Ouvrir",
                        url=str(url),
                        style=discord.ButtonStyle.link,
                        emoji="🔗",
                    )
                )
            except Exception as e:
                logger.warning(f"Bouton lien ignoré ({e})", category="cr_relance")

    # --- Envoi du DM ---
    try:
        user = await bot.fetch_user(recipient_id)
        if view is not None:
            sent = await user.send(embed=embed, view=view)
        else:
            sent = await user.send(embed=embed)
    except discord.Forbidden:
        logger.warning(
            f"DM interdit pour le destinataire {recipient_id}", category="cr_relance"
        )
        return web.json_response(
            {"ok": False, "error": "dm_forbidden"}, status=502
        )
    except Exception as e:
        logger.error(f"Échec de l'envoi du DM /api/notify : {e}", category="cr_relance")
        return web.json_response(
            {"ok": False, "error": "send_failed"}, status=502
        )

    # --- Réaction ✅ (cr_rdv uniquement) ---
    if actions.get("rdv_reaction"):
        try:
            await sent.add_reaction("✅")
        except Exception as e:
            logger.warning(
                f"Impossible d'ajouter la réaction ✅ : {e}", category="cr_relance"
            )

    # --- Persistance du contexte pour les interactions ultérieures ---
    try:
        interaction_store.put(
            sent.id,
            {
                "context": context,
                "coach_discord_id": (str(coach_discord_id) if coach_discord_id else None),
                "type": notif_type,
            },
        )
    except Exception as e:
        logger.error(
            f"Échec de la persistance de l'interaction {sent.id} : {e}",
            category="cr_relance",
        )

    logger.success(
        f"Relance '{notif_type}' envoyée à {recipient_id} (message_id={sent.id})",
        category="cr_relance",
    )
    return web.json_response({"ok": True, "message_id": str(sent.id)})


# ---------------------------------------------------------------------------
# Démarrage du serveur
# ---------------------------------------------------------------------------

async def start_web_server(bot: discord.Client, host: str = "0.0.0.0", port: int = 8080) -> None:
    """Lance le serveur web aiohttp en parallèle du bot."""

    async def post_handler(request: web.Request) -> web.Response:
        return await _handle_post(request, bot)

    async def notify_handler(request: web.Request) -> web.Response:
        try:
            return await _handle_notify(request, bot)
        except Exception as e:
            logger.error(f"Erreur inattendue /api/notify : {e}", category="cr_relance")
            return web.json_response(
                {"ok": False, "error": "internal_error"}, status=500
            )

    app = web.Application()
    app.router.add_get("/connect", _handle_get)
    app.router.add_post("/connect", post_handler)
    app.router.add_post("/api/notify", notify_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()

    logger.success(f"Serveur web démarré sur http://{host}:{port}", category="connect")
