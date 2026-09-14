# カメラレディー公開用レポジトリ

AIWolfDial 2026に条件付き採択された論文 **Target-Agent Power Play Instructions in LLM Werewolf: Cue-Conditioned Execution and Matched-Game Outcomes**（Shoma Yato、Ikumu Komatsu、Haruka Itakura、Daisuke Katagami）のカメラレディー用研究資料です。

4つの評価（元固定盤面・元400試合・追加A・追加B）のコードと保存データの再解析手順を整理した、コード配布用スナップショット `1.0.0-rc.4` です。コード配布先は [Mainlst/aiwolf-nlp-agent-llm](https://github.com/Mainlst/aiwolf-nlp-agent-llm)、データ配布先は [Hugging Face](https://huggingface.co/datasets/synonym/aiwolf-nlp-agent-llm/tree/release-20260914)（固定版 `release-20260914`）です。今回の更新は採択状況と取得手順の説明であり、解析実装は[初回コード版 `1.0.0-rc.3`](https://github.com/Mainlst/aiwolf-nlp-agent-llm/tree/d427dc299bacf4eb4cb41c114c8af476b71ac7ed)と同一です。コード・データはMITで配布し、同梱コードの上流著作権表示を保持します。論文の公開識別子・DOIは未確定です。

2026-09-10の投票先・自己CO抽出の採点訂正を反映しています。主要比較は不変ですが、成分値・到達後診断は訂正されています。対応する訂正済み `dataset/` を使ってください。旧解析と元生成・判定は履歴として保持しています。[訂正記録](experiments/pp/revision/scoring-correction-20260910.md)。

2026-09-14に実験時の固定盤面判定器、確認できた事例選定記録、サーバーの実験時ソースと構築手順を復元しました。現行事例の正確な選定順序は復元できていないため、定性的な例として扱います。保存された標本・判定・数値・生アーカイブは変更していません。

## 報告した解析をオフラインで再現する

先に公開データ全体を取得します。依存関係のインストールとデータ取得にはネットワークが必要ですが、公開データの取得にAPIトークンは不要です。

```bash
python3 -m venv .venv
.venv/bin/python3 -m pip install -r release/requirements-offline.txt
.venv/bin/python3 -m pip install huggingface_hub
.venv/bin/hf download synonym/aiwolf-nlp-agent-llm --repo-type dataset --revision release-20260914 --local-dir ./dataset
.venv/bin/python3 -B release/reproduce.py --data ./dataset --output ./reproduced
```

APIキー・元環境・対戦サーバーなしで、保存判定から再採点し、主要結果と補助診断を再集計します。成功すると `reproduced/verification.json` が生成されます。`dataset/` は別配布物としてGit管理対象から除外しています。全41ファイル（約505 MB）が必要であり、閲覧用のconfigを1つ取得するだけでは再解析に必要な証拠は揃いません。`.gitattributes`、`raw/`、`provenance/`、`checksums.sha256`を含めて保持してください。

実験コードは[共有評価・実行基盤・PP各系列](experiments/README.md)、テストは[対象別のディレクトリ](tests/README.md)に整理しています。共有コードの移動先と、全件・系列別のテスト実行方法は各READMEに記載しています。

詳しい構成と結果は[英語README](README.md)、残作業は[公開状況](docs/release-status.md)を参照してください。

原稿ソース・PDF（`paper/`）はこのレポジトリには含めません。
