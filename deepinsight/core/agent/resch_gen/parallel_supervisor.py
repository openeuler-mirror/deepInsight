from typing import List, Literal, Dict, Any, Annotated
import operator

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command, interrupt

from langchain_core.messages import get_buffer_string, HumanMessage, AIMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser

from langgraph.constants import END, START
from langgraph.graph import StateGraph, MessagesState
from langchain_core.runnables import RunnableConfig


from deepinsight.core.utils.utils import get_today_str
from deepinsight.core.agent.resch_gen.supervisor import ClarifyWithUser, graph as deep_researcher, review_by_expert, publish_result
from deepinsight.core.types.research import ClarifyNeedUser
from deepinsight.core.types.graph_config import ExpertDef
from deepinsight.core.utils.research_utils import load_expert_config, parse_research_config

DEFAULT_EXPERT_YAML_PATH = "./experts.yaml"
experts_config = load_expert_config(DEFAULT_EXPERT_YAML_PATH)
write_experts = [expert for expert in experts_config if expert.type=="writer"]
MAX_EXPERT_NUM = len(write_experts)

class ParallelState(MessagesState):
    first_input: str
    report_list: Annotated[List[str], operator.add]


async def parallel_clarify_with_user(state: ParallelState, config: RunnableConfig):
    """Analyze and ask clarification if needed, then move to wait node."""
    rc = parse_research_config(config)
    messages = state.get("messages", [])
    configurable_model = rc.get_model()
    prompt_manager = rc.prompt_manager
    prompt_group: str = rc.prompt_group
    clarification_model = (
        configurable_model
        .with_retry(stop_after_attempt=rc.max_structured_output_retries)
    )

    prompt_tpl = prompt_manager.get_prompt(
            name="clarify_with_user_instructions",
            group=prompt_group,
    )
    parser = PydanticOutputParser(pydantic_object=ClarifyWithUser)
    sys_msgs = prompt_tpl.format_messages(messages=get_buffer_string(messages), date=get_today_str())
    sys_content = sys_msgs[0].content if sys_msgs else ""
    system_message = SystemMessage(content=sys_content + "\n\n---\n" + parser.get_format_instructions())
    chain = clarification_model | parser
    result: ClarifyWithUser = await chain.with_retry().ainvoke(input=[system_message] + messages)
    question_text = result.question.strip()

    # 返回一个明确的跳转指令（字典式示例）
    return {"messages": [AIMessage(content=question_text)]}


async def parallel_wait_user_clarification(state: ParallelState):
    user_reply = interrupt(ClarifyNeedUser(question=state["messages"][-1].content))
    return {"messages": [AIMessage(content=user_reply)]}


def make_deepresearch_node(expert: ExpertDef):
    async def deepresearch_node(state: ParallelState, config: RunnableConfig):
        config["configurable"]["expert_name"] = expert.prompt_key
        init_state = {
            "messages": [HumanMessage(content=state["messages"][0].content)],
        }
        dr_config = dict(config)
        dr_config["parent_message_id"] = expert.prompt_key
        dr_config["configurable"]["allow_publish_result"] = False

        response = await deep_researcher.with_config(dr_config).ainvoke(init_state)
        cur_reports = state.get("report_list", [])
        new_reports = cur_reports + [response.get("final_report")]  # ✅ 创建新列表

        return Command(
            update={
                "report_list": new_reports,  # ✅ 这里是真正的更新
            }
        )

    return f"expert_{expert.prompt_key}", deepresearch_node


def summary_node(state: ParallelState, config: RunnableConfig):
    rc = parse_research_config(config)
    default_model = rc.get_model()
    prompt_manager = rc.prompt_manager
    all_sub_reports = state.get("report_list", [])
    summary_prompt = prompt_manager.get_prompt(
        name="summary_prompt",
        group="summary_experts",
    ).format(
        report="\n\n".join(all_sub_reports)
    )
    response = default_model.invoke([SystemMessage(content=summary_prompt)])
    # todo return  这里会和原来的final report重复
    return dict(final_report=response.content)


def enabled_think_selector(state: ParallelState, config) -> List[str]:
    """
    This function returns the list of downstream node keys to execute.
    LangGraph will call this during graph execution to decide which outgoing branch(es)
    to follow from the 'intent_expander' node.
    """
    rc = parse_research_config(config)
    cfg_experts = rc.write_experts or []
    cfg_experts = config.get("configurable", {}).get("write_experts",[])
    enabled = [f"expert_{expert_name}" for expert_name in cfg_experts]
    return enabled


checkpointer = InMemorySaver()
graph_builder = StateGraph(
    ParallelState,
)
graph_builder.add_node("parallel_clarify_with_user", parallel_clarify_with_user)
graph_builder.add_node("parallel_wait_user_clarification",
                 parallel_wait_user_clarification)  # Wait user clarification phase
graph_builder.add_node("summary_node", summary_node)
graph_builder.add_node("expert_review", review_by_expert)
graph_builder.add_node("publish_result", publish_result)
for i, expert in enumerate(write_experts):
    node_name, node_fn = make_deepresearch_node(expert)
    graph_builder.add_node(node_name, node_fn)
    graph_builder.add_edge(node_name, "summary_node")
graph_builder.add_edge(START, "parallel_clarify_with_user")
graph_builder.add_edge("parallel_clarify_with_user", "parallel_wait_user_clarification")
possible_branches = {f"expert_{expert.prompt_key}": f"expert_{expert.prompt_key}" for expert in write_experts}
graph_builder.add_conditional_edges("parallel_wait_user_clarification", enabled_think_selector, possible_branches)
graph_builder.add_edge("summary_node", "expert_review")
graph_builder.add_edge("expert_review", "publish_result")
graph_builder.add_edge("publish_result", END)

graph = graph_builder.compile(checkpointer=checkpointer)
