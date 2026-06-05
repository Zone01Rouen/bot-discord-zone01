import discord
import re
import json
from datetime import datetime
from utils.progress_fetcher import fetch_progress
from utils.config_loader import load_config
from utils.logger import logger


async def resolve_channel(bot, channel_id, *, promo="?", purpose="channel"):
    """Résout un salon de manière robuste.

    Tente d'abord le cache (`get_channel`) puis, si absent, interroge l'API
    Discord (`fetch_channel`). Retourne `None` et loggue précisément en cas
    d'échec, sans jamais lever d'exception.
    """
    if not channel_id:
        logger.warning(
            f"[progress] {purpose} non configuré (id={channel_id!r}) pour la promo {promo}.",
            category="progress",
        )
        return None

    channel = bot.get_channel(channel_id)
    if channel is not None:
        return channel

    # Cache non peuplé : on demande explicitement à l'API Discord.
    try:
        channel = await bot.fetch_channel(channel_id)
        return channel
    except discord.NotFound:
        logger.error(
            f"[progress] {purpose} introuvable (NotFound) id={channel_id} promo={promo}.",
            category="progress",
        )
    except discord.Forbidden:
        logger.error(
            f"[progress] Accès refusé (Forbidden) au {purpose} id={channel_id} promo={promo}.",
            category="progress",
        )
    except discord.HTTPException as e:
        logger.error(
            f"[progress] Erreur HTTP en récupérant le {purpose} id={channel_id} promo={promo}: {e}",
            category="progress",
        )
    except Exception as e:  # filet de sécurité : ne jamais casser le scheduler
        logger.error(
            f"[progress] Erreur inattendue en récupérant le {purpose} id={channel_id} promo={promo}: {e}",
            category="progress",
        )
    return None


def extract_current_project(item):
    """Extrait le nom du projet courant depuis `currentProjects`.

    Le champ peut être :
      - {"single": "Mini-framework"}  -> mono-piste
      - {"rust": "...", "java": "..."} -> multi-piste
    """
    current = item.get("currentProjects")
    if isinstance(current, dict):
        if "single" in current and current["single"]:
            return str(current["single"])
        parts = []
        for track in ("rust", "java"):
            val = current.get(track)
            if val:
                parts.append(f"{track.capitalize()}: {val}")
        if parts:
            return " | ".join(parts)
    # Rétro-compat éventuelle : ancien champ plat.
    if item.get("currentProject"):
        return str(item["currentProject"])
    return "Non spécifié"


async def fetch_and_send_progress(bot):
    """Récupère la progression et envoie les embeds dans les salons appropriés."""
    payload = await fetch_progress()
    config = load_config()

    # L'API renvoie {"success": bool, "data": [...], "timestamp": ...}.
    # On reste tolérant si jamais une liste brute est renvoyée.
    if isinstance(payload, dict):
        progress_data = payload.get("data", [])
    elif isinstance(payload, list):
        progress_data = payload
    else:
        progress_data = None

    if not progress_data:
        logger.warning(
            "[progress] Aucune donnée de progression à traiter pour le moment.",
            category="progress",
        )
        print("Impossible de récupérer les données de progression pour le moment.")
        return

    # Salon de modération : chargé depuis la config (pas en dur).
    channel_modo_id = config.get("channel_modo_id")
    channel_modo = None
    if channel_modo_id:
        channel_modo = await resolve_channel(
            bot, channel_modo_id, purpose="salon modération"
        )
    else:
        logger.warning(
            "[progress] 'channel_modo_id' absent de la config : les erreurs ne seront pas relayées sur Discord.",
            category="progress",
        )

    async def notify_modo(description):
        """Envoie un embed d'erreur au salon modération si disponible."""
        if channel_modo is None:
            return
        try:
            await channel_modo.send(
                embed=discord.Embed(
                    title="🚫 Erreur Automatique",
                    description=description,
                    color=discord.Color.red(),
                )
            )
        except discord.HTTPException as e:
            logger.error(
                f"[progress] Impossible d'envoyer l'alerte au salon modération: {e}",
                category="progress",
            )

    for raw_item in progress_data:
        # Ensure item is a dict (some sources may return JSON strings)
        item = raw_item
        if isinstance(raw_item, str):
            try:
                item = json.loads(raw_item)
            except Exception:
                logger.warning(
                    f"[progress] Item non parsable en JSON, ignoré: {raw_item!r}",
                    category="progress",
                )
                continue
        if not isinstance(item, dict):
            logger.warning(
                f"[progress] Type d'item inattendu ({type(item)}), ignoré.",
                category="progress",
            )
            continue

        # --- Extraction des champs (format API: promotion / timeline) ---
        promotion = item.get("promotion") or {}
        timeline = item.get("timeline") or {}

        promo_name = promotion.get("key") or item.get("promotionName")
        if not promo_name:
            logger.warning(
                "[progress] Item sans nom de promotion (promotion.key), ignoré.",
                category="progress",
            )
            continue

        # Statut : 'success'/'error' (string) côté API, bool en rétro-compat.
        status = item.get("status")
        if isinstance(status, str):
            is_success = status.lower() == "success"
        else:
            is_success = bool(item.get("success", True))

        # Progression
        try:
            progress_val = int(timeline.get("progress", item.get("progress", 0)))
        except (ValueError, TypeError):
            progress_val = 0
        completed = max(0, min(10, progress_val // 10))
        progress_emoji = "🟩" * completed + "🟥" * (10 - completed)

        current_project = extract_current_project(item)

        # --- Création de l'embed ---
        embed = discord.Embed(
            title=f"📚 Projet en cours : `{current_project}`",
            description=f"👤 **Promotion** : `{promo_name}`",
            color=discord.Color.green() if is_success else discord.Color.red(),
            timestamp=datetime.utcnow(),
        )
        embed.set_author(
            name="Suivi de progression Zone01",
            icon_url="https://example.com/logo.png",
        )
        embed.add_field(
            name="📈 Progression",
            value=f"`{progress_val}%`  \n{progress_emoji}",
            inline=True,
        )

        # Extraction de la date à partir de 'agenda'
        agenda = timeline.get("agenda", item.get("agenda", ["Non spécifié"]))
        agenda_str = agenda[0] if isinstance(agenda, list) and agenda else "Non spécifié"
        match = re.search(
            r"(Fin de la promo:|Fin du projet actuel :)\s*(\d{4}-\d{2}-\d{2})",
            str(agenda_str),
        )
        if match:
            date_str = match.group(2)
            try:
                date_obj = datetime.strptime(date_str, "%Y-%m-%d")
                formatted_date = (
                    f"Le {date_obj.day} {date_obj.strftime('%B')} {date_obj.year}"
                )
            except ValueError:
                formatted_date = "Non spécifié"
        else:
            formatted_date = "Non spécifié"

        embed.add_field(
            name="⏳ Échéance estimée",
            value=f"`{formatted_date}`",
            inline=True,
        )
        if item.get("notes"):
            embed.add_field(
                name="📝 **Notes supplémentaires**",
                value=item["notes"],
                inline=False,
            )
        embed.set_footer(
            text="Zone01 Normandie • Mise à jour automatique",
            icon_url="https://example.com/footer-icon.png",
        )

        # --- Résolution du salon de la promotion ---
        # Clé construite depuis le nom de promo de l'API : "P1 2022" -> channel_progress_P1_2022
        channel_name = f"channel_progress_{promo_name.replace(' ', '_')}"
        channel_id = config.get(channel_name)

        if not channel_id:
            logger.warning(
                f"[progress] Channel non configuré pour la promotion {promo_name} (clé attendue: {channel_name}).",
                category="progress",
            )
            await notify_modo(
                f"Channel non configuré pour la promotion {promo_name} (clé attendue: `{channel_name}`)."
            )
            continue

        channel = await resolve_channel(
            bot, channel_id, promo=promo_name, purpose="salon progression"
        )
        if channel is None:
            await notify_modo(
                f"Salon avec l'ID {channel_id} pour la promotion {promo_name} non trouvé."
            )
            continue

        # Supprimer les anciens messages du bot, puis envoyer le nouvel embed.
        try:
            async for message in channel.history(limit=100):
                if message.author == bot.user:
                    try:
                        await message.delete()
                    except discord.HTTPException:
                        pass
            await channel.send(embed=embed)
            logger.success(
                f"[progress] Embed envoyé pour {promo_name} dans #{getattr(channel, 'name', channel_id)}.",
                category="progress",
            )
        except discord.Forbidden:
            logger.error(
                f"[progress] Permissions insuffisantes pour poster dans le salon {channel_id} (promo {promo_name}).",
                category="progress",
            )
            await notify_modo(
                f"Permissions insuffisantes pour poster dans le salon {channel_id} (promo {promo_name})."
            )
        except discord.HTTPException as e:
            logger.error(
                f"[progress] Erreur HTTP en postant pour {promo_name} (salon {channel_id}): {e}",
                category="progress",
            )
            await notify_modo(
                f"Erreur HTTP en postant pour la promotion {promo_name} (salon {channel_id}): {e}"
            )
