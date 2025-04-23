import json
import logging
import asyncio
import azure.functions as func
from langgraph.checkpoint.memory import MemorySaver
from open_deep_research.graph import graph
from open_deep_research.configuration import Configuration
import uuid
import os
from azure.identity import DefaultAzureCredential
from azure.core.credentials import TokenCredential
from langchain.chat_models import AzureChatOpenAI
from langchain.chat_models import init_chat_model
from functools import wraps
import aiohttp
import html
from open_deep_research.utils import deduplicate_and_format_sources, select_and_execute_search

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

# Azure AI Search用の非同期検索関数
async def azure_ai_search_async(search_queries, index_name=None, search_type="vector", top_k=5, 
                               semantic_configuration=None, vector_fields=None):
    """
    Azure AI Searchを使って非同期で検索を実行する関数
    
    Args:
        search_queries (Union[str, List[str]]): 検索クエリまたはクエリのリスト
        index_name (Optional[str]): 検索対象のインデックス名（必須）
        search_type (Optional[str]): "vector", "semantic", "hybrid"のいずれか
        top_k (int): 各クエリに対して返す結果の最大数
        semantic_configuration (Optional[str]): 意味検索を使用する場合の設定名
        vector_fields (Optional[List[str]]): ベクトル検索に使用するフィールド名のリスト
        
    Returns:
        List[dict]: 各クエリの検索結果のリスト。形式は以下の通り:
        [
            {
                "query": "検索クエリ",
                "results": [
                    {
                        "title": "タイトル",
                        "url": "URL",
                        "content": "コンテンツ要約",
                        "score": 1.0,
                        "raw_content": "生のコンテンツ"
                    },
                    ...
                ]
            },
            ...
        ]
    """
    from azure.core.credentials import AzureKeyCredential
    from azure.search.documents import SearchClient
    from azure.search.documents.models import VectorizableTextQuery, QueryType, QueryCaptionType, QueryAnswerType
    
    if isinstance(search_queries, str):
        search_queries = [search_queries]
    
    # 環境変数から情報を取得
    search_service = os.environ.get("AZURE_SEARCH_SERVICE")
    if not search_service:
        raise ValueError("環境変数 AZURE_SEARCH_SERVICE が設定されていません")
    
    # インデックス名が指定されていない場合はエラー
    if not index_name:
        index_name = os.environ.get("AZURE_SEARCH_INDEX")
        if not index_name:
            raise ValueError("インデックス名が指定されていないか、環境変数 AZURE_SEARCH_INDEX が設定されていません")
    
    # デフォルトのベクトルフィールド
    if not vector_fields and search_type in ["vector", "hybrid"]:
        vector_fields = ["contentVector"]
    
    # Azure AI Searchクライアントを初期化
    endpoint = f"https://{search_service}.search.windows.net"
    
    # 認証情報を取得 (キーまたはマネージドID)
    api_key = os.environ.get("AZURE_SEARCH_KEY")
    
    if api_key:
        # API Keyを使用
        credential = AzureKeyCredential(api_key)
    else:
        # マネージドIDを使用
        credential = DefaultAzureCredential()
    
    # SearchClientの初期化
    search_client = SearchClient(
        endpoint=endpoint,
        index_name=index_name,
        credential=credential
    )
    
    # セマフォで同時リクエスト数を制限
    semaphore = asyncio.Semaphore(5)
    results = []
    
    async def search_single_query(query):
        async with semaphore:
            query_result = {
                "query": query,
                "results": [],
                "follow_up_questions": None,
                "answer": None,
                "images": []
            }
            
            try:
                # ループを取得して同期処理を実行
                loop = asyncio.get_event_loop()
                
                # 検索処理を実行する関数
                def perform_search():
                    if search_type == "vector":
                        # ベクトル検索の実行
                        search_results = search_client.search(
                            search_text=None,
                            vector_queries=[
                                VectorizableTextQuery(
                                    text=query,
                                    k=top_k,
                                    fields=vector_fields
                                )
                            ],
                            top=top_k
                        )
                    elif search_type == "semantic":
                        # セマンティック検索の実行
                        if not semantic_configuration:
                            raise ValueError("セマンティック検索には semantic_configuration が必要です")
                        
                        search_results = search_client.search(
                            search_text=query,
                            query_type=QueryType.SEMANTIC,
                            semantic_configuration_name=semantic_configuration,
                            query_caption=QueryCaptionType.EXTRACTIVE,
                            query_answer=QueryAnswerType.EXTRACTIVE,
                            top=top_k
                        )
                    elif search_type == "hybrid":
                        # ハイブリッド検索の実行（ベクトル+キーワード）
                        if not semantic_configuration:
                            # セマンティック設定なしのハイブリッド検索
                            search_results = search_client.search(
                                search_text=query,
                                vector_queries=[
                                    VectorizableTextQuery(
                                        text=query,
                                        k=top_k,
                                        fields=vector_fields
                                    )
                                ],
                                top=top_k
                            )
                        else:
                            # セマンティック機能付きのハイブリッド検索
                            search_results = search_client.search(
                                search_text=query,
                                query_type=QueryType.SEMANTIC,
                                semantic_configuration_name=semantic_configuration,
                                query_caption=QueryCaptionType.EXTRACTIVE,
                                vector_queries=[
                                    VectorizableTextQuery(
                                        text=query,
                                        k=top_k,
                                        fields=vector_fields
                                    )
                                ],
                                top=top_k
                            )
                    else:
                        # デフォルトはキーワード検索
                        search_results = search_client.search(query, top=top_k)
                    
                    return list(search_results)
                
                # 同期検索処理を実行
                search_results = await loop.run_in_executor(None, perform_search)
                
                # 結果の処理
                for i, result in enumerate(search_results):
                    # score値を取得、なければインデックスからスコアを生成
                    score = getattr(result, "@search.score", 1.0 - (i * 0.1))
                    
                    # ドキュメントからタイトル、URL、コンテンツを抽出
                    # 注: 実際のフィールド名はインデックスによって異なる可能性があります
                    title = getattr(result, "title", "タイトルなし")
                    url = getattr(result, "url", "URLなし")
                    content = getattr(result, "content", "")
                    
                    # セマンティック検索の場合、キャプションがあれば使用
                    if hasattr(result, "@search.captions") and result["@search.captions"]:
                        caption = result["@search.captions"][0]["text"]
                        if caption:
                            content = caption
                    
                    search_result = {
                        "title": title,
                        "url": url,
                        "content": content,
                        "score": score,
                        "raw_content": content  # raw_contentとcontentを同じにする
                    }
                    
                    query_result["results"].append(search_result)
            
            except Exception as e:
                logging.error(f"Azure AI Search中にエラーが発生しました: {str(e)}")
                # エラーが発生しても処理を続行し、空の結果を返す
            
            return query_result
    
    # すべてのクエリの検索を非同期で実行
    tasks = [search_single_query(query) for query in search_queries]
    results = await asyncio.gather(*tasks)
    
    return results

# オリジナルの select_and_execute_search 関数をパッチ
original_select_and_execute_search = select_and_execute_search

@wraps(original_select_and_execute_search)
async def patched_select_and_execute_search(search_api: str, query_list: list[str], params_to_pass: dict) -> str:
    """Select and execute the appropriate search API with Azure AI Search support.
    """
    # Azure AI Search APIのサポートを追加
    if search_api == "azure_ai_search":
        # パラメータの取得
        index_name = params_to_pass.get("index_name")
        search_type = params_to_pass.get("search_type", "vector")
        top_k = params_to_pass.get("top_k", 5)
        semantic_configuration = params_to_pass.get("semantic_configuration")
        vector_fields = params_to_pass.get("vector_fields")
        
        # Azure AI Search検索の実行
        search_results = await azure_ai_search_async(
            query_list, 
            index_name=index_name,
            search_type=search_type,
            top_k=top_k,
            semantic_configuration=semantic_configuration,
            vector_fields=vector_fields
        )
        return deduplicate_and_format_sources(search_results, max_tokens_per_source=4000)
    else:
        # 他の検索APIは元の関数を使用
        return await original_select_and_execute_search(search_api, query_list, params_to_pass)

# オリジナル関数をパッチで置き換え
select_and_execute_search = patched_select_and_execute_search

# init_chat_modelのパッチ処理
original_init_chat_model = init_chat_model

@wraps(original_init_chat_model)
def patched_init_chat_model(model, model_provider=None, **kwargs):
    """
    Azure OpenAIをマネージドIDで呼び出すためにパッチされたinit_chat_model関数
    """
    # Azure OpenAIの場合はマネージドIDを使用
    if model_provider == "azure-openai":
        # 必要な環境変数
        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
        if not endpoint:
            raise ValueError("AZURE_OPENAI_ENDPOINT 環境変数が設定されていません")
        
        # デプロイメント名として使用するモデル名
        deployment_name = model
        
        # TokenCredentialを使用してAzure OpenAIに接続
        token_provider = get_azure_openai_token_authenticator()
        
        # AzureChatOpenAIインスタンスを直接作成
        return AzureChatOpenAI(
            azure_endpoint=endpoint,
            azure_deployment=deployment_name,
            openai_api_version="2023-12-01-preview",  # 適切なAPIバージョンに更新してください
            azure_ad_token_provider=token_provider,
            **kwargs
        )
    
    # それ以外のプロバイダーには元の関数を使用
    return original_init_chat_model(model=model, model_provider=model_provider, **kwargs)

# オリジナル関数を置き換え
init_chat_model = patched_init_chat_model

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
    logging.info('Open Deep Research function triggered')
    
    try:
        # リクエストデータを取得
        req_body = req.get_json()
        
        # 必須フィールドの確認
        if 'topic' not in req_body:
            return func.HttpResponse(
                json.dumps({"error": "トピックが指定されていません。'topic'フィールドは必須です。"}),
                mimetype="application/json",
                status_code=400
            )
        
        # トピックを取得
        topic = req_body.get('topic')
        
        # 設定パラメータを取得
        thread_config = {
            "thread_id": str(uuid.uuid4()),
            "search_api": req_body.get('search_api', "tavily"),
            "planner_provider": req_body.get('planner_provider', "anthropic"),
            "planner_model": req_body.get('planner_model', "claude-3-7-sonnet-latest"),
            "writer_provider": req_body.get('writer_provider', "anthropic"),
            "writer_model": req_body.get('writer_model', "claude-3-5-sonnet-latest"),
            "max_search_depth": req_body.get('max_search_depth', 1),
        }
        
        # オプションのパラメータ
        if 'report_structure' in req_body:
            thread_config["report_structure"] = req_body.get('report_structure')
        
        if 'number_of_queries' in req_body:
            thread_config["number_of_queries"] = req_body.get('number_of_queries')
            
        if 'search_api_config' in req_body:
            thread_config["search_api_config"] = req_body.get('search_api_config')
        
        # スレッド設定を作成
        thread = {"configurable": thread_config}
        
        # メモリセーバーを初期化
        memory = MemorySaver()
        
        # グラフを実行
        result = await graph.invoke({"topic": topic}, thread)
        
        # 結果からレポートを取得
        final_report = result.get('final_report', '')
        
        # JSONレスポンスを返す
        return func.HttpResponse(
            json.dumps({
                "report": final_report,
                "status": "success"
            }),
            mimetype="application/json"
        )
    
    except Exception as e:
        logging.error(f"エラーが発生しました: {str(e)}")
        return func.HttpResponse(
            json.dumps({
                "error": str(e),
                "status": "error"
            }),
            mimetype="application/json",
            status_code=500
        ) 