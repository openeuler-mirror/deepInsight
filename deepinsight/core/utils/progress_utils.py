import asyncio
from functools import wraps
from pydantic import BaseModel

from langgraph.config import get_stream_writer


class ProgressEvent(BaseModel):
    """Custom progress event type for LangGraph streaming."""
    description: str


def emit_progress_event(description: str):
    """
    Emit a unified progress event through LangGraph's stream system.
    This can be used anywhere within an agent or graph node to report progress.
    
    Args:
        description (str): A short message describing the current task or stage.
    """
    writer = get_stream_writer()
    writer(ProgressEvent(
        description=description,
    ))


def progress_stage(description: str):
    """
    Decorator for automatically sending progress events before and after a function runs.
    Works for both async and sync functions.
    
    Args:
        description (str): Description of the stage being executed.
    """
    def decorator(func):
        if asyncio.iscoroutinefunction(func):
            @wraps(func)
            async def async_wrapper(*args, **kwargs):
                emit_progress_event(f"开始任务: {description}")
                try:
                    result = await func(*args, **kwargs)
                    emit_progress_event(f"完成任务: {description}")
                    return result
                except Exception as e:
                    emit_progress_event(f"任务处理失败: {description}")
                    raise
            return async_wrapper
        else:
            @wraps(func)
            def sync_wrapper(*args, **kwargs):
                emit_progress_event(f"开始任务: {description}")
                try:
                    result = func(*args, **kwargs)
                    emit_progress_event(f"完成任务: {description}")
                    return result
                except Exception as e:
                    emit_progress_event(f"任务处理失败: {description}")
                    raise
            return sync_wrapper
    return decorator
