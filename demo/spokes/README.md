# Spokes

Order service of the Spokes online bike shop (FastAPI, SQLAlchemy, SQLite).

```
pip install -r requirements.txt
uvicorn app.main:app --port 8000
python seed.py
pytest
```

See [docs/INTERNAL.md](docs/INTERNAL.md) for the data model, the payment flow and known data quirks.
