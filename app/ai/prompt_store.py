import json
from pathlib import Path
from typing import Any

PROMPTS_DIR = Path(__file__).parent / "prompts"
EXAMPLES_FILE = PROMPTS_DIR / "bids_examples.json"


class PromptJsonError(ValueError):
    pass


def read_prompt_json(path: Path = EXAMPLES_FILE) -> str:
    data = _load_json(path)
    return json.dumps(data, ensure_ascii=False, indent=2)


def ensure_prompt_json(path: Path, default_path: Path = EXAMPLES_FILE) -> None:
    if path.exists():
        _load_json(path)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _load_json(default_path)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_prompt_json(raw: str, path: Path = EXAMPLES_FILE) -> None:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PromptJsonError(f"JSON не читается: {exc.msg} на строке {exc.lineno}") from None
    _validate_prompt_data(data)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PromptJsonError(f"Не удалось прочитать JSON: {exc}") from None
    except json.JSONDecodeError as exc:
        raise PromptJsonError(f"Текущий JSON повреждён: {exc.msg} на строке {exc.lineno}") from None
    _validate_prompt_data(data)
    return data


def _validate_prompt_data(data: Any) -> None:
    if not isinstance(data, dict):
        raise PromptJsonError("Корень JSON должен быть объектом.")
    examples = data.get("examples")
    if not isinstance(examples, list):
        raise PromptJsonError('В JSON должен быть массив "examples".')
    for index, example in enumerate(examples, start=1):
        if not isinstance(example, dict):
            raise PromptJsonError(f"Пример #{index} должен быть объектом.")
        if not isinstance(example.get("input"), dict):
            raise PromptJsonError(f'У примера #{index} поле "input" должно быть объектом.')
        if not isinstance(example.get("output"), str):
            raise PromptJsonError(f'У примера #{index} поле "output" должно быть строкой.')
