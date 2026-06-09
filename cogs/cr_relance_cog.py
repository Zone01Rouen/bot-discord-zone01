"""
Cog des interactions de relance (CR / grouping / audit / milestone).

Le dashboard déclenche le bot via `POST /api/notify` (cf. utils/web_server.py),
qui envoie un embed DM au destinataire avec, selon le cas :
  - un bouton vert « Marquer le CR comme réservé » (relances `cr_rdv`),
  - un bouton « Répondre » (View persistante, ouvre un modal — rapports d'audit).

Ce cog gère le retour des interactions vers le dashboard (callbacks) :
  - clic « Marquer le CR comme réservé » → callback `rdv_confirmed` (sans modal),
  - clic « Répondre » → modal Statut/Commentaire → callback `reply_submitted`.

Persistance : le `context` est stocké côté `utils/interaction_store.py`
(`data/pending_interactions.json`). Les Views sont persistantes (`timeout=None`,
`custom_id` stables) et ré-enregistrées au démarrage via `bot.add_view`,
ce qui permet aux boutons de rester fonctionnels après un redémarrage du bot.
"""
from __future__ import annotations

import aiohttp
import discord
from discord.ext import commands

from utils import interaction_store
from utils.config_loader import DASHBOARD_CALLBACK_URL, BOT_CALLBACK_SECRET
from utils.logger import logger


# ---------------------------------------------------------------------------
# Callback Bot → Dashboard
# ---------------------------------------------------------------------------

async def _callback(event: str, context: dict, actor_discord_id: str, data: dict | None = None) -> bool:
    """
    POST DASHBOARD_CALLBACK_URL avec header X-Bot-Secret.

    event = "rdv_confirmed" | "reply_submitted"
    Retourne True si succès (HTTP 2xx), False sinon. Ne lève jamais.
    """
    if not DASHBOARD_CALLBACK_URL:
        logger.error(
            "DASHBOARD_CALLBACK_URL non configurée — callback ignoré",
            category="cr_relance",
        )
        return False

    payload: dict = {
        "event": event,
        "context": context,
        "actor_discord_id": str(actor_discord_id),
    }
    if data is not None:
        payload["data"] = data

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                DASHBOARD_CALLBACK_URL,
                json=payload,
                headers={"X-Bot-Secret": BOT_CALLBACK_SECRET},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.ok:
                    logger.success(
                        f"Callback '{event}' transmis au dashboard (HTTP {resp.status})",
                        category="cr_relance",
                    )
                    return True
                body = await resp.text()
                logger.error(
                    f"Callback '{event}' refusé (HTTP {resp.status}) : {body[:200]}",
                    category="cr_relance",
                )
                return False
    except Exception as e:
        logger.error(f"Échec du callback '{event}' : {e}", category="cr_relance")
        return False


# ---------------------------------------------------------------------------
# Modal « Répondre »
# ---------------------------------------------------------------------------

class CRReplyModal(discord.ui.Modal, title="Répondre"):
    """
    Modal ouvert au clic sur « Répondre ».
    Rappel : un modal Discord n'accepte QUE des TextInput (pas de select).
    """

    statut = discord.ui.TextInput(
        label="Statut",
        placeholder="RDV pris / en cours / blocage",
        style=discord.TextStyle.short,
        required=True,
        max_length=100,
    )
    commentaire = discord.ui.TextInput(
        label="Commentaire",
        placeholder="Précisions (optionnel)",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=1000,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            message_id = interaction.message.id if interaction.message else None
            entry = interaction_store.get(message_id) if message_id else None

            if not entry:
                await interaction.response.send_message(
                    "❓ Contexte introuvable pour cette relance.", ephemeral=True
                )
                logger.warning(
                    f"Modal soumis sans contexte (message_id={message_id})",
                    category="cr_relance",
                )
                return

            context = entry.get("context", {})
            data = {
                "status": str(self.statut.value),
                "comment": str(self.commentaire.value or ""),
            }
            await _callback(
                "reply_submitted",
                context,
                str(interaction.user.id),
                data=data,
            )
            await interaction.response.send_message(
                "Merci, votre réponse a été transmise ✅", ephemeral=True
            )
        except Exception as e:
            logger.error(f"Erreur on_submit modal CR : {e}", category="cr_relance")
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "Une erreur est survenue, réessayez plus tard.", ephemeral=True
                    )
            except Exception:
                pass


# ---------------------------------------------------------------------------
# View persistante (bouton « Répondre »)
# ---------------------------------------------------------------------------

class CRReplyView(discord.ui.View):
    """View persistante portant le bouton « Répondre » (custom_id stable)."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Répondre",
        style=discord.ButtonStyle.primary,
        custom_id="cr_reply",
        emoji="✍️",
    )
    async def reply_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        try:
            await interaction.response.send_modal(CRReplyModal())
        except Exception as e:
            logger.error(f"Erreur ouverture modal CR : {e}", category="cr_relance")


# ---------------------------------------------------------------------------
# View persistante (bouton vert « Marquer le CR comme réservé »)
# ---------------------------------------------------------------------------

class CRBookedView(discord.ui.View):
    """
    View persistante portant le bouton vert « Marquer le CR comme réservé ».
    Utilisée pour les relances `cr_rdv` : au clic, callback `rdv_confirmed`
    (sans modal), puis le bouton est désactivé.
    """

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Marquer le CR comme réservé",
        style=discord.ButtonStyle.success,
        custom_id="cr_booked",
        emoji="✅",
    )
    async def booked_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        try:
            message_id = interaction.message.id if interaction.message else None
            entry = interaction_store.get(message_id) if message_id else None

            if not entry:
                await interaction.response.send_message(
                    "❓ Contexte introuvable pour cette relance.", ephemeral=True
                )
                logger.warning(
                    f"Bouton cr_booked sans contexte (message_id={message_id})",
                    category="cr_relance",
                )
                return

            context = entry.get("context", {})
            await _callback("rdv_confirmed", context, str(interaction.user.id))

            # Désactive le bouton et édite le message d'origine.
            button.disabled = True
            await interaction.response.edit_message(view=self)
            await interaction.followup.send(
                "✅ CR marqué comme réservé !", ephemeral=True
            )

            # Nettoyage pour éviter un double déclenchement.
            if message_id:
                interaction_store.delete(message_id)
            logger.success(
                f"CR réservé via bouton (message_id={message_id}, actor={interaction.user.id})",
                category="cr_relance",
            )
        except Exception as e:
            logger.error(f"Erreur bouton cr_booked : {e}", category="cr_relance")
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "Une erreur est survenue, réessayez plus tard.", ephemeral=True
                    )
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class CRRelanceCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        # Ré-enregistre les Views persistantes : les boutons restent
        # fonctionnels même pour des messages envoyés avant un redémarrage.
        self.bot.add_view(CRBookedView())
        self.bot.add_view(CRReplyView())
        logger.success(
            "Views persistantes CRBookedView + CRReplyView enregistrées",
            category="cr_relance",
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CRRelanceCog(bot))
