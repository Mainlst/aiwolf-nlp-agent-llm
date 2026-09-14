# 公開準備状況（2026-09-14）

このディレクトリは `1.0.0-rc.3` のコード配布用スナップショットです。コード配布先は [Mainlst/aiwolf-nlp-agent-llm](https://github.com/Mainlst/aiwolf-nlp-agent-llm) です。初回配布は `main` 上の単一の初回コミットを使用し、開発リポジトリの過去履歴は引き継ぎません。データは別配布で、公開先の確定とアップロードはまだ完了していません。論文への公開リンク設定も残っています。このコード配布に伴う新しいモデル呼出し・対戦・論文提出は行いません。

## 今回復元・修正した内容

- 元固定盤面判定時の `pp_judge.py` を記録済みSHA-256と一致するGit履歴から復元。現行runnerとは別に、由来と実VOTEの引用検証の差を保存しました。
- 初期PP原稿の事例選定計画と作業記録を最小抜粋で保存し、現行4事例を保存入力・出力・投票・採点へ対応付けました。具体的ハッシュ順と現行誤PP事例への選び直しは復元できていないため、原稿は定性的な例として説明します。[記録と限界](case-provenance.md)。
- 指定の公開サーバーと実験時ローカルcommitを照合し、実験時ソース・公開baseに対するseat修正・ライセンス・構築手順を復元しました。[新規実験の準備](new-experiments.md)。
- コード・データはMITの方針を反映し、別配布datasetにもLICENSEと同梱コードの上流表示を追加しました。合成人名・実験IDを保持する方針、元固定judgeへのVOTE提示、false PPと協調項目の範囲を論文・Dataset Cardと同期しました。
- 明示的な配布ファイル一覧を導入し、秘密設定・バックアップ・symlinkの混入を防ぐ検査、実際の配布ZIPの監査、ZIP再作成時のチェックサム更新を追加しました。

2026-09-10の採点訂正は保持しています。4評価と2補助診断、全2,792抽出・377変更抽出を再検証する入口は `release/reproduce.py` です。保存標本・意味判定・主要結果・6個のrawアーカイブを変更していません。原本と公開用のSHA-256を区別する来歴も保持しています。

## 検証記録

配布用回帰テスト16件、歴史ソースのハッシュ照合、Go 1.25.0によるサーバービルドと座席割当テストが成功しました。保存数値の再解析結果は [verification.json](verification.json) に記録します。実配布ZIPとdatasetの監査結果はZIPとは別に `dist/distribution-audit.json` へ保存します。元の配布監査JSONは古いZIPを検査していない記録だったため、現行監査と混同しないよう配布対象から除外しました。

元の除外内容は [cleanup-inventory.json](cleanup-inventory.json)、アーカイブ内部の来歴は別配布データの `provenance/release-manifest.json` と `transformations.jsonl` を参照してください。開発時のGit履歴は公開コードに含めません。復元した歴史ソースの由来commitは、監査用の記録として文書に保持しています。

## 配布と公開リンクの手順

1. `dist/camera-ready-code-1.0.0-rc.3.zip` の配布対象だけを、上記GitHubコードリポジトリの `main` に単一の初回コミットとして渡す。元のGit履歴と `dataset/` は含めない。
2. 別配布データのDataset Hub公開先を確定する。
3. `dataset/` を別途アップロードする。Gitでは意図的に除外しているため、コードだけをpushしてもデータは公開されない。`checksums.sha256`を含めた全ファイルを維持する。
4. データの公開後、README・Dataset Card・論文に取得先と対応する版を反映する。論文の公開識別子はCITATIONにも追加し、DOIは発行される場合だけ記載する。サーバーURLは [aiwolfdial/aiwolf-nlp-server](https://github.com/aiwolfdial/aiwolf-nlp-server) を使用する。
5. 公開先から再取得し、チェックサムと新しい出力先での再解析を確認する。

投稿前には採択通知・提出画面に従って著者情報、ページ数、必要ファイルを最終確認します。データ公開と論文提出は、このコード配布とは別の作業です。

## 配布物を更新する手順

新規ファイルは `release/package-files.txt` に明示追加します。意図した変更を確認後、関連テスト、`python3 release/seal.py`、新しい出力先での `python3 -B release/reproduce.py --data dataset --output <new-output>` の順に実行します。成功した `verification.json` を `docs/verification.json` へコピーし、`python3 release/package.py`、`python3 release/audit.py` を実行します。既存ZIPがある場合は、旧版も含めて `dist/` 内のZIPをチェックアウト内の非配布領域へ退避してから再作成します。監査は `dist/` 内の全ZIPを現行ファイルと照合します。公開済みの版を上書きせず、新しい版を使用してください。

原稿ソース・PDF（`paper/`）はこのコードリポジトリには含めません。保存結果の再解析と、外部モデル・サーバーを使う新規対戦は異なる手順です。モデルの同一出力を再生成できるとは保証していません。
