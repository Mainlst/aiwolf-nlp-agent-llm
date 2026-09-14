# 採点修正・再解析の完了記録（2026-09-10）

> 上流での監査・訂正作業の記録です。以下の旧runパス・テスト件数・行番号は当時の記録であり、この配布物の実行先を示しません。配布版では[再現入口](../../../README.md)を使用し、正式訂正成果物は `dataset/raw/correction.tar.gz`（展開後 `evidence/correction/`）、現在の状況は[公開準備状況](../../../docs/release-status.md)を参照してください。

[再監査](reaudit-20260909.md)で特定した投票先・自己CO抽出の不備を修正し、元固定盤面、追加A、追加B、元400試合を保存データから再解析した。**主要比較の結果は変わらないが、宣言・投票一致などの診断値は訂正が必要だった。** 以下を現行結果とし、旧runの解析は履歴として保持する。

これは結果観察後の決定論的採点訂正であり、新規実験や独立した人手正解ラベルによる精度評価ではない。追加生成・追加対戦・LLM再判定・API呼出しは0。標本、対応、反復集約、意味判定、統計設定を変更していない。

## 訂正値と結論への影響

| 対象・指標 | 旧値 | 訂正値 |
| --- | ---: | ---: |
| 元固定P−PP：宣言・実投票一致 | 56/80 | 75/80 |
| 元固定P−PP：味方回避 | 39/80 | 51/80 |
| 元固定P−PP：村側投票先の宣言 | 37/80 | 52/80 |
| A01：宣言・実投票一致 | 47/80 | 62/80 |
| A00：宣言・実投票一致 | 55/80 | 75/80 |
| BのG01：到達後の両者CO | 2/57 | 0/57 |

元固定P+の対応する3成分は80/80、72/80、72/80のまま。一致のP+−P−PP差は＋30.0から＋6.25ポイントとなるため、旧値による成分差の大きさは使わない。複合成功65/80対2/80、主要差＋78.75ポイント・95%区間［＋69.96875, ＋87.5］、決定論的実行72/80対3/80、false PP 1/40対2/40は変わらない。

AのA11/A10/A01/A00の順に、一致は78/78/62/75、村側実投票は71/68/65/54、複合成功は61/60/3/3（各80）。主要比較A11−A10は0.0ポイント［−5.0, ＋5.0］のままで、同等性やVOTE時指示が不要であることを示さない。A00→A01では一致75/80→62/80と村側実投票54/80→65/80が逆方向に動き、一致と正しさを分ける必要があるという解釈は維持される。

Bの補助一致診断も全件再集計した。到達後の分母は条件ごとの自然到達試合数であり、全体の勝率の分母とは異なる。

| 条件 | 到達数 | 人狼の一致（訂正後） | 狂人の一致（訂正後） | 両者CO（訂正後） |
| --- | ---: | ---: | ---: | ---: |
| G00 | 59 | 56/59 | 53/59 | 0/59 |
| G10 | 57 | 54/57 | 54/57 | 0/57 |
| G01 | 57 | 51/57 | 52/57 | 0/57 |
| G11 | 56 | 56/56 | 56/56 | 56/56 |

Bの人狼陣営勝利53/55/55/65（各200）、主要G11−G00差＋6.0ポイント［−3.0, ＋15.5］、補助勝率対比、到達数、実投票は不変。G01の人狼COと狂人CO後の人狼COも2/57から0/57となる。G11の到達後の両者CO・両者村側投票・勝利56/56は不変で、到達後の集計だけから勝率向上や媒介効果を推定しない。

元400試合の勝利56/200対54/200、差＋1.0ポイント［−8.0, ＋10.0］、McNemar p値0.9142116371、全補助指標は不変。元固定とのHolm補正値も不変で、固定盤面の調整済みp値は0.0000199998。元400試合で使われた別実装の事後heuristicは維持し、訂正固定解析を参照するprovenance hashのみ変わった。

## 修正したコードと手順

- [scoring_rules.py](../fixed_boards/scoring_rules.py)：最後に明示された現在の投票候補または直接的な協調依頼の対象と、肯定的な一人称の役職COを抽出する。他者投票の説明、引用、否定・帰属・条件付きCOを除外し、`X is my vote` 等の語順や一意に解決できる局所的代名詞に対応した。
- [score_confirmation.py](../fixed_boards/score_confirmation.py)：既存の呼出しsignatureを保持して共通抽出器へ接続した。条件・役職・実投票・勝敗・特定IDによる補正は使わない。
- [reanalyze.py](reanalyze.py)：保存source・凍結証拠を検証し、元データと別の出力先に全4解析を保存するオフライン入口を追加した。入力とコードが途中で変化した場合、正式manifestを完成させない。既存の訂正出力の上書きも拒否する。
- [訂正规則](scoring-correction-protocol.md)：事後修正の位置づけ、変更する測定、保持する評価手順と限界を明文化した。

抽出器の版は `english-declarations-v2-20260910`、`scoring_rules.py` のSHA-256は `8d0fb29a936cbfc3674d0fe0ef15a404e8ee335ec7f51c249b876e5658486a6c`。

再解析では元固定の480生成を保存済みの3判定多数決へ再結合し、追加Aの480共有TALK／960評価単位、追加Bの200ブロック／800試合を同じ採用範囲で処理した。元固定の40内容cluster・10,000 bootstrap・100,000符号反転、Aの40cluster・Bの200blockの10,000 bootstrap、元400試合の100,000 bootstrapと元のseed・多重性処理を保持した。

## 検証と成果物

正式成果物は pp-scoring-correction-20260910-v1（旧run: `../../runs/pp-scoring-correction-20260910-v1/`） に保存した。

| 内容 | 記録 |
| --- | --- |
| 元固定・A・B・元400試合の訂正解析 | original-fixed（旧run: `../../runs/pp-scoring-correction-20260910-v1/original-fixed/analysis.json`）、A（旧run: `../../runs/pp-scoring-correction-20260910-v1/A/analysis.json`）、B（旧run: `../../runs/pp-scoring-correction-20260910-v1/B/analysis.json`）、original-normal（旧run: `../../runs/pp-scoring-correction-20260910-v1/original-normal/analysis.json`） |
| 全解析フィールドの旧新差分 | changes.json（旧run: `../../runs/pp-scoring-correction-20260910-v1/changes.json`） |
| 全2,792対象発話の入力・抽出・根拠と旧新比較 | extractions.jsonl（旧run: `../../runs/pp-scoring-correction-20260910-v1/extractions.jsonl`）、changed-extractions.jsonl（旧run: `../../runs/pp-scoring-correction-20260910-v1/changed-extractions.jsonl`） |
| 入力hash・訂正source snapshot・出力hash | input-manifest.json（旧run: `../../runs/pp-scoring-correction-20260910-v1/input-manifest.json`）、manifest.json（旧run: `../../runs/pp-scoring-correction-20260910-v1/manifest.json`） |
| 元固定・Aの全960 TALK点検 | parser-review-notes.md（旧run: `../../runs/pp-scoring-correction-20260910-v1/verification/parser-review-notes.md`）と同ディレクトリの全件ledger・再現スクリプト |
| Bの458役職対話／1,832 TALKと別作成ledgerの照合 | b-review-notes.md（旧run: `../../runs/pp-scoring-correction-20260910-v1/verification/b-review-notes.md`）、正式出力照合（旧run: `../../runs/pp-scoring-correction-20260910-v1/verification/b-formal-output-verification.json`）（不一致0） |
| 全161テスト | unittest-final.log（旧run: `../../runs/pp-scoring-correction-20260910-v1/verification/unittest-final.log`）（成功） |
| 元入力保全・別出力への再実行・主要指標不変の最終照合 | final-reanalysis-verification.json（旧run: `../../runs/pp-scoring-correction-20260910-v1/verification/final-reanalysis-verification.json`）、同ディレクトリの `verify-correction.py` |

元固定・Aの陽性で確認された投票対象の誤抽出はそれぞれ20・22発話。全条件の対照も点検した。全発話で旧新の抽出結果が変わる377件には対照や途中TALKも含まれ、377件すべてを主要指標の誤りや独立に確定した意味誤判定と数えるものではない。

Aの16,685＋Bの59,273＝75,958凍結証拠を集合・hash・固定時stateまで再確認した。元固定・元400試合・元判定runの19,844ファイルも既存checkpointのhashと一致。元runのsource、manifest、frozen、判定、旧解析と過去アーカイブは保持している。

別ディレクトリへの再実行で、4つの `analysis.json` を含む113/114成果物がバイト単位で一致した。残り1件の手掛かり別監査JSONも集計は一致し、出力先を記録するrun ID・パス2項目だけが異なる。採点コード・入力台帳・差分台帳も一致した。

再実行はリポジトリのルートで、新しい出力先を指定する。

```bash
.venv/bin/python3 -m experiments.pp.revision.reanalyze --output experiments/runs/pp-scoring-correction-local-check
PYTHONPATH="$PWD/src:$PWD" .venv/bin/python3 -m unittest discover -s tests -v
```

## 原稿と残る範囲

英語原稿（上流の原稿・配布対象外）のTable 3／6、付録A／B、関連考察と採点方法の開示、[README](README.md)、[査読回答案](../../../docs/release-status.md)、[成果物索引](../../../docs/artifact-index.csv)、計画・TODOを訂正値に更新した。過去の実行・編集記録は旧値を残して履歴と明記した。日本語参考版は今回の同期対象に含めていない。

正式解析と原稿の73項目を照合し、独立ディレクトリでXeLaTeXを再ビルドした。現行英語PDF（上流の原稿・配布対象外）は本文9ページ・全18ページ。未解決参照・引用、Overfull警告はなく、変更箇所を含む8ページを目視確認した。原稿検証記録（旧run: `../../runs/pp-scoring-correction-writeup-20260910-v1/verification.json`）と同ディレクトリの `before/`、`after/`、`diffs/`、`build/` に控え・hash・差分・ビルドログを保存した。最終PDFのSHA-256は `bd0e0b4cd0989ecc11d07b6f34e3cdee457c5ded061bc4482f3cf2a5f51c9a27`。

抽出するのは明示的な候補・依頼であり、内心の投票意図や確約の強さではない。一意な暫定候補は含み、矛盾した依頼では最後の対象を採る。曖昧な代名詞や未対応の表現は残り得る。コードと別に作成したledgerによる点検も、独立した人手goldによる精度検証の代わりにはならない。元のLLM意味判定自体の人手精度評価は今回行っていない。

元400試合の絶対パス等、公開配布用の可搬化は[公開計画](release-plan.md)の残作業である。今回の入口は保存済みローカルデータをサーバー・APIなしで再解析するもので、公開パッケージの完成・アップロード・論文提出を意味しない。
