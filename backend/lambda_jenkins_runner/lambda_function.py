"""
Jenkins Runner Lambda Function

Receives requests from the AI tool and triggers Jenkins jobs.
"""

import json
import logging
import os
import ssl
import urllib.request
import urllib.parse
import base64
from typing import Any

import boto3
from botocore.exceptions import ClientError

# Create SSL context that doesn't verify certificates (like curl -k)
SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.check_hostname = False
SSL_CONTEXT.verify_mode = ssl.CERT_NONE

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Configuration
JENKINS_URL = "https://jenkins-cloud.lswf.net"
JENKINS_SECRET_NAME = os.environ.get("JENKINS_SECRET_NAME", "prod/jenkins/cloud")
JENKINS_SECRET_REGION = os.environ.get("JENKINS_SECRET_REGION", "us-east-1")

# Cache for credentials
_cached_credentials = None


def get_jenkins_credentials() -> tuple[str, str]:
    """
    Get Jenkins credentials from AWS Secrets Manager.

    Returns:
        tuple: (account, token)
    """
    global _cached_credentials

    if _cached_credentials:
        return _cached_credentials

    try:
        client = boto3.client("secretsmanager", region_name=JENKINS_SECRET_REGION)
        response = client.get_secret_value(SecretId=JENKINS_SECRET_NAME)
        secret = json.loads(response["SecretString"])

        account = secret.get("account", "")
        token = secret.get("token", "")

        _cached_credentials = (account, token)
        logger.info("Successfully retrieved Jenkins credentials from Secrets Manager")
        return _cached_credentials

    except ClientError as e:
        logger.error(f"Failed to get Jenkins credentials: {e}")
        raise
    except Exception as e:
        logger.error(f"Error parsing Jenkins credentials: {e}")
        raise

# Job name mapping: maps (action, target) to Jenkins job name
# Customize this based on your Jenkins job structure
JOB_MAPPING = {
    # Format: (action, target): "jenkins-job-name"
    ("restart", "cloud1"): "restart-cloud1",
    ("restart", "cloud2"): "restart-cloud2",
    ("restart", "tomcat"): "restart-tomcat",
    ("deploy", "cloud1"): "deploy-cloud1",
    ("deploy", "cloud2"): "deploy-cloud2",
    ("stop", "tomcat"): "stop-tomcat",
    ("start", "tomcat"): "start-tomcat",
    ("build", "pinnacle"): "build-Pinnacle",
    # Add more mappings as needed
}

# Default job name pattern if not in mapping
# Uses format: {action}-{target}
DEFAULT_JOB_PATTERN = "{action}-{target}"


def get_job_name(action: str, environment: str, target: str) -> str:
    """
    Get Jenkins job name based on action and target.

    Override this function to customize job name resolution.
    """
    # Check mapping first
    key = (action.lower(), target.lower())
    if key in JOB_MAPPING:
        return JOB_MAPPING[key]

    # Fall back to default pattern
    return DEFAULT_JOB_PATTERN.format(action=action, target=target)


def get_jenkins_crumb(account: str, token: str) -> tuple[str, str] | None:
    """
    Get Jenkins CSRF crumb for API requests.

    Returns:
        tuple: (crumb_field, crumb_value) or None if CSRF is disabled
    """
    url = f"{JENKINS_URL}/crumbIssuer/api/json"
    request = urllib.request.Request(url)

    credentials = f"{account}:{token}"
    encoded_credentials = base64.b64encode(credentials.encode()).decode()
    request.add_header("Authorization", f"Basic {encoded_credentials}")

    try:
        with urllib.request.urlopen(request, timeout=10, context=SSL_CONTEXT) as response:
            data = json.loads(response.read().decode())
            crumb_field = data.get("crumbRequestField", "Jenkins-Crumb")
            crumb_value = data.get("crumb", "")
            logger.info(f"Got Jenkins crumb: {crumb_field}")
            return (crumb_field, crumb_value)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            # CSRF protection is disabled
            logger.info("Jenkins CSRF protection is disabled")
            return None
        logger.warning(f"Failed to get crumb: {e.code} {e.reason}")
        return None
    except Exception as e:
        logger.warning(f"Failed to get crumb: {e}")
        return None


def trigger_jenkins_job(job_name: str, environment: str, parameters: dict = None) -> dict[str, Any]:
    """
    Trigger a Jenkins job via REST API.

    Args:
        job_name: Name of the Jenkins job
        environment: Environment parameter to pass to the job
        parameters: Additional parameters for the job

    Returns:
        dict: Result of the Jenkins API call
    """
    # Get credentials first
    try:
        account, token = get_jenkins_credentials()
    except Exception as e:
        logger.error(f"Failed to get credentials: {e}")
        return {
            "status": "error",
            "message": f"Failed to get Jenkins credentials: {str(e)}",
        }

    # Build job URL
    if parameters:
        # Parameterized build
        url = f"{JENKINS_URL}/job/{job_name}/buildWithParameters"
        params = {"ENVIRONMENT": environment, **(parameters or {})}
        url = f"{url}?{urllib.parse.urlencode(params)}"
    else:
        # Simple build with environment parameter
        url = f"{JENKINS_URL}/job/{job_name}/buildWithParameters?ENVIRONMENT={environment}"

    logger.info(f"Triggering Jenkins job: {url}")

    # Create request with authentication
    request = urllib.request.Request(url, method="POST")

    # Add Basic Auth
    credentials = f"{account}:{token}"
    encoded_credentials = base64.b64encode(credentials.encode()).decode()
    request.add_header("Authorization", f"Basic {encoded_credentials}")

    # Get and add CSRF crumb if enabled
    crumb = get_jenkins_crumb(account, token)
    if crumb:
        crumb_field, crumb_value = crumb
        request.add_header(crumb_field, crumb_value)

    try:
        # Use SSL_CONTEXT to skip certificate verification (like curl -k)
        with urllib.request.urlopen(request, timeout=30, context=SSL_CONTEXT) as response:
            status_code = response.getcode()

            if status_code in (200, 201, 202):
                # Get queue URL from Location header if available
                queue_url = response.headers.get("Location", "")
                return {
                    "status": "triggered",
                    "message": f"Jenkins job '{job_name}' triggered successfully",
                    "queue_url": queue_url,
                }
            else:
                return {
                    "status": "error",
                    "message": f"Unexpected status code: {status_code}",
                }

    except urllib.error.HTTPError as e:
        logger.error(f"Jenkins HTTP error: {e.code} - {e.reason}")
        return {
            "status": "error",
            "message": f"Jenkins API error: {e.code} {e.reason}",
        }
    except urllib.error.URLError as e:
        logger.error(f"Jenkins URL error: {e.reason}")
        return {
            "status": "error",
            "message": f"Failed to connect to Jenkins: {e.reason}",
        }
    except Exception as e:
        logger.error(f"Jenkins error: {str(e)}")
        return {
            "status": "error",
            "message": str(e),
        }


def lambda_handler(event: dict, context: Any) -> dict[str, Any]:
    """
    Lambda handler for Jenkins runner.

    Expected event format:
    {
        "action": "restart|deploy|stop|start|status",
        "environment": "QA|staging|production|dev",
        "target": "cloud1|cloud2|tomcat|..."
    }
    """
    logger.info(f"Received event: {json.dumps(event)}")

    # Extract parameters
    action = event.get("action", "").lower()
    environment = event.get("environment", "")
    target = event.get("target", "")

    # Validate required fields
    if not action:
        return {
            "statusCode": 400,
            "body": json.dumps({"status": "error", "message": "Missing 'action' parameter"})
        }

    if not environment:
        return {
            "statusCode": 400,
            "body": json.dumps({"status": "error", "message": "Missing 'environment' parameter"})
        }

    if not target:
        return {
            "statusCode": 400,
            "body": json.dumps({"status": "error", "message": "Missing 'target' parameter"})
        }

    # Handle status check differently
    if action == "status":
        # For status, we might want to check job status instead of triggering
        # This is a placeholder - customize based on your needs
        return {
            "statusCode": 200,
            "body": json.dumps({
                "status": "info",
                "message": f"Status check for {environment}/{target} - not implemented yet"
            })
        }

    # Get job name and trigger
    job_name = get_job_name(action, environment, target)
    logger.info(f"Resolved job name: {job_name}")

    result = trigger_jenkins_job(job_name, environment)

    status_code = 200 if result["status"] in ("triggered", "info") else 500

    return {
        "statusCode": status_code,
        "body": json.dumps(result)
    }
