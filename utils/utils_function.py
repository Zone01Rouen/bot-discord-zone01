import re
import discord
from discord import app_commands
from utils.config_loader import forbidden_words, admin_ids
from discord.ext import commands


def get_query_intern(self):
    """Renvoie la valeur actuelle de query_intern."""
    return self.query_intern


def get_query_fulltime(self):
    """Renvoie la valeur actuelle de query_fulltime."""
    return self.query_fulltime


def contains_forbidden_words(text):
    """Vérifie si un texte contient des mots interdits."""
    if text:
        text = text.lower()
        for word in forbidden_words:
            if word.lower() in text:
                return True
    return False


def extract_technologies(description, technologies):
    """Extrait les technologies mentionnées dans la description."""
    extracted_techs = []
    for tech in technologies:
        if re.search(rf"\b{re.escape(tech)}\b", description, re.IGNORECASE):
            extracted_techs.append(tech)
            # print(f"'{tech}' trouvé dans la description.")
        # else:
    # print(f"'{tech}' non trouvé dans la description.")
    return extracted_techs


def is_admin():
    """Décorateur pour les commandes normales (prefix commands)"""
    async def predicate(ctx):
        if ctx.author.id in admin_ids:
            return True
        else:
            raise commands.MissingPermissions(["administrator"])

    return commands.check(predicate)

def is_admin_slash():
    """Décorateur pour les slash commands (app_commands)"""
    async def predicate(interaction: discord.Interaction) -> bool:
        # Vérifier si l'utilisateur est admin ou fait partie des IDs autorisés
        return (interaction.user.guild_permissions.administrator or
                interaction.user.id in admin_ids)

    return app_commands.check(predicate)
