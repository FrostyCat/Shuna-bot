from db import Session, init_db
from models import Attack
from sqlalchemy import func

init_db()
session = Session()

all_attacks = session.query(Attack).order_by(Attack.id).all()

seen = {}
to_delete = []

for attack in all_attacks:
    key = (attack.player_id, attack.defender, attack.stars, attack.destruction, attack.is_attack)
    if key in seen:
        to_delete.append(attack)
    else:
        seen[key] = attack.id

print(f"Found {len(to_delete)} duplicates to remove.")

for attack in to_delete:
    session.delete(attack)

session.commit()
session.close()

print("Done.")
