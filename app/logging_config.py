import logging
from contextvars import ContextVar

correlation_id_ctx: ContextVar[str] = ContextVar("correlation_id", default="-")


class CorrelationIdFormatter(logging.Formatter):
    """Custom formatter ensuring %(correlation_id)s is always available on all log records."""
    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, "correlation_id"):
            record.correlation_id = correlation_id_ctx.get("-")
        return super().format(record)


_log_format = "%(asctime)s [%(levelname)s] [corr_id=%(correlation_id)s] %(name)s: %(message)s"
_formatter = CorrelationIdFormatter(_log_format)

# Set up root logger handler
_root_logger = logging.getLogger()
_root_logger.setLevel(logging.INFO)
if not _root_logger.handlers:
    _console_handler = logging.StreamHandler()
    _console_handler.setFormatter(_formatter)
    _root_logger.addHandler(_console_handler)
else:
    for h in _root_logger.handlers:
        h.setFormatter(_formatter)

# Configure uvicorn loggers
for uvicorn_log_name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
    u_logger = logging.getLogger(uvicorn_log_name)
    for h in u_logger.handlers:
        h.setFormatter(_formatter)

logger = logging.getLogger("vocabcatcher")
