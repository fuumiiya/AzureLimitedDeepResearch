import json
import logging
import asyncio
import azure.functions as func
from langgraph.checkpoint.memory import MemorySaver
from open_deep_research.graph import graph
from open_deep_research.configuration import Configuration
import uuid
import os
import traceback
from azure.identity import DefaultAzureCredential
from azure.core.credentials import TokenCredential, AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import QueryType
from langchain_community.chat_models import AzureChatOpenAI
from langchain.chat_models import init_chat_model
from functools import wraps
import aiohttp
from open_deep_research.utils import deduplicate_and_format_sources, select_and_execute_search
from typing import List, Dict, Optional, Tuple, Any
import datetime
import sys

# ロガーの設定
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# コンソールハンドラーの設定
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)
console_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
console_handler.setFormatter(console_formatter)
logger.addHandler(console_handler)

# ファイルハンドラーの設定
file_handler = logging.FileHandler('function_app.log')
file_handler.setLevel(logging.DEBUG)
file_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
file_handler.setFormatter(file_formatter)
logger.addHandler(file_handler)

# Azure Functionsアプリの設定
app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

@app.function_name(name="HttpTrigger")
@app.route(route="main", methods=["POST"])
async def main(req: func.HttpRequest) -> func.HttpResponse:
    """
    HTTPトリガーでOpen Deep Researchのレポート生成を実行するAzure Functions関数。
    
    パラメータ：
    - topic: レポートのトピック（必須）
    - search_api: 使用する検索API（例: "tavily", "perplexity", "exa"など）
    - planner_provider: プランナーモデルのプロバイダー（例: "anthropic", "openai"など）
    - planner_model: プランナーモデル名
    - writer_provider: ライターモデルのプロバイダー
    - writer_model: ライターモデル名
    - max_search_depth: 検索の最大深度
    - report_structure: レポート構造の指定（オプション）
    - number_of_queries: セクションごとの検索クエリ数（オプション）
    - search_api_config: 検索API固有の設定（オプション）
    
    戻り値：
    - 成功時: 生成されたレポート（マークダウン形式）をJSONで返す
    - エラー時: エラーメッセージをJSONで返す
    """
    logger.info('Open Deep Research function triggered')
    
    try:
        # リクエストデータを取得
        req_body = req.get_json()
        logger.info(f"受信したリクエストボディ: {json.dumps(req_body, indent=2, ensure_ascii=False)}")
        
        # 必須フィールドの確認
        if 'topic' not in req_body:
            return func.HttpResponse(
                json.dumps({"error": "トピックが指定されていません。'topic'フィールドは必須です。"}),
                mimetype="application/json",
                status_code=400
            )
        
        # トピックを取得
        topic = req_body.get('topic')
        logger.info(f"トピック: {topic}")
        
        # 設定パラメータを取得
        thread_config = {
            "thread_id": str(uuid.uuid4()),
            "search_api": req_body.get('search_api', "tavily"),
            "planner_provider": req_body.get('planner_provider', "anthropic"),
            "planner_model": req_body.get('planner_model', "claude-3-7-sonnet-latest"),
            "writer_provider": req_body.get('writer_provider', "anthropic"),
            "writer_model": req_body.get('writer_model', "claude-3-5-sonnet-latest"),
            "max_search_depth": req_body.get('max_search_depth', 2),
        }
        logger.info(f"作成したthread_config: {json.dumps(thread_config, indent=2, ensure_ascii=False)}")
        
        # オプションのパラメータ
        if 'report_structure' in req_body:
            thread_config["report_structure"] = req_body.get('report_structure')
            logger.info(f"report_structure: {thread_config['report_structure']}")
        
        if 'number_of_queries' in req_body:
            thread_config["number_of_queries"] = req_body.get('number_of_queries')
            logger.info(f"number_of_queries: {thread_config['number_of_queries']}")
            
        if 'search_api_config' in req_body:
            thread_config["search_api_config"] = req_body.get('search_api_config')
            logger.info(f"search_api_config: {json.dumps(thread_config['search_api_config'], indent=2, ensure_ascii=False)}")
            
        # スレッド設定を作成
        thread = {"configurable": thread_config}
        logger.info(f"作成したthread: {json.dumps(thread, indent=2, ensure_ascii=False)}")
        
        # メモリセーバーを初期化
        memory = MemorySaver()
        logger.info("MemorySaverを初期化しました")
        
        # グラフ実行直前のログ
        logger.info(f"graph.ainvoke呼び出し直前: {datetime.datetime.now().isoformat()}")
        logger.info(f"呼び出しパラメータ: topic={topic}, thread={json.dumps(thread, indent=2, ensure_ascii=False)}")
        
        # グラフを実行
        try:
            logger.info("graph.ainvoke呼び出し開始")
            result = await graph.ainvoke({"topic": topic}, thread)
            logger.info("graph.ainvokeの呼び出しに成功しました")
            logger.info(f"graph.ainvokeの戻り値: {json.dumps(result, indent=2, ensure_ascii=False)}")
            logger.info("graph.ainvoke呼び出し完了")
        except Exception as e:
            logger.error(f"LangGraph起動エラー: {str(e)}")
            logger.error(f"スタックトレース:\n{traceback.format_exc()}")
            raise
        
        # グラフ実行直後のログ
        logger.info(f"graph.ainvoke呼び出し完了: {datetime.datetime.now().isoformat()}")
        logger.info(f"結果のキー: {list(result.keys()) if result else '結果なし'}")
        
        # 結果からレポートを取得
        final_report = result.get('final_report', '')
        logger.info(f"生成されたレポートの長さ: {len(final_report)}文字")
        
        # JSONレスポンスを返す
        return func.HttpResponse(
            json.dumps({
                "report": final_report,
                "status": "success"
            }),
            mimetype="application/json"
        )
    
    except Exception as e:
        logger.error(f"エラーが発生しました: {str(e)}")
        logger.error(f"スタックトレース:\n{traceback.format_exc()}")
            
        return func.HttpResponse(
            json.dumps({
                "error": str(e),
                "status": "error"
            }),
            mimetype="application/json",
            status_code=500
        )

# カスタムのAzureOpenAI認証関数
def get_azure_openai_token_authenticator():
    """Azure OpenAI用のトークン認証器を取得する"""
    credential = DefaultAzureCredential()
    
    # アクセストークンを非同期で取得する関数
    async def get_token():
        # Azure OpenAIのスコープ
        scope = "https://cognitiveservices.azure.com/.default"
        token = credential.get_token(scope)
        return token.token
    
    return get_token

# 非同期処理用の補助関数
async def run_async(func):
    """同期関数を非同期的に実行するためのヘルパー関数"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, func)

# Azure AI Search用の非同期検索関数
async def azure_ai_search_async(
    search_service: str,
    search_key: str,
    index_name: str,
    query: str,
    top_k: int = 5,
    semantic_configuration: str = "default-semantic-config",
    use_managed_identity: bool = False,
) -> List[Dict[str, any]]:
    """
    Azure AI Searchを使用してセマンティックハイブリッド検索を実行します。
    レポート作成に必要な情報のみを抽出します。

    Args:
        search_service (str): Azure AI Searchサービス名
        search_key (str): Azure AI Search APIキー
        index_name (str): 検索対象のインデックス名
        query (str): 検索クエリ
        top_k (int, optional): 取得する結果の最大数. デフォルトは5.
        semantic_configuration (str, optional): セマンティック検索設定名. デフォルトは"default-semantic-config".
        use_managed_identity (bool, optional): マネージドIDを使用するかどうか. デフォルトはFalse.

    Returns:
        List[Dict[str, any]]: 検索結果（必要な情報のみ）
    """
    logger.info(f"Azure AI Search: サービス={search_service}, インデックス={index_name}, クエリ={query}")
    
    # 必要なフィールドのみを選択
    select = [
        "metadata_spo_item_name",  # タイトル
        "metadata_spo_item_path",  # URL
        "textItems",              # テキストアイテム
    ]
    
    # 認証情報の設定
    credential = None
    if use_managed_identity:
        credential = DefaultAzureCredential()
    
    # サービスクライアントの作成
    endpoint = f"https://{search_service}.search.windows.net/"
    if use_managed_identity:
        search_client = SearchClient(endpoint=endpoint, index_name=index_name, credential=credential)
    else:
        search_client = SearchClient(endpoint=endpoint, index_name=index_name, credential=AzureKeyCredential(search_key))

    # ベクトルクエリの設定
    vector_queries = [
        {
            "kind": "text",
            "text": query,
            "k": 10,
            "fields": "embedding"
        }
    ]

    # セマンティックハイブリッド検索を実行
    logger.info(f"セマンティックハイブリッド検索を実行: 設定={semantic_configuration}")
    search_results = await run_async(lambda: search_client.search(
        search_text=query,
        vector_queries=vector_queries,
        select=select,
        top=5,
        query_type=QueryType.SEMANTIC,
        semantic_configuration_name=semantic_configuration,
        answers="extractive|count-3",
        captions="extractive"
    ))

    # 検索結果の変換
    results = []
    try:
        if search_results:
            docs = [doc async for doc in search_results]
            for document in docs:
                # 必要な情報のみを抽出
                doc_dict = {
                    "title": document.get("metadata_spo_item_name", ""),
                    "url": document.get("metadata_spo_item_path", ""),
                    "text_items": document.get("textItems", []),
                    "search_score": document.get("@search.score", 0.0),
                }
                
                # @search.captionsフィールドの処理
                if "@search.captions" in document and document["@search.captions"]:
                    captions = document["@search.captions"]
                    if captions and len(captions) > 0:
                        doc_dict["caption"] = captions[0].text
                
                # @search.answersフィールドの処理
                if "@search.answers" in document:
                    answers = document["@search.answers"]
                    if answers and len(answers) > 0:
                        doc_dict["answers"] = [answer.text for answer in answers]
                
                results.append(doc_dict)
    except Exception as e:
        logger.error(f"検索結果の処理中にエラーが発生しました: {str(e)}", exc_info=True)

    logger.info(f"検索結果: {len(results)}件")
    return results

# 検索結果を文字列に変換する関数
def format_search_results_to_string(docs: List[Dict[str, Any]]) -> str:
    """
    検索結果のリストを文字列に変換します。
    各ドキュメントのタイトル、URL、キャプション、テキストアイテムを整形します。

    Args:
        docs (List[Dict[str, Any]]): 検索結果のリスト

    Returns:
        str: フォーマットされた文字列
    """
    formatted_docs = []
    for doc in docs:
        # タイトルとURL
        title_url = f"タイトル: {doc.get('title', '')}\nURL: {doc.get('url', '')}"
        
        # キャプションまたはテキストアイテム
        content = []
        if "caption" in doc:
            content.append(f"要約: {doc['caption']}")
        
        # テキストアイテムから最初の3つを使用
        text_items = doc.get("text_items", [])[:3]
        if text_items:
            content.append("関連テキスト:")
            for item in text_items:
                content.append(f"- {item}")
        
        # ドキュメントの整形
        formatted_doc = f"{title_url}\n\n" + "\n".join(content)
        formatted_docs.append(formatted_doc)
    
    return "\n\n" + "="*50 + "\n\n".join(formatted_docs) + "\n\n" + "="*50

# オリジナルの select_and_execute_search 関数をパッチ
original_select_and_execute_search = select_and_execute_search

@wraps(original_select_and_execute_search)
async def patched_select_and_execute_search(
    reports_map: Dict[str, str],
    search_results_map: Dict[str, List[Dict[str, Any]]],
    execution_id: str,
    *,
    search_api: Optional[str] = None,
    search_api_config: Optional[Dict[str, Any]] = None,
    **kwargs,
) -> Tuple[Dict[str, str], Dict[str, List[Dict[str, Any]]]]:
    """
    検索APIを選択して実行するパッチされた関数。
    Azure AI Searchの場合は、常にセマンティックハイブリッド検索を実行します。
    複数のクエリを並列で処理します。
    """
    search_config = search_api_config or {}

    if search_api == "azure_ai_search":
        try:
            # 環境変数を取得
            search_service = os.environ.get("AZURE_SEARCH_SERVICE")
            if not search_service:
                reports_map[execution_id] = "環境変数 AZURE_SEARCH_SERVICE が設定されていません"
                return reports_map, search_results_map
            
            search_key = os.environ.get("AZURE_SEARCH_KEY")
            use_managed_identity = False
            if not search_key:
                logger.info("Azure Search APIキーが設定されていません。マネージドIDを使用します。")
                use_managed_identity = True
            
            default_index = os.environ.get("AZURE_SEARCH_INDEX", "default")
            
            # 設定パラメータを取得
            index_name = search_config.get("index_name", default_index)
            top_k = search_config.get("top_k", 5)
            semantic_configuration = search_config.get("semantic_configuration", "default-semantic-config")
            
            # クエリの取得
            generated_queries = kwargs.get("generated_queries", [])
            
            # クエリが空の場合は実行しない
            if not generated_queries:
                reports_map[execution_id] = "生成された検索クエリが空です"
                return reports_map, search_results_map
            
            logger.info(f"処理するクエリ数: {len(generated_queries)}")
            
            # 複数クエリを並列で処理
            search_tasks = []
            for query in generated_queries:
                search_tasks.append(azure_ai_search_async(
                    search_service=search_service,
                    search_key=search_key,
                    index_name=index_name,
                    query=query,
                    top_k=top_k,
                    semantic_configuration=semantic_configuration,
                    use_managed_identity=use_managed_identity,
                ))
            
            # 並列で検索を実行
            search_results_list = await asyncio.gather(*search_tasks)
            
            # 検索結果を結合
            combined_results = []
            for results in search_results_list:
                combined_results.extend(results)
            
            # 検索結果を文字列に変換
            source_str = format_search_results_to_string(combined_results)
            
            # 検索結果の保存（デバッグ用）
            search_results_map[execution_id] = combined_results
            
            # 文字列形式の検索結果を返す
            return reports_map, {execution_id: source_str}
            
        except Exception as e:
            reports_map[execution_id] = f"Azure AI Search検索中にエラーが発生しました: {str(e)}"
            logger.error(f"Azure AI Search検索エラー: {str(e)}", exc_info=True)
        
        return reports_map, search_results_map
    else:
        # デフォルトの検索実行
        return await original_select_and_execute_search(reports_map, search_results_map, execution_id, **kwargs)

# オリジナル関数をパッチで置き換え
select_and_execute_search = patched_select_and_execute_search

# init_chat_modelのパッチ処理
original_init_chat_model = init_chat_model

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
                   f"AZURE_OPENAI_API_VERSION={api_version}, "
                   f"OPENAI_API_VERSION={os.environ.get('OPENAI_API_VERSION')}")
        
        # デプロイメント名として使用するモデル名
        deployment_name = os.environ.get("AZURE_OPENAI_DEPLOYMENT", model)
        logger.info(f"デプロイメント名: {deployment_name}")
        
        # TokenCredentialを使用してAzure OpenAIに接続
        token_provider = get_azure_openai_token_authenticator()
        
        # 追加のパラメータを設定
        azure_kwargs = {
            "azure_endpoint": endpoint,
            "azure_deployment": deployment_name,
            "azure_ad_token_provider": token_provider
        }
        
        # キーワード引数を結合
        combined_kwargs = {**azure_kwargs, **kwargs}
        
        # リクエストメッセージの詳細をログ出力
        if 'messages' in combined_kwargs:
            logger.info("【Azure OpenAIリクエスト送信直前メッセージ】:")
            for i, msg in enumerate(combined_kwargs['messages'], 1):
                logger.info(f"メッセージ {i}:")
                logger.info(json.dumps(msg, indent=2, ensure_ascii=False))
        
        # 完全なリクエスト構造をログに出力
        logger.info("【Azure OpenAI完全リクエスト構造】:")
        logger.info(f"URL: {endpoint}/openai/deployments/{deployment_name}/chat/completions?api-version={api_version}")
        logger.info("Method: POST")
        logger.info("Headers: Content-Type: application/json, Authorization: Bearer <token>")
        
        # リクエストボディの構造
        request_body = {
            "messages": combined_kwargs.get('messages', []),
            "model": model,
            "temperature": combined_kwargs.get('temperature', 0.7),
            "max_tokens": combined_kwargs.get('max_tokens', 1000),
            "top_p": combined_kwargs.get('top_p', 1.0),
            "frequency_penalty": combined_kwargs.get('frequency_penalty', 0),
            "presence_penalty": combined_kwargs.get('presence_penalty', 0)
        }
        logger.info(f"Request Body: {json.dumps(request_body, indent=2, ensure_ascii=False)}")
        
        # AzureChatOpenAIインスタンスを作成
        try:
            # combined_kwargsの中身をログ出力
            logger.info("【AzureChatOpenAIに渡すcombined_kwargsの中身】:")
            logger.info(json.dumps(combined_kwargs, indent=2, ensure_ascii=False))
            
            client = AzureChatOpenAI(**combined_kwargs)
            logger.info("AzureChatOpenAIインスタンスの作成に成功しました")
            
            # structured_llmの中身を確認
            if hasattr(client, '_client'):
                logger.info("【structured_llmの中身】:")
                logger.info(f"client._client.api_key: {getattr(client._client, 'api_key', '設定なし')}")
                logger.info(f"client._client.base_url: {getattr(client._client, 'base_url', '設定なし')}")
                logger.info(f"client._client.default_headers: {getattr(client._client, 'default_headers', '設定なし')}")
                logger.info(f"client._client.timeout: {getattr(client._client, 'timeout', '設定なし')}")
            
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
init_chat_model = patched_init_chat_model 