# 日本株・価格データ検証版

元の `ai-hedge-fund` のパイプラインを、J-Quants V2の日足と東証の100株単位で動かす最初の実験です。実注文は送りません。`momentum` は前日までの20営業日リターンが正なら買い、そうでなければ保有しない単純な比較用シグナルです。AIによる日本企業分析や投資成果を示すものではありません。

## 実行

Python 3.11以上を用意し、このソースのディレクトリでインストールします。

```bash
python -m venv .venv
.venv/bin/python -m pip install -e .
```

APIキーなしでCLI全体の実行を先に確認できます。結果は**架空の株価**です。

```bash
.venv/bin/python japan_pilot_smoke.py > /dev/null
```

`outputs/japan-pilot-smoke.json` に3週分の注文・仮想約定・残高が出ます。これは投資成績ではありません。

### 実データ

```bash
export JQUANTS_API_KEY='自分のJ-Quants-V2-APIキー'
mkdir -p outputs
.venv/bin/aihf japan-pilot.yaml --data-provider jquants \
  --tickers 7203,6758 --backtest \
  --start 2026-01-01 --date 2026-05-31 --out outputs/japan-pilot-result.json
```

APIキーはログ、公開リポジトリ、共有する結果ファイルに入れないでください。上記の日付はコマンド例です。契約プランで取得可能な期間に設定してください。無料プランには12週間の遅延があり、最新日を指定してもその日の株価は取得できません。`--tickers` は現段階では**数字のみ**の4桁または5桁の東証内国株コードを指定します。`.T` 接尾辞も受け付けます。アルファベットを含む証券コードと、銘柄ごとに売買単位が異なるETFなどを売買対象にする機能は未実装です。初回の取得には銘柄・ベンチマークごとに日足のAPIリクエストが発生します。出力はJSONで、注文・仮想約定・残高と各リバランス日の記録が含まれます。

```bash
.venv/bin/python -m pytest hedge_fund/data/test_jquants.py \
  hedge_fund/signals/test_momentum.py \
  hedge_fund/backtesting/test_japan_pilot.py
```

## 検証結果を読むとき

- `japan-pilot.yaml` の資金は100万円、週次リバランス、単一銘柄の上限は資金の50%、注文単位は100株です。小さな配分は100株に満たず、現金で残ります。
- `1306` はTOPIX連動ETFの価格を使う比較対象です。TOPIX指数のトータルリターンではありません。
- 終値の**未調整**データを使います。J-Quantsの`AdjFactor`が1以外の銘柄・期間は停止し、分割・併合を反映した持株数の再計算は行いません。無取引日は発注しません。保有銘柄の約定日バーがなければ結果の計算を停止します。
- 配当・ETF分配金、税、手数料、スリッページ、売買インパクト、実際の終値での約定可能性は反映されません。生存銘柄だけを後から選ぶことによるバイアスも別途検証が必要です。利益や取引可能な運用成績として公表しないでください。
- この接続は**株価のみ**です。既存のBuffettなどのエージェントや決算イベント戦略を日本株に移植するには、決算・財務データの時点整合性、単位・通貨、会計項目の対応付けが別途必要です。CLIではJ-Quants利用時に`momentum`以外のモデルを拒否します。
- 外部にデータや分析結果を継続配布する場合は、J-Quantsの契約条件を事前に確認してください。APIキーを持たない環境では、実データでの動作は未確認です。

資料: [J-Quants V2日足仕様](https://jpx-jquants.com/en/spec/eq-bars-daily), [J-Quants](https://jpx-jquants.com/en), [JPX売買単位](https://www.jpx.co.jp/equities/trading/domestic/03.html)
