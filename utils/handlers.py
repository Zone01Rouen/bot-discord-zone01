import discord
from discord import DMChannel
from utils.models import add_response_to_hub
from utils.logger import logger

async def handle_dm(bot, message):
    """Gère les messages privés reçus par le bot"""
    if isinstance(message.channel, DMChannel) and not message.author.bot:
        # Les réponses des Helpers sont maintenant gérées par réactions
        # Ce handler gère uniquement les autres messages DM (retours apprenants → hub)
        loading_msg = await message.channel.send("⏳ Enregistrement en cours...")
        try:
            user = message.author.name
            response = message.content

            attributed = await add_response_to_hub(user, response)

            await loading_msg.delete()
            await message.channel.send("✅ Réponse enregistrée, merci !")
            if attributed:
                logger.info(f"Réponse enregistrée dans le hub de {user}", category="retours")
            else:
                logger.warning(f"Réponse de {user} enregistrée dans le hub, pseudo non rattaché à un apprenant", category="retours")

        except Exception as e:
            await loading_msg.delete()
            logger.error(f"Erreur hub (retours) pour {message.author.name} : {e}", category="retours")
            await message.channel.send("❌ Une erreur est survenue lors de l'enregistrement.")
