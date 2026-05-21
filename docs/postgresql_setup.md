# PostgreSQL Setup for TokenLeak

This guide creates a minimal-privilege PostgreSQL database for TokenLeak
with exec-related functions revoked from the application role.

## 1. Prerequisites

- PostgreSQL 14+ installed and running
- Access to `psql` as the `postgres` superuser

## 2. Create database, schema, and role

```sql
-- Run as postgres superuser
CREATE DATABASE tokenleak
    ENCODING 'UTF8'
    LC_COLLATE 'en_US.UTF-8'
    LC_CTYPE 'en_US.UTF-8'
    TEMPLATE template0;

\connect tokenleak

CREATE SCHEMA tokenleak;

-- Application role — no LOGIN yet
CREATE ROLE tokenleak_role NOLOGIN;

-- Login user for the application
CREATE USER tokenleak WITH
    PASSWORD 'REPLACE_WITH_STRONG_PASSWORD'
    NOSUPERUSER
    NOCREATEDB
    NOCREATEROLE
    NOINHERIT
    CONNECTION LIMIT 10;

GRANT tokenleak_role TO tokenleak;
```

## 3. Grant minimal privileges

```sql
\connect tokenleak

-- Schema access
GRANT USAGE ON SCHEMA tokenleak TO tokenleak_role;
GRANT USAGE ON SCHEMA public    TO tokenleak_role;

-- Table privileges (application needs: SELECT, INSERT, UPDATE on its own tables)
-- These will cover tables created by the application on first run.
ALTER DEFAULT PRIVILEGES IN SCHEMA tokenleak
    GRANT SELECT, INSERT, UPDATE ON TABLES TO tokenleak_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA tokenleak
    GRANT USAGE, SELECT ON SEQUENCES TO tokenleak_role;

-- Allow creating tables (needed for first-run schema migration)
GRANT CREATE ON SCHEMA tokenleak TO tokenleak_role;
```

## 4. Revoke dangerous exec-capable functions

PostgreSQL has built-in functions that can read/write files or execute OS commands.
Revoke them from PUBLIC and ensure the application role cannot use them.

```sql
\connect tokenleak

-- File I/O functions (superuser-only by default, but revoke from PUBLIC explicitly)
REVOKE EXECUTE ON FUNCTION pg_read_file(text)           FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION pg_read_file(text, bigint, bigint) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION pg_read_binary_file(text)    FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION pg_read_binary_file(text, bigint, bigint) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION pg_ls_dir(text)              FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION pg_ls_dir(text, boolean, boolean) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION pg_stat_file(text)           FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION pg_stat_file(text, boolean)  FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION pg_write_file(text, text)    FROM PUBLIC;

-- lo_import / lo_export — large object file access
REVOKE EXECUTE ON FUNCTION lo_import(text)               FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION lo_import(text, oid)          FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION lo_export(oid, text)          FROM PUBLIC;

-- COPY is not a function — restrict via pg_hba / role (see below)
-- Ensure the tokenleak role cannot use COPY TO/FROM PROGRAM
-- This is enforced by NOT granting SUPERUSER.
```

> **Note**: `COPY TO/FROM PROGRAM` requires superuser or `pg_execute_server_program` role.
> The `tokenleak` user has neither, so this is safe by default.

## 5. Configure pg_hba.conf

Restrict the `tokenleak` user to connect only from localhost with password auth:

```
# /etc/postgresql/15/main/pg_hba.conf (adjust path for your system)
# TYPE  DATABASE    USER        ADDRESS         METHOD
local   tokenleak   tokenleak                   scram-sha-256
host    tokenleak   tokenleak   127.0.0.1/32    scram-sha-256
host    tokenleak   tokenleak   ::1/128         scram-sha-256
```

Reload PostgreSQL after changes:
```bash
sudo systemctl reload postgresql
```

## 6. Configure search_path

The application connects with `options="-c search_path=tokenleak,public"` to ensure
all DDL is created in the `tokenleak` schema by default. Verify this works:

```bash
psql "postgresql://tokenleak:PASSWORD@localhost/tokenleak?options=-c%20search_path%3Dtokenleak,public"
\dt tokenleak.*
```

## 7. Environment variables

```bash
TOKENLEAK_DB_TYPE=postgres
TOKENLEAK_DB_HOST=localhost
TOKENLEAK_DB_PORT=5432
TOKENLEAK_DB_NAME=tokenleak
TOKENLEAK_DB_USER=tokenleak
TOKENLEAK_DB_PASSWORD=REPLACE_WITH_STRONG_PASSWORD
```

## 8. Verify privileges

After the application has run once and created its schema, verify the role has
no extra privileges:

```sql
\connect tokenleak

-- Should show only SELECT/INSERT/UPDATE on tokenleak tables
\dp tokenleak.*

-- Confirm no superuser
SELECT usename, usesuper, usecreatedb, usecreaterole
FROM pg_user WHERE usename = 'tokenleak';
```

Expected output for the last query:
```
 usename  | usesuper | usecreatedb | usecreaterole
----------+----------+-------------+---------------
 tokenleak | f        | f           | f
```

## 9. Backup

```bash
pg_dump -U postgres -d tokenleak -n tokenleak -F c -f tokenleak_backup.dump
```

Restore:
```bash
pg_restore -U postgres -d tokenleak -n tokenleak tokenleak_backup.dump
```
