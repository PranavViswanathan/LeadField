# LEADFIELD

> Modular local-business lead generation: discover businesses, audit their web
> presence, and draft a personalized cold email for every one - then explore it
> all in an animated dashboard.

No API keys. Discovery uses OpenStreetMap (Overpass) to find real local
businesses, with DuckDuckGo and Google as alternative web-search backends; every
email is written locally by [Ollama](https://ollama.com). You can trigger a scan
for any location straight from the dashboard and watch it populate live.

<img width="2024" height="1084" alt="image" src="https://github.com/user-attachments/assets/b37b45e8-78d7-458b-94fd-31413c27fc27" />

<img width="1892" height="504" alt="image" src="https://github.com/user-attachments/assets/3ef2f6b8-7604-414a-804f-af5d431bf978" />


<img width="1596" height="533" alt="image" src="https://github.com/user-attachments/assets/7d104ca2-4dcc-4143-808f-15ec50d9dd13" />

<img width="1592" height="918" alt="image" src="https://github.com/user-attachments/assets/d08e9ade-c863-42f5-b0f9-b7ae069d5c4c" />

<img width="1154" height="689" alt="image" src="https://github.com/user-attachments/assets/fdfef02f-6e3b-4d61-a764-394917cc59d2" />



```
search -> cluster -> audit -> draft -> store -> export
```

## Quickstart

```bash
make setup         # venv + dependencies
make ollama-pull   # pull the llama3.2 model
make run           # run the pipeline -> data/leads.db
make ui            # open the dashboard at http://localhost:8000
```

Run `make` on its own to see every available target. Prefer no Make?
`pip install -r requirements.txt && python run_local.py`.

## Docs

| Doc | What's inside |
|-----|---------------|
| [docs/architecture.md](docs/architecture.md) | Component map, design principles, module responsibilities |
| [docs/pipeline.md](docs/pipeline.md) | Stage flow, the website-vs-no-website branch, audit heuristics |
| [docs/data-model.md](docs/data-model.md) | SQLite schema (ER diagram), Pydantic models, idempotency |
| [docs/deployment.md](docs/deployment.md) | Local CLI and Airflow/Docker, configuration, troubleshooting |
| [docs/dashboard.md](docs/dashboard.md) | The web UI, its API, and design notes |
| [docs/testing.md](docs/testing.md) | Test suite layout and conventions |

## Project layout

```
lead_gen/
├── config.py             # typed Settings (env-overridable)
├── run_local.py          # CLI orchestrator
├── export.py             # incremental CSV export
├── tasks/                # standalone pipeline modules
│   ├── search.py  cluster.py  website_checker.py
│   ├── email_generator.py  ollama_client.py  storage.py  models.py
├── dags/lead_gen_dag.py  # Airflow DAG (same tasks, wired via XCom)
├── webapp/               # FastAPI + static animated dashboard
├── tests/                # pytest suite (mocked HTTP, temp SQLite)
├── docker-compose.yml    # Airflow (LocalExecutor) + Postgres
└── docs/                 # diagrams and topic docs
```

## How it works (in one breath)

A single `Business` model flows through the pipeline, enriched at each stage.
Businesses on a directory listing (Yelp, Facebook, ...) or with no URL are
flagged "no website" and get a *build-a-site* pitch; the rest are fetched,
audited for concrete weaknesses, and get an *improve-your-site* pitch that
references those findings. Everything lands in SQLite and exports to CSV.

The same task functions run two ways: the local CLI and an Airflow DAG. See
[docs/architecture.md](docs/architecture.md) to go deeper.
