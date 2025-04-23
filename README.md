# Open Deep Research - Azure Functions

Open Deep Researchをサーバーレス環境で実行するためのAzure Functions実装です。

## 概要

このプロジェクトは、LangChainの[Open Deep Research](https://github.com/langchain-ai/open_deep_research)をAzure Functionsで動作させ、HTTPリクエストでレポート生成を行えるようにしたものです。人の介入なしで自動的にレポートを生成します。

### ソースコード

このプロジェクトは[langchain-ai/open_deep_research](https://github.com/langchain-ai/open_deep_research/tree/main/src/open_deep_research)を元にして、以下の拡張機能を追加しています：

1. **Azure Functions統合** - オリジナルのLangGraphワークフローをHTTPトリガーで呼び出せるように実装
2. **マネージドID認証** - Azure OpenAIへのアクセスにマネージドIDを使用する機能を追加
3. **Bing Search API対応** - Azureの検索サービスであるBing Search API (v7)を使った検索機能を追加
4. **自動実行フロー** - 人間の介入なしで完全自動化されたレポート生成を実現

## セットアップ方法

### 前提条件

- Azure Functionsの開発環境
- Python 3.9+
- Azure CLI

### 環境変数の設定

以下の環境変数を`local.settings.json`（ローカル開発用）またはAzure Functionsアプリケーション設定（本番環境用）に設定します：

```json
{
  "IsEncrypted": false,
  "Values": {
    "FUNCTIONS_WORKER_RUNTIME": "python",
    "AzureWebJobsStorage": "UseDevelopmentStorage=true",
    
    "TAVILY_API_KEY": "<your_tavily_api_key>",
    "ANTHROPIC_API_KEY": "<your_anthropic_api_key>",
    "OPENAI_API_KEY": "<your_openai_api_key>",
    "PERPLEXITY_API_KEY": "<your_perplexity_api_key>",
    "EXA_API_KEY": "<your_exa_api_key>",
    "PUBMED_API_KEY": "<your_pubmed_api_key>",
    "PUBMED_EMAIL": "<your_email@example.com>",
    "LINKUP_API_KEY": "<your_linkup_api_key>",
    "GOOGLE_API_KEY": "<your_google_api_key>",
    "GOOGLE_CX": "<your_google_custom_search_engine_id>",
    "BING_API_KEY": "<your_bing_api_key>",
    "BING_ENDPOINT": "https://api.bing.microsoft.com/v7.0/search"
  }
}
```

使用する検索APIとモデルに応じて必要なAPIキーを設定してください。

### Azure OpenAIのマネージドID認証

このアプリケーションは、Azure OpenAIへのアクセスにマネージドIDを使用する機能をサポートしています。マネージドIDを使用することで、APIキーを環境変数に保存せずに安全にAzure OpenAIを利用できます。

#### 設定手順

1. **マネージドIDの有効化**:
   Azure Portalで関数アプリのIDセクションにアクセスし、システム割り当てマネージドIDを有効にします。

2. **アクセス権の付与**:
   Azure OpenAIリソースのIAMセクションで、関数アプリのマネージドIDに「Cognitive Services OpenAI User」または「Cognitive Services OpenAI Contributor」のロールを割り当てます。

3. **必要な環境変数**:
   マネージドID認証を使用するには、以下の環境変数のみが必要です：
   ```json
   {
     "AZURE_OPENAI_ENDPOINT": "https://<your-resource-name>.openai.azure.com/"
   }
   ```

4. **モデルプロバイダーの指定**:
   リクエスト時に `planner_provider` または `writer_provider` パラメータを `"azure-openai"` に設定します：
   ```json
   {
     "topic": "AIの最新動向",
     "planner_provider": "azure-openai",
     "planner_model": "<your-deployment-name>"
   }
   ```

### Bing Search APIの使用

このアプリケーションは、Web検索にBing Search API (v7)を使用する機能をサポートしています。Bing Search APIを使用することで、Microsoftの高品質な検索結果をレポート生成に活用できます。

#### 設定手順

1. **Bing Search APIキーの取得**:
   [Azure Portal](https://portal.azure.com)でBing Search v7リソースを作成し、APIキーを取得します。

2. **環境変数の設定**:
   以下の環境変数を設定します：
   ```json
   {
     "BING_API_KEY": "<your-bing-api-key>",
     "BING_ENDPOINT": "https://api.bing.microsoft.com/v7.0/search"
   }
   ```

3. **検索APIとして指定**:
   リクエスト時に `search_api` パラメータを `"bing"` に設定します：
   ```json
   {
     "topic": "AIの最新動向",
     "search_api": "bing",
     "search_api_config": {
       "num_results": 8,
       "freshness": "Month"
     }
   }
   ```

4. **追加の設定オプション**:
   - `num_results`: 各クエリあたりの検索結果数（デフォルト: 5）
   - `freshness`: 結果の鮮度フィルター（"Day"、"Week"、"Month"）

### デプロイ

#### ローカル実行

```bash
func start
```

#### Azure へのデプロイ

```bash
az login
az functionapp create --resource-group <resource-group-name> --consumption-plan-location <location> --runtime python --runtime-version 3.9 --functions-version 4 --name <app-name> --storage-account <storage-account-name>
func azure functionapp publish <app-name>
```

## 使用方法

### リクエスト例

```bash
curl -X POST https://<app-name>.azurewebsites.net/api/generate-report \
  -H "Content-Type: application/json" \
  -H "x-functions-key: <function-key>" \
  -d '{
    "topic": "AIにおける大規模言語モデルの最近の進歩",
    "search_api": "bing",
    "planner_provider": "azure-openai",
    "planner_model": "gpt-4",
    "writer_provider": "azure-openai",
    "writer_model": "gpt-35-turbo",
    "max_search_depth": 2,
    "number_of_queries": 3,
    "search_api_config": {
      "num_results": 8,
      "freshness": "Month"
    }
  }'
```

### レスポンス例

```json
{
  "report": "## 導入\n\nAIにおける大規模言語モデル（LLM）は...",
  "status": "success"
}
```

## 設定オプション

| パラメータ | 説明 | デフォルト値 |
|------------|------|------------|
| `topic` | レポートのトピック（必須） | - |
| `search_api` | 検索API（"tavily", "perplexity", "exa", "bing"など） | "tavily" |
| `planner_provider` | プランナーモデルのプロバイダー | "anthropic" |
| `planner_model` | プランナーモデル名 | "claude-3-7-sonnet-latest" |
| `writer_provider` | ライターモデルのプロバイダー | "anthropic" |
| `writer_model` | ライターモデル名 | "claude-3-5-sonnet-latest" |
| `max_search_depth` | 検索の最大深度 | 1 |
| `number_of_queries` | セクションごとの検索クエリ数 | 2 |
| `report_structure` | レポート構造の指定 | デフォルト構造 |
| `search_api_config` | 検索API固有の設定 | {} |

### search_api_configのオプション

各検索APIは特定の設定パラメータをサポートしています：

* **Bing**
  - `num_results`: 各クエリの結果数（デフォルト: 5）
  - `freshness`: 結果の鮮度（"Day", "Week", "Month"）

* **Exa**
  - `max_characters`: 最大文字数
  - `num_results`: 結果数
  - `include_domains`: 含めるドメイン（リスト）
  - `exclude_domains`: 除外するドメイン（リスト）
  - `subpages`: サブページの数

* **ArXiv**
  - `load_max_docs`: 最大ドキュメント数
  - `get_full_documents`: 完全なドキュメントを取得するかどうか
  - `load_all_available_meta`: すべての利用可能なメタデータを読み込むかどうか

* **PubMed**
  - `top_k_results`: 上位k件の結果
  - `email`: 電子メール
  - `api_key`: APIキー
  - `doc_content_chars_max`: ドキュメントコンテンツの最大文字数

* **Linkup**
  - `depth`: 検索の深さ

## 注意事項

- レポート生成には時間がかかる場合があります。Azure Functionsのタイムアウト設定に注意してください。
- APIキーの使用量とコストを監視してください。
- 大量のリクエストを送信する場合は、レート制限に注意してください。
- マネージドIDを使用する場合、関数アプリに適切なアクセス権が付与されていることを確認してください。

# Open Deep Research
 
Open Deep Research is an open source assistant that automates research and produces customizable reports on any topic. It allows you to customize the research and writing process with specific models, prompts, report structure, and search tools. 

![report-generation](https://github.com/user-attachments/assets/6595d5cd-c981-43ec-8e8b-209e4fefc596)

## 🚀 Quickstart

Ensure you have API keys set for your desired search tools and models.

Available search tools:

* [Tavily API](https://tavily.com/) - General web search
* [Perplexity API](https://www.perplexity.ai/hub/blog/introducing-the-sonar-pro-api) - General web search
* [Exa API](https://exa.ai/) - Powerful neural search for web content
* [ArXiv](https://arxiv.org/) - Academic papers in physics, mathematics, computer science, and more
* [PubMed](https://pubmed.ncbi.nlm.nih.gov/) - Biomedical literature from MEDLINE, life science journals, and online books
* [Linkup API](https://www.linkup.so/) - General web search
* [DuckDuckGo API](https://duckduckgo.com/) - General web search
* [Google Search API/Scrapper](https://google.com/) - Create custom search engine [here](https://programmablesearchengine.google.com/controlpanel/all) and get API key [here](https://developers.google.com/custom-search/v1/introduction)

Open Deep Research uses a planner LLM for report planning and a writer LLM for report writing: 

* You can select any model that is integrated [with the `init_chat_model()` API](https://python.langchain.com/docs/how_to/chat_models_universal_init/)
* See full list of supported integrations [here](https://python.langchain.com/api_reference/langchain/chat_models/langchain.chat_models.base.init_chat_model.html)

### Using the package

```bash
pip install open-deep-research
```

As mentioned above, ensure API keys for LLMs and search tools are set: 
```bash
export TAVILY_API_KEY=<your_tavily_api_key>
export ANTHROPIC_API_KEY=<your_anthropic_api_key>
```

See [src/open_deep_research/graph.ipynb](src/open_deep_research/graph.ipynb) for example usage in a Jupyter notebook:

Compile the graph:
```python
from langgraph.checkpoint.memory import MemorySaver
from open_deep_research.graph import builder
memory = MemorySaver()
graph = builder.compile(checkpointer=memory)
```

Run the graph with a desired topic and configuration:
```python
import uuid 
thread = {"configurable": {"thread_id": str(uuid.uuid4()),
                           "search_api": "tavily",
                           "planner_provider": "anthropic",
                           "planner_model": "claude-3-7-sonnet-latest",
                           "writer_provider": "anthropic",
                           "writer_model": "claude-3-5-sonnet-latest",
                           "max_search_depth": 1,
                           }}

topic = "Overview of the AI inference market with focus on Fireworks, Together.ai, Groq"
async for event in graph.astream({"topic":topic,}, thread, stream_mode="updates"):
    print(event)
```

The graph will stop when the report plan is generated, and you can pass feedback to update the report plan:
```python
from langgraph.types import Command
async for event in graph.astream(Command(resume="Include a revenue estimate (ARR) in the sections"), thread, stream_mode="updates"):
    print(event)
```

When you are satisfied with the report plan, you can pass `True` to proceed to report generation:
```python
async for event in graph.astream(Command(resume=True), thread, stream_mode="updates"):
    print(event)
```

### Running LangGraph Studio UI locally

Clone the repository:
```bash
git clone https://github.com/langchain-ai/open_deep_research.git
cd open_deep_research
```

Then edit the `.env` file to customize the environment variables according to your needs. These environment variables control the model selection, search tools, and other configuration settings. When you run the application, these values will be automatically loaded via `python-dotenv` (because `langgraph.json` point to the "env" file).
```bash
cp .env.example .env
```

Set whatever APIs needed for your model and search tools.

Here are examples for several of the model and tool integrations available:
```bash
export TAVILY_API_KEY=<your_tavily_api_key>
export ANTHROPIC_API_KEY=<your_anthropic_api_key>
export OPENAI_API_KEY=<your_openai_api_key>
export PERPLEXITY_API_KEY=<your_perplexity_api_key>
export EXA_API_KEY=<your_exa_api_key>
export PUBMED_API_KEY=<your_pubmed_api_key>
export PUBMED_EMAIL=<your_email@example.com>
export LINKUP_API_KEY=<your_linkup_api_key>
export GOOGLE_API_KEY=<your_google_api_key>
export GOOGLE_CX=<your_google_custom_search_engine_id>
```

Launch the assistant with the LangGraph server locally, which will open in your browser:

#### Mac

```bash
# Install uv package manager
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies and start the LangGraph server
uvx --refresh --from "langgraph-cli[inmem]" --with-editable . --python 3.11 langgraph dev
```

#### Windows / Linux

```powershell
# Install dependencies 
pip install -e .
pip install -U "langgraph-cli[inmem]" 

# Start the LangGraph server
langgraph dev
```

Use this to open the Studio UI:
```
- 🚀 API: http://127.0.0.1:2024
- 🎨 Studio UI: https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024
- 📚 API Docs: http://127.0.0.1:2024/docs
```

(1) Provide a `Topic` and hit `Submit`:

<img width="1326" alt="input" src="https://github.com/user-attachments/assets/de264b1b-8ea5-4090-8e72-e1ef1230262f" />

(2) This will generate a report plan and present it to the user for review.

(3) We can pass a string (`"..."`) with feedback to regenerate the plan based on the feedback.

<img width="1326" alt="feedback" src="https://github.com/user-attachments/assets/c308e888-4642-4c74-bc78-76576a2da919" />

(4) Or, we can just pass `true` to accept the plan.

<img width="1480" alt="accept" src="https://github.com/user-attachments/assets/ddeeb33b-fdce-494f-af8b-bd2acc1cef06" />

(5) Once accepted, the report sections will be generated.

<img width="1326" alt="report_gen" src="https://github.com/user-attachments/assets/74ff01cc-e7ed-47b8-bd0c-4ef615253c46" />

The report is produced as markdown.

<img width="1326" alt="report" src="https://github.com/user-attachments/assets/92d9f7b7-3aea-4025-be99-7fb0d4b47289" />

## 📖 Customizing the report

You can customize the research assistant's behavior through several parameters:

- `report_structure`: Define a custom structure for your report (defaults to a standard research report format)
- `number_of_queries`: Number of search queries to generate per section (default: 2)
- `max_search_depth`: Maximum number of reflection and search iterations (default: 2)
- `planner_provider`: Model provider for planning phase (default: "anthropic", but can be any provider from supported integrations with `init_chat_model` as listed [here](https://python.langchain.com/api_reference/langchain/chat_models/langchain.chat_models.base.init_chat_model.html))
- `planner_model`: Specific model for planning (default: "claude-3-7-sonnet-latest")
- `writer_provider`: Model provider for writing phase (default: "anthropic", but can be any provider from supported integrations with `init_chat_model` as listed [here](https://python.langchain.com/api_reference/langchain/chat_models/langchain.chat_models.base.init_chat_model.html))
- `writer_model`: Model for writing the report (default: "claude-3-5-sonnet-latest")
- `search_api`: API to use for web searches (default: "tavily", options include "perplexity", "exa", "arxiv", "pubmed", "linkup")

These configurations allow you to fine-tune the research process based on your needs, from adjusting the depth of research to selecting specific AI models for different phases of report generation.

### Search API Configuration

Not all search APIs support additional configuration parameters. Here are the ones that do:

- **Exa**: `max_characters`, `num_results`, `include_domains`, `exclude_domains`, `subpages`
  - Note: `include_domains` and `exclude_domains` cannot be used together
  - Particularly useful when you need to narrow your research to specific trusted sources, ensure information accuracy, or when your research requires using specified domains (e.g., academic journals, government sites)
  - Provides AI-generated summaries tailored to your specific query, making it easier to extract relevant information from search results
- **ArXiv**: `load_max_docs`, `get_full_documents`, `load_all_available_meta`
- **PubMed**: `top_k_results`, `email`, `api_key`, `doc_content_chars_max`
- **Linkup**: `depth`

Example with Exa configuration:
```python
thread = {"configurable": {"thread_id": str(uuid.uuid4()),
                           "search_api": "exa",
                           "search_api_config": {
                               "num_results": 5,
                               "include_domains": ["nature.com", "sciencedirect.com"]
                           },
                           # Other configuration...
                           }}
```

### Model Considerations

(1) You can pass any planner and writer models that are integrated [with the `init_chat_model()` API](https://python.langchain.com/docs/how_to/chat_models_universal_init/). See full list of supported integrations [here](https://python.langchain.com/api_reference/langchain/chat_models/langchain.chat_models.base.init_chat_model.html).

(2) **The planner and writer models need to support structured outputs**: Check whether structured outputs are supported by the model you are using [here](https://python.langchain.com/docs/integrations/chat/).

(3) With Groq, there are token per minute (TPM) limits if you are on the `on_demand` service tier:
- The `on_demand` service tier has a limit of `6000 TPM`
- You will want a [paid plan](https://github.com/cline/cline/issues/47#issuecomment-2640992272) for section writing with Groq models

(4) `deepseek-R1` [is not strong at function calling](https://api-docs.deepseek.com/guides/reasoning_model), which the assistant uses to generate structured outputs for report sections and report section grading. See example traces [here](https://smith.langchain.com/public/07d53997-4a6d-4ea8-9a1f-064a85cd6072/r).  
- Consider providers that are strong at function calling such as OpenAI, Anthropic, and certain OSS models like Groq's `llama-3.3-70b-versatile`.
- If you see the following error, it is likely due to the model not being able to produce structured outputs (see [trace](https://smith.langchain.com/public/8a6da065-3b8b-4a92-8df7-5468da336cbe/r)):
```
groq.APIError: Failed to call a function. Please adjust your prompt. See 'failed_generation' for more details.
```

## How it works
   
1. `Plan and Execute` - Open Deep Research follows a [plan-and-execute workflow](https://github.com/assafelovic/gpt-researcher) that separates planning from research, allowing for human-in-the-loop approval of a report plan before the more time-consuming research phase. It uses, by default, a [reasoning model](https://www.youtube.com/watch?v=f0RbwrBcFmc) to plan the report sections. During this phase, it uses web search to gather general information about the report topic to help in planning the report sections. But, it also accepts a report structure from the user to help guide the report sections as well as human feedback on the report plan.
   
2. `Research and Write` - Each section of the report is written in parallel. The research assistant uses web search via [Tavily API](https://tavily.com/), [Perplexity](https://www.perplexity.ai/hub/blog/introducing-the-sonar-pro-api), [Exa](https://exa.ai/), [ArXiv](https://arxiv.org/), [PubMed](https://pubmed.ncbi.nlm.nih.gov/) or [Linkup](https://www.linkup.so/) to gather information about each section topic. It will reflect on each report section and suggest follow-up questions for web search. This "depth" of research will proceed for any many iterations as the user wants. Any final sections, such as introductions and conclusions, are written after the main body of the report is written, which helps ensure that the report is cohesive and coherent. The planner determines main body versus final sections during the planning phase.

3. `Managing different types` - Open Deep Research is built on LangGraph, which has native support for configuration management [using assistants](https://langchain-ai.github.io/langgraph/concepts/assistants/). The report `structure` is a field in the graph configuration, which allows users to create different assistants for different types of reports. 

## UX

### Local deployment

Follow the [quickstart](#-quickstart) to start LangGraph server locally.

### Hosted deployment
 
You can easily deploy to [LangGraph Platform](https://langchain-ai.github.io/langgraph/concepts/#deployment-options). 

---

## 謝辞

本プロジェクトは、[LangChain](https://github.com/langchain-ai)チームが開発した[Open Deep Research](https://github.com/langchain-ai/open_deep_research)をベースにしています。オリジナルのコードとその素晴らしいアーキテクチャを提供してくださったLangChainチームに深く感謝いたします。

### ライセンス

このプロジェクトは、オリジナルのOpen Deep Researchと同様に[MIT License](https://opensource.org/licenses/MIT)の下で公開されています。
