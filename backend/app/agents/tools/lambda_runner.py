"""
Lambda Runner tool for executing AWS Lambda functions.
This tool allows AI to invoke Lambda functions like Jenkins jobs, deployments, etc.
"""

import json
import logging
import os
from typing import Any, Literal

import boto3
from botocore.exceptions import ClientError

from app.agents.tools.agent_tool import AgentTool
from app.repositories.models.custom_bot import BotModel
from app.routes.schemas.conversation import type_model_name
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Configuration
LAMBDA_RUNNER_REGION = os.environ.get("LAMBDA_RUNNER_REGION", "us-east-1")


class JenkinsRunnerInput(BaseModel):
    """Input schema for Jenkins runner tool"""

    action: Literal["restart", "deploy", "stop", "start", "status"] = Field(
        description="The action to perform on the target"
    )
    environment: Literal["QA", "staging", "production", "dev"] = Field(
        description="The environment name (QA, staging, production, dev)"
    )
    target: str = Field(
        description="The target system or service name (e.g., cloud1, cloud2, api-server, web-app)"
    )


class LambdaRunnerInput(BaseModel):
    """Generic input schema for Lambda runner tool"""

    function_name: str = Field(description="The name of the Lambda function to invoke")
    payload: dict = Field(
        default_factory=dict, description="The JSON payload to send to the Lambda function"
    )


def invoke_lambda(
    function_name: str,
    payload: dict,
    region: str = LAMBDA_RUNNER_REGION,
) -> dict[str, Any]:
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


def _jenkins_runner_function(
    input_data: JenkinsRunnerInput,
    bot: BotModel | None,
    model: type_model_name | None,
) -> dict[str, Any]:
    """
    Jenkins runner tool function.
    Invokes a Lambda function that triggers Jenkins jobs.

    Args:
        input_data: Jenkins runner input
        bot: Bot model (may contain custom Lambda function name)
        model: Model name (not used)

    Returns:
        dict: Result of the Jenkins job execution
    """
    # Get Lambda function name from bot config or use default
    lambda_function_name = "jenkins_runner"
    if bot and bot.agent and bot.agent.tools:
        for tool in bot.agent.tools:
            if hasattr(tool, "tool_type") and tool.tool_type == "lambda":
                if hasattr(tool, "lambdaConfig") and tool.lambdaConfig:
                    lambda_function_name = tool.lambdaConfig.function_name
                    break

    # Build payload for Jenkins Lambda
    payload = {
        "action": input_data.action,
        "environment": input_data.environment,
        "target": input_data.target,
    }

    result = invoke_lambda(lambda_function_name, payload)

    if result["success"]:
        return {
            "content": f"Successfully executed {input_data.action} on {input_data.environment}/{input_data.target}",
            "source_name": "Jenkins Runner",
            "details": result.get("result", {}),
        }
    else:
        return {
            "content": f"Failed to execute {input_data.action}: {result.get('error', 'Unknown error')}",
            "source_name": "Jenkins Runner",
            "error": result.get("message", result.get("error")),
        }


def _lambda_runner_function(
    input_data: LambdaRunnerInput,
    bot: BotModel | None,
    model: type_model_name | None,
) -> dict[str, Any]:
    """
    Generic Lambda runner tool function.

    Args:
        input_data: Lambda runner input
        bot: Bot model (not used)
        model: Model name (not used)

    Returns:
        dict: Result of the Lambda invocation
    """
    result = invoke_lambda(input_data.function_name, input_data.payload)

    if result["success"]:
        return {
            "content": f"Lambda function '{input_data.function_name}' executed successfully",
            "source_name": "Lambda Runner",
            "result": result.get("result", {}),
        }
    else:
        return {
            "content": f"Lambda function '{input_data.function_name}' failed: {result.get('error', 'Unknown error')}",
            "source_name": "Lambda Runner",
            "error": result.get("message", result.get("error")),
        }


# Create the Jenkins runner tool instance
jenkins_runner_tool = AgentTool(
    name="jenkins_runner",
    description=(
        "Execute Jenkins jobs to manage cloud environments and infrastructure. "
        "Use this tool when user wants to restart, deploy, stop, start, or check status of cloud services. "
        "Examples: 'restart QA cloud1', 'deploy to staging', 'check status of production api-server'"
    ),
    args_schema=JenkinsRunnerInput,
    function=_jenkins_runner_function,
)

# Create a generic Lambda runner tool instance
lambda_runner_tool = AgentTool(
    name="lambda_runner",
    description=(
        "Execute AWS Lambda functions directly. "
        "Use this tool when you need to invoke a specific Lambda function with custom payload."
    ),
    args_schema=LambdaRunnerInput,
    function=_lambda_runner_function,
)
