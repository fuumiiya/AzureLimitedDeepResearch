# Azure Limited Deep Research

Azure OpenAI と Azure AI Search を統合し、LangGraph によるエージェントワークフローを構築した PoC。  
Azure Functions 上で Managed Identity を活用し、安全な検索・レポート生成を実装しています。

## 技術構成
- Azure Functions (Python)
- Azure OpenAI Service / Azure AI Search
- LangChain / LangGraph
- Bing Search API

---

Open Deep Researchをサーバーレス環境で実行するためのAzure Functions実装です。

## 概要

このプロジェクトは、LangChainの[Open Deep Research](https://github.com/langchain-ai/open_deep_research)をAzure Functionsで動作させ、HTTPリクエストでレポート生成を行えるようにしたものです。**組織の内部ドキュメントのみを対象としたディープリサーチ**に特化してカスタマイズされています。

## 🎯 主な特徴

### オリジナルからの変更点

| 項目 | オリジナル | この実装 |
|------|------------|----------|
| **検索対象** | インターネット全体（Tavily、Perplexity等） | **組織の内部ドキュメントのみ**（Azure AI Search） |
| **実行環境** | ローカル/Jupyter | **Azure Functions（サーバーレス）** |
| **認証方式** | APIキー | **マネージドID認証** |
| **実行方式** | 対話型（人間の承認が必要） | **完全自動実行** |
| **検索機能** | キーワード検索のみ | **ハイブリッド検索**（キーワード+ベクトル+セマンティック） |

### カスタマイズ内容

1. **🔍 Azure AI Searchインデックス専用検索**
   - Azure AI Searchのインデックス内のデータのみを検索対象とする
   - インデックス化されたドキュメント（SharePoint、OneDrive、Teams等から取り込まれたデータ）を検索
   - インターネット検索は一切行わない

2. **☁️ Azure Functions統合**
   - HTTPトリガーでレポート生成を実行
   - サーバーレス環境での自動スケーリング
   - 人間の介入なしで完全自動実行

3. **🔐 マネージドID認証**
   - Azure OpenAIとAzure AI SearchへのアクセスにマネージドIDを使用
   - APIキーの管理が不要でセキュリティが向上

4. **🧠 高度な検索機能**
   - **ハイブリッド検索**: キーワード検索 + ベクトル検索 + セマンティック検索
   - **セマンティック検索**: 意味的な関連性に基づく検索
   - **ベクトル検索**: 文書の意味的類似性に基づく検索

5. **📊 詳細なロギング**
   - 各処理ステップでの詳細なログ出力
   - デバッグとモニタリングの強化

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
    
    "AZURE_OPENAI_ENDPOINT": "https://<your-resource-name>.openai.azure.com/",
    "AZURE_OPENAI_API_VERSION": "2023-10-01-preview",
    "AZURE_OPENAI_DEPLOYMENT": "<your-deployment-name>",
    
    "AZURE_SEARCH_SERVICE": "<your-search-service-name>",
    "AZURE_SEARCH_INDEX": "<your-search-index-name>",
    
    "USE_MANAGED_IDENTITY": "true"
  }
}
```

マネージドIDを使用する場合は、APIキー設定は不要です。

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
     "AZURE_OPENAI_ENDPOINT": "https://<your-resource-name>.openai.azure.com/",
     "AZURE_OPENAI_API_VERSION": "2023-10-01-preview",
     "AZURE_OPENAI_DEPLOYMENT": "<your-deployment-name>"
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

### Azure AI Searchの使用

このアプリケーションでは、Azure AI Searchを使用して**インデックス内のデータのみ**を対象としたセマンティックハイブリッド検索を実行します。

#### 🔍 検索の特徴

- **対象**: Azure AI Searchのインデックス内のデータのみ
- **データソース**: インデックス化されたドキュメント（SharePoint、OneDrive、Teams等から取り込まれたデータ）
- **検索方式**: キーワード検索 + ベクトル検索 + セマンティック検索の組み合わせ
- **セキュリティ**: 組織のデータガバナンスポリシーに準拠
- **インターネット検索**: 一切行わない（インデックス内のデータのみ）

#### セットアップ手順

1. **Azure AI Searchサービスの作成**:
   - [Azure Portal](https://portal.azure.com/)でAzure AI Searchサービスを作成
   - インデックスの作成と設定（組織のドキュメントをインデックス化）
   - **セマンティック検索の設定**（必須）

2. **環境変数の設定**:
   ```json
   {
     "AZURE_SEARCH_SERVICE": "<あなたのサービス名>",
     "AZURE_SEARCH_INDEX": "<あなたのインデックス名>"
   }
   ```

3. **マネージドID認証の設定**:
   - Azure AI SearchリソースのIAMで関数アプリのマネージドIDに「Search Index Data Reader」ロールを付与
   - APIキーは不要

4. **リクエストでの指定**:
   ```json
   {
     "search_api": "azure_ai_search",
     "search_api_config": {
       "index_name": "your-index-name",
       "top_k": 5,
       "vector_fields": "embedding",
       "semantic_configuration": "your-semantic-config",
       "use_managed_identity": true
     }
   }
   ```

### ロギング機能

このアプリケーションには、以下のような詳細なロギング機能が実装されています：

1. **処理ステップのログ**:
   - レポート生成計画の作成
   - 検索クエリの生成
   - 検索結果の取得
   - セクションの生成
   - 最終レポートのコンパイル

2. **エラーと例外のログ**:
   - API呼び出しのエラー
   - 認証エラー
   - 処理中の例外

3. **デバッグ情報**:
   - リクエストパラメータ
   - 環境変数の設定状態
   - API呼び出しの詳細

ログは以下の場所に出力されます：
- コンソール出力
- ファイル出力（`function_app.log`）
- Azure Functionsのログストリーム

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
curl -X POST https://<app-name>.azurewebsites.net/api/main \
  -H "Content-Type: application/json" \
  -H "x-functions-key: <function-key>" \
  -d '{
    "topic": "⚪︎⚪︎区で過去10年間にわたって行ってきた地域包括ケアの取組みについて詳細なreportを作成して",
    "search_api": "azure_ai_search",
    "planner_provider": "azure-openai",
    "planner_model": "gpt-4o",
    "writer_provider": "azure-openai",
    "writer_model": "gpt-4o",
    "max_search_depth": 2,
    "number_of_queries": 3,
    "search_api_config": {
      "index_name": "your-index-name",
      "top_k": 3,
      "vector_fields": "embedding",
      "semantic_configuration": "your-semantic-config",
      "use_managed_identity": true
    }
  }'
```

### レスポンス例

```json
{
  "report": "## 導入\n\n⚪︎⚪︎区における地域包括ケアの取組みは...",
  "status": "success"
}
```

## 設定オプション

### リクエストパラメータ

#### 必須パラメータ

| パラメータ | 説明 | 例 |
|------------|------|----|
| `topic` | レポートのトピック | "生成AIによる自治体の業務改革" |
| `search_api` | 使用する検索API | "azure_ai_search" |

#### オプションパラメータ

| パラメータ | 説明 | デフォルト値 | 例 |
|------------|------|------------|----|
| `planner_provider` | プランナーモデルのプロバイダー | "anthropic" | "azure-openai" |
| `planner_model` | プランナーモデル名 | "claude-3-7-sonnet-latest" | "gpt-4" |
| `writer_provider` | ライターモデルのプロバイダー | "anthropic" | "azure-openai" |
| `writer_model` | ライターモデル名 | "claude-3-5-sonnet-latest" | "gpt-4" |
| `max_search_depth` | 検索の最大深度 | 2 | 2 |
| `number_of_queries` | セクションごとの検索クエリ数 | 2 | 3 |
| `report_structure` | レポート構造の指定 | デフォルト構造 | - |
| `search_api_config` | 検索API固有の設定 | {} | 下記参照 |

### search_api_configのオプション（Azure AI Searchの場合）

| パラメータ | 説明 | デフォルト値 | 必須 |
|------------|------|------------|------|
| `index_name` | 検索対象のインデックス名 | 環境変数`AZURE_SEARCH_INDEX` | 環境変数が設定されていない場合は必須 |
| `top_k` | 取得する結果の最大数 | 5 | 任意 |
| `semantic_configuration` | セマンティック検索設定名 | "default-semantic-config" | 任意 |
| `use_managed_identity` | マネージドID認証を使用するか | false | 任意 |

## ⚠️ 注意事項

### 実行環境
- レポート生成には時間がかかる場合があります。Azure Functionsのタイムアウト設定に注意してください。
- 大量のリクエストを送信する場合は、レート制限に注意してください。

### 認証・セキュリティ
- マネージドIDを使用する場合、関数アプリに適切なアクセス権が付与されていることを確認してください。
- Azure OpenAIリソースのIAMで「Cognitive Services OpenAI User」ロールを付与してください。
- Azure AI SearchリソースのIAMで「Search Index Data Reader」ロールを付与してください。

### Azure AI Search設定
- 事前にインデックスを適切に設定し、ベクトル検索とセマンティック設定が有効になっていることを確認してください。
- セマンティック設定がない場合は、通常のハイブリッド検索にフォールバックします。
- Azure AI Searchのインデックスに組織のドキュメントが適切にインデックス化されていることを確認してください。

### ログ管理
- ログファイルのサイズに注意し、必要に応じてログローテーションを設定してください。
- デバッグ時は詳細なログが出力されるため、本番環境では適切なログレベルを設定してください。

## 参考資料

- [オリジナルのOpen Deep Research](https://github.com/langchain-ai/open_deep_research) - このプロジェクトのベースとなったLangChainのオリジナル実装
- [LangGraph Documentation](https://langchain-ai.github.io/langgraph/) - LangGraphの公式ドキュメント
- [Azure Functions Documentation](https://docs.microsoft.com/ja-jp/azure/azure-functions/) - Azure Functionsの公式ドキュメント

## 謝辞

本プロジェクトは、[LangChain](https://github.com/langchain-ai)チームが開発した[Open Deep Research](https://github.com/langchain-ai/open_deep_research)をベースにしています。オリジナルのコードとその素晴らしいアーキテクチャを提供してくださったLangChainチームに深く感謝いたします。

### ライセンス

このプロジェクトは、オリジナルのOpen Deep Researchと同様に[MIT License](https://opensource.org/licenses/MIT)の下で公開されています。
