-- Schéma idempotent : rejouable sans erreur.

CREATE TABLE IF NOT EXISTS processed_files (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    filename     TEXT        NOT NULL,
    content_hash CHAR(64)    NOT NULL UNIQUE,  -- SHA-256 en hexadécimal
    processed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS transactions (
    id                   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    file_id              BIGINT         NOT NULL REFERENCES processed_files (id),
    datetime_transaction TIMESTAMP      NOT NULL,
    iban_origine         TEXT           NOT NULL,
    pays_source          TEXT           NOT NULL,
    banque_source        TEXT           NOT NULL,
    iban_destinataire    TEXT           NOT NULL,
    pays_destinataire    TEXT           NOT NULL,
    montant              NUMERIC(14, 2) NOT NULL CHECK (montant > 0),
    is_flagged           BOOLEAN        NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_transactions_file_id ON transactions (file_id);

CREATE TABLE IF NOT EXISTS total_sent_by_iban (
    iban  TEXT PRIMARY KEY,
    total NUMERIC(14, 2) NOT NULL
);

CREATE TABLE IF NOT EXISTS total_sent_by_bank (
    banque TEXT PRIMARY KEY,
    total  NUMERIC(14, 2) NOT NULL
);

CREATE TABLE IF NOT EXISTS total_received_by_iban (
    iban  TEXT PRIMARY KEY,
    total NUMERIC(14, 2) NOT NULL
);
