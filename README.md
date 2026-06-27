# Veludo_Saas / Rosterly Architecture README

この README は、リポジトリ全体のアーキテクチャと System B / Rosterly の設計意図を説明するための文書です。  
System A の具体的な業務内容は扱わず、System B との連携境界のみを記載します。

## 展示リンク：https://api.rosterlyreverse.com/dashboard/demo/admin/veludo-demo-admin-20260413/

## 1. プロジェクト概要

Veludo_Saas は、公開フロントアプリと予約管理 SaaS Core を分離した Django プロジェクトです。

- `system_b_saas`
  - Rosterly SaaS Core
  - 複数店舗、予約、シフト、スタッフ、SSO、通知、Webhook、管理画面を担当します。
- `system_a_veludo`
  - System B と SSO / Integration API で連携する外部フロントアプリです。
  - 本 README では業務内容を記載しません。

全体としては、System B を「認証・予約・店舗運営データの中核」として設計し、System A はその外側にある利用画面として扱います。

## 2. 設計方針

このプロジェクトの中心方針は、フロント体験と予約基盤を分離することです。  
画面側の変更、店舗側の運用変更、予約処理、通知処理を同じ責務に混ぜず、変更の影響範囲を小さくすることを狙っています。

主な設計方針:

- 店舗データは `Tenant` を境界として分離する
- 認証と権限は `SaaSUser.role` と `tenant` を基準に判定する
- 予約対象は `Resource` として抽象化する
- 公開プロフィールは `ResourceProfile` として Resource 本体から分離する
- 予約可能時間は `Availability` として管理する
- 予約本体は `Booking` として確定情報を保持する
- 外部フロント連携は SSO と Integration API に分ける
- メール送信や Webhook は Celery worker に逃がし、HTTP request を重くしない
- ログイン不要で利用する画面には、推測困難な access token を発行する

## 3. システム全体構成

```text
User / Admin / Staff
  |
  | HTTPS
  v
Nginx
  |
  +--> System A
  |      - external frontend
  |      - SSO callback
  |      - System B Integration API client
  |
  +--> System B / Rosterly
         - Django
         - Gunicorn
         - Tenant management
         - Booking / Shift / Resource management
         - SSO provider
         - Integration API
         |
         +--> PostgreSQL
         +--> Redis
         +--> Celery worker
         +--> Discord OAuth
         +--> SMTP
         +--> Webhook receiver endpoint
```

System B は、店舗運営に必要な状態を集中管理します。  
System A は System B のデータを直接共有するのではなく、SSO と署名付き API によって必要な範囲だけ連携します。

## 4. アプリケーション境界

### System B / Rosterly の責務

System B は SaaS Core として以下を担当します。

- 店舗 `Tenant` 管理
- ユーザー `SaaSUser` 管理
- 管理者、スタッフ、一般ユーザーの権限管理
- Discord OAuth を利用したログイン
- System A 向け SSO provider
- 店舗別公開予約ページ
- 予約対象 `Resource` 管理
- 公開プロフィール `ResourceProfile` 管理
- 空き枠 `Availability` 管理
- サービス `ServicePreset` 管理
- 予約 `Booking` 管理
- ログイン不要の予約詳細・キャンセル・通報導線
- スタッフ招待リンク
- メール通知
- Webhook 通知
- Super Admin 向け横断管理

### System A との境界

System A は System B の内部 DB に直接依存しません。  
連携は以下の 2 種類に分けています。

- SSO
  - System B 側で本人確認し、System A にログイン状態を渡す
- Integration API
  - Resource、Profile、Availability、Booking などの必要データをサーバー間で同期する

この境界により、System A の表示や導線を変更しても、System B の予約基盤を壊しにくくしています。

## 5. ドメインモデル設計

System B の中心モデルは `Tenant` です。  
予約、スタッフ、サービス、メールテンプレート、通報などは Tenant に紐づき、店舗単位で扱います。

### Tenant

店舗または契約単位を表します。

保持する主な情報:

- 店舗名、slug、連絡先
- ロゴ
- 予約受付期間
- キャンセル期限
- カスタム規約
- API key / API secret
- Webhook URL
- 契約状態
- 削除・復旧関連情報

`Tenant.slug` は公開予約 URL の識別子としても利用します。

### SaaSUser

System B 内のログインユーザーです。

主な設計:

- `tenant`
  - 所属店舗
- `role`
  - `ADMIN`
  - `STAFF`
  - `CONSUMER`
- `discord_id`
  - Discord OAuth 連携用

同じ Django user でも、System B 上では tenant と role により操作できる範囲が変わります。

### Resource

予約対象を表します。  
人、設備、枠など、予約される対象を抽象化したモデルです。

主な設計:

- `tenant` に所属する
- `external_id` により外部システム側の ID と対応できる
- `linked_user` によりスタッフ本人の `SaaSUser` と 1:1 で紐づけられる
- `is_active` で予約対象としての有効・無効を管理する

### ResourceProfile

Resource の公開プロフィールです。  
Resource 本体とは分けているため、予約対象としての状態と表示用データを別々に扱えます。

保持する主な情報:

- 自己紹介
- タグ
- アバター URL
- YouTube URL
- 表示順
- 対応可能な所要時間
- metadata
- 規約同意状態

### Availability

Resource の予約可能時間またはシフト枠です。

現在の主な情報:

- `resource`
- `start_time`
- `end_time`
- `is_booked`
- `is_recurring`

現時点では、Booking と Availability の 0..1 関係を DB 制約として完成させている段階ではありません。  
将来的な改善案として、予約成立時に Availability を切り出し、booked slot と Booking を 0..1 で紐づける設計を想定しています。

### ServicePreset

店舗ごとの予約サービス定義です。

保持する主な情報:

- サービス名
- 説明
- 価格
- 所要時間
- 有効状態
- 表示順

予約画面では、選択された ServicePreset の所要時間をもとに予約終了時刻を計算します。

### Booking

予約本体です。  
予約時点の顧客情報、対象 Resource、サービス、時間、公開 access token を保持します。

保持する主な情報:

- `tenant`
- `resource`
- 顧客名、顧客メール
- customer id / Discord id
- `selected_service`
- `selected_service_name`
- `start_time`
- `end_time`
- `status`
- `public_access_token`
- `public_detail_url`
- 通報回数

`public_access_token` により、ログイン不要の予約詳細・キャンセル・通報ページを提供します。

### StaffInvite

スタッフ招待リンクを管理します。

設計ポイント:

- token は推測困難な文字列
- tenant に紐づく
- role を指定できる
- max uses / used count により使用回数を制限する
- expires at により期限切れを判定する
- 使用時は transaction と `select_for_update()` で invite をロックする

### SSOAuthCode

System A 向け SSO の短命認可 code を管理します。

設計ポイント:

- 生の code ではなく hash を保存する
- expires at で短命化する
- used at により one-time use を保証する
- client id / redirect uri / nonce を保持する
- exchange 時に transaction 内で使用済みにする

## 6. マルチテナント設計

System B は、1 つのアプリケーションで複数店舗を扱います。  
DB schema を店舗ごとに分けるのではなく、各モデルが `Tenant` を参照するアプリケーションレベルのマルチテナント設計です。

店舗別公開予約 URL:

```text
/dashboard/book/<tenant_slug>/
```

この `tenant_slug` により対象店舗を特定し、以下のデータを tenant 単位で取得します。

- Resource
- ResourceProfile
- Availability
- ServicePreset
- Booking
- EmailTemplate
- BookingReport

tenant 分離の考え方:

- 管理者は自店舗のデータだけ操作する
- スタッフは自分に紐づく Resource を中心に操作する
- 公開予約ページは対象 tenant の有効データのみ表示する
- Super Admin は全 tenant を横断して管理する
- Integration API は API key から tenant を特定する

## 7. 権限設計

System B の権限は、主に `SaaSUser.role` と `tenant` の組み合わせで判断します。

### ADMIN

店舗管理者です。

操作範囲:

- 店舗設定
- スタッフ招待
- Resource 管理
- ServicePreset 管理
- 予約管理
- メールテンプレート管理
- メッセージ・お知らせ管理

### STAFF

店舗スタッフです。

操作範囲:

- 自分に紐づく ResourceProfile
- 自分のシフト
- 自分に関係する予約
- 必要に応じた予約詳細確認

### CONSUMER

公開予約や外部フロント連携に使われる一般ユーザーです。  
管理画面の操作権限は持たせない前提です。

### Super Admin

システム全体を管理する権限です。  
tenant を横断して状態確認、停止、復旧、調査を行います。

## 8. SSO 設計

System B は System A に対する SSO provider として動きます。  
本人確認は Discord OAuth を利用し、System A には短命 code の交換結果としてユーザー情報を返します。

SSO の流れ:

```text
System A
  |
  | authorize request
  | client_id / redirect_uri / state / nonce
  v
System B /sso/authorize
  |
  | Discord OAuth
  v
System B
  |
  | create short-lived SSOAuthCode
  v
System A callback
  |
  | code exchange
  v
System B /api/v1/auth/sso/exchange
  |
  | verified user payload
  v
System A session login
```

セキュリティ設計:

- `state` で CSRF を防ぐ
- `nonce` でレスポンスの取り違えを防ぐ
- 認可 code は短命にする
- 認可 code は hash 保存する
- code exchange 後は `used_at` を更新し再利用を防ぐ
- `client_id` と `redirect_uri` を検証する
- exchange 処理は transaction 内で行う

## 9. Integration API 設計

Integration API は、System A と System B のサーバー間連携に使います。  
ブラウザから直接叩く公開 API ではなく、tenant ごとの API key / secret を使う内部連携 API です。

認証方式:

- `X-Tenant-Key`
- `X-Tenant-Timestamp`
- `X-Tenant-Signature`

署名対象:

- HTTP method
- path + query
- timestamp
- body hash

防御方針:

- API key から tenant を特定する
- API secret で HMAC 署名を検証する
- timestamp の許容範囲を設ける
- replay cache により同じ署名の再利用を拒否する
- API 停止状態の tenant は拒否する

Integration API の責務:

- 外部 ID と Resource の対応づけ
- ResourceProfile の同期
- Availability の同期
- Booking の作成・参照
- identity / role 情報の連携

## 10. 予約設計

予約は、店舗、予約対象、サービス、時間、顧客情報の組み合わせです。  
公開予約ページではログインなしで予約作成できますが、後端側で tenant / resource / service / availability / conflict を検証します。

予約作成時の主な検証:

- tenant が存在する
- tenant が削除・停止状態ではない
- resource が有効である
- service preset が有効である
- 開始時刻が予約可能期間内である
- 選択時間が availability の範囲内である
- 既存 booking と時間が重ならない
- buffer を含めても衝突しない

予約成立後:

- Booking を作成する
- public access token を発行する
- public detail URL を作成する
- transaction commit 後に通知 task を enqueue する

## 11. 重複予約防止設計

現在の実装では、予約作成を `bookings.services.create_confirmed_booking_with_lock()` に集約しています。
公開予約と Integration API 予約のどちらも、DB transaction 内で悲観ロックを取得してから Booking を作成します。

現在の状態:

- `transaction.atomic()` 内で処理する
- 対象 `Resource` を `select_for_update()` でロックする
- 対象時間をカバーする `Availability` を `select_for_update()` でロックする
- lock 取得後に、Availability の範囲確認を再実行する
- lock 取得後に、既存 Booking との時間範囲衝突を再確認する
- buffer を含めて衝突判定する
- 衝突時は Booking を作成せず `409 Conflict` 相当の応答にする
- DB commit 後に Celery task を enqueue する

現在の作成順序:

```text
1. transaction を開始する
2. 対象 Resource を select_for_update() でロックする
3. 対象時間を含む Availability を select_for_update() でロックする
4. Availability の範囲を確認する
5. 既存 Booking との重複を buffer 込みで確認する
6. Booking を作成する
7. public access token / public detail URL を保存する
8. transaction.on_commit() で通知 task を予約する
9. commit により lock を解放する
```

この方式により、同じ Resource / Availability に対する同時予約 request は DB transaction により直列化されます。
片方が先に予約を確定した場合、後続 request は lock 解放後の最新状態を見て失敗できます。

未実装の改善候補:

- 予約成立時に Availability を左側 available slot / booked slot / 右側 available slot に切り出す
- booked slot と Booking を 0..1 で紐づける
- DB constraint で booked slot の一意性をさらに強制する

## 12. ログイン不要機能の設計

予約詳細、キャンセル、通報などは、顧客が System B にログインしなくても利用できるようにしています。

設計:

- Booking ごとに `public_access_token` を発行する
- URL には DB id ではなく token を使う
- token は unique かつ推測困難な文字列にする
- 予約詳細ページで token から Booking を取得する
- キャンセルや通報も token を使って対象 Booking を特定する

目的:

- 顧客にアカウント作成を強制しない
- 予約後の確認導線をメールから直接開ける
- DB の連番 id を外部に出さない

## 13. 非同期処理設計

予約作成後の副作用は Celery worker に分離します。

非同期化する処理:

- 予約確定メール
- キャンセル通知
- Webhook 通知

設計ポイント:

- request / response を外部 API やメール送信の遅延に巻き込まない
- `transaction.on_commit()` により DB commit 後に task を enqueue する
- worker 側では booking id から最新状態を再取得する
- Webhook やメール送信失敗が予約作成そのものを巻き戻さない

これにより、予約作成の主処理と通知の副作用を分離しています。

## 14. 外部サービス連携設計

### Discord OAuth

System B のログインと SSO の本人確認に利用します。  
Discord id は `SaaSUser.discord_id` に保持し、同一ユーザーの識別に使います。

### SMTP

予約確定、キャンセルなどのメール通知に利用します。  
店舗ごとのメール文面は `EmailTemplate` として管理します。

### Webhook

tenant ごとに `webhook_url` を持ちます。  
予約作成後、worker が外部 endpoint に payload を送信します。

### Stripe

サブスクリプション関連の backend code は残っています。  
ただし、短期運用では課金機能を使わない方針のため、フロント上の導線は非表示にしています。

## 15. フロントエンド設計

System B は Django Template を中心に構成しています。  
管理画面、スタッフ画面、公開予約画面を同じ Django app 内で扱いながら、画面ごとに責務を分けています。

主な画面:

- tenant dashboard
- staff home
- profile management
- booking list
- public booking
- public booking detail
- schedule management
- message center
- announcement
- super admin dashboard

モバイル対応:

- 排班画面は独立して最適化
- それ以外の主要 System B ページには共通 mobile optimization partial を適用
- subscription 関連の導線は現時点では非表示

## 16. データ整合性の考え方

System B では、以下の層で整合性を守ります。

- DB model
  - foreign key
  - unique constraint
  - indexed token
- application service
  - tenant scope
  - role check
  - availability check
  - conflict check
- transaction
  - SSO code consumption
  - staff invite consumption
  - booking creation with Resource / Availability pessimistic lock
- async boundary
  - commit 後に通知を開始する

現在の予約競合防止は、後端検証に加えて Resource / Availability の悲観ロックを利用します。
さらに強い DB レベルの表現が必要な場合は、Availability を booked slot として切り出し、Booking と 0..1 で紐づける設計を導入する想定です。

## 17. ディレクトリ構成

```text
.
├── system_b_saas/
│   ├── bookings/          # Booking, booking report, booking API, async tasks
│   ├── dashboard/         # 管理画面、公開予約画面、SSO 周辺画面
│   ├── resources/         # Resource, Profile, Availability, ServicePreset
│   ├── tenants/           # Tenant, SaaSUser, SSOAuthCode, StaffInvite
│   └── config/            # Django settings, Celery
├── system_a_veludo/       # 外部フロントアプリ（業務内容は非記載）
├── docs/                  # 詳細技術文書
├── deploy_scripts/        # リリース補助スクリプト
├── init_sql/              # DB 初期化 SQL
├── media/                 # uploaded media
├── static_root/           # System A static output
├── static_root_saas/      # System B static output
├── docker-compose.yml
├── compose.rosterly.yml
└── compose.veludo.yml
```

## 18. 技術スタック

- Python
- Django
- Django Template
- PostgreSQL
- Redis
- Celery
- Gunicorn
- Docker Compose
- Nginx
- Discord OAuth
- SMTP
- Webhook

## 19. 最小限の起動・検証コマンド

この README の主目的は設計説明のため、詳細なデプロイ手順は `docs/` と `deploy_scripts/` を参照してください。  
ここでは開発時に使う最小限の入口だけ記載します。

ローカル起動:

```bash
docker compose up -d --build
```

System B migration:

```bash
docker compose exec system-b python /app/system_b_saas/manage.py migrate
```

System B check:

```bash
DEBUG=True SECRET_KEY=dev-check-key python3 system_b_saas/manage.py check
```

System B 本番反映の基本:

```bash
docker compose -f compose.rosterly.yml exec -T rosterly-core python /app/system_b_saas/manage.py migrate
docker compose -f compose.rosterly.yml exec -T rosterly-core python /app/system_b_saas/manage.py collectstatic --noinput
docker compose -f compose.rosterly.yml restart rosterly-core rosterly-worker
```

System A 本番反映の基本:

```bash
docker compose -f compose.veludo.yml exec -T system_a python /app/system_a_veludo/manage.py migrate
docker compose -f compose.veludo.yml exec -T system_a python /app/system_a_veludo/manage.py collectstatic --noinput
docker compose -f compose.veludo.yml restart system_a
```

## 20. 関連ドキュメント

- `docs/rosterly_detailed_technical_implementation_ja.md`
- `docs/rosterly_public_technical_architecture_ja.md`
- `docs/deployment_diagram_spec_zh.md`
- `docs/ER_diagram_explanation_zh.md`
- `技術文档_API接口规范_SystemA_SystemB.md`
- `技術文档_架构部署与运维.md`
- `迭代履历_CaseStudy_Veludo_Rosterly.md`

## 21. 注意事項

- System A の業務内容は本 README では扱いません。
- 実運用の `.env`、秘密鍵、DB password は Git にコミットしません。
- 本番環境で `docker compose down -v` は実行しません。
- モデル変更後は migration を作成・適用します。
- static ファイル変更後は `collectstatic` を実行します。
