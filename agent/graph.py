"""LangGraph pipeline: load_user → resolve_input → classify_intent → execute_skill → save_user."""
from __future__ import annotations
from langgraph.graph import StateGraph, END
from agent.state import AgentState
from agent.nodes.load_user import load_user_node
from agent.nodes.resolve_input import resolve_input_node
from agent.nodes.classify_intent import classify_intent_node
from agent.nodes.execute_skill import execute_skill_node
from agent.nodes.save_user import save_user_node


def _should_continue_after_load(state: dict) -> str:
    """Skip pipeline if user is pending/ignored."""
    intent = state.get("intent", "")
    if intent in ("__pending__", "__ignored__"):
        return "save_user"
    return "resolve_input"


def _should_continue_after_resolve(state: dict) -> str:
    intent = state.get("intent", "")
    if intent == "__error__":
        return "save_user"
    return "classify_intent"


def build_graph() -> StateGraph:
    graph = StateGraph(AgentState)

    graph.add_node("load_user", load_user_node)
    graph.add_node("resolve_input", resolve_input_node)
    graph.add_node("classify_intent", classify_intent_node)
    graph.add_node("execute_skill", execute_skill_node)
    graph.add_node("save_user", save_user_node)

    graph.set_entry_point("load_user")

    graph.add_conditional_edges("load_user", _should_continue_after_load, {
        "resolve_input": "resolve_input",
        "save_user": "save_user",
    })

    graph.add_conditional_edges("resolve_input", _should_continue_after_resolve, {
        "classify_intent": "classify_intent",
        "save_user": "save_user",
    })

    graph.add_edge("classify_intent", "execute_skill")
    graph.add_edge("execute_skill", "save_user")
    graph.add_edge("save_user", END)

    return graph.compile()
