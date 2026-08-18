"""Error formatting utilities for deepflow.

This module provides consistent, prominent error formatting across the framework.
All error display logic is centralized here to maintain consistency and follow
Python best practices for error handling and logging.
"""

from __future__ import annotations

import traceback
from typing import Any


class ErrorFormatter:
    """Formats exceptions with prominent, readable output."""

    SEPARATOR = "=" * 80
    HEADER_TEMPLATE = "\n\n{separator}\n{title}\n{separator}"
    FOOTER_TEMPLATE = "{separator}\n\nFull Traceback:\n{traceback}\n{separator}\n"

    @classmethod
    def format_component_error(
        cls,
        component_name: str,
        error: Exception,
    ) -> tuple[str, str]:
        """Format a component execution error.

        Args:
            component_name: Name of the component that failed
            error: The exception that was raised

        Returns:
            Tuple of (log_message, result_message)
            - log_message: Detailed message for logging
            - result_message: Concise message for result status
        """
        error_type = type(error).__name__
        error_msg = str(error) or f"{error_type} (no message)"

        # Extract error location from traceback
        error_location = cls._extract_error_location(error)

        # Build detailed log message
        header = cls.HEADER_TEMPLATE.format(
            separator=cls.SEPARATOR,
            title="COMPONENT ERROR",
        )

        body_lines = [
            f"Component:  {component_name}",
            f"Error Type: {error_type}",
            f"Message:    {error_msg}",
        ]
        if error_location:
            body_lines.append(f"Location:   {error_location}")

        body = "\n".join(body_lines) + "\n"

        tb_str = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        footer = cls.FOOTER_TEMPLATE.format(
            separator=cls.SEPARATOR,
            traceback=tb_str,
        )

        log_message = f"{header}\n{body}{footer}"
        result_message = f"{error_type}: {error_msg}"

        return log_message, result_message

    @classmethod
    def format_step_failure(
        cls,
        step_name: str,
        reason: str,
    ) -> str:
        """Format a pipeline step failure message.

        Args:
            step_name: Name/path of the failed step
            reason: Reason for failure

        Returns:
            Formatted error message
        """
        return (
            f"\n\n{cls.SEPARATOR}\n"
            f"PREPROCESS STEP FAILED\n"
            f"{cls.SEPARATOR}\n"
            f"Step:    {step_name}\n"
            f"Reason:  {reason}\n"
            f"{cls.SEPARATOR}\n"
        )

    @classmethod
    def format_case_error(
        cls,
        case_id: str,
        error: Exception,
        completed: int | None = None,
        total: int | None = None,
    ) -> str:
        """Format a case execution error.

        Args:
            case_id: ID of the failed case
            error: The exception that was raised
            completed: Number of completed cases (optional)
            total: Total number of cases (optional)

        Returns:
            Formatted error message for logging
        """
        error_type = type(error).__name__
        error_msg = str(error) or f"{error_type} (no message)"
        tb_str = "".join(traceback.format_exception(type(error), error, error.__traceback__))

        header = cls.HEADER_TEMPLATE.format(
            separator=cls.SEPARATOR,
            title="CASE EXECUTION FAILED",
        )

        case_info = case_id
        if completed is not None and total is not None:
            case_info = f"{case_id} [{completed}/{total}]"

        body_lines = [
            f"Case:       {case_info}",
            f"Error Type: {error_type}",
            f"Message:    {error_msg}",
        ]
        body = "\n".join(body_lines) + "\n"

        footer = cls.FOOTER_TEMPLATE.format(
            separator=cls.SEPARATOR,
            traceback=tb_str,
        )

        return f"{header}\n{body}{footer}"

    @classmethod
    def format_retry_warning(
        cls,
        component_name: str,
        attempt: int,
        max_attempts: int,
        error: Exception,
        delay: float,
    ) -> str:
        """Format a retry warning message.

        Args:
            component_name: Name of the component being retried
            attempt: Current attempt number
            max_attempts: Maximum number of attempts
            error: The exception that triggered the retry
            delay: Delay in seconds before retry

        Returns:
            Formatted warning message
        """
        error_type = type(error).__name__
        error_msg = str(error) or f"{error_type} (no message)"

        return (
            f"\nRETRY: Component {component_name} attempt {attempt}/{max_attempts} "
            f"failed with {error_type}: {error_msg}\n"
            f"   Retrying in {delay:.1f}s...\n"
        )

    @classmethod
    def _extract_error_location(cls, error: Exception) -> str:
        """Extract the error location from traceback.

        Finds the last code frame and returns
        its file path and line number.

        Args:
            error: The exception to extract location from

        Returns:
            String like "path/to/file.py:L42" or empty string if not found
        """
        tb = error.__traceback__
        error_location = ""

        while tb is not None:
            frame = tb.tb_frame
            filename = frame.f_code.co_filename
            lineno = tb.tb_lineno
            error_location = f"{filename}:L{lineno}"
            tb = tb.tb_next

        return error_location

    @classmethod
    def get_error_info(cls, error: Exception) -> tuple[str, str]:
        """Extract error type and message from exception.

        Args:
            error: The exception to extract info from

        Returns:
            Tuple of (error_type, error_message)
        """
        error_type = type(error).__name__
        error_msg = str(error) or f"{error_type} (no message)"
        return error_type, error_msg
