import json
import logging
import sys

from prismatic.observability.logging import init_logging, get_logger


def test_structured_logging_output(capsys):
    # Initialize logging (intercept_print=True by default)
    init_logging(level=logging.INFO, intercept_print=True)

    # 1. Test structlog logger
    logger = get_logger("test_structlog")
    logger.info("Hello structlog", extra_key="value")

    captured = capsys.readouterr()
    log_lines = [json.loads(line) for line in captured.out.strip().split("\n") if line.strip()]
    
    # Verify structlog log line
    structlog_line = next(line for line in log_lines if line.get("logger") == "test_structlog")
    assert structlog_line["event"] == "Hello structlog"
    assert structlog_line["level"] == "info"
    assert "timestamp" in structlog_line

    # 2. Test standard library logging
    stdlib_logger = logging.getLogger("test_stdlib")
    stdlib_logger.info("Hello stdlib")

    captured = capsys.readouterr()
    log_lines = [json.loads(line) for line in captured.out.strip().split("\n") if line.strip()]
    
    # Verify stdlib log line
    stdlib_line = next(line for line in log_lines if line.get("logger") == "test_stdlib")
    assert stdlib_line["event"] == "Hello stdlib"
    assert stdlib_line["level"] == "info"
    assert "timestamp" in stdlib_line

    # 3. Test intercepted print
    print("Hello print stdout")
    print("Hello print stderr", file=sys.stderr)

    captured = capsys.readouterr()
    # Intercepted print stdout/stderr both redirect to the same StreamHandler (sys.stdout)
    log_lines = [json.loads(line) for line in captured.out.strip().split("\n") if line.strip()]

    print_stdout_line = next(line for line in log_lines if line.get("event") == "Hello print stdout")
    assert print_stdout_line["logger"] == "print"
    assert print_stdout_line["level"] == "info"

    print_stderr_line = next(line for line in log_lines if line.get("event") == "Hello print stderr")
    assert print_stderr_line["logger"] == "print"
    assert print_stderr_line["level"] == "error"
