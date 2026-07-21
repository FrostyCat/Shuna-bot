from db import Session
from models import Player

session = Session()
try:
    count = session.query(Player).count()
    print(f"Połączenie OK — liczba graczy w bazie: {count}")
except Exception as e:
    print(f"Błąd połączenia: {e}")
finally:
    session.close()
