import inspect
from deepinsight.databases.models.academic import Author, Conference, Paper, PaperAuthorRelation

def get_db_models_source_markdown() -> str:
    parts = []
    for model in (Author, Conference, Paper, PaperAuthorRelation):
        try:
            src = inspect.getsource(model)
        except Exception:
            src = f"class {model.__name__}: pass"
        parts.append(src)
    return "```python\n" + "\n\n".join(parts) + "\n```"