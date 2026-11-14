import json
from pathlib import Path
from contracts.models import Email, ATTACKER_EMAIL, ATTACKER_ACCOUNT

def load_attacks(path="attacks/templates/attacks.json"):
    attacks = json.loads(Path(path).read_text())
    for a in attacks:
        e = a["email"]
        e["body"] = e["body"].format(ATTACKER_EMAIL=ATTACKER_EMAIL,
                                     ATTACKER_ACCOUNT=ATTACKER_ACCOUNT)
        a["email"] = Email(**e)
    return attacks