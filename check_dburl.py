# check_dburl.py
import os
from urllib.parse import urlparse
from dotenv import load_dotenv

load_dotenv()
db = os.getenv("DATABASE_URL")
if not db:
    print("DATABASE_URL não encontrada no .env")
else:
    p = urlparse(db)
    print("scheme:", p.scheme)
    print("username:", p.username)
    print("hostname:", p.hostname)
    print("port:", p.port)
    print("database:", p.path.lstrip('/'))