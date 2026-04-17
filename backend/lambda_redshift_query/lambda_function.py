"""
Redshift Query Lambda Function

Executes SQL queries on Redshift for firmware update statistics.
Uses redshift_connector for direct connection.
"""

import json
import logging
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError
import redshift_connector

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Configuration
REDSHIFT_SECRET_NAME = os.environ.get("REDSHIFT_SECRET_NAME", "prod/redshift/cret")
REDSHIFT_SECRET_REGION = os.environ.get("REDSHIFT_SECRET_REGION", "us-east-1")
REDSHIFT_DATABASE = os.environ.get("REDSHIFT_DATABASE", "dev")
REDSHIFT_PORT = int(os.environ.get("REDSHIFT_PORT", "5439"))

# Cache for credentials
_cached_credentials = None

# Allowed tables for security
ALLOWED_TABLES = [
    "fus_model_installed",
    "fus_model",
    "fus_model_hardware",
    "fus_model_status",
]

# Schema information for AI context
SCHEMA_INFO = """
Available tables in public schema:

1. fus_model_installed - Firmware installation statistics
   - date (DATE): Date of the record
   - model_number (VARCHAR): Device model (e.g., 'WHW03', 'EA6350')
   - installed_version (VARCHAR): Firmware version installed (e.g., '2.1.20.216877')
   - reqs (INTEGER): Number of requests/installations

2. fus_model - Model request statistics
   - date (DATE): Date of the record
   - model_number (VARCHAR): Device model
   - reqs (INTEGER): Number of requests

3. fus_model_hardware - Hardware version statistics
   - date (DATE): Date of the record
   - model_number (VARCHAR): Device model
   - hardware_version (VARCHAR): Hardware version
   - reqs (INTEGER): Number of requests

4. fus_model_status - Status code statistics
   - date (DATE): Date of the record
   - model_number (VARCHAR): Device model
   - status_code (INTEGER): Status code returned
   - reqs (INTEGER): Number of requests

Common queries:
- Count installations: SELECT SUM(reqs) FROM fus_model_installed WHERE model_number = 'XXX' AND installed_version = 'YYY'
- Daily trend: SELECT date, SUM(reqs) FROM fus_model WHERE model_number = 'XXX' GROUP BY date ORDER BY date
- Top models: SELECT model_number, SUM(reqs) as total FROM fus_model GROUP BY model_number ORDER BY total DESC LIMIT 10
"""


def get_redshift_credentials() -> dict:
    """
    Get Redshift credentials from AWS Secrets Manager.

    Returns:
        dict: {host, account, pwd}
    """
    global _cached_credentials

    if _cached_credentials:
        return _cached_credentials

    try:
        client = boto3.client("secretsmanager", region_name=REDSHIFT_SECRET_REGION)
        response = client.get_secret_value(SecretId=REDSHIFT_SECRET_NAME)
        secret = json.loads(response["SecretString"])

        _cached_credentials = {
            "host": secret.get("host", ""),
            "user": secret.get("account", ""),
            "password": secret.get("pwd", ""),
        }
        logger.info("Successfully retrieved Redshift credentials from Secrets Manager")
        return _cached_credentials

    except ClientError as e:
        logger.error(f"Failed to get Redshift credentials: {e}")
        raise
    except Exception as e:
        logger.error(f"Error parsing Redshift credentials: {e}")
        raise


def validate_query(sql: str) -> tuple[bool, str]:
    """
    Validate SQL query for security.

    Returns:
        tuple: (is_valid, error_message)
    """
    sql_upper = sql.strip().upper()

    # Only allow SELECT statements
    if not sql_upper.startswith("SELECT"):
        return False, "Only SELECT queries are allowed"

    # Block dangerous keywords
    dangerous_keywords = ["INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "ALTER", "CREATE", "GRANT", "REVOKE"]
    for keyword in dangerous_keywords:
        if keyword in sql_upper:
            return False, f"Query contains forbidden keyword: {keyword}"

    # Check that query only references allowed tables
    sql_lower = sql.lower()
    for table in ALLOWED_TABLES:
        if table in sql_lower:
            return True, ""

    # If no allowed table found, check if it's a simple query
    if "from" in sql_lower:
        return False, f"Query must reference one of: {', '.join(ALLOWED_TABLES)}"

    return True, ""


def execute_query(sql: str) -> dict[str, Any]:
    """
    Execute SQL query on Redshift.

    Args:
        sql: SQL SELECT query

    Returns:
        dict: {columns, rows, row_count}
    """
    credentials = get_redshift_credentials()

    conn = redshift_connector.connect(
        host=credentials["host"],
        database=REDSHIFT_DATABASE,
        port=REDSHIFT_PORT,
        user=credentials["user"],
        password=credentials["password"],
    )

    try:
        cursor = conn.cursor()
        cursor.execute(sql)

        # Get column names
        columns = [desc[0] for desc in cursor.description] if cursor.description else []

        # Fetch results (limit to 1000 rows for safety)
        rows = cursor.fetchmany(1000)

        # Convert to serializable format
        results = []
        for row in rows:
            row_dict = {}
            for i, col in enumerate(columns):
                value = row[i]
                # Convert non-serializable types
                if hasattr(value, 'isoformat'):
                    value = value.isoformat()
                elif isinstance(value, (bytes, bytearray)):
                    value = value.decode('utf-8', errors='replace')
                row_dict[col] = value
            results.append(row_dict)

        return {
            "columns": columns,
            "rows": results,
            "row_count": len(results),
        }

    finally:
        cursor.close()
        conn.close()


def format_results(results: dict) -> str:
    """Format query results as readable text."""
    if not results["rows"]:
        return "No results found."

    columns = results["columns"]
    rows = results["rows"]

    # Build table format
    output = []

    # Header
    output.append(" | ".join(columns))
    output.append("-" * len(output[0]))

    # Rows (limit display to 50)
    for row in rows[:50]:
        values = [str(row.get(col, "")) for col in columns]
        output.append(" | ".join(values))

    if len(rows) > 50:
        output.append(f"... and {len(rows) - 50} more rows")

    output.append(f"\nTotal: {results['row_count']} rows")

    return "\n".join(output)


def lambda_handler(event: dict, context: Any) -> dict[str, Any]:
    """
    Lambda handler for Redshift queries.

    Expected event format:
    {
        "action": "query" | "schema",
        "sql": "SELECT ..." (for action=query)
    }
    """
    logger.info(f"Received event: {json.dumps(event)}")

    action = event.get("action", "query")

    # Return schema info
    if action == "schema":
        return {
            "statusCode": 200,
            "body": json.dumps({
                "status": "success",
                "schema": SCHEMA_INFO,
            })
        }

    # Execute query
    sql = event.get("sql", "")

    if not sql:
        return {
            "statusCode": 400,
            "body": json.dumps({
                "status": "error",
                "message": "Missing 'sql' parameter"
            })
        }

    # Validate query
    is_valid, error_msg = validate_query(sql)
    if not is_valid:
        return {
            "statusCode": 400,
            "body": json.dumps({
                "status": "error",
                "message": error_msg
            })
        }

    try:
        results = execute_query(sql)
        formatted = format_results(results)

        return {
            "statusCode": 200,
            "body": json.dumps({
                "status": "success",
                "message": formatted,
                "data": results,
            })
        }

    except Exception as e:
        logger.error(f"Query error: {e}")
        return {
            "statusCode": 500,
            "body": json.dumps({
                "status": "error",
                "message": f"Query failed: {str(e)}"
            })
        }
