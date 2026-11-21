CREATE TABLE IF NOT EXISTS travel_security (
 owner_id BIGINT UNSIGNED PRIMARY KEY,
 recovery_digest BINARY(32),
 avatar MEDIUMBLOB,
 FOREIGN KEY (owner_id) REFERENCES osint_users(id) ON DELETE CASCADE
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS osint_login_checks (
 username VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
 failures INT UNSIGNED NOT NULL DEFAULT 0,
 challenge_digest BINARY(32),
 challenge_until DATETIME(6),
 expires_at DATETIME(6) NOT NULL,
 INDEX idx_login_check_expiry (expires_at)
) ENGINE=InnoDB;
INSERT IGNORE INTO osint_schema_versions(version) VALUES (3);
