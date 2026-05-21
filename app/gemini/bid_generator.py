import json
import logging
from pathlib import Path
from typing import Literal

from google.genai import types

from app.projects import Project

from .client import GeminiClient

log = logging.getLogger(__name__)

Language = Literal["ru", "ua"]

PROMPTS_DIR = Path(__file__).parent / "prompts"
EXAMPLES_FILE = PROMPTS_DIR / "bids_examples.json"
SYSTEM_PROMPT_FILE = PROMPTS_DIR / "system_prompt.md"

# Чисто украинские буквы — їх відсутність у тексті означає, що це російська/інша мова.
_UA_ONLY_LETTERS = set("іїєґІЇЄҐ")


class BidGenerationError(Exception):
    pass


class BidGenerator:
    """Generates a freelance bid in Nikita's style for a given project."""

    def __init__(
        self,
        client: GeminiClient,
        examples_path: Path = EXAMPLES_FILE,
        system_prompt_path: Path = SYSTEM_PROMPT_FILE,
    ) -> None:
        self._client = client
        self._system_prompt = system_prompt_path.read_text(encoding="utf-8").strip()
        self._examples = json.loads(examples_path.read_text(encoding="utf-8"))

    async def generate(self, project: Project, language: Language | None = None) -> str:
        lang: Language = language or detect_language(f"{project.title}\n{project.description}")
        contents = self._build_contents(project, lang)
        try:
            return await self._client.generate(
                system_instruction=self._system_prompt,
                contents=contents,
            )
        except Exception as exc:
            raise BidGenerationError(str(exc)) from exc

    def _build_contents(self, project: Project, lang: Language) -> list[types.Content]:
        contents: list[types.Content] = []
        for example in self._examples.get("examples", []):
            user_payload = json.dumps(example["input"], ensure_ascii=False)
            contents.append(types.Content(role="user", parts=[types.Part.from_text(text=user_payload)]))
            contents.append(types.Content(role="model", parts=[types.Part.from_text(text=example["output"])]))

        target_payload = {
            "project_title": project.title,
            "project_description": project.description,
            "budget": project.budget or None,
            "language": lang,
            "instructions": (
                "Сгенерируй ставку. В строке цены/сроков оставь литералы {price} и {deadline} вместо чисел — "
                "пользователь подставит их вручную перед отправкой."
                if lang == "ru"
                else "Згенеруй ставку. У рядку ціни/термінів залиш літерали {price} і {deadline} замість чисел — "
                "користувач підставить їх вручну перед відправленням."
            ),
        }
        contents.append(
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=json.dumps(target_payload, ensure_ascii=False))],
            )
        )
        return contents


def detect_language(text: str) -> Language:
    if not text:
        return "ru"
    for ch in text:
        if ch in _UA_ONLY_LETTERS:
            return "ua"
    return "ru"
