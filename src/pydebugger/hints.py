"""Stateful, bounded hint progression implemented as a LangGraph workflow."""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import TypedDict

from langchain_core.prompts import PromptTemplate
from langgraph.graph import END, START, StateGraph

from .models import ChatMessage, HintProgress
from .providers import ChatProvider

_HINT_PROMPT = PromptTemplate.from_template(
    "{retry}Level {level}: {stage}\nProblem:\n{problem}\n\n"
    "Relevant history:\n{conversation}\n\nEarlier hints:\n{prior}"
)


class HintState(TypedDict):
    problem: str
    conversation: str
    previous_hints: tuple[str, ...]
    level: int
    stage_instruction: str
    hint: str
    attempts: int
    duplicate: bool


class HintWorkflow:
    _STAGES = (
        "Ask one diagnostic question or point out one observation to inspect.",
        "Name the relevant Python concept and explain what to verify.",
        "Suggest a small debugging procedure or pseudocode, without complete code.",
        "Give one targeted implementation detail, but do not reveal the full solution.",
    )

    def __init__(self, chat: ChatProvider) -> None:
        self._chat = chat
        graph = StateGraph(HintState)
        graph.add_node("choose_stage", self._choose_stage)
        graph.add_node("generate", self._generate)
        graph.add_node("validate", self._validate)
        graph.add_node("fallback", self._fallback)
        graph.add_edge(START, "choose_stage")
        graph.add_edge("choose_stage", "generate")
        graph.add_edge("generate", "validate")
        graph.add_conditional_edges(
            "validate",
            self._route,
            {"done": END, "retry": "generate", "fallback": "fallback"},
        )
        graph.add_edge("fallback", END)
        self._graph = graph.compile()

    def next_hint(
        self,
        problem: str,
        history: list[ChatMessage],
        previous_hints: list[str],
        conversation: str,
    ) -> HintProgress:
        cleaned_problem = problem.strip()
        if not cleaned_problem:
            raise ValueError("A problem or code sample is required for a hint")
        state = self._graph.invoke(
            {
                "problem": cleaned_problem,
                "conversation": conversation,
                "previous_hints": tuple(
                    item.strip() for item in previous_hints if item.strip()
                ),
                "level": 1,
                "stage_instruction": "",
                "hint": "",
                "attempts": 0,
                "duplicate": False,
            }
        )
        hint = str(state["hint"]).strip()
        hints = (*state["previous_hints"], hint)
        return HintProgress(cleaned_problem, int(state["level"]), hint, hints)

    def _choose_stage(self, state: HintState) -> dict[str, object]:
        level = min(len(state["previous_hints"]) + 1, len(self._STAGES))
        return {"level": level, "stage_instruction": self._STAGES[level - 1]}

    def _generate(self, state: HintState) -> dict[str, object]:
        prior = "\n".join(f"- {item}" for item in state["previous_hints"]) or "None"
        retry = (
            "Your previous attempt repeated an earlier hint. Change both the wording and "
            "the concrete next action.\n\n"
            if state["attempts"]
            else ""
        )
        hint = self._chat.complete(
            "Give exactly one concise programming hint. Do not reveal the solution or "
            "repeat an earlier hint.",
            _HINT_PROMPT.format(
                retry=retry,
                level=state["level"],
                stage=state["stage_instruction"],
                problem=state["problem"],
                conversation=state["conversation"] or "None",
                prior=prior,
            ),
        ).strip()
        return {"hint": hint, "attempts": state["attempts"] + 1}

    @staticmethod
    def _validate(state: HintState) -> dict[str, object]:
        normalized = " ".join(state["hint"].lower().split())
        duplicate = not normalized or any(
            SequenceMatcher(None, normalized, " ".join(old.lower().split())).ratio()
            >= 0.82
            for old in state["previous_hints"]
        )
        return {"duplicate": duplicate}

    @staticmethod
    def _route(state: HintState) -> str:
        if not state["duplicate"]:
            return "done"
        return "retry" if state["attempts"] < 2 else "fallback"

    @staticmethod
    def _fallback(state: HintState) -> dict[str, object]:
        return {
            "hint": (
                f"Hint {state['level']}: isolate the smallest failing expression, write down "
                "its actual value and type, then compare that observation with what the next "
                "line expects."
            ),
            "duplicate": False,
        }
