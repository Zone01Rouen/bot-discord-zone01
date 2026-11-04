import aiohttp
from datetime import datetime, timedelta
from utils.config_loader import HUB_FEEDBACK_URL, LEARNER_FEEDBACK_SECRET

# Les retours apprenants partent vers le hub (POST /api/learner-feedback), qui
# remplace la base Notion « Retours Apprenants (AUTO) ». Contrat côté hub :
# docs/RETOURS_APPRENANTS.md. Même rangement que Notion : une réponse par
# apprenant et par période « Mois du JJ/MM/AAAA », une nouvelle réponse sur la
# même période remplace la précédente.
if not LEARNER_FEEDBACK_SECRET:
    print("⚠️ LEARNER_FEEDBACK_SECRET non défini dans .env : le hub refusera l'enregistrement des réponses (401).")


def get_first_monday_of_month(reference_date=None):
    """Retourne le premier lundi du mois (UTC)"""
    if reference_date is None:
        reference_date = datetime.utcnow()

    first_day = reference_date.replace(day=1)
    days_to_monday = (7 - first_day.weekday()) % 7
    first_monday = first_day + timedelta(days=days_to_monday)
    return first_monday.replace(hour=0, minute=0, second=0, microsecond=0)


def build_feedback_payload(user, response, now=None):
    """Corps envoyé au hub pour une réponse reçue en DM."""
    if now is None:
        now = datetime.utcnow()
    return {
        # Pseudo Discord, comme la colonne « pseudo » de Notion : le hub le relie
        # à l'apprenant, ou garde le retour « non rattaché » s'il ne le connaît pas.
        "pseudo": user,
        "period": get_first_monday_of_month(now).strftime("Mois du %d/%m/%Y"),
        "text": response,
        "receivedAt": now.isoformat() + "Z",
    }


def check_hub_result(status, body):
    """Résultat du hub pour un retour : (rattaché ?) ; lève une erreur sinon.

    Le hub répond 200 dès que le corps est lisible, même si le retour est
    refusé : le verdict est dans data.results[0]. Le texte de la réponse n'est
    jamais repris dans l'erreur (il peut contenir des éléments de vie privée).
    """
    if status != 200 or not isinstance(body, dict) or not body.get("success"):
        raise RuntimeError(f"hub HTTP {status}")
    results = (body.get("data") or {}).get("results") or []
    if not results:
        raise RuntimeError("hub : réponse sans résultat")
    result = results[0]
    if not result.get("ok"):
        raise RuntimeError(f"hub : retour refusé ({result.get('error')})")
    return bool(result.get("attributed"))


async def add_response_to_hub(user, response):
    headers = {"Authorization": f"Bearer {LEARNER_FEEDBACK_SECRET}"}
    async with aiohttp.ClientSession() as session:
        async with session.post(
            HUB_FEEDBACK_URL,
            json=build_feedback_payload(user, response),
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            try:
                body = await resp.json(content_type=None)
            except ValueError:
                # Page d'erreur HTML du proxy (502…) : seul le statut compte.
                body = None
            return check_hub_result(resp.status, body)
