# Pipeline

The pipeline runs five stages in sequence, then exports. Data flows as a list of
`Business` models that get enriched stage by stage, fanning out into `Email`
models at the generation step.

## Stage flow

```mermaid
flowchart LR
    A([search]) --> B([cluster])
    B --> C([website_checker])
    C --> D([persist businesses])
    D --> E([email_generator])
    E --> F([persist emails])
    F --> G([export CSV])

    style A fill:#1d2630,stroke:#f0a93b,color:#ece7da
    style C fill:#1d2630,stroke:#f0a93b,color:#ece7da
    style E fill:#1d2630,stroke:#ff6a3d,color:#ece7da
    style G fill:#1d2630,stroke:#7fe3c4,color:#ece7da
```

## Sequence (one business)

```mermaid
sequenceDiagram
    participant S as search
    participant C as cluster
    participant W as website_checker
    participant O as ollama_client
    participant E as email_generator
    participant DB as storage

    S->>S: query "dentists in Austin, TX"
    S->>C: Business(name, url, ...)
    C->>C: classify -> "medical"
    C->>W: Business(category="medical")
    alt url is a directory or missing
        W->>W: mark has_website = false
    else real website
        W->>W: fetch + extract observations
    end
    W->>E: enriched Business
    alt has_website
        E->>O: build "improve site" prompt
    else no website
        E->>O: build "build site" prompt
    end
    O-->>E: generated text (or fallback)
    E->>DB: Email(subject, body, type)
```

## Branching logic

The single most important branch is **website vs no website**, decided in
`website_checker.check_business`:

```mermaid
flowchart TD
    START{url present?} -->|no| NOSITE[has_website = false<br/>reason: no url]
    START -->|yes| DIR{directory domain?}
    DIR -->|yes| NOSITE2[has_website = false<br/>reason: directory listing]
    DIR -->|no| FETCH{fetch ok?}
    FETCH -->|no| NOSITE3[has_website = false<br/>reason: fetch error]
    FETCH -->|yes| AUDIT[has_website = true<br/>+ observations]

    NOSITE --> BUILD[[build_site email]]
    NOSITE2 --> BUILD
    NOSITE3 --> BUILD
    AUDIT --> IMPROVE[[improve_site email]]
```

## Website audit heuristics

`website_checker._build_observations` produces up to four observations that the
LLM references in the email:

- no mobile viewport meta tag (likely not responsive)
- missing meta description (SEO)
- missing or empty `<title>` tag
- very thin content (word count under 150)
- table-based layout (outdated markup)
- no recent copyright year
- no images detected

## Search backends

`search.py` supports three discovery backends, selected by
`LEADGEN_SEARCH_BACKEND`:

| Backend | Source | Notes |
|---------|--------|-------|
| `overpass` (default) | OpenStreetMap via Overpass | Real business entities; many have no `website` tag, which become "build a site" leads. Geocodes the location with Nominatim, then queries OSM tags mapped from each category. |
| `duckduckgo` | DuckDuckGo HTML endpoint | Keyless web search; returns roundup/listicle pages as often as individual sites. |
| `google` | googlesearch-python | Often returns nothing now (Google serves a JavaScript-gated page to scrapers). |

All backends return the same `Business` shape, so the rest of the pipeline is
identical regardless of source.

## Clustering

`cluster.classify` scores each business against a keyword map and assigns the
highest-scoring of: `restaurant`, `retail`, `medical`, `legal`, `construction`,
`beauty`, `fitness`, `automotive`, `professional_services`, or `other`.

## Throttling and resilience

- A configurable delay (`LEADGEN_SEARCH_DELAY_SECONDS`, default 2.5s) is inserted
  between Google queries.
- `ollama_client` retries transient network and 5xx errors with exponential
  backoff (tenacity) and falls back from the primary to the secondary model.
- Every stage degrades gracefully; see [architecture.md](architecture.md).
