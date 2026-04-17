"""
Redshift Query tool for legacy agents.
Allows AI to query firmware update statistics from Redshift.
"""

import json
import logging
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError

from app.agents.tools.agent_tool import AgentTool
from app.repositories.models.custom_bot import BotModel
from app.routes.schemas.conversation import type_model_name
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Configuration
REDSHIFT_LAMBDA_FUNCTION = os.environ.get("REDSHIFT_LAMBDA_FUNCTION", "redshift_query")
REDSHIFT_LAMBDA_REGION = os.environ.get("REDSHIFT_LAMBDA_REGION", "us-east-1")


class RedshiftQueryInput(BaseModel):
    """Input schema for Redshift query tool"""

    sql_query: str = Field(
        description=(
            "SQL SELECT query to execute on Redshift. Only SELECT queries are allowed. "
            "Must reference allowed tables: fus_model_installed, fus_model, fus_model_hardware, fus_model_status. "
            "Use SUM(reqs) to get total counts. Use LIMIT to restrict results."
        )
    )


class RedshiftSchemaInput(BaseModel):
    """Input schema for Redshift schema tool"""
    pass  # No input needed


def _invoke_lambda(function_name: str, payload: dict, region: str = REDSHIFT_LAMBDA_REGION) -> dict:
    """Invoke the Redshift query Lambda function."""
    logger.info(f"[REDSHIFT_QUERY] Invoking Lambda: {function_name}")

    try:
        lambda_client = boto3.client("lambda", region_name=region)

        response = lambda_client.invoke(
            FunctionName=function_name,
            InvocationType="RequestResponse",
            Payload=json.dumps(payload),
        )

        response_payload = json.loads(response["Payload"].read().decode("utf-8"))

        if "FunctionError" in response:
            return {
                "success": False,
                "error": "Lambda function execution error",
                "details": response_payload,
            }

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


def _redshift_query_function(
    input_data: RedshiftQueryInput,
    bot: BotModel | None,
    model: type_model_name | None,
) -> dict[str, Any]:
    """Execute Redshift query."""
    payload = {
        "action": "query",
        "sql": input_data.sql_query,
    }

    result = _invoke_lambda(REDSHIFT_LAMBDA_FUNCTION, payload)

    if result["success"]:
        return {
            "content": result.get("message", "Query executed successfully"),
            "source_name": "Redshift Query",
            "data": result.get("data", {}),
        }
    else:
        return {
            "content": f"Query failed: {result.get('error', 'Unknown error')}",
            "source_name": "Redshift Query",
            "error": result.get("error"),
        }


def _redshift_schema_function(
    input_data: RedshiftSchemaInput,
    bot: BotModel | None,
    model: type_model_name | None,
) -> dict[str, Any]:
    """Get Redshift schema info."""
    payload = {"action": "schema"}
    result = _invoke_lambda(REDSHIFT_LAMBDA_FUNCTION, payload)

    if result["success"]:
        return {
            "content": result.get("message", ""),
            "source_name": "Redshift Schema",
        }
    else:
        # Return hardcoded schema as fallback
        return {
            "content": """Available tables:
1. fus_model_installed (date, model_number, installed_version, reqs) - Firmware installations
2. fus_model (date, model_number, reqs) - Model requests
3. fus_model_hardware (date, model_number, hardware_version, reqs) - Hardware versions
4. fus_model_status (date, model_number, status_code, reqs) - Status codes""",
            "source_name": "Redshift Schema",
        }


# Create tool instances
redshift_query_tool = AgentTool(
    name="redshift_query",
    description=(
        "Execute SQL query on Redshift to get firmware update statistics. "
        "Use this tool when users ask about firmware versions, device models, installation counts, or update statistics. "
        "Available tables: fus_model_installed, fus_model, fus_model_hardware, fus_model_status. "
        "Example: 'SELECT SUM(reqs) FROM fus_model_installed WHERE model_number = 'WHW03' AND installed_version = '2.1.20.216877'"
    ),
    args_schema=RedshiftQueryInput,
    function=_redshift_query_function,
)

redshift_schema_tool = AgentTool(
    name="redshift_schema",
    description=(
        "Get database schema information for Redshift tables. "
        "Use this tool to understand what tables and columns are available before writing queries."
    ),
    args_schema=RedshiftSchemaInput,
    function=_redshift_schema_function,
)
