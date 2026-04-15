"""
Lambda Runner tool for Strands integration.
Allows AI to execute AWS Lambda functions like Jenkins jobs, deployments, etc.
"""

import json
import logging
import os
from typing import Literal

import boto3
from botocore.exceptions import ClientError

from app.repositories.models.custom_bot import BotModel
from strands import tool

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Configuration
LAMBDA_RUNNER_REGION = os.environ.get("LAMBDA_RUNNER_REGION", "us-east-1")
DEFAULT_JENKINS_FUNCTION = os.environ.get("JENKINS_LAMBDA_FUNCTION", "jenkins_runner")


def _invoke_lambda(
    function_name: str,
    payload: dict,
    region: str = LAMBDA_RUNNER_REGION,
) -> dict:
    """
    Invoke an AWS Lambda function.

    Args:
        function_name: Name or ARN of the Lambda function
        payload: JSON payload to send
        region: AWS region

    Returns:
        dict: Lambda response or error
    """
    logger.info(f"[LAMBDA_RUNNER] Invoking Lambda: {function_name}")
    logger.info(f"[LAMBDA_RUNNER] Payload: {json.dumps(payload)}")

    try:
        lambda_client = boto3.client("lambda", region_name=region)

        response = lambda_client.invoke(
            FunctionName=function_name,
            InvocationType="RequestResponse",
            Payload=json.dumps(payload),
        )

        # Read response payload
        response_payload = json.loads(response["Payload"].read().decode("utf-8"))

        # Check for Lambda execution errors
        if "FunctionError" in response:
            logger.error(f"[LAMBDA_RUNNER] Function error: {response_payload}")
            return {
                "success": False,
                "error": "Lambda function execution error",
                "details": response_payload,
            }

        logger.info(f"[LAMBDA_RUNNER] Success: {response_payload}")
        return {
            "success": True,
            "result": response_payload,
        }

    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        error_message = e.response["Error"]["Message"]
        logger.error(f"[LAMBDA_RUNNER] AWS Error: {error_code} - {error_message}")
        return {
            "success": False,
            "error": f"AWS Error: {error_code}",
            "message": error_message,
        }
    except Exception as e:
        logger.error(f"[LAMBDA_RUNNER] Error: {str(e)}")
        return {
            "success": False,
            "error": str(e),
        }


def create_jenkins_runner_tool(bot: BotModel | None = None):
    """Create Jenkins runner tool with bot context closure."""

    # Get Lambda function name from bot config or environment
    lambda_function_name = DEFAULT_JENKINS_FUNCTION
    if bot and bot.agent and bot.agent.tools:
        for tool_config in bot.agent.tools:
            if hasattr(tool_config, "tool_type") and tool_config.tool_type == "lambda":
                if hasattr(tool_config, "lambdaConfig") and tool_config.lambdaConfig:
                    lambda_function_name = tool_config.lambdaConfig.function_name
                    break

    @tool
    def jenkins_runner(
        action: Literal["restart", "deploy", "stop", "start", "status"],
        environment: Literal["QA", "staging", "production", "dev"],
        target: str,
    ) -> str:
        """
        Execute Jenkins jobs to manage cloud environments and infrastructure.
        Use this tool when user wants to restart, deploy, stop, start, or check status of cloud services.

        Args:
            action: The action to perform (restart, deploy, stop, start, status)
            environment: The environment name (QA, staging, production, dev)
            target: The target system or service name (e.g., cloud1, cloud2, tomcat, api-server, web-app)

        Returns:
            str: Result of the Jenkins job execution
        """
        logger.info(f"[JENKINS_RUNNER] Action: {action}, Env: {environment}, Target: {target}")
        logger.debug(f"[JENKINS_RUNNER] Bot context: {bot.id if bot else 'None'}")
        logger.debug(f"[JENKINS_RUNNER] Lambda function: {lambda_function_name}")

        # Build payload for Jenkins Lambda
        payload = {
            "action": action,
            "environment": environment,
            "target": target,
        }

        result = _invoke_lambda(lambda_function_name, payload)

        if result["success"]:
            details = result.get("result", {})
            return (
                f"Successfully executed '{action}' on {environment}/{target}.\n"
                f"Status: {details.get('status', 'completed')}\n"
                f"Message: {details.get('message', 'Job completed successfully')}"
            )
        else:
            error = result.get("error", "Unknown error")
            message = result.get("message", "")
            return f"Failed to execute '{action}' on {environment}/{target}.\nError: {error}\n{message}"

    return jenkins_runner


def create_lambda_runner_tool(bot: BotModel | None = None):
    """Create generic Lambda runner tool with bot context closure."""

    @tool
    def lambda_runner(function_name: str, payload: str = "{}") -> str:
        """
        Execute AWS Lambda functions directly.
        Use this tool when you need to invoke a specific Lambda function with custom payload.

        Args:
            function_name: The name or ARN of the Lambda function to invoke
            payload: JSON string payload to send to the Lambda function (default: "{}")

        Returns:
            str: Result of the Lambda invocation
        """
        logger.info(f"[LAMBDA_RUNNER] Function: {function_name}")
        logger.debug(f"[LAMBDA_RUNNER] Bot context: {bot.id if bot else 'None'}")

        try:
            payload_dict = json.loads(payload)
        except json.JSONDecodeError as e:
            return f"Error: Invalid JSON payload - {str(e)}"

        result = _invoke_lambda(function_name, payload_dict)

        if result["success"]:
            return (
                f"Lambda function '{function_name}' executed successfully.\n"
                f"Result: {json.dumps(result.get('result', {}), indent=2)}"
            )
        else:
            error = result.get("error", "Unknown error")
            message = result.get("message", "")
            return f"Lambda function '{function_name}' failed.\nError: {error}\n{message}"

    return lambda_runner
