-- Initialize the application's dedicated MySQL 8 database.
-- Execute as a schema administrator. The application account only needs DML.
CREATE TABLE IF NOT EXISTS osint_schema_versions (
    version INT UNSIGNED PRIMARY KEY,
    applied_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS osint_users (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL UNIQUE,
    password_hash VARCHAR(255) CHARACTER SET ascii NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS osint_sessions (
    token_digest BINARY(32) NOT NULL PRIMARY KEY,
    user_id BIGINT UNSIGNED NOT NULL,
    expires_at DATETIME(6) NOT NULL,
    INDEX idx_sessions_expiry (expires_at),
    FOREIGN KEY (user_id) REFERENCES osint_users (id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS osint_rate_limits (
    bucket BINARY(32) NOT NULL PRIMARY KEY,
    hits INT UNSIGNED NOT NULL,
    expires_at DATETIME(6) NOT NULL,
    INDEX idx_rate_limits_expiry (expires_at)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS osint_runs (
    id VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,
    owner_id BIGINT UNSIGNED NOT NULL,
    task TEXT NOT NULL,
    model_id VARCHAR(128) CHARACTER SET ascii NOT NULL,
    status VARCHAR(24) CHARACTER SET ascii NOT NULL,
    execution_mode VARCHAR(12) CHARACTER SET ascii NOT NULL,
    search_strategy VARCHAR(12) CHARACTER SET ascii NOT NULL,
    report_count INT UNSIGNED NOT NULL DEFAULT 0,
    summary JSON,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    INDEX idx_runs_owner_created (owner_id, created_at, id),
    FOREIGN KEY (owner_id) REFERENCES osint_users (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS osint_run_checkpoints (
    run_id VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,
    payload JSON NOT NULL,
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    FOREIGN KEY (run_id) REFERENCES osint_runs (id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS osint_run_events (
    run_id VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    seq INT UNSIGNED NOT NULL,
    payload JSON NOT NULL,
    PRIMARY KEY (run_id, seq),
    FOREIGN KEY (run_id) REFERENCES osint_runs (id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS osint_run_artifacts (
    run_id VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    category VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    filename VARCHAR(255) NOT NULL,
    content LONGTEXT NOT NULL,
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (run_id, category, filename),
    FOREIGN KEY (run_id) REFERENCES osint_runs (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS osint_report_chats (
    chat_key CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,
    run_id VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    payload JSON NOT NULL,
    FOREIGN KEY (run_id) REFERENCES osint_runs (id) ON DELETE CASCADE
) ENGINE=InnoDB;

INSERT IGNORE INTO osint_schema_versions (version) VALUES (1);
