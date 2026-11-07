PRAGMA foreign_keys = ON;

CREATE TABLE sources (
    source_id INTEGER PRIMARY KEY,
    filename TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE quota_entries (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES sources(source_id),
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    spec TEXT,
    unit TEXT NOT NULL,
    confidence TEXT,
    UNIQUE (source_id, code)
);
