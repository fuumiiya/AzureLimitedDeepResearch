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

# Bing Search API (v7) 用の非同期検索関数
async def bing_search_async(search_queries, num_results=5, freshness=None):
    """
    Bing Search API (v7) を使って非同期でWeb検索を実行する関数
    
    Args:
        search_queries (Union[str, List[str]]): 検索クエリまたはクエリのリスト
        num_results (int, optional): 各クエリあたりの結果数。デフォルトは5。
        freshness (str, optional): 結果の鮮度フィルター (Day, Week, Month)。デフォルトはNone。
        
    Returns:
        List[dict]: 各クエリの検索結果のリスト。形式は以下の通り:
        [
            {
                "query": "検索クエリ",
                "results": [
                    {
                        "title": "タイトル",
                        "url": "URL",
                        "content": "コンテンツの要約",
                        "score": 1.0,
                        "raw_content": "生のコンテンツ"
                    },
                    ...
                ]
            },
            ...
        ]
    """
    if isinstance(search_queries, str):
        search_queries = [search_queries]
    
    # 環境変数からBing Search APIのキーとエンドポイントを取得
    subscription_key = os.environ.get('BING_API_KEY')
    endpoint = os.environ.get('BING_ENDPOINT')
    
    if not subscription_key:
        raise ValueError("環境変数 'BING_API_KEY' が設定されていません")
    if not endpoint:
        # デフォルトのエンドポイントを使用
        endpoint = "https://api.bing.microsoft.com/v7.0/search"
    
    # セマフォで同時リクエスト数を制限
    semaphore = asyncio.Semaphore(5)
    results = []
    
    async def search_single_query(query):
        async with semaphore:
            headers = {
                "Ocp-Apim-Subscription-Key": subscription_key
            }
            
            params = {
                "q": query,
                "count": num_results,
                "textDecorations": "true",
                "textFormat": "HTML"
            }
            
            # 鮮度フィルターを適用
            if freshness:
                params["freshness"] = freshness
            
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(endpoint, headers=headers, params=params) as response:
                        if response.status != 200:
                            error_text = await response.text()
                            logging.error(f"Bing Search API エラー: {response.status} - {error_text}")
                            return {
                                "query": query,
                                "results": []
                            }
                        
                        response_json = await response.json()
                        
                        # 検索結果を整形
                        formatted_results = []
                        
                        if "webPages" in response_json and "value" in response_json["webPages"]:
                            for i, result in enumerate(response_json["webPages"]["value"]):
                                # HTMLタグを除去してプレーンテキストに変換
                                snippet = html.unescape(result.get("snippet", ""))
                                
                                # 検索結果オブジェクトを作成
                                search_result = {
                                    "title": result.get("name", ""),
                                    "url": result.get("url", ""),
                                    "content": snippet,
                                    "score": 1.0 - (i * 0.1),  # スコアは順番に基づいて降順
                                    "raw_content": snippet
                                }
                                formatted_results.append(search_result)
                        
                        return {
                            "query": query,
                            "results": formatted_results,
                            "follow_up_questions": None,
                            "answer": None,
                            "images": []
                        }
            except Exception as e:
                logging.error(f"Bing検索中にエラーが発生しました: {str(e)}")
                return {
                    "query": query,
                    "results": [],
                    "follow_up_questions": None,
                    "answer": None,
                    "images": []
                }
    
    # すべてのクエリの検索を非同期で実行
    tasks = [search_single_query(query) for query in search_queries]
    results = await asyncio.gather(*tasks)
    
    return results

# オリジナルの select_and_execute_search 関数をパッチ
original_select_and_execute_search = select_and_execute_search

@wraps(original_select_and_execute_search)
async def patched_select_and_execute_search(search_api: str, query_list: list[str], params_to_pass: dict) -> str:
    """Select and execute the appropriate search API with Bing support.
    """
    # Bing検索APIのサポートを追加
    if search_api == "bing":
        # パラメータの取得
        num_results = params_to_pass.get("num_results", 5)
        freshness = params_to_pass.get("freshness", None)
        
        # Bing検索の実行
        search_results = await bing_search_async(query_list, num_results=num_results, freshness=freshness)
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