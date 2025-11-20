# StateGraph 接 AgentState（input/output 都是 AgentState）
import logging
from typing import Any, Dict, Annotated, TypedDict, List

from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableConfig
from langgraph.constants import END, START
from langgraph.graph import StateGraph

from deepinsight.core.types.graph_config import ExpertDef
from deepinsight.core.utils.research_utils import dict_merge_reducer, parse_research_config


class AgentState(TypedDict):
    final_report: str
    expert_comments: Annotated[Dict[str, str], dict_merge_reducer]


def make_expert_node(expert_def: ExpertDef):
    name = expert_def.name
    prompt_key = expert_def.prompt_key

    async def expert_node(state: AgentState, config: RunnableConfig):
        rc = parse_research_config(config)

        report = state["final_report"]
        if not report:
            logging.error(f"expert_node {name}: missing final_report")
            return {"expert_comments": state["expert_comments"] or {}}

        try:
            raw_prompt_template = rc.prompt_manager.get_prompt(
                name=prompt_key,
                group="expert_review"
            )
        except Exception as e:
            logging.error(f"expert_node {name}: can't load prompt {prompt_key}: {e}")
            raw_prompt_template = rc.prompt_manager.get_prompt(
                name="default_review_system",
                group="expert_review"
            )
        expert_config = dict(config)
        expert_config["parent_message_id"] = prompt_key
        print(f'\n\n\n expert_config: {prompt_key}\n\n\n')
        # format prompt
        chat_prompt = raw_prompt_template.format(
            expert_name=name
        )

        # choose model
        model = rc.get_model()

        messages = [
            SystemMessage(content=chat_prompt),
            HumanMessage(content=state["final_report"])
        ]

        try:
            resp = await model.with_config(expert_config).ainvoke(messages)
            comment_text = resp.content
        except Exception as e:
            logging.error(f"expert_node {name}: model invocation error: {e}")
            comment_text = f"Error: {e}"

        new_comments = dict(state["expert_comments"] or {})
        new_comments[name] = comment_text

        return {"expert_comments": new_comments}

    return f"expert_review_{name}", expert_node


def build_expert_review_graph(expert_defs: List[ExpertDef]):
    builder = StateGraph(AgentState, output=AgentState)
    for expert_def in expert_defs:
        node_name, node_fn = make_expert_node(expert_def)
        builder.add_node(node_name, node_fn)
    for expert_def in expert_defs:
        node_name = f"expert_review_{expert_def.name}"
        builder.add_edge(START, node_name)
    for expert_def in expert_defs:
        node_name = f"expert_review_{expert_def.name}"
        builder.add_edge(node_name, END)
    return builder.compile()
