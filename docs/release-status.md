# 公開状況（2026-09-14）

このディレクトリは `1.0.0-rc.4` のコード配布用スナップショットです。対象論文はAIWolfDial 2026に条件付き採択されており、最終採択の確認はこれからです。コードは [Mainlst/aiwolf-nlp-agent-llm](https://github.com/Mainlst/aiwolf-nlp-agent-llm)、データは [Hugging Face](https://huggingface.co/datasets/synonym/aiwolf-nlp-agent-llm/tree/release-20260914)（固定版 `release-20260914`）から取得できます。英語原稿にも両URLを反映しています。

`1.0.0-rc.4` はREADME・CITATIONの採択状況、再解析の見出し、データ取得手順と配布メタデータを更新した版です。解析実装と保存データは `1.0.0-rc.3` と同一です。初回コードの単一コミット [`d427dc2`](https://github.com/Mainlst/aiwolf-nlp-agent-llm/tree/d427dc299bacf4eb4cb41c114c8af476b71ac7ed) を保持して文書更新を追加するため、公開データ内のコード参照は引き続き有効です。開発リポジトリの過去履歴は取り込みません。

## 今回復元・修正した内容

- 元固定盤面判定時の `pp_judge.py` を記録済みSHA-256と一致するGit履歴から復元。現行runnerとは別に、由来と実VOTEの引用検証の差を保存しました。
- 初期PP原稿の事例選定計画と作業記録を最小抜粋で保存し、現行4事例を保存入力・出力・投票・採点へ対応付けました。具体的ハッシュ順と現行誤PP事例への選び直しは復元できていないため、原稿は定性的な例として説明します。[記録と限界](case-provenance.md)。
- 指定の公開サーバーと実験時ローカルcommitを照合し、実験時ソース・公開baseに対するseat修正・ライセンス・構築手順を復元しました。[新規実験の準備](new-experiments.md)。
- コード・データはMITの方針を反映し、別配布datasetにもLICENSEと同梱コードの上流表示を追加しました。合成人名・実験IDを保持する方針、元固定judgeへのVOTE提示、false PPと協調項目の範囲を論文・Dataset Cardと同期しました。
- 明示的な配布ファイル一覧を導入し、秘密設定・バックアップ・symlinkの混入を防ぐ検査、実際の配布ZIPの監査、ZIP再作成時のチェックサム更新を追加しました。

2026-09-10の採点訂正は保持しています。4評価と2補助診断、全2,792抽出・377変更抽出を再検証する入口は `release/reproduce.py` です。保存標本・意味判定・主要結果・6個のrawアーカイブを変更していません。原本と公開用のSHA-256を区別する来歴も保持しています。

## 検証記録

初回復元時には配布用回帰テスト16件、歴史ソースのハッシュ照合、Go 1.25.0によるサーバービルドと座席割当テストが成功しました。公開データは認証なしで41ファイルを再取得し、全チェックサムと6種類のHub表示を確認しています。現行コード版での保存数値の再解析結果は [verification.json](verification.json) に記録します。実配布ZIPとdatasetの監査結果はZIPとは別に `dist/distribution-audit.json` へ保存します。元の配布監査JSONは古いZIPを検査していない記録だったため、現行監査と混同しないよう配布対象から除外しました。

元の除外内容は [cleanup-inventory.json](cleanup-inventory.json)、アーカイブ内部の来歴は別配布データの `provenance/release-manifest.json` と `transformations.jsonl` を参照してください。開発時のGit履歴は公開コードに含めません。復元した歴史ソースの由来commitは、監査用の記録として文書に保持しています。

## 公開済み資料と残る確認

1. 現行のコード配布ZIPは `dist/camera-ready-code-1.0.0-rc.4.zip`。GitHubには明示的な配布ファイル一覧に含まれるコード・文書を公開し、`dataset/` は含めない。
2. 公開データのタグ `release-20260914` はコミット [`7beca23d409e323069f994c90fcb3deaad4c209c`](https://huggingface.co/datasets/synonym/aiwolf-nlp-agent-llm/commit/7beca23d409e323069f994c90fcb3deaad4c209c) に対応する。全41ファイルを取得し、Hub管理ファイル `.gitattributes` と `checksums.sha256` も保持する。
3. README・Dataset Card・英語原稿に取得先を記載した。公開識別子は確定後にCITATIONへ追加し、DOIは発行される場合だけ記載する。サーバーは [aiwolfdial/aiwolf-nlp-server](https://github.com/aiwolfdial/aiwolf-nlp-server) を参照する。
4. 最終採択を確認した後、README両言語・CITATION・Dataset Card・この文書の `conditionally accepted`／条件付き採択の限定を更新する。採択確認までは現在の状態を維持する。

投稿前には採択通知・提出画面に従って著者情報、ページ数、必要ファイルを最終確認します。カメラレディー版の提出は未実施です。今回の説明更新ではモデル呼出しや新規対戦を行っていません。

## 配布物を更新する手順

新規ファイルは `release/package-files.txt` に明示追加します。意図した変更を確認後、関連テスト、`python3 release/seal.py`、新しい出力先での `python3 -B release/reproduce.py --data dataset --output <new-output>` の順に実行します。成功した `verification.json` を `docs/verification.json` へコピーし、`python3 release/package.py`、`python3 release/audit.py` を実行します。既存ZIPがある場合は、旧版も含めて `dist/` 内のZIPをチェックアウト内の非配布領域へ退避してから再作成します。監査は `dist/` 内の全ZIPを現行ファイルと照合します。公開済みの版を上書きせず、新しい版を使用してください。

原稿ソース・PDF（`paper/`）はこのコードリポジトリには含めません。保存結果の再解析と、外部モデル・サーバーを使う新規対戦は異なる手順です。モデルの同一出力を再生成できるとは保証していません。
