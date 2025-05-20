import json
import logging
import asyncio
import azure.functions as func
from langgraph.checkpoint.memory import MemorySaver
from open_deep_research.configuration import Configuration
import uuid
import os
import sys
import inspect
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "src")))
sys.modules.pop("open_deep_research.graph", None)
sys.modules.pop("open_deep_research", None)  # ←これも重要

import traceback
from azure.identity import DefaultAzureCredential
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import QueryType, VectorizableTextQuery
from langchain_openai import AzureChatOpenAI
from functools import wraps
import aiohttp
import open_deep_research.utils as odr_utils
import langchain.chat_models as lcm
from typing import List, Dict, Optional, Any
import datetime
from azure.core.exceptions import HttpResponseError

# ✅ ロガー設定をルートで再構成（force=True で他の設定をリセット）
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True
)

# ✅ ファイル出力を追加したい場合のみこれを残す
file_handler = logging.FileHandler('function_app.log')
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(
    logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
)
logging.getLogger().addHandler(file_handler)
logger = logging.getLogger("app")


app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)
# =======================================================================
#  HTTP Trigger main
# =======================================================================
@app.function_name(name="HttpTrigger")
@app.route(route="main", methods=["POST"])
async def main(req: func.HttpRequest) -> func.HttpResponse:
    import importlib
    import open_deep_research.graph as graph_module
    importlib.reload(graph_module)
    graph = graph_module.graph

    print("🔥 Using graph from:", graph_module.__file__)
    logger.info(f"🔥 Using graph from: {graph_module.__file__}")
    logger.info(f"✅ utils.py path: {inspect.getfile(odr_utils)}")

    """
    HTTP トリガーで Open Deep Research を実行する関数
    """
    logger.info('Open Deep Research function triggered')

    try:
        # --- リクエスト取得 -------------------------------------------------
        req_body = req.get_json()
        logger.info(f"受信ボディ:\n{json.dumps(req_body, indent=2, ensure_ascii=False)}")

        if 'topic' not in req_body:
            return func.HttpResponse(
                json.dumps({"error": "'topic' フィールドは必須です。"}),
                mimetype="application/json",
                status_code=400,
            )

        topic = req_body['topic']
        logger.info(f"トピック: {topic}")

        # --- thread_config 作成 -------------------------------------------
        thread_config = {
            "thread_id": str(uuid.uuid4()),
            "search_api": req_body.get('search_api', "tavily"),
            "planner_provider": req_body.get('planner_provider', "anthropic"),
            "planner_model": req_body.get('planner_model', "claude-3-7-sonnet-latest"),
            "writer_provider": req_body.get('writer_provider', "anthropic"),
            "writer_model": req_body.get('writer_model', "claude-3-5-sonnet-latest"),
            "max_search_depth": req_body.get('max_search_depth', 2),
        }
        # optional
        for opt in ('report_structure', 'number_of_queries', 'search_api_config'):
            if opt in req_body:
                thread_config[opt] = req_body[opt]

        logger.info(f"thread_config:\n{json.dumps(thread_config, indent=2, ensure_ascii=False)}")
        thread = {"configurable": thread_config}

        # --- グラフ実行 -----------------------------------------------------
        #from open_deep_research.graph import graph   # インポートはパッチ後
        logger.info("graph.ainvoke 開始")
        result = await graph.ainvoke({"topic": topic}, thread)
        logger.info("[graph.ainvoke result - formatted]\n%s", json.dumps(result, ensure_ascii=False, indent=2))

        logger.info("graph.ainvoke 完了")

        final_report = result.get("final_report", "")
        logger.info("[Final Report - formatted output]\n\n%s", final_report)

        return func.HttpResponse(
            json.dumps({"report": final_report, "status": "success"}),
            mimetype="application/json",
        )

    except Exception as e:
        logger.error("エラー:", exc_info=True)
        return func.HttpResponse(
            json.dumps({"error": str(e), "status": "error"}),
            mimetype="application/json",
            status_code=500,
        )

# =======================================================================
#  共通ユーティリティ
# =======================================================================
def get_azure_openai_token_authenticator():
    """
    Managed-Identity で Azure OpenAI を呼ぶための
    **同期** トークンプロバイダ。
    """
    from azure.identity import DefaultAzureCredential
    credential = DefaultAzureCredential()
    scope = "https://cognitiveservices.azure.com/.default"

    def token_provider() -> str:
        # DefaultAzureCredential.get_token は同期メソッドなので
        # そのまま呼んで文字列を返せば OK
        return credential.get_token(scope).token

    return token_provider

async def run_async(func):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, func)

# ---------------- Azure AI Search ハイブリッド検索 --------------------
async def azure_ai_search_async(
    search_service: str,
    search_key: str | None,
    index_name: str,
    query: str,
    top_k: int = 5,
    vector_fields: str | None  = None,
    semantic_configuration: str | None = None,
    use_managed_identity: bool = False,
) -> List[Dict[str, Any]]:
    logger.info(f"Azure AI Search: {query}")
    credential = DefaultAzureCredential() if use_managed_identity else AzureKeyCredential(search_key)
    client = SearchClient(
        endpoint=f"https://{search_service}.search.windows.net/",
        index_name=index_name,
        credential=credential,
        api_version="2023-10-01-Preview"
    )

    # --- 検索オプションを組み立て --------------------------
    search_kwargs: dict[str, Any] = {
        "search_text": query,
        "top": top_k,
        "select": ["metadata_spo_item_name",
                   "metadata_spo_item_path",
                   "textItems"],
    }

    # ベクトル検索を入れたいとき
    if vector_fields:
        search_kwargs["vector_queries"] = [
            {
                "kind": "text",
                "text": query,
                "k": top_k,
                "fields": vector_fields,
            }
        ]

    # セマンティック検索を入れたいとき
    if semantic_configuration:
        search_kwargs.update(
            {
                "query_type": QueryType.SEMANTIC,
                "semantic_configuration_name": semantic_configuration,
                "query_answer": "extractive",
                "query_caption": "extractive",
            }
        )
    try:
        res = await run_async(lambda: client.search(**search_kwargs))

    except HttpResponseError as e:
        logger.error("Status: %s\nMessage: %s", e.status_code, e.message)
        if e.response:
            logger.error("Body: %s", e.response.text())   # ← ここをコピーして貼ってください
        raise

    docs_raw = list(res)
    docs: List[Dict[str, Any]] = []
    for d in docs_raw:
        docs.append(
            {
                "title": d.get("metadata_spo_item_name", ""),
                "url":   d.get("metadata_spo_item_path", ""),
                "content": (
                    # キャプション優先、なければ最初のテキスト 3 本
                    d["@search.captions"][0].text
                    if d.get("@search.captions")
                    else " ".join(d.get("textItems", [])[:3])
                ),
                "raw_content": " ".join(d.get("textItems", [])),   # 全文（長くて OK）
                "score": d.get("@search.score", 0)                  # なくても動くが一応
            }
        )
    logger.info("🔎 Azure AI Search: 検索ドキュメント数 = %d", len(docs))
    for i, doc in enumerate(docs[:3], 1):  # 多すぎるとログが大変なので3件だけ
        logger.info("📄 Doc %d: title=%s, url=%s, content(抜粋)=%s", i, doc["title"], doc["url"], doc["content"][:100])

    return docs


def format_search_results_to_string(docs: List[Dict[str, Any]]) -> str:
    parts = []
    for d in docs:
        head = f"タイトル: {d.get('title')}\nURL: {d.get('url')}"
        body = []
        if "caption" in d:
            body.append(f"要約: {d['caption']}")
        for t in d.get("text_items", [])[:3]:
            body.append(f"- {t}")
        parts.append(head + ("\n" + "\n".join(body) if body else ""))
    return "\n\n" + "=" * 50 + "\n\n".join(parts) + "\n\n" + "=" * 50

# =======================================================================
#  ここからが **変更点** です
# =======================================================================
# <<< CHANGED >>> --------------------------------------------------------
#   1. 旧「巨大パッチ」ブロックを **削除** しました
#   2. 最小限の select_and_execute_search パッチを新設しました
# -----------------------------------------------------------------------

# オリジナル関数を保持
_orig_search = odr_utils.select_and_execute_search

async def _patched_select_and_execute_search(search_api, query_list, params=None):
    if search_api == "azure_ai_search":
        cfg = params or {}
        service    = os.environ["AZURE_SEARCH_SERVICE"]
        api_key    = os.environ.get("AZURE_SEARCH_KEY")
        use_mi     = api_key is None
        index_name = cfg.get("index_name", os.getenv("AZURE_SEARCH_INDEX","default"))
        top_k      = cfg.get("top_k", 5)
        vec_fields = cfg.get("vector_fields","embedding")
        sem_conf   = cfg.get("semantic_configuration")


        # 1) 全クエリ分のドキュメント辞書をまとめる
        all_responses: List[Dict[str,Any]] = []        # ← リスト名をわかり易く
        for q in query_list:
            docs = await azure_ai_search_async(
                service, api_key, index_name,
                q, top_k, vec_fields, sem_conf, use_mi
            )
            # ★ここで “1クエリ＝1辞書” にまとめる
            all_responses.append({
                "query": q,
                "results": docs,            # ← ユーティリティが期待するキー
                "follow_up_questions": None,
                "answer": None,
                "images": [],
            })

        # 2) まとめた辞書リストを一度だけ dedupe + フォーマット
        return odr_utils.deduplicate_and_format_sources(
            all_responses,
            max_tokens_per_source=4000,
            include_raw_content=False,
        )

    # それ以外は元の処理
    return await _orig_search(search_api, query_list, params or {})

# 差し替え
odr_utils.select_and_execute_search = _patched_select_and_execute_search

# init_chat_modelのパッチ処理
original_init_chat_model = lcm.init_chat_model

@wraps(original_init_chat_model)
def patched_init_chat_model(model, model_provider=None, **kwargs):
    """
    Azure OpenAIをマネージドIDで呼び出すためにパッチされたinit_chat_model関数
    """
    logger.info(f"init_chat_model called with model={model}, model_provider={model_provider}, kwargs={kwargs}")

    # Azure OpenAIの場合はマネージドIDを使用
    if model_provider == "azure-openai":
        logger.info("Azure OpenAI分岐に入りました")

        # 必要な環境変数を取得
        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
        if not endpoint:
            raise ValueError("AZURE_OPENAI_ENDPOINT 環境変数が設定されていません")

        # APIバージョンを環境変数から取得
        api_version = os.environ.get("AZURE_OPENAI_API_VERSION")
        if not api_version:
            raise ValueError("AZURE_OPENAI_API_VERSION 環境変数が設定されていません")

        # 環境変数の値をログ出力
        logger.info(f"環境変数: AZURE_OPENAI_ENDPOINT={endpoint}, "
                   f"\nAZURE_OPENAI_API_VERSION={api_version}, "
                   f"\nOPENAI_API_VERSION={os.environ.get('OPENAI_API_VERSION')}")

        # デプロイメント名として使用するモデル名
        deployment_name = os.environ.get("AZURE_OPENAI_DEPLOYMENT", model)
        token_provider = get_azure_openai_token_authenticator()

        # 追加のパラメータを設定
        azure_kwargs = {
            "azure_endpoint": endpoint,
            "azure_deployment": deployment_name,
            "azure_ad_token_provider": token_provider
        }
        # キーワード引数を結合
        model_kwargs = kwargs.pop("model_kwargs", {})
        combined_kwargs = {**azure_kwargs, **model_kwargs, **kwargs}

        # リクエストメッセージの詳細をログ出力
        if 'messages' in combined_kwargs:
            logger.info("【Azure OpenAIリクエスト送信直前メッセージ】:")
            for i, msg in enumerate(combined_kwargs['messages'], 1):
                logger.info(f"メッセージ {i}:")
                logger.info(json.dumps(msg, indent=2, ensure_ascii=False))

        # 完全なリクエスト構造をログに出力
        logger.info("【Azure OpenAIリクエスト構造】:")
        logger.info(f"URL: {endpoint}/openai/deployments/{deployment_name}/chat/completions?api-version={api_version}")
        logger.info("Method: POST")
        logger.info("Headers: Content-Type: application/json, Authorization: Bearer <token>")

        # リクエストボディの構造
        request_body = {
            "messages": combined_kwargs.get('messages', []),
            "model": model,
            "temperature": combined_kwargs.get('temperature', 0.7),
            "max_tokens": combined_kwargs.get('max_tokens', 4000),
            "top_p": combined_kwargs.get('top_p', 1.0),
            "frequency_penalty": combined_kwargs.get('frequency_penalty', 0),
            "presence_penalty": combined_kwargs.get('presence_penalty', 0)
        }
        logger.info(f"Request Body: {json.dumps(request_body, indent=2, ensure_ascii=False)}")

        # AzureChatOpenAIインスタンスを作成
        try:
            # combined_kwargsの中身をログ出力
            logger.info("【AzureChatOpenAIに渡すcombined_kwargsの中身】:")
            logger.info(json.dumps(combined_kwargs, indent=2, ensure_ascii=False, default=str))

            client = AzureChatOpenAI(**combined_kwargs)
            logger.info("AzureChatOpenAIインスタンスの作成に成功しました")

            try:
                from open_deep_research.state import Sections
                structured = client.with_structured_output(Sections)
                logger.info("structured_llm.output_schema: %s", structured.output_schema.schema())
            except Exception as e:
                logger.warning("スキーマ確認失敗: %s", str(e))

            logger.info(f"client.model: {getattr(client, 'model', '設定なし')}")
            logger.info(f"client.model_provider: {getattr(client, 'model_provider', '設定なし')}")
            logger.info(f"client.temperature: {getattr(client, 'temperature', '設定なし')}")
            logger.info(f"client.max_tokens: {getattr(client, 'max_tokens', '設定なし')}")
            logger.info(f"client.top_p: {getattr(client, 'top_p', '設定なし')}")
            logger.info(f"client.frequency_penalty: {getattr(client, 'frequency_penalty', '設定なし')}")
            logger.info(f"client.presence_penalty: {getattr(client, 'presence_penalty', '設定なし')}")

            return client
        except Exception as e:
            logger.error(f"AzureChatOpenAIインスタンスの作成に失敗しました: {str(e)}")
            raise

    # それ以外のプロバイダーには元の関数を使用
    logger.info(f"その他のプロバイダー({model_provider})の処理を実行します")
    client = original_init_chat_model(model=model, model_provider=model_provider, **kwargs)

    # structured_llmの中身を確認
    logger.info("【structured_llmの中身】:")
    logger.info(f"client.model: {getattr(client, 'model', '設定なし')}")
    logger.info(f"client.model_provider: {getattr(client, 'model_provider', '設定なし')}")
    logger.info(f"client.temperature: {getattr(client, 'temperature', '設定なし')}")
    logger.info(f"client.max_tokens: {getattr(client, 'max_tokens', '設定なし')}")
    logger.info(f"client.top_p: {getattr(client, 'top_p', '設定なし')}")
    logger.info(f"client.frequency_penalty: {getattr(client, 'frequency_penalty', '設定なし')}")
    logger.info(f"client.presence_penalty: {getattr(client, 'presence_penalty', '設定なし')}")

    return client

# オリジナル関数を置き換え
lcm.init_chat_model = patched_init_chat_model
from open_deep_research.utils import deduplicate_and_format_sources
