"""Transparent multi-label coding. Each label describes wording, not a finding."""

import re

from evidence.text import fold_text

BRAND = re.compile(r"allianz|euler[ -]+hermes|安[联聯]|アリアンツ|알리안츠", re.I)

TOPICS = {
    "Claims": r"\b(claims?|schaden\w*|schadenfall|sinistres?|siniestros?|sinistr[oi]|karigeny\w*)\b",
    "Premiums and pricing": r"\b(premiums?|pric(e|ing)|praemi\w*|primes?|tarif\w*|prim[ae]|beitrag\w*)\b",
    "Cancellation and renewal": r"\b(cancel\w*|renew\w*|(?:ge)?kuendig\w*|resili\w*|rinnovo|renovaci\w*)\b",
    "Customer service": r"\b(customer service|support|kundenservice|kundendienst|service client|assistenza)\b",
    "Travel insurance": r"\b(travel|trip|flug\w*|reise\w*|voyage\w*|viaggi\w*|viaje\w*)\b",
    "Investments and shares": r"\b(shares?|stocks?|dividend\w*|aktie\w*|invest\w*|asset management|allianzgi)\b",
    "Sport and venues": r"\b(arena|stadium|stadion|stade|stadio|parque|allianz field|football|bundesliga)\b",
}
CUES = {
    "Mentions a refusal": r"\b(denied|declined|refused|abgelehnt|verweigert|refusee?|rifiutat\w*|rechazad\w*)\b",
    "Mentions waiting": r"\b(waiting|still waiting|delay(?:ed|s)?|warte\w*|attends|attente|ritardo|esperando)\b",
    "Mentions a payout": r"\b(paid out|paid my claim|reimbursed me|ausgezahlt|zurueckerstattet|remboursement recu|rimborsato)\b",
    "Uses praise wording": r"\b(great service|excellent service|very satisfied|sehr zufrieden|tres satisfait\w*|ottimo servizio)\b",
    "Uses complaint wording": r"\b(terrible service|poor service|very disappointed|beschwerde\w*|unzufrieden|reclamation\w*|pessimo servizio)\b",
    "Asks a question": r"\?",
}
RULES = {
    group: {name: re.compile(expr) for name, expr in labels.items()}
    for group, labels in (("topic", TOPICS), ("cue", CUES))
}


def labels(text):
    folded = fold_text(text)
    return [
        (group, name)
        for group, rules in RULES.items()
        for name, pattern in rules.items()
        if pattern.search(folded)
    ]
