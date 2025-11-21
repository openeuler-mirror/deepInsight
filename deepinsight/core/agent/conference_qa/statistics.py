import asyncio
import json
import sys
from typing import Literal, TypedDict, Optional

from langchain_core.messages import HumanMessage
from langchain.agents import create_agent
from langchain_experimental.tools import PythonREPLTool
from langgraph.graph import StateGraph, MessagesState, START, END
from langgraph.types import Command

from deepinsight.core.utils.research_utils import parse_research_config
from deepinsight.utils.db_schema_utils import get_db_models_source_markdown

class State(MessagesState):
    next_step: str


def create_statistic_agent(llm, config):
    rc = parse_research_config(config)

    system_prompt = rc.prompt_manager.get_prompt(
        name="static_agent_system_prompt",
        group=rc.prompt_group,
    ).format(db_models_description=get_db_models_source_markdown())
    agent = create_agent(
        model=llm,
        tools=[PythonREPLTool()],
        system_prompt=system_prompt
    )

    return agent


async def statistic_agent_node(state: State, config) -> Command[Literal[END]]:
    rc = parse_research_config(config)
    statistic_agent = create_statistic_agent(llm=rc.get_model(), config=config)
    result = await statistic_agent.ainvoke(state, config=config)
    return Command(
        update={
            "messages": [
                HumanMessage(content=result["messages"][-1].content, name="statistic_agent")
            ]
        },
        goto=END,
    )


graph_builder = StateGraph(State)
graph_builder.add_node("statistic_agent", statistic_agent_node)
graph_builder.add_edge(START, "statistic_agent")
graph = graph_builder.compile()
