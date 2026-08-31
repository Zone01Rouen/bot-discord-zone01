import os

import aiohttp

# Le hub n'expose plus cette route sans authentification : elle recalcule la
# timeline des promotions et met à jour leur statut. Le bot n'a pas de compte,
# il s'authentifie donc avec le secret partagé des appels machine — le même que
# les crons du VPS (CRON_SECRET côté hub, HUB_API_SECRET ici).
HUB_TIMELINE_URL = os.getenv(
    "HUB_TIMELINE_URL", "https://hub.zone01normandie.org/api/timeline_project"
)
HUB_API_SECRET = os.getenv("HUB_API_SECRET", "")


async def fetch_progress():
    headers = {}
    if HUB_API_SECRET:
        headers["Authorization"] = f"Bearer {HUB_API_SECRET}"
    else:
        print("HUB_API_SECRET manquant : le hub refusera la requete (401)")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(HUB_TIMELINE_URL, headers=headers) as response:
                response.raise_for_status()
                data = await response.json()
                return data
    except aiohttp.ClientError as e:
        print(f"Error fetching progress: {e}")
        return None
