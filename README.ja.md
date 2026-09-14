# カメラレディー公開用レポジトリ

4つの評価（元固定盤面・元400試合・追加A・追加B）のコードと保存データの再解析手順を整理した、コード配布用スナップショット `1.0.0-rc.3` です。コード配布先は [Mainlst/aiwolf-nlp-agent-llm](https://github.com/Mainlst/aiwolf-nlp-agent-llm) です。初回コード配布は `main` 上の単一の初回コミットを使用し、開発時の過去履歴は引き継ぎません。データは別配布で、公開と取得先の設定はまだ完了していません。コード・データはMITで配布し、同梱コードの上流著作権表示を保持します。論文の公開識別子・DOIは未確定です。

2026-09-10の投票先・自己CO抽出の採点訂正を反映しています。主要比較は不変ですが、成分値・到達後診断は訂正されています。対応する訂正済み `dataset/` を使ってください。旧解析と元生成・判定は履歴として保持しています。[訂正記録](experiments/pp/revision/scoring-correction-20260910.md)。

2026-09-14に実験時の固定盤面判定器、確認できた事例選定記録、サーバーの実験時ソースと構築手順を復元しました。現行事例の正確な選定順序は復元できていないため、定性的な例として扱います。保存された標本・判定・数値・生アーカイブは変更していません。

```bash
python3 -m venv .venv
.venv/bin/python3 -m pip install -r release/requirements-offline.txt
.venv/bin/python3 -B release/reproduce.py --data ./dataset --output ./reproduced
```

APIキー・元環境・対戦サーバーなしで、保存判定から再採点し、主要結果と補助診断を再集計します。成功すると `reproduced/verification.json` が生成されます。`dataset/` はHF向けの別配布物としてGit管理対象から除外しています。コードだけを取得した場合のデータ取得先は、公開時に設定が必要です。

実験コードは[共有評価・実行基盤・PP各系列](experiments/README.md)、テストは[対象別のディレクトリ](tests/README.md)に整理しています。共有コードの移動先と、全件・系列別のテスト実行方法は各READMEに記載しています。

詳しい構成と結果は[英語README](README.md)、残作業は[公開状況](docs/release-status.md)を参照してください。

原稿ソース・PDF（`paper/`）はこのレポジトリには含めません。
