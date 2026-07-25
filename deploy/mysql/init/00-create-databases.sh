#!/usr/bin/env bash
set -Eeuo pipefail

required_variables=(
  MYSQL_ROOT_PASSWORD
  BUSINESS_DB_NAME
  BUSINESS_DB_USER
  BUSINESS_DB_PASSWORD
  AGENT_DB_NAME
  AGENT_DB_USER
  AGENT_DB_PASSWORD
)

for variable_name in "${required_variables[@]}"; do
  if [[ -z "${!variable_name:-}" ]]; then
    echo "Required environment variable is missing: ${variable_name}" >&2
    exit 1
  fi
done

for identifier in "$BUSINESS_DB_NAME" "$BUSINESS_DB_USER" "$AGENT_DB_NAME" "$AGENT_DB_USER"; do
  if [[ ! "$identifier" =~ ^[A-Za-z0-9_]+$ ]]; then
    echo "Database names and users may contain only letters, digits, and underscores." >&2
    exit 1
  fi
done

for password in "$BUSINESS_DB_PASSWORD" "$AGENT_DB_PASSWORD"; do
  if [[ "$password" == *"'"* || "$password" == *"\\"* || "$password" == *$'\n'* || "$password" == *$'\r'* ]]; then
    echo "Database passwords contain unsupported SQL quoting characters." >&2
    exit 1
  fi
done

mysql --protocol=socket -uroot -p"$MYSQL_ROOT_PASSWORD" <<SQL
CREATE DATABASE IF NOT EXISTS \`${BUSINESS_DB_NAME}\`
  CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE IF NOT EXISTS \`${AGENT_DB_NAME}\`
  CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

CREATE USER IF NOT EXISTS '${BUSINESS_DB_USER}'@'%' IDENTIFIED BY '${BUSINESS_DB_PASSWORD}';
ALTER USER '${BUSINESS_DB_USER}'@'%' IDENTIFIED BY '${BUSINESS_DB_PASSWORD}';
GRANT ALL PRIVILEGES ON \`${BUSINESS_DB_NAME}\`.* TO '${BUSINESS_DB_USER}'@'%';

CREATE USER IF NOT EXISTS '${AGENT_DB_USER}'@'%' IDENTIFIED BY '${AGENT_DB_PASSWORD}';
ALTER USER '${AGENT_DB_USER}'@'%' IDENTIFIED BY '${AGENT_DB_PASSWORD}';
GRANT ALL PRIVILEGES ON \`${AGENT_DB_NAME}\`.* TO '${AGENT_DB_USER}'@'%';

SET GLOBAL time_zone = '+08:00';
FLUSH PRIVILEGES;
SQL

echo "Initialized isolated databases '${BUSINESS_DB_NAME}' and '${AGENT_DB_NAME}'."

