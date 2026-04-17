"""
Redshift Query tool for Strands integration.
Allows AI to query firmware update statistics from Redshift.
"""

import json
import logging
import os

import boto3
from botocore.exceptions import ClientError

from app.repositories.models.custom_bot import BotModel
from strands import tool

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Configuration
REDSHIFT_LAMBDA_FUNCTION = os.environ.get("REDSHIFT_LAMBDA_FUNCTION", "redshift_query")
REDSHIFT_LAMBDA_REGION = os.environ.get("REDSHIFT_LAMBDA_REGION", "us-east-1")

# Schema info for tool description
SCHEMA_DESCRIPTION = """
Available tables:
1. fus_model_installed (date, model_number, installed_version, reqs) - Firmware installations
2. fus_model (date, model_number, reqs) - Model requests
3. fus_model_hardware (date, model_number, hardware_version, reqs) - Hardware versions
4. fus_model_status (date, model_number, status_code, reqs) - Status codes

Example models: WHW03, EA6350, E7350, MR7500, etc.
"""


def _invoke_lambda(function_name: str, payload: dict, region: str = REDSHIFT_LAMBDA_REGION) -> dict:
    """Invoke the Redshift query Lambda function."""
    logger.info(f"[REDSHIFT_QUERY] Invoking Lambda: {function_name}")
    logger.info(f"[REDSHIFT_QUERY] Payload: {json.dumps(payload)}")

    try:
        lambda_client = boto3.client("lambda", region_name=region)

        response = lambda_client.invoke(
            FunctionName=function_name,
            InvocationType="RequestResponse",
            Payload=json.dumps(payload),
        )

        response_payload = json.loads(response["Payload"].read().decode("utf-8"))

        if "FunctionError" in response:
            logger.error(f"[REDSHIFT_QUERY] Function error: {response_payload}")
            return {
                "success": False,
                "error": "Lambda function execution error",
                "details": response_payload,
            }

        # Parse the body from Lambda response
        body = response_payload.get("body", "{}")
        if isinstance(body, str):
            body = json.loads(body)

        if body.get("status") == "success":
            return {
                "success": True,
                "message": body.get("message", ""),
                "data": body.get("data", {}),
            }
        else:
            return {
                "success": False,
                "error": body.get("message", "Unknown error"),
            }

    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        error_message = e.response["Error"]["Message"]
        logger.error(f"[REDSHIFT_QUERY] AWS Error: {error_code} - {error_message}")
        return {
            "success": False,
            "error": f"AWS Error: {error_code}",
            "message": error_message,
        }
    except Exception as e:
        logger.error(f"[REDSHIFT_QUERY] Error: {str(e)}")
        return {
            "success": False,
            "error": str(e),
        }


def create_redshift_query_tool(bot: BotModel | None = None):
    """Create Redshift query tool with bot context closure."""

    @tool
    def redshift_query(sql_query: str) -> str:
        """
        Execute SQL query on Redshift to get firmware update statistics.
        Use this tool when users ask about firmware versions, device models, installation counts, or update statistics.

        Available tables:
        1. fus_model_installed (date, model_number, installed_version, reqs) - Firmware installation counts
        2. fus_model (date, model_number, reqs) - Model request counts
        3. fus_model_hardware (date, model_number, hardware_version, reqs) - Hardware version counts
        4. fus_model_status (date, model_number, status_code, reqs) - Status code counts

        Args:
            sql_query: SQL SELECT query to execute. Only SELECT queries are allowed.
                      Must reference allowed tables (fus_model_installed, fus_model, fus_model_hardware, fus_model_status).
                      Use SUM(reqs) to get total counts. Use LIMIT to restrict results.

        Returns:
            str: Query results formatted as a table, or error message

        Examples:
            - Count WHW03 with specific version:
              SELECT SUM(reqs) as total FROM fus_model_installed WHERE model_number = 'WHW03' AND installed_version = '2.1.20.216877'
            - Get daily trend for a model:
              SELECT date, SUM(reqs) as total FROM fus_model WHERE model_number = 'WHW03' AND date >= '2026-04-01' GROUP BY date ORDER BY date
            - Top 10 installed versions for a model:
              SELECT installed_version, SUM(reqs) as total FROM fus_model_installed WHERE model_number = 'WHW03' GROUP BY installed_version ORDER BY total DESC LIMIT 10
        """
        logger.info(f"[REDSHIFT_QUERY] SQL: {sql_query}")
        logger.debug(f"[REDSHIFT_QUERY] Bot context: {bot.id if bot else 'None'}")

        payload = {
            "action": "query",
            "sql": sql_query,
        }

        result = _invoke_lambda(REDSHIFT_LAMBDA_FUNCTION, payload)

        if result["success"]:
            return result.get("message", "Query executed successfully")
        else:
            error = result.get("error", "Unknown error")
            return f"Query failed: {error}"

    return redshift_query


def create_redshift_schema_tool(bot: BotModel | None = None):
    """Create tool to get Redshift schema information."""

    @tool
    def redshift_schema() -> str:
        """
        Get the database schema information for Redshift tables.
        Use this tool to understand what tables and columns are available before writing queries.

        Returns:
            str: Schema information including table names, columns, and example queries
        """
        logger.info("[REDSHIFT_SCHEMA] Getting schema info")

        payload = {"action": "schema"}
        result = _invoke_lambda(REDSHIFT_LAMBDA_FUNCTION, payload)

        if result["success"]:
            return result.get("message", SCHEMA_DESCRIPTION)
        else:
            return SCHEMA_DESCRIPTION  # Return hardcoded schema as fallback

    return redshift_schema
