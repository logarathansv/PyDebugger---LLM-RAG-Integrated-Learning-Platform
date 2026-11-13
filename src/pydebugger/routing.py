"""Bounded, deterministic request routing with no classification model call."""

from __future__ import annotations

import re
from enum import Enum
from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class RequestRoute(str, Enum):
    DEBUG = "debug"
    RAG = "rag"
    TUTOR = "tutor"


class RouteState(TypedDict):
    query: str
    route: str


_DEBUG_PATTERN = re.compile(
    r"\b(traceback|syntaxerror|typeerror|valueerror|nameerror|exception|"
    r"stack trace|error|bug|debug|not working|fails?)\b|```",
    re.IGNORECASE,
)
_TUTOR_PATTERN = re.compile(
    r"\b(explain|teach|hint|understand|concept|why|how does|example)\b",
    re.IGNORECASE,
)


class RequestRouter:
    """LangGraph router whose classification is intentionally rule-based."""

    def __init__(self) -> None:
        graph = StateGraph(RouteState)
        graph.add_node("classify", self._classify)
        graph.add_node("debug", lambda _: {"route": RequestRoute.DEBUG.value})
        graph.add_node("rag", lambda _: {"route": RequestRoute.RAG.value})
        graph.add_node("tutor", lambda _: {"route": RequestRoute.TUTOR.value})
        graph.add_edge(START, "classify")
        graph.add_conditional_edges(
            "classify",
            lambda state: state["route"],
            {
                RequestRoute.DEBUG.value: "debug",
                RequestRoute.RAG.value: "rag",
                RequestRoute.TUTOR.value: "tutor",
            },
        )
        graph.add_edge("debug", END)
        graph.add_edge("rag", END)
        graph.add_edge("tutor", END)
        self._graph = graph.compile()

    def route(self, query: str) -> RequestRoute:
        result = self._graph.invoke({"query": query.strip(), "route": ""})
        return RequestRoute(result["route"])

    @staticmethod
    def _classify(state: RouteState) -> dict[str, str]:
        query = state["query"]
        if _DEBUG_PATTERN.search(query):
            return {"route": RequestRoute.DEBUG.value}
        if _TUTOR_PATTERN.search(query):
            return {"route": RequestRoute.TUTOR.value}
        return {"route": RequestRoute.RAG.value}
