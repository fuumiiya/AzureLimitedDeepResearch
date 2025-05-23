from typing import Literal, Dict, Any
import json
import logging
import sys
logger = logging.getLogger("app")

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from langgraph.constants import Send
from langgraph.graph import START, END, StateGraph
from langgraph.types import interrupt, Command

from open_deep_research.state import (
    ReportStateInput,
    ReportStateOutput,
    Sections,
    ReportState,
    SectionState,
    SectionOutputState,
    Queries,
    Feedback
)

from open_deep_research.prompts import (
    report_planner_query_writer_instructions,
    report_planner_instructions,
    query_writer_instructions,
    section_writer_instructions,
    final_section_writer_instructions,
    section_grader_instructions,
    section_writer_inputs
)

from open_deep_research.configuration import Configuration
from open_deep_research.utils import (
    format_sections,
    get_config_value,
    get_search_params,
    select_and_execute_search
)

## Nodes --
async def generate_report_plan(state: ReportState, config: RunnableConfig):
    """Generate the initial report plan with sections."""

    # ★ ノード開始ログ
    logger.info(f"[DEBUG] generate_report_plan 受け取った state: {state}")
    logger.info("▶︎ generate_report_plan START topic=%s", state["topic"])

    topic = state["topic"]
    feedback = state.get("feedback_on_report_plan", None)

    configurable = Configuration.from_runnable_config(config)
    report_structure = configurable.report_structure
    number_of_queries = configurable.number_of_queries
    search_api = get_config_value(configurable.search_api)
    search_api_config = configurable.search_api_config or {}
    params_to_pass = get_search_params(search_api, search_api_config)

    configurable.writer_model_kwargs = {
    "temperature": 0.7,
    "max_tokens": 8000
    }
    configurable.planner_model_kwargs = {
    "temperature": 0.7,
    "max_tokens": 8000
    }

    if isinstance(report_structure, dict):
        report_structure = str(report_structure)

    #Step1: generating search queries with structured_llm
    writer_provider = get_config_value(configurable.writer_provider)
    writer_model_name = get_config_value(configurable.writer_model)
    writer_model_kwargs = get_config_value(configurable.writer_model_kwargs or {})
    writer_model = init_chat_model(model=writer_model_name,
                                   model_provider=writer_provider,
                                   model_kwargs=writer_model_kwargs)
    structured_llm = writer_model.with_structured_output(Queries)

    system_instructions_query = report_planner_query_writer_instructions.format(
        topic=topic,
        report_organization=report_structure,
        number_of_queries=number_of_queries
    )

    logger.info("Step1: generating search queries with structured_llm")
    results = await structured_llm.ainvoke([
        SystemMessage(content=system_instructions_query),
        HumanMessage(content="Generate search queries that will help with planning the sections of the report.")
    ])
    logger.info("Step1 complete: search queries generated =\n%s",
            json.dumps(results, indent=2, ensure_ascii=False, default=str))
    logger.info("generate_report_plan queries=%d", len(results.queries))

    #Step2: executing search queries
    queries = results.queries
    query_list = [q.search_query for q in results.queries]
    source_str = await select_and_execute_search(search_api, query_list, params_to_pass)

    logger.info("Step2: executing search queries: %s", query_list)
    logger.info("Step2 complete: search results length = %d", len(source_str))

    #Step3: generating report sections
    system_instructions_sections = report_planner_instructions.format(
        topic=topic,
        report_organization=report_structure,
        context=source_str,
        feedback=feedback
    )

    planner_provider = get_config_value(configurable.planner_provider)
    planner_model = get_config_value(configurable.planner_model)
    planner_model_kwargs = get_config_value(configurable.planner_model_kwargs or {})

    planner_llm = init_chat_model(model=planner_model,
                                  model_provider=planner_provider,
                                  model_kwargs=planner_model_kwargs)
    structured_llm = planner_llm.with_structured_output(Sections)

    planner_message = (
        "Generate the sections of the report. Your response must include a "
        "'sections' field containing a list of sections. Each section must "
        "have: name, description, plan, research, and content fields."
    )

    logger.info("Step3: generating report sections")
    logger.info("=== セクション生成 SystemMessage ===\n%s", system_instructions_sections)
    logger.info("=== セクション生成 HumanMessage ===\n%s", planner_message)
    try:
        report_sections = await structured_llm.ainvoke([
            SystemMessage(content=system_instructions_sections),
            HumanMessage(content=planner_message)
        ])
    except Exception as e:
        logger.exception("セクション生成時にエラーが発生しました")
        raise e
    logger.info("Step3 complete: number of sections = %d", len(report_sections.sections))

    for i, sec in enumerate(report_sections.sections):
        logger.info("Section[%d]: name=%s", i, sec.name)

    sections = report_sections.sections

    # ★ ここで research フラグを強制 ON ★
    for sec in sections:
        sec.research = True
    logger.info("generate_report_plan    Step3  sections=%d (all research=True)", len(sections))
    logger.info("◀︎ generate_report_plan    END")

    return {
        "topic"            : topic,
        "queries"          : queries,
        "sections"         : sections,
        "completed_sections": [],        # 空で OK（後続で埋まる）
        "final_report": ""
    }

def human_feedback(state: ReportState, config: RunnableConfig) -> Command[Literal["build_section_with_web_research"]]:
    """Process report plan and proceed to section writing automatically.

    This node:
    1. Formats the current report plan (for logging purposes only)
    2. Automatically approves the plan without human intervention
    3. Routes research-enabled sections to the research workflow

    Args:
        state: Current graph state with sections to process
        config: Configuration for the workflow

    Returns:
        Command to start section writing
    """
    # ★ ノード開始ログ
    logger.info("▶︎ human_feedback START")

    # Get sections
    topic = state["topic"]
    sections = state['sections']
    sections_str = "\n\n".join(
        f"Section: {section.name}\n"
        f"Description: {section.description}\n"
        f"Research needed: {'Yes' if section.research else 'No'}\n"
        for section in sections
    )

    # Log the sections (optional, for debugging)
    # print(f"Automatically proceeding with report plan:\n{sections_str}")

    # Automatically approve the report plan and kick off section writing for research sections only
    logger.info("◀︎ human_feedback END")
    return Command(goto=[
        Send("build_section_with_web_research", {"topic": topic, "section": s, "search_iterations": 0})
        for s in sections
        if s.research
    ])

def generate_queries(state: SectionState, config: RunnableConfig):
    """Generate search queries for researching a specific section.

    This node uses an LLM to generate targeted search queries based on the
    section topic and description.

    Args:
        state: Current state containing section details
        config: Configuration including number of queries to generate

    Returns:
        Dict containing the generated search queries
    """

    # Get state
    topic = state["topic"]
    section = state["section"]
    logger.info(f"[generate_queries] 処理開始: セクション名={section.name}")
    logger.info(f"[generate_queries] セクション説明: {section.description}")

    # Get configuration
    configurable = Configuration.from_runnable_config(config)
    number_of_queries = configurable.number_of_queries

    # Generate queries
    writer_provider = get_config_value(configurable.writer_provider)
    writer_model_name = get_config_value(configurable.writer_model)
    writer_model = init_chat_model(model=writer_model_name, model_provider=writer_provider)
    structured_llm = writer_model.with_structured_output(Queries)

    # Format system instructions
    system_instructions = query_writer_instructions.format(topic=topic,
                                                           section_topic=section.description,
                                                           number_of_queries=number_of_queries)
    logger.info(f"[generate_queries] クエリ生成用SystemMessage:\n{system_instructions}")
    logger.info(f"[generate_queries] クエリ生成用HumanMessage:\nGenerate search queries on the provided topic.")

    # Generate queries 
    queries = structured_llm.invoke([SystemMessage(content=system_instructions),
                                     HumanMessage(content="Generate search queries on the provided topic.")])

    # 生成されたクエリをログ出力
    logger.info(f"[generate_queries] 生成されたクエリ数: {len(queries.queries)}")
    for i, query in enumerate(queries.queries, 1):
        logger.info(f"[generate_queries] クエリ {i}: {query.search_query}")

    return {"search_queries": queries.queries}

async def search_web(state: SectionState, config: RunnableConfig):
    """Execute web searches for the section queries.

    This node:
    1. Takes the generated queries
    2. Executes searches using configured search API
    3. Formats results into usable context

    Args:
        state: Current state with search queries
        config: Search API configuration

    Returns:
        Dict with search results and updated iteration count
    """

    # Get state
    search_queries = state["search_queries"]

    logger.info(f"[search_web] 処理開始: 検索クエリ数={len(search_queries)}")
    for i, query in enumerate(search_queries, 1):
        logger.info(f"[search_web] 検索クエリ {i}: {query.search_query}")

    # Get configuration
    configurable = Configuration.from_runnable_config(config)
    search_api = get_config_value(configurable.search_api)
    search_api_config = configurable.search_api_config or {}  # Get the config dict, default to empty
    params_to_pass = get_search_params(search_api, search_api_config)  # Filter parameters

    logger.info(f"[search_web] 検索API: {search_api}")
    logger.info(f"[search_web] 検索パラメータ: {json.dumps(params_to_pass, indent=2, ensure_ascii=False)}")

    # Web search
    query_list = [query.search_query for query in search_queries]

    # Search the web with parameters
    source_str = await select_and_execute_search(search_api, query_list, params_to_pass)
    logger.info(f"[search_web] 検索結果の長さ: {len(source_str)}文字")

    return {
    **state,
    "source_str": source_str,
    "search_iterations": state["search_iterations"] + 1
    }

def write_section(state: SectionState, config: RunnableConfig) -> Command[Literal[END, "search_web"]]:
    """Write a section of the report and evaluate if more research is needed.

    This node:
    1. Writes section content using search results
    2. Evaluates the quality of the section
    3. Either:
       - Completes the section if quality passes
       - Triggers more research if quality fails

    Args:
        state: Current state with search results and section info
        config: Configuration for writing and evaluation

    Returns:
        Command to either complete section or do more research
    """

    # Get state
    topic = state["topic"]
    section = state["section"]
    source_str = state["source_str"]

    logger.info(f"[write_section] 処理開始: セクション名={section.name}")
    logger.info(f"[write_section] 検索結果の長さ: {len(source_str)}文字")

    # Get configuration
    configurable = Configuration.from_runnable_config(config)

    # Format system instructions
    section_writer_inputs_formatted = section_writer_inputs.format(topic=topic,
                                                             section_name=section.name,
                                                             section_topic=section.description,
                                                             context=source_str,
                                                             section_content=section.content)
    logger.info(f"[write_section] セクション生成用SystemMessage:\n{section_writer_instructions}")
    logger.info(f"[write_section] セクション生成用HumanMessage:\n{section_writer_inputs_formatted}")

    # Generate section 
    writer_provider = get_config_value(configurable.writer_provider)
    writer_model_name = get_config_value(configurable.writer_model)
    writer_model = init_chat_model(model=writer_model_name, model_provider=writer_provider)

    section_content = writer_model.invoke([SystemMessage(content=section_writer_instructions),
                                           HumanMessage(content=section_writer_inputs_formatted)])

    # Write content to the section object 
    section.content = section_content.content
    logger.info(f"[write_section] 生成されたセクションの長さ: {len(section.content)}文字")

    # Grade prompt
    section_grader_message = ("Grade the report and consider follow-up questions for missing information. "
                              "If the grade is 'pass', return empty strings for all follow-up queries. "
                              "If the grade is 'fail', provide specific search queries to gather missing information.")

    section_grader_instructions_formatted = section_grader_instructions.format(topic=topic,
                                                                               section_topic=section.description,
                                                                               section=section.content,
                                                                               number_of_follow_up_queries=configurable.number_of_queries)
    logger.info(f"[write_section] 評価用SystemMessage:\n{section_grader_instructions_formatted}")
    logger.info(f"[write_section] 評価用HumanMessage:\n{section_grader_message}")

    # Use planner model for reflection
    planner_provider = get_config_value(configurable.planner_provider)
    planner_model = get_config_value(configurable.planner_model)

    if planner_model == "claude-3-7-sonnet-latest":
        # Allocate a thinking budget for claude-3-7-sonnet-latest as the planner model
        reflection_model = init_chat_model(model=planner_model,
                                           model_provider=planner_provider,
                                           max_tokens=20_000,
                                           thinking={"type": "enabled", "budget_tokens": 16_000}).with_structured_output(Feedback)
    else:
        reflection_model = init_chat_model(model=planner_model,
                                           model_provider=planner_provider).with_structured_output(Feedback)
    # Generate feedback
    feedback = reflection_model.invoke([SystemMessage(content=section_grader_instructions_formatted),
                                        HumanMessage(content=section_grader_message)])

    logger.info(f"[write_section] 評価結果: grade={feedback.grade}")
    if feedback.grade == "fail":
        logger.info(f"[write_section] 追加検索クエリ数: {len(feedback.follow_up_queries)}")
        for i, query in enumerate(feedback.follow_up_queries, 1):
            logger.info(f"[write_section] 追加クエリ {i}: {query}")

    # If the section is passing or the max search depth is reached, publish the section to completed sections
    if feedback.grade == "pass" or state["search_iterations"] >= configurable.max_search_depth:
        logger.info(f"[write_section] セクション完了: grade={feedback.grade}, search_iterations={state['search_iterations']}")
        # Publish the section to completed sections
        return  Command(
        update={"completed_sections": [section]},
        goto=END
    )

    # Update the existing section with new content and update search queries
    else:
        logger.info(f"[write_section] 追加リサーチが必要: grade={feedback.grade}, search_iterations={state['search_iterations']}")
        return  Command(
        update={"search_queries": feedback.follow_up_queries, "section": section},
        goto="search_web"
        )

def write_final_sections(state: SectionState, config: RunnableConfig):
    """Write sections that don't require research using completed sections as context.

    This node handles sections like conclusions or summaries that build on
    the researched sections rather than requiring direct research.

    Args:
        state: Current state with completed sections as context
        config: Configuration for the writing model

    Returns:
        Dict containing the newly written section
    """

    # Get configuration
    configurable = Configuration.from_runnable_config(config)

    # Get state
    topic = state["topic"]
    section = state["section"]
    completed_report_sections = state["report_sections_from_research"]

    logger.info(f"[write_final_sections] 処理開始: セクション名={section.name}")
    logger.info(f"[write_final_sections] 完了済みセクションの長さ: {len(completed_report_sections)}文字")

    # Format system instructions
    system_instructions = final_section_writer_instructions.format(topic=topic, section_name=section.name, section_topic=section.description, context=completed_report_sections)
    logger.info(f"[write_final_sections] セクション生成用SystemMessage:\n{system_instructions}")
    logger.info(f"[write_final_sections] セクション生成用HumanMessage:\nGenerate a report section based on the provided sources.")

    # Generate section 
    writer_provider = get_config_value(configurable.writer_provider)
    writer_model_name = get_config_value(configurable.writer_model)
    writer_model = init_chat_model(model=writer_model_name, model_provider=writer_provider)

    section_content = writer_model.invoke([SystemMessage(content=system_instructions),
                                           HumanMessage(content="Generate a report section based on the provided sources.")])

    # Write content to section
    section.content = section_content.content
    logger.info(f"[write_final_sections] 生成されたセクションの長さ: {len(section.content)}文字")

    # Write the updated section to completed sections
    return {"completed_sections": [section]}

def gather_completed_sections(state: ReportState):
    """Format completed sections as context for writing final sections.

    This node takes all completed research sections and formats them into
    a single context string for writing summary sections.

    Args:
        state: Current state with completed sections

    Returns:
        Dict with formatted sections as context
    """

    # List of completed sections
    completed_sections = state["completed_sections"]

    logger.info(f"[gather_completed_sections] 処理開始: 完了セクション数={len(completed_sections)}")
    for i, section in enumerate(completed_sections, 1):
        logger.info(f"[gather_completed_sections] セクション {i}:")
        logger.info(f"  名前: {section.name}")
        logger.info(f"  説明: {section.description}")
        logger.info(f"  内容の長さ: {len(section.content)}文字")

    # Format completed section to str to use as context for final sections
    completed_report_sections = format_sections(completed_sections)
    logger.info(f"[gather_completed_sections] フォーマット後の全体の長さ: {len(completed_report_sections)}文字")

    return {"report_sections_from_research": completed_report_sections}

def compile_final_report(state: ReportState):
    """Compile all sections into the final report.

    This node:
    1. Gets all completed sections
    2. Orders them according to original plan
    3. Combines them into the final report

    Args:
        state: Current state with all completed sections

    Returns:
        Dict containing the complete report
    """

    # Get sections
    sections = state["sections"]
    completed_sections = {s.name: s.content for s in state["completed_sections"]}

    logger.info(f"[compile_final_report] 処理開始: セクション数={len(sections)}")
    logger.info(f"[compile_final_report] 完了セクション数={len(completed_sections)}")

    # Update sections with completed content while maintaining original order
    for section in sections:
        section.content = completed_sections[section.name]
        logger.info(f"[compile_final_report] セクション '{section.name}' の内容を更新: 長さ={len(section.content)}文字")

    # Compile final report
    all_sections = "\n\n".join([s.content for s in sections])
    logger.info(f"[compile_final_report] 最終レポートの長さ: {len(all_sections)}文字")

    return {"final_report": all_sections}

def initiate_final_section_writing(state: ReportState):
    """Create parallel tasks for writing non-research sections.

    This edge function identifies sections that don't need research and
    creates parallel writing tasks for each one.

    Args:
        state: Current state with all sections and research context

    Returns:
        List of Send commands for parallel section writing
    """

    # Kick off section writing in parallel via Send() API for any sections that do not require research
    non_research_nodes = [
        Send("write_final_sections",
             {"topic": state["topic"],
              "section": s,
              "report_sections_from_research": state["report_sections_from_research"]})
        for s in state["sections"] if not s.research
    ]

    # 非リサーチ章が１件以上 ⇒ その並列ジョブへ
    if non_research_nodes:
        return non_research_nodes

    # ０件 ⇒ そのまま最終レポートへ
    return "compile_final_report"


# Report section sub-graph --

# Add nodes
section_builder = StateGraph(SectionState, output=SectionOutputState)
section_builder.add_node("generate_queries", generate_queries)
section_builder.add_node("search_web", search_web)
section_builder.add_node("write_section", write_section)

# Add edges
section_builder.add_edge(START, "generate_queries")
section_builder.add_edge("generate_queries", "search_web")
section_builder.add_edge("search_web", "write_section")

# Outer graph for initial report plan compiling results from each section --

# Add nodes
builder = StateGraph(ReportState, input=ReportStateInput, output=ReportStateOutput, config_schema=Configuration)
builder.add_node("generate_report_plan", generate_report_plan)
builder.add_node("human_feedback", human_feedback)
builder.add_node("build_section_with_web_research", section_builder.compile())
builder.add_node("gather_completed_sections", gather_completed_sections)
builder.add_node("write_final_sections", write_final_sections)
builder.add_node("compile_final_report", compile_final_report)

# Add edges
builder.add_edge(START, "generate_report_plan")
builder.add_edge("generate_report_plan", "human_feedback")
builder.add_edge("build_section_with_web_research", "gather_completed_sections")
builder.add_conditional_edges("gather_completed_sections", initiate_final_section_writing, ["write_final_sections", "compile_final_report"])
builder.add_edge("gather_completed_sections", "compile_final_report")   # ★これを追加
builder.add_edge("write_final_sections", "compile_final_report")
builder.add_edge("compile_final_report", END)

graph = builder.compile()