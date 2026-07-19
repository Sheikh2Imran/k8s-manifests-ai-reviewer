import logging
import sys
from typing import Optional


def setup_logger(name: str = "k8s-ai-reviewer", level: Optional[str] = None) -> logging.Logger:
    logger = logging.getLogger(name)
    
    if logger.handlers:
        return logger
    
    if level is None:
        import os
        level = os.getenv("LOG_LEVEL", "INFO").upper()
    
    logger.setLevel(getattr(logging, level, logging.INFO))
    
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logger.level)
    
    formatter = logging.Formatter(
        fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    handler.setFormatter(formatter)
    
    logger.addHandler(handler)
    logger.propagate = False
    return logger
